import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ call: vi.fn(), busy: vi.fn(), localMode: false, mounts: 0, unmounts: 0, recorder: null as null | { onBusyChange: (busy: boolean) => void; onCaptureChange: (state: { capture: string; elapsedSeconds: number }) => void; onConversationChange: (id: string | null) => Promise<void>; customerName: string } }))
vi.mock('react-router-dom', async original => ({ ...await original<typeof import('react-router-dom')>(), useBlocker: () => ({ state: 'unblocked', reset: vi.fn() }) }))
vi.mock('../contexts/OfficeContext', () => ({ useOffice: () => ({ user: { id: 'u' }, workspace: { id: 'w', member_id: 'member' }, localMode: mocks.localMode, setCaptureBusy: mocks.busy }) }))
vi.mock('../zoho/shared/api.js', () => ({ call: mocks.call }))
vi.mock('./Enquiries/ConversationRecorder', async () => {
  const { useEffect } = await import('react')
  return { default: (props: NonNullable<typeof mocks.recorder>) => {
    mocks.recorder = props
    useEffect(() => { mocks.mounts++; return () => { mocks.unmounts++ } }, [])
    return <div aria-label="recorder customer">{props.customerName}</div>
  } }
})
vi.mock('../components/ConversationTranscript', () => ({ default: () => <div>transcript</div> }))
vi.mock('../components/ProgressivePlayback', () => ({ default: () => <div>player</div> }))
import VoiceSessions, { liveProcessingStatus } from './VoiceSessions'
beforeEach(() => {
  vi.clearAllMocks(); mocks.recorder = null; mocks.mounts = 0; mocks.unmounts = 0; mocks.localMode = false
  Object.assign(URL, { createObjectURL: vi.fn().mockReturnValue('blob:document'), revokeObjectURL: vi.fn() })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  mocks.call.mockImplementation(async (path: string) => {
    if (path === '/chats') return [{ id: 'A', name: 'Customer A' }, { id: 'B', name: 'Customer B' }]
    if (path.startsWith('/session-contacts')) return { contacts: [] }
    if (path.endsWith('/history')) { const id = path.split('/')[2]; return { session: { name: `Customer ${id}`, phone: id === 'A' ? '9876543210' : '9876543211' }, messages: [], recorder_sessions: [] } }
    return { notes: [], product_requirements: [], contacts: [], items: [] }
  })
})
describe('session recording identity', () => {
  it('blocks customer switches and global workspace changes while recording is busy', async () => {
    render(<MemoryRouter><VoiceSessions /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: /Customer A/ }))
    await waitFor(() => expect(mocks.recorder?.customerName).toBe('Customer A'))
    await act(async () => {})
    act(() => mocks.recorder!.onBusyChange(true))
    expect(screen.getByRole('button', { name: /Customer B/ })).toBeDisabled()
    expect(mocks.busy).toHaveBeenLastCalledWith(true)
    fireEvent.click(screen.getByRole('button', { name: /Customer B/ }))
    expect(mocks.recorder?.customerName).toBe('Customer A')
    act(() => mocks.recorder!.onBusyChange(false))
    fireEvent.click(screen.getByRole('button', { name: /Customer B/ }))
    await waitFor(() => expect(mocks.recorder?.customerName).toBe('Customer B'))
    await act(async () => mocks.recorder!.onConversationChange('clip-b'))
    expect(mocks.call).toHaveBeenCalledWith('/voice-sessions/B/conversation', expect.objectContaining({ method: 'POST', body: '{"conversation_id":"clip-b"}' }), 'w')
  })
})

const fan = { item_id: 'fan-1', name: 'BLDC fan', sku: 'FAN-1', rate: 4499 }
const motor = { item_id: 'motor-1', name: 'Copper motor', sku: 'MOTOR-1', rate: 6999 }
const liveTurn = { id: 'turn-1', start_seconds: 0, end_seconds: 10, english_text: 'Two fans and one motor, please.', source_text: 'Two fans and one motor, please.', speaker_ref: 'speaker_0', speaker_role: 'customer' }
function liveData(id = 'clip-a', text = liveTurn.english_text) {
  return { conversation_id: id, transcript_preview: [{ ...liveTurn, english_text: text }], recommendations: [fan, motor], keywords: ['fan', 'motor'], insights: [], analysis_status: 'complete', recording: { id, capture_state: 'recording', final_state: 'pending' } }
}
function installLiveApi(overrides: (path: string, options: { method?: string; body?: string }) => unknown = () => undefined) {
  mocks.call.mockImplementation(async (path: string, options = {}) => {
    const overridden = overrides(path, options)
    if (overridden !== undefined) return overridden
    if (path === '/chats') return [{ id: 'A', name: 'Customer A' }, { id: 'B', name: 'Customer B' }]
    if (path.startsWith('/session-contacts')) return { contacts: [] }
    if (path.endsWith('/history')) return { session: { name: `Customer ${path.split('/')[2]}`, phone: '9876543210', email: 'qa@example.test' }, messages: [], recorder_sessions: [] }
    if (path.endsWith('/live')) return liveData(path.includes('/B/') ? 'clip-b' : 'clip-a')
    if (path.endsWith('/sales-documents/options')) return { mode: 'zoho', customer_type: 'customer', email: { provider: 'smtp2go', configured: true }, document_types: ['invoice', 'quote', 'sales-order'], customer: { name: 'Customer A', phone: '9876543210', email: 'qa@example.test' } }
    if (path.endsWith('/context')) return { product_requirements: [], cart: [] }
    return { notes: [], contacts: [], items: [] }
  })
}
async function openFirst() {
  render(<MemoryRouter><VoiceSessions /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('button', { name: /Customer A/ }))
  await waitFor(() => expect(mocks.recorder?.customerName).toBe('Customer A'))
  await act(async () => {})
}

describe('live session integration', () => {
  it('labels local sample inventory and opens a download-only demo document review', async () => {
    mocks.localMode = true
    installLiveApi(path => path.endsWith('/live') ? { ...liveData(), recommendations: [{ ...fan, catalogue_source: 'local-demo' }], notices: [{ code: 'local_demo_catalogue' }] } : path.endsWith('/sales-documents/options') ? { mode: 'local-demo', email: { provider: 'smtp2go', configured: false }, document_types: ['invoice', 'quote', 'sales-order'], customer: { name: 'Customer A', phone: '9876543210', email: '' } } : undefined)
    await openFirst()
    await act(async () => mocks.recorder!.onConversationChange('clip-a'))
    expect(await screen.findByText('Demo catalogue')).toBeInTheDocument()
    expect(screen.getByText(/products, prices and stock are sample data/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Add to cart BLDC fan' }))
    await screen.findByRole('button', { name: 'Complete cart' })
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: 'Complete cart' }))
    expect(await screen.findByRole('dialog', { name: 'Create sales document' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create & download PDF' })).toBeEnabled()
    expect(mocks.call.mock.calls.some(([path]) => path === '/zoho/status')).toBe(false)
  })

  it('shows waiting rather than claiming translation is running when only the microphone is active', async () => {
    installLiveApi(path => path.endsWith('/live') ? { conversation_id: 'clip-a', transcript_preview: [], recording: { id: 'clip-a', capture_state: 'recording', transcription_state: 'pending', english_state: 'pending' }, processing_mode: 'progressive', provider_readiness: { speech_configured: true, translation_configured: true }, processing_status: { stage: 'waiting' } } : undefined)
    await openFirst()
    await act(async () => {
      mocks.recorder!.onBusyChange(true)
      mocks.recorder!.onCaptureChange({ capture: 'recording', elapsedSeconds: 5 })
      await mocks.recorder!.onConversationChange('clip-a')
    })
    await waitFor(() => expect(document.querySelector('.live-header-pill')).toHaveTextContent('Waiting for first audio window'))
    expect(screen.queryByText('Transcribing & translating')).not.toBeInTheDocument()
    expect(document.querySelector('.live-header-pill')).toHaveClass('status-ready')
  })

  it('shows translated speech and all catalogue matches before recording ends, and opens the cart on add', async () => {
    installLiveApi()
    await openFirst()
    await act(async () => { mocks.recorder!.onBusyChange(true); await mocks.recorder!.onConversationChange('clip-a') })
    expect((await screen.findAllByText(liveTurn.english_text)).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Add to cart Copper motor' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Add to cart BLDC fan' }))
    await waitFor(() => expect(document.querySelector('.live-session-workspace')).toHaveAttribute('data-layout', 'cart'))
    await waitFor(() => expect(mocks.call).toHaveBeenCalledWith('/voice-sessions/A/context', expect.objectContaining({ method: 'PUT', body: expect.stringContaining('fan-1') }), 'w'))
    expect(mocks.busy).toHaveBeenLastCalledWith(true)
    expect(mocks.mounts).toBe(1)
    expect(mocks.unmounts).toBe(0)
  })

  it('retains the visible translation when Stop reports the same conversation ID', async () => {
    installLiveApi()
    await openFirst()
    await act(async () => mocks.recorder!.onConversationChange('clip-a'))
    await screen.findAllByText(liveTurn.english_text)
    await act(async () => mocks.recorder!.onConversationChange('clip-a'))
    expect(screen.getAllByText(liveTurn.english_text).length).toBeGreaterThan(0)
  })

  it('surfaces API clarification guidance without speaking for the agent and clears it for another customer', async () => {
    const question = 'What supply phase is available at the installation?'
    installLiveApi(path => path === '/voice-sessions/A/live' ? { ...liveData(), current_intent: { summary: 'Choose a compatible motor' }, clarification_suggestions: [{ id: 'phase', question, missing_fields: ['phase'] }], catalogue_status: 'syncing' } : undefined)
    await openFirst()
    await act(async () => mocks.recorder!.onConversationChange('clip-a'))
    expect(await screen.findByText(question)).toBeVisible()
    expect(screen.getByRole('region', { name: 'Live translated conversation' })).not.toHaveTextContent(question)
    expect(screen.getByRole('region', { name: 'Agent guidance' })).toHaveTextContent('Choose a compatible motor')
    fireEvent.click(screen.getByRole('button', { name: /Customer B/ }))
    await waitFor(() => expect(mocks.recorder?.customerName).toBe('Customer B'))
    expect(screen.queryByText(question)).not.toBeInTheDocument()
  })

  it('shows unavailable AI analysis while preserving the visible translated conversation', async () => {
    installLiveApi(path => path === '/voice-sessions/A/live' ? { ...liveData(), analysis_status: 'unavailable', recommendations: [], notices: [{ code: 'voice_ai_provider_error', message: 'The AI provider is unavailable. Existing requirements are preserved.' }] } : undefined)
    await openFirst()
    await act(async () => mocks.recorder!.onConversationChange('clip-a'))
    expect(await screen.findByText('Product analysis temporarily unavailable')).toBeVisible()
    expect(screen.getByRole('region', { name: 'Live translated conversation' })).toHaveTextContent(liveTurn.english_text)
    expect(screen.getByText('The AI provider is unavailable. Existing requirements are preserved.', { exact: false })).toBeVisible()
  })

  it('uses the same technical review guard in product details and catalogue search', async () => {
    const uncertain = { ...motor, match_status: 'needs_confirmation', missing_specs: ['Supply phase'], matched_specs: ['1 HP'], availability_status: 'needs_review' }
    installLiveApi(path => path.endsWith('/live') ? { ...liveData(), recommendations: [uncertain] } : path.startsWith('/products?') ? { items: [motor] } : undefined)
    await openFirst()
    await act(async () => mocks.recorder!.onConversationChange('clip-a'))
    expect(await screen.findByRole('button', { name: 'Add to cart Copper motor' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'View details' }))
    const details = screen.getByRole('dialog', { name: 'Copper motor' })
    expect(details).toHaveTextContent('Needs confirmation')
    expect(details.querySelector('button.primary')).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Close product details' }))
    fireEvent.click(screen.getByRole('button', { name: 'Search catalogue' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Search inventory' }), { target: { value: 'motor' } })
    fireEvent.click(screen.getByRole('button', { name: /^Search$/ }))
    const search = screen.getByRole('dialog', { name: 'Add another product' })
    await waitFor(() => expect(search).toHaveTextContent('Copper motor'))
    const button = search.querySelector('.lws-product-action button')!
    expect(button).toBeDisabled()
    fireEvent.click(search.querySelector('input[type="checkbox"]')!)
    expect(button).toBeEnabled()
    fireEvent.click(button)
    await waitFor(() => expect(document.querySelector('.live-session-workspace')).toHaveAttribute('data-layout', 'cart'))
    await waitFor(() => expect(mocks.call.mock.calls.some(([path, options]) => path.endsWith('/context') && options.method === 'PUT' && options.body.includes('match_confirmation_acknowledged'))).toBe(true))
    const save = mocks.call.mock.calls.find(([path, options]) => path.endsWith('/context') && options.method === 'PUT')
    expect(save![1].body).toContain('"match_status":"needs_confirmation"')
    expect(mocks.mounts).toBe(1)
    expect(mocks.unmounts).toBe(0)
  })

  it('refreshes an open product review when live match evidence changes', async () => {
    let candidate = { ...motor, match_status: 'verified', missing_specs: [] as string[], matched_specs: ['1 HP'] }
    installLiveApi(path => path.endsWith('/live') ? { ...liveData(), recommendations: [candidate] } : undefined)
    await openFirst()
    await act(async () => mocks.recorder!.onConversationChange('clip-a'))
    fireEvent.click(await screen.findByRole('button', { name: 'View details' }))
    const details = screen.getByRole('dialog', { name: 'Copper motor' })
    expect(details.querySelector('button.primary')).toBeEnabled()
    candidate = { ...candidate, match_status: 'needs_confirmation', missing_specs: ['Voltage'] }
    await act(async () => document.dispatchEvent(new Event('visibilitychange')))
    await waitFor(() => expect(details).toHaveTextContent('Voltage'))
    expect(details.querySelector('button.primary')).toBeDisabled()
    expect(details).toHaveTextContent('Needs confirmation')
  })

  it('ignores a delayed live response from a different customer', async () => {
    let finishOld!: (value: unknown) => void
    const old = new Promise(resolve => { finishOld = resolve })
    installLiveApi(path => path === '/voice-sessions/A/live' ? old : path === '/voice-sessions/B/live' ? liveData('clip-b', 'Only customer B belongs here.') : undefined)
    await openFirst()
    await act(async () => mocks.recorder!.onConversationChange('clip-a'))
    fireEvent.click(screen.getByRole('button', { name: /Customer B/ }))
    await waitFor(() => expect(mocks.recorder?.customerName).toBe('Customer B'))
    await act(async () => mocks.recorder!.onConversationChange('clip-b'))
    await screen.findAllByText('Only customer B belongs here.')
    await act(async () => finishOld(liveData('clip-a', 'Old customer A private text.')))
    expect(screen.queryByText('Old customer A private text.')).not.toBeInTheDocument()
  })

  it('never writes an empty cart while saved customer context is still loading', async () => {
    let finishContext!: (value: unknown) => void
    const delayed = new Promise(resolve => { finishContext = resolve })
    installLiveApi((path, options) => path.endsWith('/context') && options.method !== 'PUT' ? delayed : undefined)
    await openFirst()
    expect(mocks.call.mock.calls.filter(([path, options]) => path.endsWith('/context') && options?.method === 'PUT')).toHaveLength(0)
    await act(async () => finishContext({ product_requirements: [], cart: [{ ...fan, quantity: 2 }] }))
    await waitFor(() => expect(document.querySelector('.live-session-workspace')).toHaveAttribute('data-layout', 'cart'))
    expect(mocks.call.mock.calls.filter(([path, options]) => path.endsWith('/context') && options?.method === 'PUT')).toHaveLength(0)
  })

  it('serializes saves and restores an empty cart when an add is removed before its save finishes', async () => {
    let finishSave!: (value: unknown) => void
    const delayed = new Promise(resolve => { finishSave = resolve })
    let saves = 0
    installLiveApi((path, options) => path.endsWith('/context') && options.method === 'PUT' && ++saves === 1 ? delayed : undefined)
    await openFirst()
    await act(async () => mocks.recorder!.onConversationChange('clip-a'))
    await screen.findAllByText(liveTurn.english_text)
    fireEvent.click(screen.getByRole('button', { name: 'Add to cart BLDC fan' }))
    await waitFor(() => expect(saves).toBe(1))
    fireEvent.click(screen.getByRole('button', { name: 'Remove BLDC fan from cart' }))
    await act(async () => finishSave({}))
    await waitFor(() => expect(saves).toBe(2))
    const writes = mocks.call.mock.calls.filter(([path, options]) => path.endsWith('/context') && options?.method === 'PUT')
    expect(JSON.parse(writes.at(-1)![1].body).cart).toEqual([])
    expect(document.querySelector('.live-session-workspace')).toHaveAttribute('data-layout', 'conversation')
  })
})

const pdfResult = (status = 'not_requested') => ({ document: { id: 'workflow-1', number: 'INV-1', type: 'invoice', demo: false }, delivery: { status, provider: 'smtp2go' }, pdf: { filename: 'INV-1.pdf', mime_type: 'application/pdf', base64: btoa('%PDF-1.4\nSynthetic document') } })
async function reviewCart() {
  await openFirst()
  await act(async () => mocks.recorder!.onConversationChange('clip-a'))
  fireEvent.click(await screen.findByRole('button', { name: 'Add to cart BLDC fan' }))
  await act(async () => {})
  fireEvent.click(screen.getByRole('button', { name: 'Complete cart' }))
  await screen.findByRole('dialog', { name: 'Create sales document' })
}
describe('session sales document workflow', () => {
  it('downloads a real PDF with no customer email and uses the scoped idempotent workflow', async () => {
    installLiveApi((path, options) => path.endsWith('/sales-documents/options') ? { mode: 'zoho', email: { configured: true }, document_types: ['invoice', 'quote', 'sales-order'], customer: { name: 'Actual Customer', phone: '9876543210', email: '' } } : path.endsWith('/sales-documents') && options.method === 'POST' ? pdfResult() : undefined)
    await reviewCart()
    expect(screen.getByText('Actual Customer')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Create & download PDF' }))
    expect(await screen.findByText('Document created and downloaded as PDF.')).toBeInTheDocument()
    const writes = mocks.call.mock.calls.filter(([path, options]) => path.endsWith('/sales-documents') && options.method === 'POST')
    expect(writes).toHaveLength(1)
    const body = JSON.parse(writes[0][1].body)
    expect(body).toMatchObject({ type: 'invoice', delivery: 'download', recipient: '', line_items: [{ item_id: 'fan-1', quantity: 1, rate: 4499 }] })
    expect(writes[0][1].headers['Idempotency-Key']).toBe(body.request_id)
    expect(writes[0][1].timeoutMs).toBe(90000)
    expect(URL.createObjectURL).toHaveBeenCalledWith(expect.objectContaining({ type: 'application/pdf' }))
    expect(mocks.call.mock.calls.some(([path]) => ['/invoices', '/quotes', '/orders'].includes(path))).toBe(false)
  })

  it('retries a failed email on the created document and reports acceptance accurately', async () => {
    installLiveApi((path, options) => path.endsWith('/sales-documents') && options.method === 'POST' ? pdfResult('failed') : path.endsWith('/sales-documents/workflow-1/email') ? { delivery: { status: 'accepted', provider: 'smtp2go' } } : undefined)
    await reviewCart()
    fireEvent.click(screen.getByRole('button', { name: 'Create & send' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Retry email' }))
    expect(await screen.findByText('Document created. Submitted for delivery via SMTP2GO.')).toBeInTheDocument()
    expect(mocks.call.mock.calls.filter(([path, options]) => path.endsWith('/sales-documents') && options.method === 'POST')).toHaveLength(1)
    expect(mocks.call.mock.calls.filter(([path]) => path.endsWith('/sales-documents/workflow-1/email'))).toHaveLength(1)
    expect(screen.queryByText(/delivered successfully/)).not.toBeInTheDocument()
  })

  it('uses a new delivery action after each confirmed email rejection without recreating the document', async () => {
    let deliveries = 0
    installLiveApi((path, options) => path.endsWith('/sales-documents') && options.method === 'POST' ? pdfResult('failed') : path.endsWith('/sales-documents/workflow-1/email') ? { delivery: { status: ++deliveries === 1 ? 'failed' : 'accepted', provider: 'smtp2go' } } : undefined)
    await reviewCart()
    fireEvent.click(screen.getByRole('button', { name: 'Create & send' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Retry email' }))
    await waitFor(() => expect(deliveries).toBe(1))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry email' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Retry email' }))
    await screen.findByText('Document created. Submitted for delivery via SMTP2GO.')
    const retries = mocks.call.mock.calls.filter(([path]) => path.endsWith('/sales-documents/workflow-1/email'))
    expect(retries).toHaveLength(2)
    expect(JSON.parse(retries[0][1].body).request_id).not.toBe(JSON.parse(retries[1][1].body).request_id)
    expect(mocks.call.mock.calls.filter(([path, options]) => path.endsWith('/sales-documents') && options.method === 'POST')).toHaveLength(1)
  })

  it('allows PDF download after a definitive SMTP pre-creation failure', async () => {
    let attempts = 0
    installLiveApi((path, options) => path.endsWith('/sales-documents') && options.method === 'POST' ? ++attempts === 1 ? Promise.reject(Object.assign(new Error('SMTP2GO is not configured'), { status: 503, details: { document_started: false } })) : pdfResult() : undefined)
    await reviewCart()
    fireEvent.click(screen.getByRole('button', { name: 'Create & send' }))
    await screen.findByText('SMTP2GO is not configured')
    expect(screen.getByRole('textbox', { name: 'Email subject' })).toBeEnabled()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Download PDF instead of emailing' }))
    fireEvent.click(screen.getByRole('button', { name: 'Create & download PDF' }))
    await screen.findByText('Document created and downloaded as PDF.')
    expect(attempts).toBe(2)
  })

  it('resumes the same failed document after closing and reopening its unchanged cart', async () => {
    installLiveApi((path, options) => path.endsWith('/sales-documents') && options.method === 'POST' ? pdfResult('failed') : path.endsWith('/sales-documents/workflow-1/email') ? { delivery: { status: 'accepted', provider: 'smtp2go' } } : undefined)
    await reviewCart()
    fireEvent.click(screen.getByRole('button', { name: 'Create & send' }))
    await screen.findByRole('button', { name: 'Retry email' })
    fireEvent.click(screen.getByRole('button', { name: 'Back to cart' }))
    fireEvent.click(screen.getByRole('button', { name: 'Complete cart' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Retry email' }))
    await screen.findByText('Document created. Submitted for delivery via SMTP2GO.')
    expect(mocks.call.mock.calls.filter(([path, options]) => path.endsWith('/sales-documents') && options.method === 'POST')).toHaveLength(1)
  })

  it('reuses the same request ID after a lost create response and blocks customer changes while submitting', async () => {
    let finish!: (value: unknown) => void
    let creates = 0
    const pending = new Promise(resolve => { finish = resolve })
    installLiveApi((path, options) => path.endsWith('/sales-documents') && options.method === 'POST' ? ++creates === 1 ? Promise.reject(new Error('Response was lost')) : pending : undefined)
    await reviewCart()
    fireEvent.click(screen.getByRole('button', { name: 'Create & send' }))
    await screen.findByText('Response was lost')
    expect(screen.getByRole('textbox', { name: 'Email subject' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Check document status' }))
    await waitFor(() => expect(creates).toBe(2))
    expect(screen.getByText('Customer B').closest('button')).toBeDisabled()
    const writes = mocks.call.mock.calls.filter(([path, options]) => path.endsWith('/sales-documents') && options.method === 'POST')
    expect(JSON.parse(writes[0][1].body).request_id).toBe(JSON.parse(writes[1][1].body).request_id)
    await act(async () => finish(pdfResult('accepted')))
    await screen.findByText('Document created. Submitted for delivery via SMTP2GO.')
  })
})

describe('speech processing status independently of capture', () => {
  const captureState = { capture: 'recording' }
  const ready = { speech_configured: true, translation_configured: true }
  it.each([
    ['transcribing', 'transcribing', 'Transcribing'],
    ['translating', 'translating', 'Translating'],
    ['blocked', 'failed', 'Speech processing needs attention'],
    ['up_to_date', 'ready', 'Live translation up to date'],
  ])('uses the server %s stage during active capture', (stage, key, label) => {
    expect(liveProcessingStatus({ captureState, providerReadiness: ready, processingStatus: { stage } })).toMatchObject({ key, label })
  })
  it('shows a blocked provider during recording and keeps completed output authoritative', () => {
    const providerReadiness = { speech_configured: true, translation_configured: false }
    expect(liveProcessingStatus({ captureState, providerReadiness })).toMatchObject({ key: 'failed', label: 'Translation unavailable' })
    expect(liveProcessingStatus({ snapshot: { capture_state: 'stopped', final_state: 'complete', english_state: 'complete' }, providerReadiness })).toMatchObject({ key: 'complete', label: 'Processing complete' })
  })
  it('does not let capture hide a failed stage, or claim old translated text is new processing', () => {
    expect(liveProcessingStatus({ captureState, snapshot: { transcription_state: 'blocked' } })).toMatchObject({ key: 'failed', label: 'Transcription unavailable' })
    expect(liveProcessingStatus({ captureState, snapshot: { transcription_state: 'available', english_state: 'available' }, turns: [liveTurn] })).toMatchObject({ key: 'ready', label: 'Live translation up to date', available: true })
  })
  it('clearly identifies a recording that only supports processing after stopping', () => {
    expect(liveProcessingStatus({ captureState, processingMode: 'finalized_only' })).toMatchObject({ key: 'ready', label: 'Translation starts after recording ends' })
  })
})
