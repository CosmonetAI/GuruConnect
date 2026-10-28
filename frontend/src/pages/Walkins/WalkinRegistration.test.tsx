import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  register: vi.fn(), attach: vi.fn(), navigate: vi.fn(), demo: false,
  panel: null as null | { onConversationChange: (id: string | null) => void; onBusyChange: (busy: boolean) => void },
}))
vi.mock('react-router-dom', async () => ({ ...(await vi.importActual<object>('react-router-dom')), useNavigate: () => mocks.navigate, useBlocker: () => ({ state: 'unblocked', reset: vi.fn() }) }))
vi.mock('../../contexts/OfficeContext', () => ({ useOffice: () => ({ user: { id: 'u1' }, workspace: { id: 'w1' } }) }))
vi.mock('../../services/walkinService', () => ({ registerWalkin: mocks.register, attachWalkinRecording: mocks.attach, isWalkinDemoMode: () => mocks.demo }))
vi.mock('../Enquiries/ConversationRecorder', () => ({ default: (props: NonNullable<typeof mocks.panel>) => { mocks.panel = props; return <div>compact recorder</div> } }))
import WalkinRegistration from './WalkinRegistration'

function fill() {
  fireEvent.change(screen.getByLabelText(/customer full name/i), { target: { value: 'Asha Rao' } })
  fireEvent.change(screen.getByLabelText(/mobile number/i), { target: { value: '9876543210' } })
  fireEvent.change(screen.getByLabelText(/email address/i), { target: { value: 'asha@example.com' } })
}
const submit = () => fireEvent.click(screen.getByRole('button', { name: /register visitor/i }))
beforeEach(() => {
  vi.clearAllMocks(); sessionStorage.clear(); mocks.demo = false
  mocks.register.mockResolvedValue({ id: 'v1', visitNumber: 'WI-1', ticket: { number: 3, token: 't' } })
  mocks.attach.mockResolvedValue({ ok: true })
})
function setup() { render(<MemoryRouter><WalkinRegistration /></MemoryRouter>) }

describe('WalkinRegistration voice assist', () => {
  it('never submits on its own and applies voice values only through the form', () => {
    setup()
    fill()
    expect(mocks.register).not.toHaveBeenCalled()
    expect(screen.getByLabelText(/customer full name/i)).toHaveValue('Asha Rao')
  })
  it('keeps validation and blocks registration while a recording is unfinished', async () => {
    setup()
    submit()
    expect(await screen.findByText(/enter the customer’s full name/i)).toBeInTheDocument()
    fill(); act(() => mocks.panel!.onBusyChange(true))
    expect(screen.getByRole('button', { name: /register visitor/i })).toBeDisabled()
    expect(mocks.register).not.toHaveBeenCalled()
  })
  it('registers without audio exactly as before', async () => {
    setup(); fill(); submit()
    await waitFor(() => expect(mocks.navigate).toHaveBeenCalled())
    expect(mocks.navigate).toHaveBeenCalledWith('/walkins', { replace: true })
    expect(mocks.attach).not.toHaveBeenCalled()
  })
  it('attaches saved audio after registration', async () => {
    setup(); fill(); mocks.panel!.onConversationChange('c1'); submit()
    await waitFor(() => expect(mocks.attach).toHaveBeenCalledWith('w1', 'v1', 'c1'))
    expect(mocks.navigate).toHaveBeenCalledWith('/walkins', { replace: true })
    expect(sessionStorage.length).toBe(0)
  })
  it('stages audio and still navigates when the backend refuses the link', async () => {
    mocks.attach.mockRejectedValue(new Error('walkin_assigned_agent_required'))
    setup(); fill(); mocks.panel!.onConversationChange('c1'); submit()
    await waitFor(() => expect(mocks.navigate).toHaveBeenCalled())
    expect(sessionStorage.getItem('guruconnect:walkin-audio:u1:w1:v1')).toBe('c1')
    expect(mocks.navigate).toHaveBeenCalledWith('/walkins', { replace: true })
  })
})
