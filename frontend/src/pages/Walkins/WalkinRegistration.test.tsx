import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { VisitorField, VisitorFieldSuggestion } from './visitorExtraction'
import WalkinRegistration from './WalkinRegistration'

const services = vi.hoisted(() => ({
  register: vi.fn(),
  attach: vi.fn(),
  demo: false,
}))

vi.mock('../../contexts/OfficeContext', () => ({ useOffice: () => ({ workspace: { id: 'workspace-a' } }) }))
vi.mock('../../services/walkinService', () => ({
  attachWalkinRecording: services.attach,
  isWalkinDemoMode: () => services.demo,
  registerWalkin: services.register,
}))
vi.mock('./WalkinVoiceAssistant', () => ({
  default: (props: {
    onConversationChange: (id: string | null) => void
    onSuggestions: (suggestions: Partial<Record<VisitorField, VisitorFieldSuggestion>>) => void
  }) => <div>
    <button type="button" onClick={() => props.onConversationChange('conversation-a')}>Set recording</button>
    <button type="button" onClick={() => props.onSuggestions({
      customerName: { value: 'Rahul Sharma', confidence: 0.93, sourceTurnId: 'turn-a', sourceText: 'My name is Rahul Sharma.', conflict: false, alternatives: ['Rahul Sharma'] },
      mobileNumber: { value: '+91 98765 43210', confidence: 0.96, sourceTurnId: 'turn-b', sourceText: 'My number is +91 98765 43210.', conflict: false, alternatives: ['+91 98765 43210'] },
      purpose: { value: 'Product demonstration', confidence: 0.82, sourceTurnId: 'turn-c', sourceText: 'I am here for a demo.', conflict: false, alternatives: ['Product demonstration'] },
    })}>Apply transcript suggestions</button>
  </div>,
}))

function renderPage() {
  return render(<MemoryRouter><WalkinRegistration /></MemoryRouter>)
}

beforeEach(() => {
  services.register.mockReset().mockResolvedValue({ id: 'visit-a', visitNumber: 'WI-20261014-ABC123' })
  services.attach.mockReset().mockResolvedValue(undefined)
  services.demo = false
})
afterEach(cleanup)

describe('walk-in voice registration', () => {
  it('fills visitor fields, preserves receptionist edits, and attaches saved audio after registration', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: 'Apply transcript suggestions' }))
    const customerName = screen.getByRole('textbox', { name: /Customer full name/ })
    expect(customerName).toHaveValue('Rahul Sharma')
    expect(screen.getAllByText('AI detected · review before registering')).toHaveLength(3)

    await user.clear(customerName)
    await user.type(customerName, 'Manual correction')
    await user.click(screen.getByRole('button', { name: 'Apply transcript suggestions' }))
    expect(customerName).toHaveValue('Manual correction')

    await user.click(screen.getByRole('button', { name: 'Set recording' }))
    await user.click(screen.getByRole('button', { name: 'Register visitor' }))
    await waitFor(() => expect(services.register).toHaveBeenCalledOnce())
    expect(services.register.mock.calls[0][1]).toMatchObject({
      customerName: 'Manual correction',
      mobileNumber: '+91 98765 43210',
      purpose: 'Product demonstration',
      appointmentStatus: 'Walk-in',
    })
    expect(services.attach).toHaveBeenCalledWith('workspace-a', 'visit-a', 'conversation-a')
  })

  it('prevents duplicate registration submissions while the first request is pending', async () => {
    let finish: (value: { id: string; visitNumber: string }) => void = () => {}
    services.register.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: 'Apply transcript suggestions' }))
    const register = screen.getByRole('button', { name: 'Register visitor' })
    await user.click(register)
    expect(register).toBeDisabled()
    fireEvent.click(register)
    expect(services.register).toHaveBeenCalledOnce()
    finish({ id: 'visit-a', visitNumber: 'WI-20261014-ABC123' })
    await waitFor(() => expect(services.attach).not.toHaveBeenCalled())
  })
})
