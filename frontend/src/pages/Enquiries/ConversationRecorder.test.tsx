import { StrictMode } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CaptureState, RecordingOptions } from '../../recording/session'

const mocks = vi.hoisted(() => ({
  office: { user: { id: 'alice' }, workspace: { id: 'workspace-a' }, loading: false, error: '' } as { user: { id: string } | null; workspace: { id: string; member_id?: string } | null; loading: boolean; error: string },
  request: vi.fn(), recoveries: vi.fn(), prepare: vi.fn(), upload: vi.fn(), recover: vi.fn(), start: vi.fn(), pause: vi.fn(), resume: vi.fn(), stop: vi.fn(), dispose: vi.fn(), discard: vi.fn(),
  options: null as RecordingOptions | null,
  localMode: false,
}))
vi.mock('../../contexts/OfficeContext', () => ({ useOffice: () => ({ ...mocks.office, localMode: mocks.localMode }) }))
vi.mock('../../services/officeApi', () => ({
  request: mocks.request,
  get localMode() { return mocks.localMode },
  json: (method: string, body: object, key?: string) => ({ method, body: JSON.stringify(body), headers: key ? { 'Idempotency-Key': key } : {} }),
  message: (error: unknown) => error instanceof Error ? error.message : String(error),
  duration: (seconds: number) => `${seconds.toFixed(1)}s`,
}))
vi.mock('../../recording/session', () => ({
  listRecoveries: mocks.recoveries, recoverRecording: mocks.recover, discardRecovery: mocks.discard,
  RecorderSession: class {
    constructor(options: RecordingOptions) { mocks.options = options }
    subscribe = vi.fn(() => () => {})
    start = mocks.start; pause = mocks.pause; resume = mocks.resume; stop = mocks.stop; dispose = mocks.dispose
  },
}))
vi.mock('../../recording/upload', () => ({
  MAX_AUDIO_FILE_BYTES: 100 * 1024 * 1024,
  AudioUploadError: class extends Error { constructor(message: string, public preserved: boolean) { super(message) } },
  prepareAudioFile: mocks.prepare, uploadAudioFile: mocks.upload,
}))
import ConversationRecorder from './ConversationRecorder'
import { AudioUploadError } from '../../recording/upload'

const state = (values: Partial<CaptureState> = {}): CaptureState => ({ capture: 'recording', samples: 0, elapsedSeconds: 0, level: 0, pendingChunks: 0, pendingBytes: 0, confirmedSamples: 0, saving: false, ...values })
function setup() {
  const onConversationChange = vi.fn(); const onBusyChange = vi.fn(); const onSaved = vi.fn()
  render(<StrictMode><ConversationRecorder customerName="Synthetic Customer" customerPhone="9876543210" onConversationChange={onConversationChange} onBusyChange={onBusyChange} onSaved={onSaved} /></StrictMode>)
  return { onConversationChange, onBusyChange, onSaved }
}
async function ready() { await waitFor(() => expect(screen.getByRole('button', { name: 'Start recording' })).not.toBeDisabled()) }
function consent() { fireEvent.click(screen.getByRole('checkbox', { name: /permission from all/ })) }
function file() {
  fireEvent.change(screen.getByLabelText('Choose audio file'), { target: { files: [new File(['synthetic'], 'synthetic.m4a', { type: 'audio/mp4' })] } })
}
beforeEach(() => {
  vi.clearAllMocks()
  mocks.localMode = false
  mocks.office = { user: { id: 'alice' }, workspace: { id: 'workspace-a' }, loading: false, error: '' }
  mocks.recoveries.mockResolvedValue([]); mocks.prepare.mockResolvedValue({ name: 'synthetic.m4a', samples: new Float32Array([0, .2, -.2]) }); mocks.upload.mockResolvedValue(undefined); mocks.recover.mockImplementation(async () => { mocks.recoveries.mockResolvedValue([]) }); mocks.discard.mockResolvedValue(undefined); mocks.dispose.mockResolvedValue(undefined)
  mocks.request.mockImplementation(async (path: string) => {
    if (path === '/engine-settings') return { default_engine_id: 'elevenlabs' }
    if (path === '/connections') return { items: [{ engine_id: 'elevenlabs', enabled: true, state: 'connected' }] }
    if (path === '/conversations') return { id: 'conversation-1' }
    if (path.endsWith('/lease')) return { part_id: 'part-1', lease_token: 'lease-1' }
    return { id: 'conversation-1', upload_state: 'pending' }
  })
  mocks.start.mockImplementation(async () => mocks.options?.onState(state()))
  mocks.pause.mockResolvedValue(undefined); mocks.resume.mockResolvedValue(undefined)
  mocks.stop.mockImplementation(async () => mocks.options?.onState(state({ capture: 'stopped', samples: 32000, elapsedSeconds: 2, confirmedSamples: 32000 })))
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn().mockReturnValue('blob:synthetic'), revokeObjectURL: vi.fn() }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('authenticated conversation audio', () => {
  it('starts a customer session without an email address so a PDF can be downloaded later', async () => {
    render(<ConversationRecorder sessionOnly customerName="Customer without email" customerPhone="9876543210" onConversationChange={vi.fn()} />)
    consent()
    fireEvent.click(screen.getByRole('button', { name: 'Start session' }))
    await screen.findByRole('button', { name: 'End session' })
    expect(JSON.parse(mocks.request.mock.calls.find(([path]) => path === '/conversations')![2].body)).toMatchObject({ customer_name: 'Customer without email', email: null })
  })

  it('uses runtime provider configuration ahead of stale connection metadata', async () => {
    const normal = mocks.request.getMockImplementation()!
    mocks.request.mockImplementation(async (...args) => args[0] === '/connections' ? { items: ['elevenlabs', 'openai-translator'].map(engine_id => ({ engine_id, state: 'configured', runtime_configured: true, last_error_code: 'engine_unconfigured' })) } : normal(...args))
    setup()
    await act(async () => {})
    expect(screen.queryByText(/Speech processing is unavailable/)).not.toBeInTheDocument()
  })

  it('does not treat a connected metadata row as runtime credentials when the server says they are missing', async () => {
    const normal = mocks.request.getMockImplementation()!
    mocks.request.mockImplementation(async (...args) => args[0] === '/connections' ? { items: ['elevenlabs', 'openai-translator'].map(engine_id => ({ engine_id, state: 'connected', runtime_configured: false })) } : normal(...args))
    setup()
    await screen.findByText(/Speech processing is unavailable/)
    expect(screen.getByRole('button', { name: 'Start recording' })).toBeDisabled()
    consent()
    expect(screen.getByRole('button', { name: 'Start recording' })).toBeEnabled()
  })

  it('updates readiness in place and removes a stale unavailable warning once processing completes', async () => {
    const normal = mocks.request.getMockImplementation()!
    mocks.request.mockImplementation(async (...args) => args[0] === '/connections' ? { items: [] } : normal(...args))
    const props = { customerName: 'Live Customer', customerPhone: '9876543210', customerEmail: 'live@example.com', onConversationChange: vi.fn(), sessionOnly: true }
    const view = render(<ConversationRecorder {...props} />)
    await screen.findByText(/Speech processing is unavailable/)
    view.rerender(<ConversationRecorder {...props} providerReadiness={{ speech_configured: true, translation_configured: true }} />)
    expect(screen.queryByText(/Speech processing is unavailable/)).not.toBeInTheDocument()
    view.rerender(<ConversationRecorder {...props} processingStatus={{ key: 'complete', label: 'Processing complete', detail: 'Complete' }} />)
    expect(screen.queryByText(/Speech processing is unavailable/)).not.toBeInTheDocument()
    expect(screen.queryByText(/waiting for the managed speech services/)).not.toBeInTheDocument()
    expect(mocks.dispose).not.toHaveBeenCalled()
  })

  it('allows private localhost audio capture before provider keys are configured', async () => {
    mocks.localMode = true
    const normal = mocks.request.getMockImplementation()!
    mocks.request.mockImplementation(async (...args) => args[0] === '/connections' ? { items: [] } : normal(...args))
    setup(); consent(); await ready()
    expect(screen.getByText(/Audio and enquiries save on this computer/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Start recording' }))
    await screen.findByRole('button', { name: 'Stop & save' })
    expect(mocks.start).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole('button', { name: 'Stop & save' }))
    await screen.findByText('Audio saved in your workspace')
  })
  it('starts microphone capture when processing connections are unavailable', async () => {
    const normal = mocks.request.getMockImplementation()!
    mocks.request.mockImplementation(async (...args) => {
      if (args[0] === '/engine-settings' || args[0] === '/connections') throw new Error('processing unavailable')
      return normal(...args)
    })
    setup(); consent(); await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Start recording' }))
    await screen.findByRole('button', { name: 'Stop & save' })
    expect(mocks.start).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole('button', { name: 'Stop & save' }))
    await screen.findByText('Audio saved in your workspace')
  })
  it('guards an unsent selected file until upload or explicit removal', async () => {
    const callbacks = setup(); consent(); await ready(); file()
    await waitFor(() => expect(callbacks.onBusyChange).toHaveBeenLastCalledWith(true))
    fireEvent.click(screen.getByRole('button', { name: 'Remove selected file' }))
    await waitFor(() => expect(callbacks.onBusyChange).toHaveBeenLastCalledWith(false))
    expect(mocks.upload).not.toHaveBeenCalled()
    expect(mocks.request.mock.calls.some(([path]) => path === '/conversations')).toBe(false)
  })
  it('requires consent before opening the microphone or uploading', async () => {
    setup()
    await waitFor(() => expect(screen.queryByText('Checking processing connections…')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Start recording' })).toBeDisabled()
    expect(mocks.start).not.toHaveBeenCalled()
    expect(mocks.request.mock.calls.some(([path]) => path === '/conversations')).toBe(false)
    consent(); await ready()
  })
  it('creates authenticated consent and lease once, then reports saved only after Stop finalizes', async () => {
    const callbacks = setup(); consent(); await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Start recording' }))
    await screen.findByRole('button', { name: 'Stop & save' })
    const createCalls = mocks.request.mock.calls.filter(([path]) => path === '/conversations')
    expect(createCalls).toHaveLength(1)
    expect(createCalls[0][1]).toBe('workspace-a')
    expect(JSON.parse(createCalls[0][2].body)).toMatchObject({ customer_name: 'Synthetic Customer', consent_confirmed: true, audio_only: false })
    expect(callbacks.onSaved).not.toHaveBeenCalled()
    expect(callbacks.onBusyChange).toHaveBeenLastCalledWith(true)
    fireEvent.click(screen.getByRole('button', { name: 'Stop & save' }))
    await screen.findByText('Audio saved in your workspace')
    expect(callbacks.onSaved).toHaveBeenCalledOnce()
    expect(callbacks.onSaved).toHaveBeenCalledWith('conversation-1')
    await waitFor(() => expect(callbacks.onBusyChange).toHaveBeenLastCalledWith(false))
  })
  it('keeps Stop available after a pause error, so the microphone cannot be hidden', async () => {
    mocks.pause.mockRejectedValue(new Error('pause failure'))
    setup(); consent(); await ready(); fireEvent.click(screen.getByRole('button', { name: 'Start recording' }))
    await screen.findByRole('button', { name: 'Pause' }); fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    await screen.findByText('pause failure')
    expect(screen.getByRole('button', { name: 'Stop & save' })).not.toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Retry microphone' })).not.toBeInTheDocument()
  })
  it('decodes a selected file before creating a conversation and uses the same authenticated upload path', async () => {
    const callbacks = setup(); consent(); await ready(); file()
    fireEvent.click(screen.getByRole('button', { name: 'Upload & process' }))
    await screen.findByText('Audio saved in your workspace')
    expect(mocks.prepare).toHaveBeenCalledOnce()
    expect(mocks.prepare.mock.invocationCallOrder[0]).toBeLessThan(mocks.request.mock.invocationCallOrder[mocks.request.mock.calls.findIndex(([path]) => path === '/conversations')])
    expect(mocks.upload).toHaveBeenCalledWith(expect.objectContaining({ scope: { userId: 'alice', workspaceId: 'workspace-a' }, conversationId: 'conversation-1', partId: 'part-1', leaseToken: 'lease-1' }))
    expect(callbacks.onSaved).toHaveBeenCalledWith('conversation-1')
    expect(mocks.start).not.toHaveBeenCalled()
  })
  it('rejects corrupt files before creating or claiming to save audio', async () => {
    mocks.prepare.mockRejectedValue(new Error('This audio file could not be decoded.'))
    const callbacks = setup(); consent(); await ready(); file(); fireEvent.click(screen.getByRole('button', { name: 'Upload & process' }))
    await screen.findByText('This audio file could not be decoded.')
    expect(mocks.request.mock.calls.some(([path]) => path === '/conversations')).toBe(false)
    expect(mocks.upload).not.toHaveBeenCalled(); expect(callbacks.onSaved).not.toHaveBeenCalled()
  })
  it('retains a preserved failed upload, offers scoped recovery, and does not silently submit twice', async () => {
    const recovery = { key: 'alice/workspace-a/conversation-1', scope: 'alice/workspace-a', conversationId: 'conversation-1', partId: 'part-1', leaseToken: 'lease-1', totalSamples: 32000, chunkCount: 1, stopped: true, updatedAt: '2026-10-13' }
    mocks.upload.mockImplementation(async () => { mocks.recoveries.mockResolvedValue([recovery]); throw new AudioUploadError('Your audio is preserved.', true) })
    const callbacks = setup(); consent(); await ready(); file(); fireEvent.click(screen.getByRole('button', { name: 'Upload & process' }))
    await screen.findByRole('button', { name: 'Recover audio' })
    expect(screen.queryByText('Audio is waiting on this device')).not.toBeInTheDocument()
    expect(screen.queryByText(/Recover it to attach its saved conversation/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry audio upload' })).not.toBeInTheDocument()
    expect(mocks.upload).toHaveBeenCalledOnce(); expect(callbacks.onSaved).not.toHaveBeenCalled()
    expect(callbacks.onBusyChange).toHaveBeenLastCalledWith(true)
    fireEvent.click(screen.getByRole('button', { name: 'Recover audio' }))
    await screen.findByText('Audio saved in your workspace')
    expect(mocks.recover.mock.calls[0][0]).toEqual({ userId: 'alice', workspaceId: 'workspace-a' })
    expect(mocks.upload).toHaveBeenCalledOnce()
  })
  it('disposes active capture on unmount without starting another microphone', async () => {
    setup(); consent(); await ready(); fireEvent.click(screen.getByRole('button', { name: 'Start recording' }))
    await screen.findByRole('button', { name: 'Stop & save' })
    await act(async () => cleanup())
    expect(mocks.dispose).toHaveBeenCalledOnce()
    expect(mocks.start).toHaveBeenCalledOnce()
  })
})

describe('live customer sessions', () => {
  it('saves short capture chunks and keeps Pause, Resume and End available without ending on pause', async () => {
    const onSaved = vi.fn()
    mocks.pause.mockImplementation(async () => mocks.options?.onState(state({ capture: 'paused', samples: 160000, elapsedSeconds: 10 })))
    mocks.resume.mockImplementation(async () => mocks.options?.onState(state({ capture: 'recording', samples: 160000, elapsedSeconds: 10 })))
    render(<ConversationRecorder sessionOnly grouped chunkSeconds={5} customerName="Live Customer" customerPhone="9876543210" customerEmail="live@example.com" onConversationChange={vi.fn()} onSaved={onSaved} />)
    consent()
    fireEvent.click(screen.getByRole('button', { name: 'Start session' }))
    await screen.findByRole('button', { name: 'End session' })
    expect(mocks.options?.chunkSeconds).toBe(5)

    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    await screen.findByRole('button', { name: 'Resume' })
    expect(mocks.stop).not.toHaveBeenCalled()
    expect(onSaved).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'End session' })).not.toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    await screen.findByRole('button', { name: 'Pause' })
    fireEvent.click(screen.getByRole('button', { name: 'End session' }))
    await screen.findByRole('button', { name: 'Record again' })
    expect(onSaved).toHaveBeenCalledWith('conversation-1')
    fireEvent.click(screen.getByRole('button', { name: 'Record again' }))
    await screen.findByRole('button', { name: 'Start session' })
    expect(screen.getByRole('checkbox', { name: /permission from all/ })).not.toBeChecked()
  })

  it('offers recovery for a customer session whose upload was interrupted', async () => {
    mocks.recoveries.mockResolvedValue([{ key: 'alice/workspace-a/conversation-1', scope: 'alice/workspace-a', conversationId: 'conversation-1', partId: 'part-1', leaseToken: 'lease-1', totalSamples: 80000, chunkCount: 1, stopped: true, updatedAt: '2026-10-28' }])
    render(<ConversationRecorder sessionOnly customerName="Live Customer" customerPhone="9876543210" customerEmail="live@example.com" onConversationChange={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Recover audio' }))
    await screen.findByText('Session ended')
    expect(mocks.recover).toHaveBeenCalledOnce()
  })
})


describe('recording identity and consent boundaries', () => {
  it('requires explicit participant consent in compact mode', async () => {
    render(<ConversationRecorder compact customerName="Visitor" customerPhone="9876543210" onConversationChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Start recording' })).toBeDisabled()
    expect(mocks.request.mock.calls.some(([path]) => path === '/conversations')).toBe(false)
    consent(); fireEvent.click(screen.getByRole('button', { name: 'Start recording' }))
    await screen.findByRole('button', { name: 'Stop & save' })
    expect(JSON.parse(mocks.request.mock.calls.find(([path]) => path === '/conversations')![2].body)).toMatchObject({ consent_confirmed: true, audio_only: false })
    expect(mocks.options?.chunkSeconds).toBe(10)
  })
  it('starts a fresh separately saved clip only after resetting the saved recorder', async () => {
    const callbacks = setup(); consent(); await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Start recording' })); await screen.findByRole('button', { name: 'Stop & save' })
    fireEvent.click(screen.getByRole('button', { name: 'Stop & save' })); await screen.findByText('Audio saved in your workspace')
    fireEvent.click(screen.getByRole('button', { name: 'Record another conversation' }))
    await waitFor(() => expect(callbacks.onConversationChange).toHaveBeenLastCalledWith(null))
    expect(screen.getByRole('checkbox', { name: /permission from all/ })).not.toBeChecked()
    consent(); fireEvent.click(screen.getByRole('button', { name: 'Start recording' })); await screen.findByRole('button', { name: 'Stop & save' })
    const creates = mocks.request.mock.calls.filter(([path]) => path === '/conversations')
    expect(creates).toHaveLength(2)
    expect(creates[0][2].headers['Idempotency-Key']).not.toBe(creates[1][2].headers['Idempotency-Key'])
  })
  it('aborts an outstanding file upload and ignores its late result after changing accounts', async () => {
    let finish!: () => void
    mocks.upload.mockImplementation(() => new Promise<void>(resolve => { finish = resolve }))
    const onSaved = vi.fn(); const onConversationChange = vi.fn()
    const element = () => <ConversationRecorder customerName="Visitor" customerPhone="9876543210" onConversationChange={onConversationChange} onSaved={onSaved} />
    const view = render(element()); consent(); file(); fireEvent.click(screen.getByRole('button', { name: 'Upload & process' }))
    await waitFor(() => expect(mocks.upload).toHaveBeenCalledOnce())
    const signal = mocks.upload.mock.calls[0][0].signal as AbortSignal
    mocks.office = { ...mocks.office, user: { id: 'bob' } }; view.rerender(element())
    expect(signal.aborted).toBe(true)
    await act(async () => finish())
    expect(onSaved).not.toHaveBeenCalled()
    expect(screen.queryByText('Audio saved in your workspace')).not.toBeInTheDocument()
  })
  it('discards only the selected scoped pending audio after a successful server deletion', async () => {
    const row = { key: 'alice/workspace-a/pending', scope: 'alice/workspace-a', conversationId: 'pending', partId: 'p', leaseToken: 'l', totalSamples: 16000, chunkCount: 1, stopped: true, updatedAt: '2026-10-26' }
    mocks.recoveries.mockResolvedValue([row])
    setup(); await screen.findByRole('button', { name: 'Discard pending audio' })
    fireEvent.click(screen.getByRole('button', { name: 'Discard pending audio' }))
    await waitFor(() => expect(mocks.discard).toHaveBeenCalledWith({ userId: 'alice', workspaceId: 'workspace-a' }, 'pending'))
    expect(mocks.request).toHaveBeenCalledWith('/conversations/pending', 'workspace-a', expect.objectContaining({ method: 'DELETE' }), undefined)
  })
})


describe('owner member-review view', () => {
  it('allows recording only in the signed-in person’s own view', async () => {
    mocks.office.workspace = { id: 'workspace-a', member_id: 'another-person' }
    setup(); consent()
    expect(screen.getByText('Switch to your own account view to record.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start recording' })).toBeDisabled()
    expect(screen.getByLabelText('Choose audio file')).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Start recording' }))
    expect(mocks.start).not.toHaveBeenCalled()
    expect(mocks.request.mock.calls.some(([path]) => path === '/conversations')).toBe(false)
  })
})


describe('reload recovery lease identity', () => {
  it('reuses the same scoped tab identity and explicitly requests recovery after a remount', async () => {
    sessionStorage.clear()
    setup(); consent(); fireEvent.click(screen.getByRole('button', { name: 'Start recording' }))
    await screen.findByRole('button', { name: 'Stop & save' })
    const originalTab = JSON.parse(mocks.request.mock.calls.find(([path]) => path.endsWith('/lease'))![2].body).tab_id
    await act(async () => cleanup())
    mocks.recoveries.mockResolvedValue([{ key: 'alice/workspace-a/conversation-1', scope: 'alice/workspace-a', conversationId: 'conversation-1', partId: 'part-1', leaseToken: 'lease-1', totalSamples: 16000, chunkCount: 1, stopped: true, updatedAt: '2026-10-27' }])
    render(<ConversationRecorder customerName="Synthetic Customer" customerPhone="9876543210" initialConversationId="conversation-1" onConversationChange={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Recover audio' }))
    await screen.findByText('Audio saved in your workspace')
    const leases = mocks.request.mock.calls.filter(([path]) => path.endsWith('/lease'))
    expect(JSON.parse(leases.at(-1)![2].body)).toMatchObject({ tab_id: originalTab, takeover: true, purpose: 'recover' })
  })
})
