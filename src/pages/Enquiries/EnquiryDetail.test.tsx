import type { ReactNode } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Enquiry } from '../../types/enquiry'

const mocks = vi.hoisted(() => ({ resource: { data: undefined as Enquiry | undefined, error: '', loading: false, reload: vi.fn() }, path: vi.fn() }))
vi.mock('../../hooks/useScopedResource', () => ({ useScopedResource: (path: string) => { mocks.path(path); return mocks.resource } }))
vi.mock('../../components/OfficeUi', () => ({ OfficeNotice: ({ children, error }: { children: ReactNode; error?: boolean }) => <div role={error ? 'alert' : 'status'}>{children}</div>, OfficeStatus: ({ value }: { value?: string }) => <span>{value || 'not_started'}</span> }))
vi.mock('../../components/ConversationTranscript', () => ({ default: ({ conversationId }: { conversationId: string }) => <section aria-label="Live transcript boundary">{conversationId}</section> }))
import EnquiryDetail from './EnquiryDetail'

function setup() {
  render(<MemoryRouter initialEntries={['/enquiries/saved-id']}><Routes><Route path="/enquiries/:id" element={<EnquiryDetail />} /></Routes></MemoryRouter>)
}
beforeEach(() => { vi.clearAllMocks(); mocks.resource = { data: undefined, error: '', loading: false, reload: vi.fn() } })
describe('saved enquiry details', () => {
  it('uses the scoped durable detail path and shows loading without stale customer details', () => {
    mocks.resource.loading = true; setup()
    expect(mocks.path).toHaveBeenCalledWith('/enquiries/saved-id')
    expect(screen.getByRole('heading', { name: 'Loading enquiry…' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Live transcript boundary' })).not.toBeInTheDocument()
  })
  it('shows denied/unavailable errors and retries without falling back to demo data', () => {
    mocks.resource.error = 'This enquiry is unavailable'; setup()
    expect(screen.getByRole('alert')).toHaveTextContent('This enquiry is unavailable')
    fireEvent.click(screen.getByRole('button', { name: 'Retry loading' }))
    expect(mocks.resource.reload).toHaveBeenCalledOnce()
  })
  it('renders preserved SKU, project, note and follow-up fields with the linked processing workflow', () => {
    mocks.resource.data = { id: 'saved-id', enquiryNumber: 'ENQ-2026-000001', customerName: 'Synthetic Customer', phone: '9876543210', channel: 'Project', branch: 'Test branch', assignedExecutive: 'Salesperson',
      product: 'SKU-123', quantity: 3, unit: 'nos', createdAt: '2026-10-13T04:00:00Z', customerType: 'Project', company: 'Test Company', projectName: 'Test Site', preferredContactMethod: 'Phone',
      customerNotes: 'Customer facts', salespersonNotes: 'Internal facts', requirementsSummary: 'Requirements', followUpDate: '2026-11-30', followUpOwner: 'Follow-up Executive', priority: 'High',
      productRequirements: [{ product: '', sku: 'SKU-123', quantity: 3, unit: 'nos', customerSegment: 'Hospitality', budget: '10000', paymentPreference: 'Card', timeline: 'Next week', stockCheck: true, notes: 'Product facts' }],
      conversationId: 'conversation-id', recording: { captureState: 'stopped', uploadState: 'saved', transcriptionState: 'complete', englishState: 'pending', speakerState: 'complete', finalState: 'running' },
    }
    setup()
    for (const value of ['Test Site', 'Customer facts', 'Internal facts', 'Requirements', 'Hospitality', 'Card', 'Next week', 'Product facts', 'Follow-up Executive']) expect(screen.getByText(value)).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Live transcript boundary' })).toHaveTextContent('conversation-id')
    expect(screen.getByText('Yes — verify availability')).toBeInTheDocument()
  })
})
