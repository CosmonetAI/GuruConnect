import { useCallback, useState } from 'react'
import { act, render, screen } from '@testing-library/react'
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
  return <><p aria-label="form">{JSON.stringify(values)}</p><VoiceAssistPanel customerName="" values={values} validate={validate as never} onAiFieldsChange={onAi} onApply={(field, value) => { onApply(field, value); setValues(previous => ({ ...previous, [field]: value })) }} onConversationChange={vi.fn()} onBusyChange={vi.fn()} /></>
}
function setup(values = empty, onAi?: (f: string[]) => void) {
  const onApply = vi.fn()
  render(<Harness initial={values} onApply={onApply} onAi={onAi} />)
  return onApply
}
const segment = (text: string, speaker: LiveSegment['speaker'] = 'Unknown'): LiveSegment => ({ id: text, text, final: true, speaker })
beforeEach(() => { vi.clearAllMocks(); mocks.speech = null; mocks.supported = true })

describe('VoiceAssistPanel', () => {
  it('starts captions automatically with recording and stops when paused', () => {
    setup()
    expect(mocks.speech).toBeNull()
    act(() => mocks.emit!(state()))
    expect(mocks.speech).not.toBeNull()
    act(() => mocks.emit!(state({ capture: 'paused' })))
    expect(mocks.stop).toHaveBeenCalledTimes(1)
  })
  it('shows a compact transcript without configuration controls', () => {
    setup()
    act(() => mocks.emit!(state()))
    act(() => mocks.speech!.onSegment(segment('how can I help you today?')))
    expect(screen.getByText('how can I help you today?')).toBeInTheDocument()
    expect(screen.queryByText(/Auto-fill confidence threshold/)).toBeNull()
    expect(screen.queryByText(/Speaker label/)).toBeNull()
  })
  it('auto-fills visitor details and never uses receptionist details', () => {
    const onApply = setup(empty)
    act(() => mocks.emit!(state()))
    act(() => mocks.speech!.onSegment(segment('my name is priya sharma', 'Visitor')))
    act(() => mocks.speech!.onSegment(segment('my mobile number is 9876543210', 'Receptionist')))
    expect(onApply).toHaveBeenCalledWith('customerName', 'Priya Sharma')
    expect(onApply).toHaveBeenCalledTimes(1)
  })
  it('does not replace a manually entered value', () => {
    const onApply = setup({ ...empty, customerName: 'Typed Name' })
    act(() => mocks.emit!(state()))
    act(() => mocks.speech!.onSegment(segment('my name is priya sharma', 'Visitor')))
    expect(onApply).not.toHaveBeenCalled()
  })
  it('surfaces caption errors without stopping the recorder', () => {
    setup()
    act(() => mocks.emit!(state()))
    act(() => mocks.speech!.onError('permission'))
    expect(screen.getByRole('alert')).toHaveTextContent(/blocked by the browser/i)
  })
})
