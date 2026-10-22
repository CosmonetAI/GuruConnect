import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import PageLoader from '../../components/PageLoader'
import { ArrowLeft, CalendarDays, Check, Clock3, FileText, UserRound, UsersRound } from 'lucide-react'
import {
  assignWalkin,
  canManageWalkinAgents,
  emptyWalkinRequirement,
  getAllowedWalkinTransitions,
  getWalkinRequirements,
  getWalkinVisit,
  isWalkinDemoMode,
  listWalkinAgents,
  listWalkinHistory,
  saveWalkinRequirements,
  updateWalkinAgentAvailability,
  updateWalkinStatus,
  walkinRecordingsPath,
} from '../../services/walkinService'
import type { WalkinAgent, WalkinHistoryEvent, WalkinRequirement, WalkinStatus, WalkinVisit } from '../../types/walkin'
import WalkinRecordingPanel from './WalkinRecordingPanel'
import './Walkins.css'
import { useOffice } from '../../contexts/OfficeContext'
import { request, type Page, type Conversation } from '../../services/officeApi'

export default function WalkinDetail() {
  const { workspace, user, captureBusy } = useOffice()
  const { id = '' } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const confirmation = (location.state as { confirmation?: string } | null)?.confirmation
  const [visit, setVisit] = useState<WalkinVisit | null>(null)
  const [agents, setAgents] = useState<WalkinAgent[]>([])
  const [canManageAgents, setCanManageAgents] = useState(false)
  const [history, setHistory] = useState<WalkinHistoryEvent[]>([])
  const [requirements, setRequirements] = useState<WalkinRequirement>(emptyWalkinRequirement())
  const [selectedAgent, setSelectedAgent] = useState('')
  const [assignmentNote, setAssignmentNote] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState(confirmation ?? '')

  const refresh = useCallback(async () => {
    if (!workspace) { setVisit(null); setLoading(false); return }
    setLoading(true)
    setError('')
    try {
      const [loadedVisit, loadedAgents, loadedHistory, loadedRequirements, manageAgents] = await Promise.all([
        getWalkinVisit(workspace.id, id),
        listWalkinAgents(workspace.id),
        listWalkinHistory(workspace.id, id),
        getWalkinRequirements(workspace.id, id),
        canManageWalkinAgents(workspace.id),
      ])
      setVisit(loadedVisit)
      setAgents(loadedAgents)
      setHistory(loadedHistory)
      setRequirements(loadedRequirements)
      setCanManageAgents(manageAgents)
      setSelectedAgent(loadedVisit?.assignedAgentId ?? '')
      setAssignmentNote(loadedVisit?.assignmentNote ?? '')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Visit details could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [id, workspace?.id])

  useEffect(() => { void refresh() }, [refresh])

  const allowedTransitions = useMemo(() => visit ? getAllowedWalkinTransitions(visit.status) : [], [visit])
  const closed = visit && ['Completed', 'Cancelled', 'Converted to Enquiry'].includes(visit.status)
  const canConvert = isWalkinDemoMode() || visit?.assignedAgentId === user?.id
  const updateRequirement = <K extends keyof WalkinRequirement>(field: K, value: WalkinRequirement[K]) => {
    setRequirements((previous) => ({ ...previous, [field]: value }))
    setMessage('')
  }

  const saveAssignment = async () => {
    if (!visit || !workspace) return
    if (visit.assignedAgentId && visit.assignedAgentId !== selectedAgent && !window.confirm('Reassign this visitor to a different sales agent?')) return
    setSaving(true)
    setError('')
    setMessage('')
    try {
      await assignWalkin(workspace.id, visit.id, selectedAgent || null, assignmentNote.trim(), visit.updatedAt)
      setMessage(selectedAgent ? 'Sales assignment saved.' : 'Visitor returned to the unassigned queue.')
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Assignment could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  const changeStatus = async (status: WalkinStatus) => {
    if (!visit || !workspace) return
    if (status === 'Cancelled' && !window.confirm('Cancel this visit? This action will be recorded in visit history.')) return
    setSaving(true)
    setError('')
    setMessage('')
    try {
      await updateWalkinStatus(workspace.id, visit.id, status, visit.updatedAt)
      setMessage(`Visit status updated to ${status}.`)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Visit status could not be updated.')
    } finally {
      setSaving(false)
    }
  }

  const saveRequirements = async () => {
    if (!visit || !workspace) return
    setSaving(true)
    setError('')
    setMessage('')
    try {
      const saved = await saveWalkinRequirements(workspace.id, visit.id, requirements, visit.updatedAt)
      setRequirements(saved)
      setMessage('Requirement draft saved.')
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Requirements could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  const changeAvailability = async (agent: WalkinAgent, availability: WalkinAgent['availability']) => {
    if (!workspace) return
    setError('')
    try {
      await updateWalkinAgentAvailability(workspace.id, agent.id, availability)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Agent availability could not be updated.')
    }
  }

  const startConversion = async () => {
    if (!visit || captureBusy || !workspace) return
    if (!visit.assignedAgentId) {
      setError('Assign this visit to a sales agent before creating an enquiry.')
      return
    }
    let conversationId: string | undefined
    try { const recordings = await request<Page<Conversation>>(walkinRecordingsPath(visit.id), workspace.id); conversationId = recordings.items.find(item => (!isWalkinDemoMode() || item.title === `Walk-in visit ${visit.id}`) && item.upload_state === 'saved')?.id }
    catch { setError('The recording service could not be checked. Refresh before converting this visit.'); return }
    navigate('/enquiries/new', {
      state: {
        walkinConversion: {
          visitId: visit.id,
          conversationId,
          visitNumber: visit.visitNumber,
          customerName: visit.customerName,
          phone: visit.mobileNumber,
          email: visit.email,
          company: visit.companyName,
          assignedExecutive: visit.assignedAgentName ?? '',
          purpose: visit.purpose,
          productOrService: requirements.productOrService || visit.productOrService,
          quantity: requirements.quantity,
          budget: requirements.budgetRange,
          timeline: requirements.expectedPurchaseTimeline,
          requirements: requirements.detailedRequirement,
          notes: [visit.internalNotes, requirements.agentNotes].filter(Boolean).join('\n'),
          followUpDate: requirements.followUpAt,
        },
      },
    })
  }

  if (loading) return <div className="walkin-page"><div className="walkin-empty"><PageLoader kind="walkins" label="Loading visitor details" detail="Preparing the customer visit record." /></div></div>
  if (!visit) return <div className="walkin-page"><div className="walkin-alert error">{error || 'Walk-in visit not found.'}</div><Link className="walkin-button secondary" to="/walkins"><ArrowLeft size={16} /> Back to queue</Link></div>

  return (
    <div className="walkin-page">
      <div className="walkin-back-row"><Link to="/walkins"><ArrowLeft size={16} /> Reception queue</Link><Link to="/walkins/history">Visit history</Link></div>
      <header className="walkin-header">
        <div>
          <div className="walkin-kicker"><UsersRound size={15} /> VISIT {visit.visitNumber}</div>
          <h1>{visit.customerName}</h1>
          <p>{visit.mobileNumber}{visit.companyName ? ` · ${visit.companyName}` : ''}</p>
        </div>
        <span className={`walkin-status large status-${visit.status.toLowerCase().replaceAll(' ', '-')}`}>{visit.status}</span>
      </header>
      {message && <div className="walkin-alert success" role="status">{message}</div>}
      {error && <div className="walkin-alert error" role="alert">{error}</div>}

      <div className="walkin-detail-grid">
        <main className="walkin-detail-main">
          <section className="walkin-card">
            <div className="walkin-card-heading"><div><h2>Customer & visit</h2><p>Registered {new Date(visit.checkInAt).toLocaleString()}</p></div></div>
            <dl className="walkin-info-grid">
              <Info label="Email" value={visit.email} />
              <Info label="Purpose" value={visit.purpose} />
              <Info label="Product / service" value={visit.productOrService} />
              <Info label="Department" value={visit.department} />
              <Info label="Requested employee" value={visit.requestedEmployee} />
              <Info label="Appointment" value={visit.appointmentStatus} />
              <Info label="Appointment reference" value={visit.appointmentReference} />
              <Info label="Reception notes" value={visit.internalNotes} wide />
              <Info label="Visit description" value={visit.purposeDescription} wide />
            </dl>
          </section>

          <section className="walkin-card">
            <div className="walkin-card-heading"><div><h2>Capture customer requirements</h2><p>Save as a draft and update as the conversation develops.</p></div><span className="walkin-card-icon"><FileText size={18} /></span></div>
            <div className="walkin-form-grid">
              <Field label="Detailed customer requirement" name="detailedRequirement" wide><textarea id="detailedRequirement" rows={4} value={requirements.detailedRequirement} onChange={(event) => updateRequirement('detailedRequirement', event.target.value)} disabled={captureBusy || !!closed} /></Field>
              <Field label="Product or service required" name="requirementProduct"><input id="requirementProduct" value={requirements.productOrService} onChange={(event) => updateRequirement('productOrService', event.target.value)} disabled={captureBusy || !!closed} /></Field>
              <Field label="Quantity / estimated volume" name="quantity"><input id="quantity" value={requirements.quantity} onChange={(event) => updateRequirement('quantity', event.target.value)} disabled={captureBusy || !!closed} placeholder="As shared by customer" /></Field>
              <Field label="Budget range (optional)" name="budgetRange"><input id="budgetRange" value={requirements.budgetRange} onChange={(event) => updateRequirement('budgetRange', event.target.value)} disabled={captureBusy || !!closed} placeholder="If customer is willing to share" /></Field>
              <Field label="Expected purchase timeline" name="expectedPurchaseTimeline"><input id="expectedPurchaseTimeline" value={requirements.expectedPurchaseTimeline} onChange={(event) => updateRequirement('expectedPurchaseTimeline', event.target.value)} disabled={captureBusy || !!closed} placeholder="As shared by customer" /></Field>
              <Field label="Preferred solution" name="preferredSolution"><input id="preferredSolution" value={requirements.preferredSolution} onChange={(event) => updateRequirement('preferredSolution', event.target.value)} disabled={captureBusy || !!closed} /></Field>
              <Field label="Competitor / alternative" name="competitorOrAlternative"><input id="competitorOrAlternative" value={requirements.competitorOrAlternative} onChange={(event) => updateRequirement('competitorOrAlternative', event.target.value)} disabled={captureBusy || !!closed} /></Field>
              <Field label="Follow-up date" name="followUpAt"><input id="followUpAt" type="date" value={requirements.followUpAt} onChange={(event) => updateRequirement('followUpAt', event.target.value)} disabled={captureBusy || !!closed} /></Field>
              <Field label="Special requests or constraints" name="specialRequests" wide><textarea id="specialRequests" rows={2} value={requirements.specialRequests} onChange={(event) => updateRequirement('specialRequests', event.target.value)} disabled={captureBusy || !!closed} /></Field>
              <Field label="Next action" name="nextAction"><input id="nextAction" value={requirements.nextAction} onChange={(event) => updateRequirement('nextAction', event.target.value)} disabled={captureBusy || !!closed} placeholder="e.g. arrange product demo" /></Field>
              <Field label="Agent internal notes" name="agentNotes"><input id="agentNotes" value={requirements.agentNotes} onChange={(event) => updateRequirement('agentNotes', event.target.value)} disabled={captureBusy || !!closed} /></Field>
            </div>
            <div className="walkin-form-actions">
              <span className="walkin-field-hint">{requirements.updatedAt ? `Last saved ${new Date(requirements.updatedAt).toLocaleString()}` : 'Not saved yet'}</span>
              <button className="walkin-button primary" type="button" onClick={() => void saveRequirements()} disabled={captureBusy || saving || !!closed}><Check size={16} /> Save requirement draft</button>
            </div>
          </section>

          <WalkinRecordingPanel
            key={visit.id}
            visitId={visit.id}
            customerName={visit.customerName}
            customerPhone={visit.mobileNumber}
            customerEmail={visit.email}
            disabled={!!closed}
            canRecord={canConvert}
          />

          <section className="walkin-card">
            <div className="walkin-card-heading"><div><h2>Visit history</h2><p>Timestamped changes and handoffs.</p></div></div>
            <div className="walkin-timeline">{history.length ? history.map((event) => (
              <article className="walkin-timeline-item" key={event.id}>
                <span className="timeline-dot" /><div><strong>{event.eventType}</strong><p>{event.oldValue && <>{event.oldValue} → </>}{event.newValue}{event.notes ? ` · ${event.notes}` : ''}</p><small>{new Date(event.createdAt).toLocaleString()} · {event.performedBy || 'System'}</small></div>
              </article>
            )) : <div className="walkin-empty">No visit events recorded.</div>}</div>
          </section>
        </main>

        <aside className="walkin-detail-side">
          <section className="walkin-card">
            <div className="walkin-card-heading"><div><h2>Sales assignment</h2><p>{visit.assignedAt ? `Assigned ${new Date(visit.assignedAt).toLocaleString()}` : 'Choose an agent or leave in the waiting queue.'}</p></div><span className="walkin-card-icon"><UserRound size={18} /></span></div>
            <div className="walkin-side-content">
              <label className="walkin-field">Sales agent
                <select value={selectedAgent} onChange={(event) => setSelectedAgent(event.target.value)} disabled={captureBusy || !!closed || !canManageAgents}>
                  <option value="">Unassigned / waiting queue</option>
                  {agents.map((agent) => <option value={agent.id} key={agent.id}>{agent.name} · {agent.department || agent.specialization || 'Sales'} · {agent.availability}</option>)}
                </select>
              </label>
              {agents.length === 0 && <p className="walkin-field-hint">No active workspace members are available. An administrator can add team members.</p>}
              {agents.map((agent) => (
                <label className="walkin-availability" key={agent.id}>
                  <span>{agent.name}<small>{agent.specialization}{agent.openVisits == null ? '' : ` · ${agent.openVisits} open`}</small></span>
                  {canManageAgents
                    ? <select aria-label={`Availability for ${agent.name}`} value={agent.availability} onChange={(event) => void changeAvailability(agent, event.target.value as WalkinAgent['availability'])}><option>Available</option><option>Busy</option><option>Unavailable</option></select>
                    : <span className={`walkin-agent-availability availability-${agent.availability.toLowerCase()}`}>{agent.availability}</span>}
                </label>
              ))}
              <label className="walkin-field">Assignment note<textarea rows={3} value={assignmentNote} onChange={(event) => setAssignmentNote(event.target.value)} disabled={captureBusy || !!closed || !canManageAgents} placeholder="Optional handoff note" /></label>
              <button type="button" className="walkin-button primary full" onClick={() => void saveAssignment()} disabled={captureBusy || saving || !!closed || !canManageAgents || (selectedAgent === (visit.assignedAgentId ?? '') && assignmentNote === visit.assignmentNote)}>
                {visit.assignedAgentId ? 'Save assignment' : 'Assign visitor'}
              </button>
            </div>
          </section>

          <section className="walkin-card">
            <div className="walkin-card-heading"><div><h2>Visit status</h2><p>Use the next appropriate step.</p></div><span className="walkin-card-icon"><Clock3 size={18} /></span></div>
            <div className="walkin-side-content">
              <label className="walkin-field">Current status<select value="" onChange={(event) => event.target.value && void changeStatus(event.target.value as WalkinStatus)} disabled={captureBusy || saving || allowedTransitions.length === 0}><option value="">Choose next status…</option>{allowedTransitions.map((next) => <option key={next}>{next}</option>)}</select></label>
              {!allowedTransitions.length && <p className="walkin-field-hint">This visit is closed. Status can no longer be changed.</p>}
              {visit.convertedEnquiryReference && <p className="walkin-linked-enquiry">Linked enquiry: <strong>{visit.convertedEnquiryReference}</strong></p>}
              {requirements.updatedAt && ['Requirements Captured', 'Follow-up Required'].includes(visit.status) && !visit.convertedEnquiryReference && (
                <><button className="walkin-button primary full" type="button" onClick={() => void startConversion()} disabled={captureBusy || saving || !canConvert}>Create sales enquiry</button>{!canConvert && <p className="walkin-field-hint">The assigned sales agent can create this visit’s enquiry.</p>}</>
              )}
              {!requirements.updatedAt && <p className="walkin-field-hint">Save reviewed requirements before creating an enquiry.</p>}
            </div>
          </section>

          <section className="walkin-card walkin-summary-card">
            <h2><CalendarDays size={17} /> Timing</h2>
            <Info label="Check-in" value={new Date(visit.checkInAt).toLocaleString()} />
            <Info label="Assignment" value={visit.assignedAt ? new Date(visit.assignedAt).toLocaleString() : 'Waiting for assignment'} />
            <Info label="Waiting" value={`${Math.max(0, Math.floor((Date.now() - new Date(visit.checkInAt).getTime()) / 60_000))} minutes`} />
          </section>
        </aside>
      </div>
    </div>
  )
}

function Info({ label, value, wide }: { label: string; value: string; wide?: boolean }) {
  return <div className={`walkin-info${wide ? ' wide' : ''}`}><dt>{label}</dt><dd>{value || '—'}</dd></div>
}

function Field({ label, name, wide, children }: { label: string; name: string; wide?: boolean; children: React.ReactNode }) {
  return <div className={`walkin-field${wide ? ' wide' : ''}`}><label htmlFor={name}>{label}</label>{children}</div>
}
