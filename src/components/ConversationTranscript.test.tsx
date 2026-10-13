import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, type Job } from '../services/officeApi'
import ConversationTranscript from './ConversationTranscript'

const calls = vi.hoisted(() => ({ request: vi.fn(), reload: vi.fn(), workspace: 'workspace-a', role: 'owner', jobs: [] as Job[], complete: false }))
vi.mock('../services/officeApi', async original => ({ ...await original<typeof import('../services/officeApi')>(), request: calls.request }))
vi.mock('../contexts/OfficeContext', () => ({ useOffice: () => ({ user: { id: 'user-a' }, workspace: { id: calls.workspace, role: calls.role } }) }))
vi.mock('./OfficeUi', async original => ({ ...await original<typeof import('./OfficeUi')>(), useOfficeResource: (path: string) => ({
  loading: false, refreshing: false, error: '', reload: calls.reload,
  data: path.endsWith('/progress') ? { conversation: { id: 'conversation-a', capture_state: 'stopped', upload_state: 'saved', transcription_state: 'complete', english_state: calls.complete ? 'complete' : 'pending', speaker_state: 'complete', finalization_state: calls.complete ? 'complete' : 'blocked', duration_seconds: 10 }, jobs: calls.jobs }
    : { version_id: 'version-a', run_id: 'run-a', status: calls.complete ? 'complete' : 'pending', turns: [{ id: 'turn-a', speaker_id: 'voice-a', start_seconds: 1, end_seconds: 3, english_text: calls.complete ? 'Please send model SKU-42.' : null, source_text: 'मॉडल SKU-42 भेजें।', source_language: 'hi', is_uncertain: true }], speaker_assignments: [] },
}) }))
function page() { return <MemoryRouter><ConversationTranscript conversationId="conversation-a" /></MemoryRouter> }
beforeEach(() => {
  calls.request.mockReset(); calls.reload.mockClear(); calls.workspace = 'workspace-a'; calls.role = 'owner'; calls.jobs = []; calls.complete = false
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('actual source and English processing results', () => {
  it('preserves pending English and source text without inventing a translation or identity', async () => {
    const user = userEvent.setup()
    render(page())
    expect(screen.getByText('English unavailable for this turn. Source text is preserved.')).toBeVisible()
    expect(screen.getByText('Detected voice 1')).toBeVisible()
    expect(screen.getByText('Provisional')).toBeVisible()
    expect(screen.queryByText('मॉडल SKU-42 भेजें।')).not.toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'Show original' }))
    expect(screen.getByText('मॉडल SKU-42 भेजें।')).toBeVisible()
    expect(screen.getByText('Speaker attribution needs review.')).toBeVisible()
    expect(calls.request).not.toHaveBeenCalled()
  })

  it('confirms provider usage before retrying only retained source translation', async () => {
    const user = userEvent.setup()
    calls.jobs = [{ id: 'job-a', run_id: 'run-a', job_type: 'translate', status: 'blocked', error_code: 'translator_unconfigured' }]
    calls.request.mockResolvedValue({ resumed_job_ids: ['job-a'], uncertain_job_ids: [] })
    render(page())
    await user.click(screen.getAllByRole('button', { name: 'Retry English translation' })[0])
    const dialog = within(screen.getByRole('dialog', { name: 'Retry English translation?' }))
    expect(dialog.getByText(/Speech audio is not submitted again/)).toBeVisible()
    expect(calls.request).not.toHaveBeenCalled()
    await user.dblClick(dialog.getByRole('button', { name: 'Confirm retry and provider usage' }))
    expect(calls.request).toHaveBeenCalledTimes(1)
    const [path, workspace, options] = calls.request.mock.calls[0]
    expect(path).toBe('/conversations/conversation-a/retry'); expect(workspace).toBe('workspace-a')
    expect(JSON.parse(options.body)).toEqual({ stage: 'translation' })
    expect(await screen.findByText(/1 eligible processing job/)).toBeVisible()
    expect(calls.request.mock.calls.some(([path]) => String(path).endsWith('/runs'))).toBe(false)
  })

  it('does not claim a resumed job when provider submission outcomes are uncertain', async () => {
    const user = userEvent.setup()
    calls.request.mockResolvedValue({ resumed_job_ids: [], uncertain_job_ids: ['uncertain-a'] })
    render(page())
    await user.click(screen.getByRole('button', { name: 'Retry English translation' }))
    await user.click(screen.getByRole('button', { name: 'Confirm retry and provider usage' }))
    expect(await screen.findByText(/They were not resubmitted/)).toBeVisible()
    expect(screen.queryByText(/eligible processing job\(s\) resumed/)).not.toBeInTheDocument()
  })

  it('requests playback only after intent and treats pending assembly honestly', async () => {
    const user = userEvent.setup()
    calls.request.mockRejectedValueOnce(new ApiError(409, 'playback_assembly_pending', 'Playback pending'))
    render(page())
    expect(calls.request).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Load playback' }))
    expect(await screen.findByText(/Playback assembly is pending/)).toBeVisible()
    expect(screen.queryByLabelText('Conversation source audio')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('aborts and discards a late signed playback response after workspace change', async () => {
    const user = userEvent.setup()
    let finish: (value: unknown) => void = () => {}
    calls.request.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const view = render(page())
    await user.click(screen.getByRole('button', { name: 'Load playback' }))
    const signal = calls.request.mock.calls[0][2].signal as AbortSignal
    calls.workspace = 'workspace-b'; view.rerender(page())
    expect(signal.aborted).toBe(true)
    await act(async () => finish({ url: 'https://storage.example.test/workspace-a.wav', expires_in: 60 }))
    expect(screen.queryByLabelText('Conversation source audio')).not.toBeInTheDocument()
  })

  it('keeps old comparison failures out of a completed active transcript banner', () => {
    calls.complete = true
    calls.jobs = [{ id: 'comparison-failure', run_id: 'old-run', job_type: 'translate', status: 'blocked', error_code: 'invalid_credentials' }]
    render(page())
    expect(screen.getByText('Please send model SKU-42.')).toBeVisible()
    expect(screen.queryByText('English translation needs attention')).not.toBeInTheDocument()
  })

  it('does not expose a new paid speech replay control to a salesperson', () => {
    calls.role = 'salesperson'
    render(page())
    expect(screen.queryByRole('button', { name: 'Create another speech pass' })).not.toBeInTheDocument()
  })
})
