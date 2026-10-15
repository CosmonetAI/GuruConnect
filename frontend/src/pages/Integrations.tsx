import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Languages, Mic } from 'lucide-react'
import { useOffice } from '../contexts/OfficeContext'
import { ApiError, errorMessageForCode, json, message, request, type Connection, type EngineSettings, type Page } from '../services/officeApi'
import { OfficeDialog, OfficeNotice, OfficeStatus, useOfficeResource } from '../components/OfficeUi'

type Engine = 'elevenlabs' | 'openai-translator'
type Configure = { engine: Engine; id?: string; generation?: number; idempotencyKey: string }
const names: Record<Engine, string> = { elevenlabs: 'ElevenLabs Scribe v2', 'openai-translator': 'English translation · OpenAI' }
const engines: Engine[] = ['elevenlabs', 'openai-translator']

export default function Integrations() {
  const { user, workspace } = useOffice()
  if (!user || !workspace) return <OfficeNotice>Sign in and select a workspace to view its connections.</OfficeNotice>
  return <ScopedIntegrations key={`${user.id}:${workspace.id}`} />
}

function ScopedIntegrations() {
  const { workspace } = useOffice()
  const connections = useOfficeResource<Page<Connection>>('/connections')
  const policy = useOfficeResource<EngineSettings>('/engine-settings')
  const [configure, setConfigure] = useState<Configure | null>(null)
  const [functional, setFunctional] = useState<Connection | null>(null)
  const [disconnect, setDisconnect] = useState<Connection | null>(null)
  const [finishInFlight, setFinishInFlight] = useState(false)
  const [stale, setStale] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const alive = useRef(true)
  const submitting = useRef(false)
  const controllers = useRef(new Set<AbortController>())
  const administrator = workspace?.role === 'owner'
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; controllers.current.forEach(controller => controller.abort()) }
  }, [])
  function reload() { connections.reload(); policy.reload() }
  async function withMutation(action: (signal: AbortSignal) => Promise<void>) {
    if (submitting.current || !workspace || !administrator) return
    submitting.current = true
    setBusy(true); setError(''); setNotice('')
    const controller = new AbortController()
    controllers.current.add(controller)
    try { await action(controller.signal) }
    catch (failure) { if (alive.current && !controller.signal.aborted) setError(message(failure)) }
    finally { submitting.current = false; controllers.current.delete(controller); if (alive.current) setBusy(false) }
  }
  function openConfiguration(engine: Engine, connection?: Connection) {
    setError(''); setNotice(''); setStale(false)
    setConfigure({ engine, id: connection?.id || undefined, generation: connection?.id ? connection.generation : undefined, idempotencyKey: crypto.randomUUID() })
  }
  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!configure || stale) return
    const form = event.currentTarget
    const input = form.elements.namedItem('api_key') as HTMLInputElement
    const credentials = { api_key: input.value.trim() }
    const selected = configure
    void withMutation(async signal => {
      try {
        await request('/connections', workspace!.id, {
          ...json('POST', { engine_id: selected.engine, credentials, config: selected.engine === 'openai-translator' ? { model: 'gpt-5.6-luna' } : {}, idempotency_key: selected.idempotencyKey, ...(selected.generation != null ? { expected_generation: selected.generation } : {}) }), signal,
        })
        input.value = ''
        if (!alive.current) return
        setConfigure(null); setStale(false); reload()
        setNotice('Configuration saved securely. Test this generation before new processing. OpenAI also needs its explicit functional smoke test.')
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 409 && failure.code === 'stale_connection_generation') {
          if (alive.current) { setStale(true); connections.reload() }
          // Keep the secret only in this active password input while the user reviews a stale generation.
        } else {
          input.value = ''
          if (alive.current) setConfigure(value => value ? { ...value, idempotencyKey: crypto.randomUUID() } : null)
        }
        throw failure
      }
    })
  }
  function reviewLatestConfiguration() {
    if (!configure) return
    const engine = configure.engine
    void withMutation(async signal => {
      const latest = await request<Page<Connection>>('/connections', workspace!.id, { signal })
      if (!alive.current) return
      const current = latest.items.find(item => item.engine_id === engine && item.id)
      setConfigure(value => value ? { ...value, id: current?.id, generation: current?.generation, idempotencyKey: crypto.randomUUID() } : null)
      setStale(false); connections.reload()
      setNotice(`Latest connection state reviewed${current ? ` (generation ${current.generation})` : ''}. Save again only if you intend to replace it.`)
    })
  }
  function action(connection: Connection, kind: 'test' | 'disable' | 'enable' | 'disconnect', body?: unknown) {
    void withMutation(async signal => {
      try {
        const result = await request<{ passed?: boolean; state?: string; functional?: boolean; last_error_code?: string }>(`/connections/${connection.id}/${kind}`, workspace!.id, { ...json('POST', body), signal })
        if (!alive.current) return
        reload()
        if (kind === 'test' && result.passed !== true) throw new Error(errorMessageForCode(result.last_error_code || 'connection_test_failed', 'This test did not establish readiness. Check permissions, credits and the saved connection, then retry.'))
        setDisconnect(null); setFunctional(null)
        setNotice(kind === 'test'
          ? connection.engine_id === 'openai-translator'
            ? result.functional ? 'OpenAI text translation executed successfully. This connection generation is ready for English processing.' : result.state === 'connected' ? 'OpenAI model access verified. This generation retains its previously verified functional readiness.' : 'OpenAI model access verified. Run the functional smoke test to verify text translation before processing.'
            : result.functional ? 'The explicit ElevenLabs sample executed successfully. Review recording quality separately.' : 'ElevenLabs connection check passed. Speech-to-text permission and credits are also needed for recordings.'
          : kind === 'disconnect' ? 'Connection disconnected. Saved audio and transcript history are preserved.' : `Connection ${kind === 'enable' ? 'enabled' : 'disabled'}.`)
      } catch (failure) { if (alive.current) reload(); throw failure }
    })
  }
  function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    void withMutation(async signal => {
      await request('/engine-settings', workspace!.id, { ...json('PATCH', {
        default_engine: String(form.get('default')) || null,
        allowed_engines: ['elevenlabs'], allow_salesperson_engine_selection: false, allow_comparison: false,
        budget_minutes: Number(form.get('budget')),
      }), signal })
      if (!alive.current) return
      policy.reload(); setNotice('Workspace policy saved for future recordings. Existing blocked recordings need an explicit retry.')
    })
  }

  return <div className="office-processing">
    <div className="office-row office-between"><div><h1 className="page-title">Connections & processing</h1><p>GuruConnect uses its own API and background worker. Provider keys belong to this workspace.</p></div><button type="button" className="office-button office-secondary" onClick={reload} disabled={connections.refreshing || policy.refreshing}>Refresh connections</button></div>
    {!administrator && <OfficeNotice>Only the workspace owner can configure, test or disconnect providers.</OfficeNotice>}
    {(connections.error || policy.error || (!configure && !functional && !disconnect && error)) && <OfficeNotice error>{connections.error || policy.error || error}</OfficeNotice>}
    {notice && <OfficeNotice>{notice}</OfficeNotice>}
    {connections.loading ? <p role="status">Loading workspace connections…</p> : <div className="office-connections-grid">{engines.map(engine => {
      const connection = connections.data?.items.find(item => item.engine_id === engine && item.id)
      const Icon = engine === 'elevenlabs' ? Mic : Languages
      return <section className="office-panel" key={engine} aria-label={names[engine]}>
        <div className="office-row office-between"><Icon size={26} aria-hidden="true" /><OfficeStatus value={connection?.state || 'disconnected'} /></div>
        <h2 style={{ marginTop: 16 }}>{names[engine]}</h2>
        <p>{engine === 'elevenlabs' ? 'Transcribes original speech with word timing and speaker groups. Both microphone recordings and uploaded audio use this path.' : 'Translates retained source turns into English using GPT-5.6 Luna. Original text, source timing and speaker associations stay linked.'}</p>
        {engine === 'openai-translator' && <p className="office-small">OpenAI token usage is billed separately. Test connection checks model access; the functional test verifies translation execution.</p>}
        <dl className="office-connection-meta"><div><dt>Readiness check</dt><dd>{connection?.last_test_at ? new Date(connection.last_test_at).toLocaleString() : 'Not verified'}</dd></div>{connection && <div><dt>Generation</dt><dd>{connection.generation}</dd></div>}</dl>
        {connection?.last_error && <OfficeNotice error>{errorMessageForCode(connection.last_error, 'This connection needs attention. Check its settings and test again.')}</OfficeNotice>}
        {administrator && <div className="office-row" style={{ marginTop: 18 }}><button type="button" className="office-button office-secondary" disabled={busy} onClick={() => openConfiguration(engine, connection)}>{connection ? 'Configure / rotate' : 'Connect'}</button>{connection && <><button type="button" className="office-button office-secondary" disabled={busy || connection.state === 'disconnected' || connection.state === 'checking'} onClick={() => action(connection, 'test', { functional: false })}>Test connection</button><button type="button" className="office-button office-secondary" disabled={busy || connection.state === 'disconnected' || connection.state === 'checking'} onClick={() => { setError(''); setFunctional(connection) }}>Functional smoke test</button><button type="button" className="office-button office-secondary" disabled={busy || ['disconnected', 'checking'].includes(connection.state)} onClick={() => action(connection, connection.state === 'disabled' ? 'enable' : 'disable')}>{connection.state === 'disabled' ? 'Enable' : 'Disable'}</button><button type="button" className="office-button office-danger" disabled={busy || connection.state === 'disconnected'} onClick={() => { setError(''); setFinishInFlight(false); setDisconnect(connection) }}>Disconnect</button></>}</div>}
      </section>
    })}</div>}
    <section className="office-panel"><h2>New-recording policy</h2><p>Enable ElevenLabs as the default after its connection check. OpenAI must pass the functional test before English translation can run.</p>{policy.loading ? <p role="status">Loading processing policy…</p> : policy.data && <form key={JSON.stringify(policy.data)} onSubmit={savePolicy}><label className="office-field">Default speech engine<select name="default" defaultValue={policy.data.default_engine_id || ''} disabled={!administrator || busy}><option value="">Save audio without an active speech engine</option>{connections.data?.items.some(item => item.engine_id === 'elevenlabs' && item.state === 'connected') && <option value="elevenlabs">ElevenLabs Scribe v2</option>}{policy.data.default_engine_id && policy.data.default_engine_id !== 'elevenlabs' && <option value={policy.data.default_engine_id} disabled>{policy.data.default_engine_id} · previous configuration</option>}</select></label><label className="office-field">Daily speech-processing budget (minutes)<input name="budget" type="number" required min={0} max={1000000} step={1} defaultValue={Math.floor(Number(policy.data.budget_policy?.max_audio_seconds_per_day ?? 600000) / 60)} disabled={!administrator || busy} /><span className="office-small">Includes progressive, final and retry speech passes. Provider credits and OpenAI token limits apply separately.</span></label>{administrator && <button className="office-button" disabled={busy}>Save new-recording policy</button>}</form>}</section>
    <OfficeNotice>Keep the GuruConnect API and background worker running. Provider readiness alone does not process a queued recording. To resume earlier blocked English jobs, open the enquiry and choose “Retry English translation”.</OfficeNotice>
    {configure && <OfficeDialog title={`Configure ${names[configure.engine]}`} busy={busy} close={() => { setConfigure(null); setError(''); setStale(false) }}>
      <p>Secrets are encrypted on the server and never returned to this browser. Enter a complete new key to configure or rotate this connection.</p>
      {configure.id && <p className="office-small">Rotation pauses dependent queued work and starts a new connection generation. Accepted external work may remain billable. Review in-flight jobs before rotating.</p>}
      {error && <OfficeNotice error>{error}</OfficeNotice>}
      {stale && <OfficeNotice>This connection changed since you opened the form. Review the latest generation before replacing it.<div style={{ marginTop: 10 }}><button type="button" className="office-button office-secondary" disabled={busy} onClick={reviewLatestConfiguration}>Review latest connection state</button></div></OfficeNotice>}
      <p className="office-small">{configure.engine === 'elevenlabs' ? 'Use a restricted ElevenLabs key with Speech to Text access and User Read permission for its metadata connection check.' : 'Use an OpenAI project API key with Responses access, API billing and access to the selected translation model. Translation sends text only.'}</p>
      <form onSubmit={save}><label className="office-field">API key<input name="api_key" type="password" required minLength={8} maxLength={500} autoComplete="new-password" autoCapitalize="none" spellCheck={false} /></label>{configure.engine === 'openai-translator' && <label className="office-field">Translation model<input aria-label="Translation model" value="gpt-5.6-luna" readOnly /></label>}<button className="office-button" disabled={busy || stale}>{busy ? 'Saving…' : 'Save secure configuration'}</button></form>
    </OfficeDialog>}
    {functional && <OfficeDialog title="Confirm functional smoke test" busy={busy} close={() => { setFunctional(null); setError('') }}>{error && <OfficeNotice error>{error}</OfficeNotice>}<p>{functional.engine_id === 'openai-translator' ? 'Translate one short built-in sentence using the configured OpenAI model. This verifies actual text execution, not accuracy on your customer conversations.' : 'Send one short built-in audio sample to ElevenLabs. This verifies actual speech processing, not accuracy on your customer conversations.'}</p><p>This test incurs provider usage and may be billed. No customer recording is used.</p><button type="button" className="office-button" disabled={busy} onClick={() => action(functional, 'test', { functional: true })}>{busy ? 'Testing…' : 'Confirm provider usage and run test'}</button></OfficeDialog>}
    {disconnect && <OfficeDialog title={`Disconnect ${names[disconnect.engine_id as Engine]}?`} busy={busy} close={() => { setDisconnect(null); setError('') }}>{error && <OfficeNotice error>{error}</OfficeNotice>}<p>New submissions stop and dependent queued jobs pause. Saved recordings, source transcripts and history remain available.</p><OfficeNotice>{disconnect.queued_jobs ?? 'Unknown'} queued jobs · {disconnect.inflight_jobs ?? 'Unknown'} in-flight jobs. Accepted provider work may still finish and remain billable.</OfficeNotice><label className="office-checkbox"><input type="checkbox" checked={finishInFlight} onChange={event => setFinishInFlight(event.target.checked)} />Allow already accepted jobs to finish under this explicit policy</label><button type="button" className="office-button office-danger" disabled={busy} onClick={() => action(disconnect, 'disconnect', { finish_in_flight: finishInFlight })}>Confirm disconnect</button></OfficeDialog>}
  </div>
}
