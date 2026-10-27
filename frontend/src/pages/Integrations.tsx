import { Languages, Mic } from 'lucide-react'
import { useOffice } from '../contexts/OfficeContext'
import { errorMessageForCode, type Connection, type EngineSettings, type Page } from '../services/officeApi'
import { OfficeNotice, OfficeStatus, useOfficeResource } from '../components/OfficeUi'
import PageLoader from '../components/PageLoader'

/** Provider credentials belong to the backend environment, never a browser form. */
export default function Integrations() {
  const { user, workspace } = useOffice()
  return user && workspace ? <ManagedProcessing key={`${user.id}:${workspace.id}`} /> : <OfficeNotice>Sign in and select a workspace to view processing status.</OfficeNotice>
}
function ManagedProcessing() {
  const connections = useOfficeResource<Page<Connection>>('/connections')
  const policy = useOfficeResource<EngineSettings>('/engine-settings')
  return <section className="office-processing" aria-label="Managed speech processing">
    <div className="office-row office-between"><div><h2>Speech processing</h2><p>Speech transcription and English translation are managed by your administrator.</p></div><button type="button" className="office-button office-secondary" disabled={connections.refreshing || policy.refreshing} onClick={() => { connections.reload(); policy.reload() }}>Refresh status</button></div>
    {(connections.error || policy.error) && <OfficeNotice error>{connections.error || policy.error}</OfficeNotice>}
    {connections.loading || policy.loading ? <PageLoader kind="connections" label="Loading processing status" compact /> : <div className="office-connections-grid">{(['elevenlabs', 'openai-translator'] as const).map(engine => {
      const connection = connections.data?.items.find(item => item.engine_id === engine)
      const Icon = engine === 'elevenlabs' ? Mic : Languages
      return <section className="office-panel" key={engine}><div className="office-row office-between"><Icon size={24} /><OfficeStatus value={connection?.state || 'unavailable'} /></div><h3>{engine === 'elevenlabs' ? 'Original speech · ElevenLabs' : 'English translation · OpenAI'}</h3><p>{engine === 'elevenlabs' ? 'Saved recordings are transcribed with source timing and detected speaker groups.' : 'Non-English source turns are translated while preserving source speech and speaker links.'}</p>{connection?.last_error && <OfficeNotice error>{errorMessageForCode(connection.last_error, 'The managed service needs attention. Contact your administrator.')}</OfficeNotice>}</section>
    })}</div>}
    <OfficeNotice>Every saved conversation is queued for source transcription and English translation. If a stage needs attention, its saved audio and source text remain available from Conversation history.</OfficeNotice>
  </section>
}
