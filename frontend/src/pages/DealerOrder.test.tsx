import { fireEvent, render, screen } from '@testing-library/react'
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
