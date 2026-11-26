import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ConversationRecorderProps } from './ConversationRecorder'

const mocks = vi.hoisted(() => ({
  create: vi.fn(), busy: vi.fn(),
  office: { user: { id: 'user-a' }, workspace: { id: 'workspace-a' }, setCaptureBusy: vi.fn() },
}))
vi.mock('../../contexts/OfficeContext', () => ({ useOffice: () => mocks.office }))
vi.mock('../../services/enquiryService', () => ({ createEnquiry: mocks.create }))
vi.mock('../../services/officeApi', () => ({ message: (error: unknown) => error instanceof Error ? error.message : String(error) }))
vi.mock('./ConversationRecorder', () => ({ default: (props: ConversationRecorderProps) => <section aria-label="Test recording controls">
  <span>{props.initialConversationId || 'No audio'}</span>
  <button type="button" onClick={() => { props.onConversationChange('550e8400-e29b-41d4-a716-446655440000'); props.onBusyChange?.(true) }}>Begin audio</button>
  <button type="button" onClick={() => { props.onSaved?.(props.initialConversationId || '550e8400-e29b-41d4-a716-446655440000'); props.onBusyChange?.(false) }}>Confirm audio saved</button>
  <button type="button" onClick={() => { props.onConversationChange(null); props.onBusyChange?.(false) }}>Detach test audio</button>
</section> }))
import NewEnquiry from './NewEnquiry'

const key = (user = 'user-a', workspace = 'workspace-a') => `guruconnect:enquiry-draft:${user}:${workspace}`
function setup() {
  const router = createMemoryRouter([
    { path: '/enquiries/new', element: <NewEnquiry /> },
    { path: '/enquiries/:id', element: <p>Saved enquiry boundary</p> },
    { path: '/enquiries', element: <p>Enquiry list boundary</p> },
  ], { initialEntries: ['/enquiries/new'] })
  return { ...render(<RouterProvider router={router} />), router }
}
function required() {
  for (const [label, value] of [['Customer full name', 'Synthetic Customer'], ['Phone number', '9876543210'], ['Branch / showroom', 'Test branch'], ['Assigned sales executive', 'Synthetic Executive']] as const) {
    fireEvent.change(screen.getByLabelText(new RegExp(label)), { target: { value } })
  }
}
beforeEach(() => {
  vi.clearAllMocks(); sessionStorage.clear()
  mocks.office = { user: { id: 'user-a' }, workspace: { id: 'workspace-a' }, setCaptureBusy: mocks.busy }
  mocks.create.mockImplementation(async (_workspace, values) => ({ ...values, id: '83bc6f7a-8c29-46a8-9587-d9e0baf1d291', enquiryNumber: 'ENQ-2026-000001', createdAt: '2026-10-13T04:00:00Z' }))
  HTMLElement.prototype.scrollIntoView = vi.fn()
})

describe('durable enquiry submission', () => {
  it('saves SKU-only requirements and individual notes to the workspace, then opens the saved detail', async () => {
    const { router } = setup(); required()
    fireEvent.change(screen.getByLabelText('SKU (if known)'), { target: { value: 'SKU-123' } })
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('Additional customer notes'), { target: { value: 'Customer facts' } })
    fireEvent.change(screen.getByLabelText('Salesperson notes'), { target: { value: 'Internal facts' } })
    fireEvent.change(screen.getByLabelText('Customer requirements summary'), { target: { value: 'Requirements' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save enquiry' }))
    await screen.findByText('Saved enquiry boundary')
    expect(mocks.create).toHaveBeenCalledOnce()
    expect(mocks.create).toHaveBeenCalledWith('workspace-a', expect.objectContaining({
      product: 'SKU-123', quantity: 3, customerNotes: 'Customer facts', salespersonNotes: 'Internal facts', requirementsSummary: 'Requirements',
      productRequirements: [expect.objectContaining({ product: '', sku: 'SKU-123', quantity: 3 })],
    }), expect.any(String), undefined)
    expect(router.state.location.pathname).toBe('/enquiries/83bc6f7a-8c29-46a8-9587-d9e0baf1d291')
    expect(sessionStorage.getItem(key())).toBeNull()
  })
  it('omits only the untouched optional row and rejects populated unidentified requirements', async () => {
    setup(); required()
    fireEvent.change(screen.getByLabelText('Additional product requirements'), { target: { value: 'Please check stock' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save enquiry' }))
    await screen.findByText('Enter a product name or SKU for these requirements')
    expect(mocks.create).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Additional product requirements'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save enquiry' }))
    await screen.findByText('Saved enquiry boundary')
    expect(mocks.create.mock.calls[0][1].productRequirements).toEqual([])
  })
  it('preserves the save key across a failed request and reload, preventing duplicate saves', async () => {
    mocks.create.mockRejectedValue(new Error('Network unavailable'))
    const first = setup(); required()
    fireEvent.click(screen.getByRole('button', { name: 'Save enquiry' }))
    await screen.findByText(/Network unavailable.*Your entries are preserved/)
    const intentKey = mocks.create.mock.calls[0][2]
    fireEvent.click(screen.getByRole('button', { name: 'Save enquiry' }))
    await waitFor(() => expect(mocks.create).toHaveBeenCalledTimes(2))
    expect(mocks.create.mock.calls[1][2]).toBe(intentKey)
    await screen.findByText(/Network unavailable.*Your entries are preserved/)
    first.unmount()
    setup()
    expect(screen.getByLabelText(/Customer full name/)).toHaveValue('Synthetic Customer')
    fireEvent.click(screen.getByRole('button', { name: 'Save enquiry' }))
    await waitFor(() => expect(mocks.create).toHaveBeenCalledTimes(3))
    expect(mocks.create.mock.calls[2][2]).toBe(intentKey)
  })
  it('does not restore an unscoped legacy draft or another account/workspace draft', () => {
    sessionStorage.setItem('guru-group:new-enquiry-draft', JSON.stringify({ customerName: 'Legacy private customer' }))
    const first = setup(); required(); first.unmount()
    mocks.office = { ...mocks.office, workspace: { id: 'workspace-b' } }
    const second = setup()
    expect(screen.getByLabelText(/Customer full name/)).toHaveValue('')
    second.unmount()
    mocks.office = { ...mocks.office, user: { id: 'user-b' }, workspace: { id: 'workspace-a' } }
    setup()
    expect(screen.getByLabelText(/Customer full name/)).toHaveValue('')
    expect(screen.queryByText('Legacy private customer')).not.toBeInTheDocument()
  })
  it('blocks Save, Cancel and route navigation until audio finalizes, then attaches the saved recording', async () => {
    const { router } = setup(); required()
    fireEvent.click(screen.getByRole('button', { name: 'Begin audio' }))
    expect(screen.getByRole('button', { name: 'Save enquiry' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(screen.getByLabelText(/Customer full name/)).toBeDisabled()
    await router.navigate('/enquiries')
    await screen.findByText(/Finish saving or detach the audio before leaving/)
    expect(router.state.location.pathname).toBe('/enquiries/new')
    fireEvent.click(screen.getByRole('button', { name: 'Confirm audio saved' }))
    expect(screen.getByRole('button', { name: 'Save enquiry' })).not.toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Save enquiry' }))
    await screen.findByText('Saved enquiry boundary')
    expect(mocks.create.mock.calls[0][1].conversationId).toBe('550e8400-e29b-41d4-a716-446655440000')
  })
  it('keeps an attached restored draft blocked until audio is verified again', async () => {
    const first = setup(); required()
    fireEvent.click(screen.getByRole('button', { name: 'Begin audio' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm audio saved' }))
    first.unmount(); setup()
    expect(screen.getByRole('button', { name: 'Save enquiry' })).toBeDisabled()
    expect(screen.getByLabelText(/Customer full name/)).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm audio saved' }))
    expect(screen.getByRole('button', { name: 'Save enquiry' })).not.toBeDisabled()
  })
  it('locks customer data and prevents a second submission while its save is pending', async () => {
    let complete: (value: object) => void = () => undefined
    mocks.create.mockImplementation(() => new Promise(resolve => { complete = resolve }))
    setup(); required()
    fireEvent.click(screen.getByRole('button', { name: 'Save enquiry' }))
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled()
    expect(screen.getByLabelText(/Customer full name/)).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(mocks.create).toHaveBeenCalledOnce()
    complete({ id: '83bc6f7a-8c29-46a8-9587-d9e0baf1d291', enquiryNumber: 'ENQ-1' })
    await screen.findByText('Saved enquiry boundary')
  })
  it('keeps current input usable when browser storage is denied', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('denied', 'SecurityError') })
    setup(); required(); fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))
    await screen.findByText(/This browser could not save the draft/)
    expect(screen.getByLabelText(/Customer full name/)).toHaveValue('Synthetic Customer')
    fireEvent.click(screen.getByRole('button', { name: 'Save enquiry' }))
    await screen.findByText('Saved enquiry boundary')
  })
})
