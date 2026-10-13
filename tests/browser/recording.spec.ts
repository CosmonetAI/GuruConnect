import { test, expect, type Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'

const microphoneFile = fileURLToPath(new URL('../fixtures/microphone.wav', import.meta.url))
const api = process.env.PLAYWRIGHT_API_URL || 'http://127.0.0.1:8011'
const workspace = '00000000-0000-0000-0000-000000000002'
const sourceHeaders = { Origin: 'http://127.0.0.1:5177', 'X-Workspace-ID': workspace }

async function customer(page: Page, label: string) {
  await page.goto('/enquiries/new')
  await expect(page.getByRole('heading', { name: 'Capture a new enquiry' })).toBeVisible()
  await page.locator('#customerName').fill(label)
  await page.locator('#phone').fill('+91 90000 00000')
  await page.locator('#branch').fill('Synthetic test showroom')
  await page.locator('#assignedExecutive').fill('Local test executive')
  await expect(page.getByText('Checking processing connections…')).toHaveCount(0)
}
async function consent(page: Page) {
  await page.getByRole('checkbox', { name: /permission from all intended participants/ }).check()
}
async function save(page: Page, name: string) {
  await expect(page.getByRole('button', { name: 'Save enquiry', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Save enquiry', exact: true }).click()
  await expect(page).toHaveURL(/\/enquiries\/[a-f0-9-]{36}$/)
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
}
async function playable(page: Page) {
  await expect(page.getByText('Speech transcription needs attention', { exact: true })).toBeVisible({ timeout: 20000 })
  await page.getByRole('button', { name: 'Load playback', exact: true }).click()
  const audio = page.getByLabel('Conversation source audio')
  await expect(audio).toBeVisible()
  await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).readyState), { timeout: 10000 }).toBeGreaterThanOrEqual(1)
  await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).duration)).toBeGreaterThan(0)
  await audio.evaluate(async node => { await (node as HTMLAudioElement).play() })
  await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).currentTime)).toBeGreaterThan(0)
  await audio.evaluate(node => (node as HTMLAudioElement).pause())
  await expect(page.getByText(/The transcript appears when the background worker completes/)).toBeVisible()
}

test.beforeEach(async ({ page }) => {
  // All browser calls stay on the isolated local test stack. No keys are set,
  // and the actual worker must report unavailable providers instead of inventing text.
  await page.route('**/*', route => {
    const target = new URL(route.request().url())
    if (!['http:', 'https:'].includes(target.protocol) || ['127.0.0.1', 'localhost'].includes(target.hostname)) return route.continue()
    return route.abort('blockedbyclient')
  })
})

test('real microphone captures, pauses, saves privately, and plays while missing-provider processing is blocked', async ({ page }) => {
  const label = `Microphone enquiry ${Date.now()}`
  await customer(page, label)
  await expect(page.getByRole('button', { name: 'Start recording', exact: true })).toBeDisabled()
  await consent(page)
  await page.getByRole('button', { name: 'Start recording', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Pause', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Save enquiry', exact: true })).toBeDisabled()
  // A nonzero clock proves real AudioWorklet PCM reached the capture state.
  await expect.poll(() => page.locator('.elapsed-timer').innerText()).not.toBe('00:00')
  await page.getByRole('button', { name: 'Pause', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Resume', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Resume', exact: true }).click()
  await page.getByRole('button', { name: 'Stop & save', exact: true }).click()
  await expect(page.getByText('Audio saved in your workspace', { exact: true })).toBeVisible({ timeout: 20000 })
  await save(page, label)
  await playable(page)
})

test('WAV upload uses the durable recording pipeline and enquiry ID remains valid after reload', async ({ page }) => {
  const label = `Uploaded enquiry ${Date.now()}`
  await customer(page, label)
  await page.getByLabel('Choose audio file').setInputFiles(microphoneFile)
  await expect(page.getByRole('button', { name: 'Upload & process', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Save enquiry', exact: true })).toBeDisabled()
  await consent(page)
  await page.getByRole('button', { name: 'Upload & process', exact: true }).click()
  await expect(page.getByText('Audio saved in your workspace', { exact: true })).toBeVisible({ timeout: 20000 })
  await save(page, label)
  const detail = page.url()
  await page.reload()
  await expect(page.getByRole('heading', { name: label, exact: true })).toBeVisible()
  await playable(page)
  await page.getByRole('link', { name: 'Back to enquiries', exact: true }).click()
  const card = page.getByRole('article').filter({ has: page.getByRole('heading', { name: label, exact: true }) })
  await expect(card).toBeVisible()
  await card.locator('.enquiry-id').click()
  await expect(page).toHaveURL(detail)
  await expect(page.getByRole('heading', { name: label, exact: true })).toBeVisible()
})

test('corrupt and oversized files are rejected before any conversation is created', async ({ page }) => {
  await customer(page, `Invalid file enquiry ${Date.now()}`)
  await consent(page)
  const created: string[] = []
  page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/conversations') created.push(request.url()) })
  await page.getByLabel('Choose audio file').setInputFiles({ name: 'corrupt.wav', mimeType: 'audio/wav', buffer: Buffer.from('This is not sound.') })
  await page.getByRole('button', { name: 'Upload & process', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'could not be decoded' })).toBeVisible()
  expect(created).toEqual([])
  await page.getByRole('button', { name: 'Remove selected file', exact: true }).click()
  // Construct only synthetic metadata here. The byte/duration limits and real
  // decoder rejection are independently exercised by the upload unit tests.
  await page.getByLabel('Choose audio file').evaluate(input => {
    const file = new File(['synthetic'], 'oversized.wav', { type: 'audio/wav' })
    Object.defineProperty(file, 'size', { value: 100 * 1024 * 1024 + 1 })
    const files = new DataTransfer(); files.items.add(file)
    ;(input as HTMLInputElement).files = files.files
    input.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await expect(page.getByRole('alert').filter({ hasText: 'up to 100 MB' })).toBeVisible()
  expect(created).toEqual([])
})

test('navigation and Cancel stay guarded until the microphone recording is finalized', async ({ page }) => {
  await customer(page, `Guarded enquiry ${Date.now()}`); await consent(page)
  await page.getByRole('button', { name: 'Start recording', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Stop & save', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cancel', exact: true })).toBeDisabled()
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Enquiries', exact: true }).click()
  await expect(page).toHaveURL(/\/enquiries\/new$/)
  await expect(page.getByRole('button', { name: 'Stop & save', exact: true })).toBeVisible()
  await expect(page.getByRole('alert').filter({ hasText: 'Finish saving or recover this audio before leaving' })).toBeVisible()
  await expect.poll(() => page.locator('.elapsed-timer').innerText()).not.toBe('00:00')
  await page.getByRole('button', { name: 'Stop & save', exact: true }).click()
  await expect(page.getByText('Audio saved in your workspace', { exact: true })).toBeVisible({ timeout: 20000 })
  await expect(page.getByRole('button', { name: 'Cancel', exact: true })).toBeEnabled()
})

test('390 px navigation opens every core route without horizontal overflow', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  // A long synthetic record exercises the real persistent list's wrapping.
  const response = await request.post(`${api}/api/v1/enquiries`, { headers: { ...sourceHeaders, 'Idempotency-Key': crypto.randomUUID() }, data: {
    customerName: 'Synthetic mobile customer with a long customer name', phone: '+91 90000 00000', channel: 'Incoming Call', branch: 'A lengthy showroom branch for mobile wrapping', assignedExecutive: 'Mobile test executive', customerType: 'Retail', productRequirements: [], priority: 'Normal',
  } })
  expect(response.ok()).toBeTruthy()
  for (const [route, heading] of [['/dashboard', 'Your conversations, connected'], ['/enquiries', 'Enquiries'], ['/enquiries/new', 'Capture a new enquiry'], ['/integrations', 'Connections & processing'], ['/settings', 'Team & account']]) {
    await page.goto(route)
    await expect(page.getByRole('heading', { name: heading, exact: true }).first()).toBeVisible()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click()
    const navigation = page.getByRole('navigation', { name: 'Mobile navigation' })
    await expect(navigation).toBeVisible()
    await expect(navigation.getByRole('link', { name: 'Enquiries', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Close navigation', exact: true }).click()
    await expect(navigation).toHaveCount(0)
  }
})
