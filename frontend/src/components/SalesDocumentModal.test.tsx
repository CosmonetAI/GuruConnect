import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import SalesDocumentModal, { type SalesDocumentModalProps } from './SalesDocumentModal'

function props(overrides: Partial<SalesDocumentModalProps> = {}): SalesDocumentModalProps {
  return { open: true, contact: { name: 'Customer Example', phone: '9876543210', email: '' }, customerType: 'customer', cart: [{ item_id: 'fan', product: 'BLDC fan', sku: 'FAN-1', rate: 4000, quantity: 2, discount: 10 }], subtotal: 8000, discount: 800, documentType: 'invoice', setDocumentType: vi.fn(), email: '', setEmail: vi.fn(), subject: 'Your invoice', setSubject: vi.fn(), message: 'Please find your document attached.', setMessage: vi.fn(), busy: false, onClose: vi.fn(), onBack: vi.fn(), onSubmit: vi.fn(), ...overrides }
}

describe('sales document review', () => {
  it('shows the actual customer and selected cart with tax pending, and offers PDF download without email', () => {
    const data = props()
    render(<SalesDocumentModal {...data} />)
    expect(screen.getByRole('dialog', { name: 'Create sales document' })).toBeInTheDocument()
    expect(screen.getByText('Customer Example')).toBeInTheDocument()
    expect(within(screen.getByRole('table')).getByText('BLDC fan')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Discount' })).toBeInTheDocument()
    expect(screen.getByText('10%')).toBeInTheDocument()
    expect(screen.getByText('Calculated in Zoho Books')).toBeInTheDocument()
    expect(screen.getAllByText('₹7,200.00').length).toBeGreaterThan(0)
    expect(screen.queryByText(/18%|GST/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Create & download PDF' }))
    expect(data.onSubmit).toHaveBeenCalledWith('invoice')
  })
  it('uses supported document options and does not claim email delivery is configured', () => {
    render(<SalesDocumentModal {...props({ documentTypes: ['quote'], documentType: 'quote', email: 'customer@example.test', emailConfigured: false })} />)
    expect(screen.getByRole('radio', { name: /Send quote/ })).toBeChecked()
    expect(screen.queryByRole('radio', { name: /Send invoice/ })).not.toBeInTheDocument()
    expect(screen.getByText('Email delivery is not configured')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create & download PDF' })).toBeEnabled()
  })
  it('makes demo documents download only, and keeps a failed created document available for retry', () => {
    const view = render(<SalesDocumentModal {...props({ demo: true, email: 'customer@example.test' })} />)
    expect(screen.getByText('Demo catalogue')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create & download PDF' })).toBeEnabled()
    view.rerender(<SalesDocumentModal {...props({ email: 'customer@example.test', createdDocument: { number: 'INV-42', deliveryStatus: 'failed' }, onDownload: vi.fn() })} />)
    expect(screen.getByText(/INV-42 has been created/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry email' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Download PDF' })).toBeEnabled()
    expect(screen.getByRole('radio', { name: /Send invoice/ })).toBeDisabled()
  })
  it('prevents close or resubmission while creating and makes uncertain delivery download-only', () => {
    const data = props({ busy: true })
    const view = render(<SalesDocumentModal {...data} />)
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(data.onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Creating document…' })).toBeDisabled()
    view.rerender(<SalesDocumentModal {...props({ email: 'customer@example.test', createdDocument: { number: 'INV-42', deliveryStatus: 'uncertain' }, onDownload: vi.fn() })} />)
    expect(screen.getByRole('button', { name: 'Delivery status uncertain' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Download PDF' })).toBeEnabled()
  })
})
