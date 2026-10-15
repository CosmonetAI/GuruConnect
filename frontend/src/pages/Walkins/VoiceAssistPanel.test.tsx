import { useCallback, useState } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CaptureState } from '../../recording/session'
import type { LiveSegment } from '../../voice/liveSpeech'

const mocks = vi.hoisted(() => ({
  emit: null as ((state: unknown) => void) | null,
  speech: null as null | { onSegment: (segment: unknown) => void; onError: (error: string) => void },
  stop: vi.fn(),
  supported: true,
}))
vi.mock('../Enquiries/ConversationRecorder', () => ({
  default: ({ onCaptureChange }: { onCaptureChange: (state: unknown) => void }) => { mocks.emit = onCaptureChange; return <div>recorder</div> },
}))
vi.mock('../../voice/liveSpeech', () => ({
  liveSpeechSupported: () => mocks.supported,
  startLiveSpeech: (handlers: NonNullable<typeof mocks.speech>) => { mocks.speech = handlers; return { stop: mocks.stop } },
}))
import VoiceAssistPanel from './VoiceAssistPanel'

const state = (values: Partial<CaptureState> = {}): CaptureState => ({ capture: 'recording', samples: 1, elapsedSeconds: 1, level: 0.05, pendingChunks: 0, pendingBytes: 0, confirmedSamples: 0, saving: false, ...values })
const empty = { customerName: '', mobileNumber: '', email: '', companyName: '' }
function Harness({ initial, onApply, onAi }: { initial: typeof empty; onApply: (f: string, v: string) => void; onAi?: (f: string[]) => void }) {
  const [values, setValues] = useState(initial)
  const validate = useCallback((field: string, value: string) => field === 'mobileNumber' && value.length < 7 ? 'Enter a valid mobile number.' : null, [])
  return <><input aria-label="typed email" value={values.email} onChange={event => setValues({ ...values, email: event.target.value })} /><p aria-label="form">{JSON.stringify(values)}</p><VoiceAssistPanel customerName="" values={values} validate={validate as never} onAiFieldsChange={onAi} onApply={(field, value) => { onApply(field, value); setValues(previous => ({ ...previous, [field]: value })) }} onConversationChange={vi.fn()} onBusyChange={vi.fn()} /></>
}
function setup(values = empty, onAi?: (f: string[]) => void) {
  const onApply = vi.fn()
  render(<Harness initial={values} onApply={onApply} onAi={onAi} />)
  return onApply
}
const segment = (text: string, speaker: LiveSegment['speaker'] = 'Unknown'): LiveSegment => ({ id: text, text, final: true, speaker })
beforeEach(() => { vi.clearAllMocks(); mocks.speech = null; mocks.supported = true })

describe('VoiceAssistPanel', () => {
  it('only starts live captions while recording, and stops when paused or unmounted', () => {
    setup()
    fireEvent.click(screen.getByRole('checkbox', { name: /live captions/i }))
    expect(mocks.speech).toBeNull()
    act(() => mocks.emit!(state()))
    expect(mocks.speech).not.toBeNull()
    act(() => mocks.emit!(state({ capture: 'paused' })))
    expect(mocks.stop).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/^Paused\./)).toBeInTheDocument()
  })
  it('disables captions and explains when unsupported', () => {
    mocks.supported = false
    setup()
    expect(screen.getByRole('checkbox', { name: /live captions/i })).toBeDisabled()
    expect(screen.getByText(/not available in this browser/i)).toBeInTheDocument()
  })
  it('auto-labels an unlabelled self-introduction as Visitor and auto-fills from it', () => {
    const onApply = setup()
    fireEvent.click(screen.getByRole('checkbox', { name: /live captions/i }))
    act(() => mocks.emit!(state()))
    act(() => mocks.speech!.onSegment(segment('how can I help you today?')))
    expect(screen.getAllByLabelText('Speaker label')[0]).toHaveValue('Receptionist')
    act(() => mocks.speech!.onSegment(segment('my name is priya sharma')))
    expect(screen.getAllByLabelText('Speaker label')[1]).toHaveValue('Visitor')
    expect(onApply).toHaveBeenCalledWith('customerName', 'Priya Sharma')
  })
  it('requires an explicit replace action when the field already has a value and blocks invalid values', () => {
    const onApply = setup({ ...empty, customerName: 'Typed Name' })
    fireEvent.click(screen.getByRole('checkbox', { name: /live captions/i }))
    act(() => mocks.emit!(state()))
    act(() => mocks.speech!.onSegment(segment('my name is priya sharma')))
    act(() => mocks.speech!.onSegment(segment('phone number is 12345678')))
    expect(onApply).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /replace “typed name”/i })).toBeInTheDocument()
    fireEvent.change(screen.getByDisplayValue('12345678'), { target: { value: '123' } })
    expect(screen.getByRole('button', { name: /use for mobile number/i })).toBeDisabled()
  })
  it('raises a low-volume alert after the configured delay and honours a custom threshold', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    try {
      setup()
      act(() => mocks.emit!(state({ level: 0.001 })))
      expect(screen.queryByText(/very quiet/i)).toBeNull()
      vi.setSystemTime(Date.now() + 6000)
      act(() => mocks.emit!(state({ level: 0.0011 })))
      expect(screen.getByText(/very quiet/i)).toBeInTheDocument()
      act(() => mocks.emit!(state({ level: 0.2 })))
      expect(screen.queryByText(/very quiet/i)).toBeNull()
    } finally { vi.useRealTimers() }
  })
  it('surfaces live caption errors without stopping the recording UI', () => {
    setup()
    fireEvent.click(screen.getByRole('checkbox', { name: /live captions/i }))
    act(() => mocks.emit!(state()))
    act(() => mocks.speech!.onError('permission'))
    expect(screen.getByRole('alert')).toHaveTextContent(/blocked by the browser/i)
    expect(screen.getByRole('checkbox', { name: /live captions/i })).not.toBeChecked()
  })
  it('auto-fills a blank field with a high-confidence value, marks it AI populated and never submits', () => {
    const onAi = vi.fn()
    const onApply = setup(empty, onAi)
    act(() => mocks.emit!(state()))
    fireEvent.click(screen.getByRole('checkbox', { name: /live captions/i }))
    act(() => mocks.speech!.onSegment(segment('my email is priya@example.com', 'Visitor')))
    expect(onApply).toHaveBeenCalledWith('email', 'priya@example.com')
    expect(onApply).toHaveBeenCalledTimes(1)
    expect(onAi).toHaveBeenLastCalledWith(['email'])
    expect(screen.queryByRole('button', { name: /use for email/i })).toBeNull()
  })
  it('keeps a manual edit of an AI-filled field and never auto-fills a user-cleared field', () => {
    const onApply = setup(empty, undefined)
    act(() => mocks.emit!(state()))
    fireEvent.click(screen.getByRole('checkbox', { name: /live captions/i }))
    act(() => mocks.speech!.onSegment(segment('my email is priya@example.com', 'Visitor')))
    fireEvent.change(screen.getByLabelText('typed email'), { target: { value: 'mine@example.com' } })
    act(() => mocks.speech!.onSegment(segment('email is other@example.com')))
    expect(onApply).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('form')).toHaveTextContent('mine@example.com')
    expect(screen.getByRole('button', { name: /replace .mine@example.com./i })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('typed email'), { target: { value: '' } })
    act(() => mocks.speech!.onSegment(segment('email is third@example.com')))
    expect(onApply).toHaveBeenCalledTimes(1)
  })
  it('does not auto-fill medium, conflicting, or non-blank cases and honours the toggle', () => {
    const onApply = setup({ ...empty, email: 'typed@example.com' })
    act(() => mocks.emit!(state()))
    fireEvent.click(screen.getByRole('checkbox', { name: /live captions/i }))
    act(() => mocks.speech!.onSegment(segment('my email is priya@example.com')))
    expect(onApply).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('checkbox', { name: /auto-fill blank fields/i }))
    act(() => mocks.speech!.onSegment(segment('my mobile number is 9876543210', 'Visitor')))
    expect(onApply).not.toHaveBeenCalled()
  })
  it('never auto-fills from receptionist speech, offering no suggestions', () => {
    const onApply = setup()
    act(() => mocks.emit!(state()))
    fireEvent.click(screen.getByRole('checkbox', { name: /live captions/i }))
    act(() => mocks.speech!.onSegment(segment('my mobile number is 9876543210', 'Receptionist')))
    act(() => mocks.speech!.onSegment(segment('may I have your email?')))
    expect(onApply).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /use for email/i })).toBeNull()
  })
  it('lets the confidence threshold decide whether a Visitor value auto-fills', () => {
    const onApply = setup()
    act(() => mocks.emit!(state()))
    fireEvent.click(screen.getByRole('checkbox', { name: /live captions/i }))
    fireEvent.change(screen.getByLabelText('Auto-fill confidence threshold'), { target: { value: '95' } })
    act(() => mocks.speech!.onSegment(segment('my name is priya sharma', 'Visitor')))
    expect(onApply).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Auto-fill confidence threshold'), { target: { value: '90' } })
    expect(onApply).toHaveBeenCalledWith('customerName', 'Priya Sharma')
  })
  it('requires confirmation when a blank field receives conflicting high-confidence values', () => {
    const onApply = setup()
    act(() => mocks.emit!(state()))
    fireEvent.click(screen.getByRole('checkbox', { name: /live captions/i }))
    act(() => mocks.speech!.onSegment(segment('email is a@example.com', 'Visitor')))
    expect(onApply).toHaveBeenCalledTimes(1)
    act(() => mocks.speech!.onSegment(segment('email is b@example.com', 'Visitor')))
    expect(onApply).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/conflicts with something said earlier/i)).toBeInTheDocument()
  })
})
