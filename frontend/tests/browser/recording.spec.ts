import { test, expect, type Page, type APIRequestContext } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { createHash } from 'node:crypto'

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
  await page.getByRole('button', { name: 'Play recording', exact: true }).click()
  const audio = page.getByLabel('Conversation source audio')
  await expect(audio).toBeVisible()
  await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).readyState), { timeout: 10000 }).toBeGreaterThanOrEqual(1)
  await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).duration)).toBeGreaterThan(0)
  await audio.evaluate(async node => { await (node as HTMLAudioElement).play() })
  await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).currentTime)).toBeGreaterThan(0)
  await audio.evaluate(node => (node as HTMLAudioElement).pause())
  await expect(page.getByText(/The transcript will appear when processing is complete/)).toBeVisible()
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
  await page.goto('/recordings')
  await page.getByPlaceholder('Search by name, phone, or email').fill(label)
  await expect(page.getByRole('heading', { name: label, exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Download', exact: true })).toHaveCount(1)
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download', exact: true }).click()
  const savedDownload = await download
  expect(savedDownload.suggestedFilename()).toMatch(/\.mp3$/)
  expect(await savedDownload.failure()).toBeNull()
  await page.goto(detail)
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
  await expect(page.getByRole('status').filter({ hasText: 'Finish saving or detach the audio before leaving' })).toBeVisible()
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
  for (const [route, heading] of [['/dashboard', 'Sales intelligence at a glance'], ['/enquiries', 'Enquiries'], ['/enquiries/new', 'Capture a new enquiry'], ['/settings#connections', 'Team & account'], ['/settings', 'Team & account']]) {
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

async function fixtureRecording(request: APIRequestContext, name: string, title: string) {
  // Generated synthetic WAV through real reservations, signed upload and finalization.
  const wav = Buffer.alloc(44 + 32000)
  wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8)
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22)
  wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34)
  wav.write('data', 36); wav.writeUInt32LE(32000, 40)
  for (let i = 0; i < 16000; i++) wav.writeInt16LE(Math.round(Math.sin(i * Math.PI * 2 * 440 / 16000) * 1000), 44 + i * 2)
  const created = await request.post(`${api}/api/v1/conversations`, { headers: { ...sourceHeaders, 'Idempotency-Key': crypto.randomUUID() }, data: { customer_name: name, phone: '+91 90000 00000', title, consent_confirmed: true, audio_only: false, audio_bucket: 'walkin-audio' } })
  expect(created.ok(), await created.text()).toBeTruthy()
  const conversationId = (await created.json()).id
  const leased = await request.post(`${api}/api/v1/conversations/${conversationId}/lease`, { headers: sourceHeaders, data: { tab_id: crypto.randomUUID(), takeover: false } })
  expect(leased.ok(), await leased.text()).toBeTruthy()
  const lease = await leased.json()
  const chunk = { conversation_id: conversationId, part_id: lease.part_id, lease_token: lease.lease_token, sequence: 0, start_sample: 0, sample_count: 16000, sample_rate: 16000, bytes: wav.length, sha256: createHash('sha256').update(wav).digest('hex'), mime: 'audio/wav' }
  const authorized = await request.post(`${api}/api/v1/conversations/${conversationId}/chunks/authorize`, { headers: sourceHeaders, data: chunk })
  expect(authorized.ok(), await authorized.text()).toBeTruthy()
  const permit = await authorized.json()
  const uploaded = await request.put(permit.upload_url, { headers: { ...sourceHeaders, ...permit.headers }, data: wav })
  expect(uploaded.ok(), await uploaded.text()).toBeTruthy()
  const confirmed = await request.post(`${api}/api/v1/conversations/${conversationId}/chunks/confirm`, { headers: sourceHeaders, data: chunk })
  expect(confirmed.ok(), await confirmed.text()).toBeTruthy()
  const finalized = await request.post(`${api}/api/v1/conversations/${conversationId}/finalize`, { headers: sourceHeaders, data: { part_id: lease.part_id, lease_token: lease.lease_token, expected_chunks: 1, total_samples: 16000 } })
  expect(finalized.ok(), await finalized.text()).toBeTruthy()
  return conversationId as string
}

test('upstream walk-in conversion keeps the same saved audio and durable enquiry pipeline', async ({ page, request }) => {
  const name = `Synthetic walk-in ${Date.now()}`
  await page.goto('/walkins')
  const visitId = await page.evaluate(async ({ workspace, name }) => {
    const service = await import('/src/services/walkinService.ts')
    const visit = await service.registerWalkin(workspace, { customerName: name, mobileNumber: '+91 90000 00000', email: '', companyName: '', purpose: 'Product enquiry', purposeDescription: '', productOrService: 'Test model', department: 'Sales', requestedEmployee: '', appointmentStatus: 'Walk-in', appointmentReference: '', internalNotes: '' }, crypto.randomUUID())
    await service.updateWalkinStatus(workspace, visit.id, 'In Discussion', visit.updatedAt)
    const current = await service.getWalkinVisit(workspace, visit.id)
    const empty = await service.getWalkinRequirements(workspace, visit.id)
    await service.saveWalkinRequirements(workspace, visit.id, { ...empty, detailedRequirement: 'Synthetic fixture requirements', productOrService: 'Test model', quantity: '2' }, current!.updatedAt)
    return visit.id
  }, { workspace, name })
  const conversationId = await fixtureRecording(request, name, `Walk-in visit ${visitId}`)
  await page.evaluate(async ({ workspace, visitId, conversationId }) => {
    const service = await import('/src/services/walkinService.ts')
    await service.attachWalkinRecording(workspace, visitId, conversationId)
    sessionStorage.setItem(`guruconnect:walkin-audio:00000000-0000-0000-0000-000000000001:${workspace}:${visitId}`, conversationId)
  }, { workspace, visitId, conversationId })
  await page.goto(`/walkins/${visitId}`)
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
  // Local owner may review recordings; capture stays receptionist-only.
  await expect(page.getByLabel('Choose audio file')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Review recording/ })).toBeVisible()
  await page.getByRole('button', { name: /create sales enquiry/i }).click()
  await expect(page).toHaveURL(/\/enquiries\/new$/)
  await expect(page.locator('#customerName')).toHaveValue(name)
  await page.locator('#branch').fill('Synthetic conversion showroom')
  await save(page, name)
  const id = new URL(page.url()).pathname.split('/').at(-1)
  const enquiry = await request.get(`${api}/api/v1/enquiries/${id}`, { headers: sourceHeaders })
  expect(await enquiry.json()).toMatchObject({ sourceEventId: `walkin:${visitId}`, conversationId })
  await page.reload()
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
})

test('a failed transfer survives reload and recovers the same audio before the enquiry saves', async ({ page }) => {
  const name = `Recovered enquiry ${Date.now()}`
  await customer(page, name); await consent(page)
  await page.route('**/api/v1/local-audio/**', route => route.abort('internetdisconnected'))
  await page.getByLabel('Choose audio file').setInputFiles(microphoneFile)
  await page.getByRole('button', { name: 'Upload & process', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Recover audio', exact: true })).toBeVisible({ timeout: 25000 })
  await expect(page.getByRole('button', { name: 'Save enquiry', exact: true })).toBeDisabled()
  await page.unroute('**/api/v1/local-audio/**')
  await page.reload()
  await expect(page.locator('#customerName')).toHaveValue(name)
  await page.getByRole('button', { name: 'Recover audio', exact: true }).click()
  await expect(page.getByText('Audio saved in your workspace', { exact: true })).toBeVisible({ timeout: 20000 })
  await save(page, name)
  await playable(page)
})


test('another account’s pending audio is not offered in the current account', async ({ page }) => {
  await customer(page, `Scoped recovery ${Date.now()}`)
  const otherId = crypto.randomUUID()
  await page.evaluate(async ({ workspace, otherId }) => {
    const outbox = await import('/src/recording/outbox.ts')
    const scope = outbox.scopeKey({ userId: 'another-account', workspaceId: workspace })
    await outbox.saveRecovery({ key: `${scope}/${otherId}`, scope, conversationId: otherId, partId: crypto.randomUUID(), leaseToken: crypto.randomUUID(), totalSamples: 16000, chunkCount: 1, stopped: true, updatedAt: new Date().toISOString() })
  }, { workspace, otherId })
  await page.reload()
  await expect(page.getByRole('button', { name: 'Recover audio', exact: true })).toHaveCount(0)
  expect(await page.evaluate(async workspace => {
    const outbox = await import('/src/recording/outbox.ts')
    return (await outbox.listRecoveries({ userId: 'another-account', workspaceId: workspace })).length
  }, workspace)).toBe(1)
})
