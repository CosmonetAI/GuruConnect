import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'

test('real local session without email reviews all sales documents and downloads sample PDFs', async ({ page }, testInfo) => {
  test.setTimeout(60000)
  const missingRoutes: string[] = [], pageErrors: string[] = [], emailRequests: string[] = []
  page.on('response', response => { if (response.status() === 404 && response.url().includes('/api/')) missingRoutes.push(response.url()) })
  page.on('pageerror', error => pageErrors.push(error.message))
  page.on('request', request => { if (request.method() === 'POST' && /\/email(?:\?|$)/.test(request.url())) emailRequests.push(request.url()) })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/ai-chats')
  const customer = `PDF customer ${Date.now()}`
  await page.getByRole('textbox', { name: 'Customer name', exact: true }).fill(customer)
  await page.getByRole('textbox', { name: 'Phone number', exact: true }).fill('9876543219')
  // The email is deliberately blank; no API responses or provider calls are mocked.
  await expect(page.getByRole('textbox', { name: 'Email address', exact: true })).toHaveValue('')
  const opened = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/api/voice-sessions'))
  await page.getByRole('button', { name: 'Open customer session', exact: true }).click()
  expect((await (await opened).json()).session).toBeTruthy()
  await expect(page.getByRole('button', { name: 'Start session', exact: true })).toBeVisible()

  for (const [index, query, product] of [[0, 'fan', 'Aero BLDC ceiling fan 1200 mm'], [1, 'motor', 'Water pump motor 1 HP']] as const) {
    await page.getByRole('button', { name: index ? 'Add another product' : 'Search catalogue', exact: true }).first().click()
    const search = page.getByRole('dialog', { name: 'Add another product', exact: true })
    await search.getByRole('textbox', { name: 'Search inventory', exact: true }).fill(query)
    await search.getByRole('button', { name: 'Search', exact: true }).click()
    await search.getByRole('button', { name: new RegExp(product) }).click()
    await expect(search).toHaveCount(0)
  }
  const cart = page.getByRole('region', { name: 'Customer cart', exact: true })
  await expect(cart.locator('.lws-cart-row')).toHaveCount(2)
  for (const [type, title] of [['invoice', 'Send invoice'], ['quote', 'Send quote'], ['sales-order', 'Create sales order']] as const) {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await cart.getByRole('button', { name: 'Complete cart', exact: true }).click()
    const modal = page.getByRole('dialog', { name: 'Create sales document', exact: true })
    await expect(modal).toBeVisible()
    await expect(modal.getByText(customer, { exact: true })).toBeVisible()
    await expect(modal.getByText('9876543219', { exact: true })).toBeVisible()
    await expect(modal.getByText('Demo catalogue', { exact: true })).toBeVisible()
    await expect(modal.getByRole('radio')).toHaveCount(3)
    await expect(modal.getByRole('table').getByText('Aero BLDC ceiling fan 1200 mm', { exact: true })).toBeVisible()
    await expect(modal.getByRole('table').getByText('Water pump motor 1 HP', { exact: true })).toBeVisible()
    if (type === 'invoice') {
      await modal.locator('.sdm-body').evaluate(element => { element.scrollTop = 0 })
      await page.screenshot({ path: testInfo.outputPath('sales-document-desktop.png'), fullPage: true })
    }
    await modal.getByText(title, { exact: true }).click()
    await expect(modal.getByRole('radio', { name: new RegExp(title) })).toBeChecked()
    await expect(modal.getByRole('textbox', { name: 'To (customer email)', exact: true })).toHaveValue('')
    await expect(modal.getByRole('textbox', { name: 'Email subject', exact: true })).toBeDisabled()
    await expect(modal.getByRole('textbox', { name: 'Email message', exact: true })).toBeDisabled()
    if (type === 'invoice') {
      await page.screenshot({ path: testInfo.outputPath('sales-document-desktop-bottom.png'), fullPage: true })
      await page.setViewportSize({ width: 390, height: 844 })
      await modal.locator('.sdm-body').evaluate(element => { element.scrollTop = 0 })
      await page.screenshot({ path: testInfo.outputPath('sales-document-mobile.png'), fullPage: false })
      await modal.getByRole('textbox', { name: 'Email message', exact: true }).scrollIntoViewIfNeeded()
      await expect(modal.getByRole('button', { name: 'Create & download PDF', exact: true })).toBeInViewport()
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false)
      await page.screenshot({ path: testInfo.outputPath('sales-document-mobile-bottom.png'), fullPage: false })
    }
    const created = page.waitForResponse(response => response.request().method() === 'POST' && /\/sales-documents$/.test(response.url()))
    const downloaded = page.waitForEvent('download')
    await modal.getByRole('button', { name: 'Create & download PDF', exact: true }).click()
    const response = await created
    expect(response.ok(), await response.text()).toBeTruthy()
    const result = await response.json()
    expect(result.document).toMatchObject({ type, demo: true })
    expect(result.delivery.status).toBe('not_requested')
    expect(result.pdf.mime_type).toBe('application/pdf')
    const download = await downloaded
    expect(download.suggestedFilename()).toMatch(/\.pdf$/)
    const path = await download.path()
    expect(path).toBeTruthy()
    const bytes = await readFile(path!)
    expect(bytes.subarray(0, 5).toString()).toBe('%PDF-')
    expect(bytes.length).toBeGreaterThan(500)
    await expect(modal).toHaveCount(0)
    await expect(page.getByText('Sample document created and downloaded as PDF.', { exact: true })).toBeVisible()
  }
  expect(emailRequests).toEqual([])
  expect(missingRoutes).toEqual([])
  expect(pageErrors).toEqual([])
})
