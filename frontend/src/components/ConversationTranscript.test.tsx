import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { type Conversation, type Job, type Transcript } from '../services/officeApi'
import ConversationTranscript from './ConversationTranscript'

const calls = vi.hoisted(() => ({ request: vi.fn(), reload: vi.fn(), workspace: 'workspace-a', role: 'owner', jobs: [] as (Job & { window_id?: string | null })[], complete: false, conversation: {} as Partial<Conversation>, transcript: {} as Partial<Transcript>, playAt: vi.fn(), playerClosed: vi.fn() }))
vi.mock('./ProgressivePlayback', async () => {
  const { forwardRef, useImperativeHandle, useEffect } = await import('react')
  return { default: forwardRef(function Player(_props, ref) { useImperativeHandle(ref, () => ({ playAt: calls.playAt })); useEffect(() => () => calls.playerClosed(), []); return <div>source player</div> }) }
})
vi.mock('../services/officeApi', async original => ({ ...await original<typeof import('../services/officeApi')>(), request: calls.request }))
vi.mock('../contexts/OfficeContext', () => ({ useOffice: () => ({ user: { id: 'user-a' }, workspace: { id: calls.workspace, role: calls.role } }) }))
vi.mock('./OfficeUi', async original => ({ ...await original<typeof import('./OfficeUi')>(), useOfficeResource: (path: string) => ({
  loading: false, refreshing: false, error: '', reload: calls.reload,
  data: path.endsWith('/progress') ? { conversation: { id: 'conversation-a', capture_state: 'stopped', upload_state: 'saved', transcription_state: 'complete', english_state: calls.complete ? 'complete' : 'pending', speaker_state: 'complete', finalization_state: calls.complete ? 'complete' : 'blocked', duration_seconds: 10, total_samples: 160000, active_transcript_version_id: 'version-a', ...calls.conversation }, jobs: calls.jobs }
    : { version_id: 'version-a', run_id: 'run-a', kind: 'final', covered_seconds: 10, status: calls.complete ? 'complete' : 'pending', turns: [{ id: 'turn-a', speaker_id: 'voice-a', start_seconds: 1, end_seconds: 3, english_text: calls.complete ? 'Please send model SKU-42.' : null, source_text: 'मॉडल SKU-42 भेजें।', source_language: 'hi', is_uncertain: true }], speaker_assignments: [], ...calls.transcript },
}) }))
function page() { return <MemoryRouter><ConversationTranscript conversationId="conversation-a" /></MemoryRouter> }
beforeEach(() => {
  calls.playAt.mockReset(); calls.playerClosed.mockReset(); calls.request.mockReset(); calls.reload.mockClear(); calls.workspace = 'workspace-a'; calls.role = 'owner'; calls.jobs = []; calls.complete = false; calls.conversation = {}; calls.transcript = {}
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

  it('shows each speech failure explanation once while retaining distinct causes and every job', () => {
    calls.jobs = [
      { id: 'window-a', run_id: 'run-a', job_type: 'transcribe', status: 'blocked', error_code: 'storage_unavailable' },
      { id: 'window-b', run_id: 'run-a', job_type: 'transcribe', status: 'failed', error_code: 'storage_unavailable' },
      { id: 'final-pass', run_id: 'run-a', job_type: 'finalise', status: 'uncertain', error_code: 'storage_unavailable' },
      { id: 'other-failure', run_id: 'run-a', job_type: 'transcribe', status: 'blocked', error_code: 'provider_quota_exceeded' },
    ]
    render(page())
    const banner = within(screen.getByText('Speech transcription needs attention').parentElement!)
    expect(banner.getAllByText(/Recording storage is unavailable/)).toHaveLength(1)
    expect(banner.getByText(/check storage access and connectivity/)).toBeVisible()
    expect(banner.getByText(/reached its credit limit/)).toBeVisible()
    const recovery = within(screen.getByRole('heading', { name: 'Processing recovery' }).parentElement!)
    expect(recovery.getAllByRole('listitem')).toHaveLength(4)
    expect(calls.request).not.toHaveBeenCalled()
  })

  it('uses processing guidance for an unrecognized stage error', () => {
    calls.jobs = [{ id: 'unknown-failure', run_id: 'run-a', job_type: 'transcribe', status: 'failed', error_code: 'unknown_stage_failure' }]
    render(page())
    expect(screen.getByText(/Check processing, provider connection and usage limits/)).toBeVisible()
    expect(screen.queryByText(/Check the worker/)).not.toBeInTheDocument()
  })

  it('starts the actual source player only after intent and seeks source turns', async () => {
    const user = userEvent.setup(); render(page())
    expect(calls.playAt).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Load playback' }))
    expect(calls.playAt).toHaveBeenLastCalledWith(0)
    await user.click(screen.getByRole('button', { name: /Play source audio at/ }))
    expect(calls.playAt).toHaveBeenLastCalledWith(1)
    expect(calls.request).not.toHaveBeenCalled()
  })
  it('disposes the previous source player after workspace change', () => {
    const view = render(page()); calls.workspace = 'workspace-b'; view.rerender(page())
    expect(calls.playerClosed).toHaveBeenCalledOnce()
  })

  it('keeps old comparison failures out of a completed active transcript banner', () => {
    calls.complete = true
    calls.jobs = [{ id: 'comparison-failure', run_id: 'old-run', job_type: 'translate', status: 'blocked', error_code: 'invalid_credentials' }]
    render(page())
    expect(screen.getByText('Please send model SKU-42.')).toBeVisible()
    expect(screen.queryByText('English translation needs attention')).not.toBeInTheDocument()
  })

  it('clears superseded progressive translation warnings only after the active final English covers the recording', () => {
    calls.complete = true
    calls.jobs = [
      { id: 'old-window', run_id: 'run-a', window_id: 'window-a', job_type: 'translate', status: 'blocked', error_code: 'translator_unconfigured' },
      { id: 'finished-final', run_id: 'run-a', window_id: null, job_type: 'translate', status: 'succeeded' },
    ]
    render(page())
    expect(screen.getByText('Please send model SKU-42.')).toBeVisible()
    expect(screen.queryByText('English translation needs attention')).not.toBeInTheDocument()
    const recovery = within(screen.getByRole('heading', { name: 'Processing recovery' }).parentElement!)
    expect(recovery.getAllByRole('listitem')).toHaveLength(2)
    expect(recovery.getByText(/managed English translation service is unavailable/)).toBeVisible()
    expect(calls.request).not.toHaveBeenCalled()
  })

  it.each([
    ['partial coverage', {}, { covered_seconds: 9 }],
    ['missing coverage', {}, { covered_seconds: undefined }],
    ['progressive version', {}, { kind: 'progressive' }],
    ['different selected version', { active_transcript_version_id: 'version-b' }, {}],
    ['missing selected version', { active_transcript_version_id: undefined }, {}],
    ['unfinished final stage', { finalization_state: 'pending' }, {}],
    ['partial English stage', { english_state: 'partial' }, {}],
    ['unfinished English version', {}, { status: 'pending' }],
    ['missing active run', {}, { run_id: undefined }],
  ] as [string, Partial<Conversation>, Partial<Transcript>][])('retains translation warnings with %s', (_name, conversation, transcript) => {
    calls.complete = true
    calls.conversation = conversation; calls.transcript = transcript
    calls.jobs = [{ id: 'old-window', run_id: 'run-a', window_id: 'window-a', job_type: 'translate', status: 'blocked', error_code: 'translator_unconfigured' }]
    render(page())
    expect(screen.getByText('English translation needs attention')).toBeVisible()
  })

  it.each([null, undefined])('retains a failed final or unclassified translation job after completion (window %s)', window_id => {
    calls.complete = true
    calls.jobs = [{ id: 'final-failure', run_id: 'run-a', window_id, job_type: 'translate', status: 'failed', error_code: 'translator_unconfigured' }]
    render(page())
    expect(screen.getByText('English translation needs attention')).toBeVisible()
  })

  it('retains uncertain progressive provider warnings and explicit paid retry protections after final completion', async () => {
    const user = userEvent.setup()
    calls.complete = true
    calls.jobs = [{ id: 'uncertain-window', run_id: 'run-a', window_id: 'window-a', job_type: 'translate', status: 'uncertain', error_code: 'external_translation_uncertain' }]
    calls.request.mockResolvedValue({ resumed_job_ids: [], uncertain_job_ids: ['uncertain-window'] })
    render(page())
    expect(screen.getByText('English translation needs attention')).toBeVisible()
    await user.click(screen.getAllByRole('button', { name: 'Retry English translation' })[0])
    expect(screen.getByText(/Submissions with an uncertain outcome require administrator reconciliation/)).toBeVisible()
    expect(calls.request).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Confirm retry and provider usage' }))
    expect(await screen.findByText(/They were not resubmitted/)).toBeVisible()
    expect(calls.request).toHaveBeenCalledOnce()
    expect(calls.request.mock.calls[0][0]).toBe('/conversations/conversation-a/retry')
    expect(JSON.parse(calls.request.mock.calls[0][2].body)).toEqual({ stage: 'translation' })
  })

  it('does not expose a new paid speech replay control to a salesperson', () => {
    calls.role = 'salesperson'
    render(page())
    expect(screen.queryByRole('button', { name: 'Create another speech pass' })).not.toBeInTheDocument()
  })
})
