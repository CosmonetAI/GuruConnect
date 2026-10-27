import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ call: vi.fn(), busy: vi.fn(), recorder: null as null | { onBusyChange: (busy: boolean) => void; onConversationChange: (id: string | null) => Promise<void>; customerName: string } }))
vi.mock('react-router-dom', async original => ({ ...await original<typeof import('react-router-dom')>(), useBlocker: () => ({ state: 'unblocked', reset: vi.fn() }) }))
vi.mock('../contexts/OfficeContext', () => ({ useOffice: () => ({ user: { id: 'u' }, workspace: { id: 'w', member_id: 'member' }, setCaptureBusy: mocks.busy }) }))
vi.mock('../zoho/shared/api.js', () => ({ call: mocks.call }))
vi.mock('./Enquiries/ConversationRecorder', () => ({ default: (props: NonNullable<typeof mocks.recorder>) => { mocks.recorder = props; return <div aria-label="recorder customer">{props.customerName}</div> } }))
vi.mock('../components/ConversationTranscript', () => ({ default: () => <div>transcript</div> }))
vi.mock('../components/ProgressivePlayback', () => ({ default: () => <div>player</div> }))
import VoiceSessions from './VoiceSessions'
beforeEach(() => {
  vi.clearAllMocks(); mocks.recorder = null
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
