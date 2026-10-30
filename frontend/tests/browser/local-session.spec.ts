import { test, expect } from '@playwright/test'

const api = 'http://127.0.0.1:8011'

test('real local session uploads during capture, queues live work, searches demo inventory and preserves its cart', async ({ page, request }, testInfo) => {
  test.setTimeout(60000)
  const missingRoutes: string[] = []
  const errors: string[] = []
  page.on('response', response => {
    if (response.status() === 404 && response.url().includes('/api/')) missingRoutes.push(response.url())
  })
  page.on('pageerror', error => errors.push(error.message))
  // Real SQLite API, explicit empty provider keys, and no mocked responses.
  await page.setViewportSize({ width: 1680, height: 960 })
  await page.goto('/ai-chats')
  const name = `Local session regression ${Date.now()}`
  await page.getByRole('textbox', { name: 'Customer name', exact: true }).fill(name)
  await page.getByRole('textbox', { name: 'Phone number', exact: true }).fill('09674738429')
  await page.getByRole('textbox', { name: 'Email address', exact: true }).fill('local-session@example.test')
  const opened = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/api/voice-sessions'))
  await page.getByRole('button', { name: 'Open customer session', exact: true }).click()
  const session = (await (await opened).json()).session
  await expect(page.getByRole('button', { name: 'Start session', exact: true })).toBeVisible()
  await expect(page.getByText('Not Found', { exact: true })).toHaveCount(0)
  const note = 'Customer asked about ceiling fans and a water pump motor.'
  await page.getByLabel('Add internal note', { exact: true }).fill(note)
  await page.getByRole('button', { name: 'Add note', exact: true }).click()
  await expect(page.getByText(note, { exact: true })).toBeVisible()
  await page.getByRole('checkbox', { name: /permission from all intended participants/ }).check()
  const created = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/api/v1/conversations'))
  await page.getByRole('button', { name: 'Start session', exact: true }).click()
  const recording = await (await created).json()
  await expect(page.getByRole('button', { name: 'End session', exact: true })).toBeVisible()
  await expect.poll(async () => {
    const response = await request.get(`${api}/api/v1/conversations/${recording.id}/progress`)
    expect(response.ok()).toBeTruthy()
    return (await response.json()).chunks.saved_samples
  }, { timeout: 30000 }).toBeGreaterThanOrEqual(160000)
  await expect.poll(async () => {
    const response = await request.get(`${api}/api/v1/conversations/${recording.id}/progress`)
    return (await response.json()).jobs.some((job: { kind: string }) => job.kind === 'live_transcribe')
  }, { timeout: 10000 }).toBeTruthy()
  const progress = await (await request.get(`${api}/api/v1/conversations/${recording.id}/progress`)).json()
  expect(progress.conversation.capture_state).toBe('recording')
  expect(progress.conversation.upload_state).toBe('pending')
  const live = await (await request.get(`${api}/api/voice-sessions/${session.id}/live`)).json()
  expect(live.processing_mode).toBe('progressive')
  expect(live.provider_readiness.speech_configured).toBe(false)
  expect(live.provider_readiness.translation_configured).toBe(false)
  expect(live.transcript_preview).toEqual([])
  expect(live.recommendations).toEqual([])
  expect(live.demo).toBe(true)
  await expect(page.getByText('Transcribing & translating', { exact: true })).toHaveCount(0)

  async function addFromCatalogue(query: string, product: string, first = false) {
    if (first) await page.getByRole('button', { name: 'Search catalogue', exact: true }).click()
    else await page.getByRole('button', { name: 'Add another product', exact: true }).first().click()
    const dialog = page.getByRole('dialog', { name: 'Add another product', exact: true })
    await expect(dialog).toBeVisible()
    await dialog.getByRole('textbox', { name: 'Search inventory', exact: true }).fill(query)
    await dialog.getByRole('button', { name: 'Search', exact: true }).click()
    await dialog.getByRole('button', { name: new RegExp(product) }).click()
    await expect(dialog).toHaveCount(0)
  }
  await addFromCatalogue('fan', 'Aero BLDC ceiling fan 1200 mm', true)
  await addFromCatalogue('motor', 'Water pump motor 1 HP')
  await addFromCatalogue('fan', 'Luxe ceiling fan 1200 mm with LED light')
  const cart = page.getByRole('region', { name: 'Customer cart', exact: true })
  await expect(cart.locator('.lws-cart-row')).toHaveCount(3)
  await page.getByRole('button', { name: 'Increase quantity of Aero BLDC ceiling fan 1200 mm', exact: true }).click()
  await page.getByRole('spinbutton', { name: 'Discount for Aero BLDC ceiling fan 1200 mm', exact: true }).fill('10')
  await expect(page.getByRole('spinbutton', { name: 'Quantity of Aero BLDC ceiling fan 1200 mm', exact: true })).toHaveValue('2')
  const complete = cart.getByRole('button', { name: 'Complete cart', exact: true })
  for (const viewport of [{ width: 1680, height: 960 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport)
    await complete.scrollIntoViewIfNeeded()
    await expect(complete).toBeInViewport()
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1), `Page overflow at ${viewport.width}px`).toBe(false)
    if (viewport.width > 760) {
      const target = await complete.boundingBox()
      const footer = await page.locator('.lws-recording-footer').boundingBox()
      expect(target && footer && target.y + target.height <= footer.y + 1, 'Cart completion must scroll above the recording footer').toBeTruthy()
    }
    await page.screenshot({ path: testInfo.outputPath(`cart-${viewport.width}.png`), fullPage: true })
  }
  await page.getByRole('button', { name: 'End session', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Record again', exact: true })).toBeVisible({ timeout: 20000 })
  expect((await (await request.get(`${api}/api/v1/conversations/${recording.id}`)).json()).upload_state).toBe('saved')
  await page.reload()
  await page.getByRole('button', { name: new RegExp(name) }).first().click()
  await expect(page.getByText(note, { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Customer cart', exact: true }).locator('.lws-cart-row')).toHaveCount(3)
  await expect(page.getByRole('spinbutton', { name: 'Quantity of Aero BLDC ceiling fan 1200 mm', exact: true })).toHaveValue('2')
  expect(missingRoutes).toEqual([])
  expect(errors).toEqual([])
})
