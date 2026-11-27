import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import DealerOrder from './DealerOrder'

const mocks = vi.hoisted(() => ({ request: vi.fn(), requestStream: vi.fn() }))
vi.mock('../contexts/OfficeContext', () => ({ useOffice: () => ({ workspace: { id: 'workspace', member_id: 'dealer' } }) }))
vi.mock('../services/officeApi', () => ({
  request: mocks.request,
  requestStream: mocks.requestStream,
  message: (error: unknown) => error instanceof Error ? error.message : 'Request failed',
}))

beforeEach(() => {
  vi.clearAllMocks()
  mocks.request.mockImplementation(async (path: string) => {
    if (path.startsWith('/dealer/products')) return { items: [{ item_id: '123', name: 'Widget', rate: 100 }] }
    if (path === '/dealer/preview') return { lines: [], subtotal: 100, discount: 0, gst: 0, total: 100 }
    if (path === '/dealer/invoices') return { invoice_id: 'invoice-123' }
    throw new Error(`Unexpected request: ${path}`)
  })
})

it('keeps a successful invoice when the PDF download fails', async () => {
  mocks.requestStream.mockRejectedValue(new Error('PDF unavailable'))
  render(<MemoryRouter><DealerOrder /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('button', { name: 'Add Widget' }))
  fireEvent.click(screen.getByRole('button', { name: 'Submit order' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Generate Invoice' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Invoice created, but the PDF could not be downloaded')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByText('0 products')).toBeInTheDocument()
})

it('shows invoice creation errors inside the preview dialog', async () => {
  mocks.request.mockImplementation(async (path: string) => {
    if (path.startsWith('/dealer/products')) return { items: [{ item_id: '123', name: 'Widget', rate: 100 }] }
    if (path === '/dealer/preview') return { lines: [], subtotal: 100, discount: 0, gst: 0, total: 100 }
    throw new Error('Invoice creation failed')
  })
  render(<MemoryRouter><DealerOrder /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('button', { name: 'Add Widget' }))
  fireEvent.click(screen.getByRole('button', { name: 'Submit order' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Generate Invoice' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Invoice creation failed')
  expect(screen.getByRole('dialog')).toContainElement(screen.getByRole('alert'))
})

it('loads an invoice into the cart and submits the edited quantities', async () => {
  mocks.request.mockImplementation(async (path: string) => {
    if (path.startsWith('/dealer/products')) return { items: [] }
    if (path === '/dealer/invoices/invoice-123') return { invoice: { status: 'draft', line_items: [{ item_id: '123', name: 'Widget', quantity: 2, rate: 100 }] } }
    if (path === '/dealer/preview') return { lines: [], subtotal: 300, discount: 0, gst: 0, total: 300 }
    if (path === '/dealer/invoices/invoice-123/replace') return { invoice_id: 'invoice-123' }
    throw new Error(`Unexpected request: ${path}`)
  })
  mocks.requestStream.mockRejectedValue(new Error('PDF unavailable'))
  render(<MemoryRouter initialEntries={['/dealer/add-to-cart?invoice=invoice-123']}><DealerOrder /></MemoryRouter>)
  expect(await screen.findByText('Widget')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Increase quantity' }))
  fireEvent.click(screen.getByRole('button', { name: 'Submit order' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Update Invoice' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Invoice updated')
  const update = mocks.request.mock.calls.find(([path]) => path === '/dealer/invoices/invoice-123/replace')
  expect(JSON.parse(update![2].body)).toMatchObject({ lines: [{ item_id: '123', quantity: 3 }] })
  expect(screen.getByRole('heading', { name: 'Add to Cart' })).toBeInTheDocument()
})

it('does not allow cart edits if invoice loading fails', async () => {
  mocks.request.mockImplementation(async (path: string) => {
    if (path.startsWith('/dealer/products')) return { items: [{ item_id: '123', name: 'Widget', rate: 100 }] }
    throw new Error('Invoice unavailable')
  })
  render(<MemoryRouter initialEntries={['/dealer/add-to-cart?invoice=invoice-123']}><DealerOrder /></MemoryRouter>)
  expect(await screen.findByRole('alert')).toHaveTextContent('Invoice unavailable')
  expect(await screen.findByRole('button', { name: 'Add Widget' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Submit order' })).toBeDisabled()
})

it('preserves repeated invoice items as separate editable cart lines', async () => {
  mocks.request.mockImplementation(async (path: string) => {
    if (path.startsWith('/dealer/products')) return { items: [] }
    if (path === '/dealer/invoices/invoice-123') return { invoice: { status: 'draft', line_items: [
      { item_id: '123', name: 'Widget', quantity: 2, rate: 100 },
      { item_id: '123', name: 'Widget', quantity: 4, rate: 100 },
      { item_id: '456', name: 'Other item', quantity: 1, rate: 50 },
    ] } }
    if (path === '/dealer/preview') return { lines: [], subtotal: 850, discount: 0, gst: 0, total: 850 }
    throw new Error(`Unexpected request: ${path}`)
  })
  render(<MemoryRouter initialEntries={['/dealer/add-to-cart?invoice=invoice-123']}><DealerOrder /></MemoryRouter>)
  expect(await screen.findAllByText('Widget')).toHaveLength(2)
  expect(screen.getByText('Other item')).toBeInTheDocument()
  fireEvent.click(screen.getAllByRole('button', { name: 'Increase quantity' })[1])
  fireEvent.click(screen.getByRole('button', { name: 'Submit order' }))
  await screen.findByRole('dialog')
  const preview = mocks.request.mock.calls.find(([path]) => path === '/dealer/preview')
  expect(JSON.parse(preview![2].body)).toMatchObject({ lines: [
    { item_id: '123', quantity: 2 },
    { item_id: '123', quantity: 5 },
    { item_id: '456', quantity: 1 },
  ] })
})

it('shows invoice loading progress and lets the user retry a failed load', async () => {
  let attempts = 0
  mocks.request.mockImplementation(async (path: string) => {
    if (path.startsWith('/dealer/products')) return { items: [] }
    if (path === '/dealer/invoices/invoice-123') {
      attempts += 1
      if (attempts === 1) throw new Error('Invoice unavailable')
      return { invoice: { status: 'draft', line_items: [{ item_id: '123', name: 'Widget', quantity: 2 }] } }
    }
    throw new Error(`Unexpected request: ${path}`)
  })
  render(<MemoryRouter initialEntries={['/dealer/add-to-cart?invoice=invoice-123']}><DealerOrder /></MemoryRouter>)
  expect(screen.getByRole('status')).toHaveTextContent('Loading invoice items')
  const error = await screen.findByRole('alert')
  expect(error).toHaveTextContent('Invoice unavailable')
  fireEvent.click(within(error).getByRole('button', { name: 'Retry' }))
  expect(await screen.findByText('Widget')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Submit order' })).toBeEnabled()
})
