import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, CalendarDays, ChevronDown, UserRound, UsersRound } from 'lucide-react'
import PageLoader from '../../components/PageLoader'
import { useOffice } from '../../contexts/OfficeContext'
import { assignWalkin, canManageWalkinAgents, getWalkinVisit, listWalkinAgents } from '../../services/walkinService'
import type { WalkinAgent, WalkinVisit } from '../../types/walkin'
import WalkinDetailRecording from './WalkinDetailRecording'
import './Walkins.css'

export default function WalkinDetail() {
  const { workspace, captureBusy } = useOffice()
  const { id = '' } = useParams()
  const [visit, setVisit] = useState<WalkinVisit | null>(null)
  const [agents, setAgents] = useState<WalkinAgent[]>([])
  const [canManageAgents, setCanManageAgents] = useState(false)
  const [selectedAgent, setSelectedAgent] = useState('')
  const [agentMenuOpen, setAgentMenuOpen] = useState(false)
  const [assignmentNote, setAssignmentNote] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const refresh = useCallback(async () => {
    if (!workspace) { setVisit(null); setLoading(false); return }
    setLoading(true)
    setError('')
    try {
      const [loadedVisit, loadedAgents, manageAgents] = await Promise.all([
        getWalkinVisit(workspace.id, id),
        listWalkinAgents(workspace.id),
        canManageWalkinAgents(workspace.id),
      ])
      setVisit(loadedVisit)
      setAgents(loadedAgents)
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
  useEffect(() => {
    if (!agentMenuOpen) return
    const closeMenu = (event: MouseEvent) => {
      if (event.target instanceof Element && !event.target.closest('.walkin-agent-picker')) setAgentMenuOpen(false)
    }
    document.addEventListener('mousedown', closeMenu)
    return () => document.removeEventListener('mousedown', closeMenu)
  }, [agentMenuOpen])

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

  if (loading) return <div className="walkin-page"><div className="walkin-empty"><PageLoader kind="walkins" label="Loading visitor details" detail="Preparing the customer visit record." /></div></div>
  if (!visit) return <div className="walkin-page"><div className="walkin-alert error">{error || 'Walk-in visit not found.'}</div><Link className="walkin-button secondary" to="/walkins"><ArrowLeft size={16} /> Back to queue</Link></div>

  const selectedAgentDetails = agents.find((agent) => agent.id === selectedAgent)

  return (
    <div className="walkin-page">
      <div className="walkin-back-row"><Link to="/walkins"><ArrowLeft size={16} /> Reception queue</Link></div>
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
        <div className="walkin-detail-column walkin-timing-column">
          <section className="walkin-card walkin-summary-card">
            <h2><CalendarDays size={17} /> Timing</h2>
            <Info label="Check-in" value={new Date(visit.checkInAt).toLocaleString()} />
            <Info label="Assignment" value={visit.assignedAt ? new Date(visit.assignedAt).toLocaleString() : 'Waiting for assignment'} />
            <Info label="Waiting" value={`${Math.max(0, Math.floor((Date.now() - new Date(visit.checkInAt).getTime()) / 60_000))} minutes`} />
          </section>
          <WalkinDetailRecording registeredAt={visit.checkInAt} />
        </div>

        <section className="walkin-card walkin-customer-column">
          <div className="walkin-card-heading"><div><h2>Customer information</h2><p>Details captured when the visitor was registered.</p></div></div>
          <dl className="walkin-info-grid">
            <Info label="Name (customer full name)" value={visit.customerName} />
            <Info label="Phone (mobile number)" value={visit.mobileNumber} />
            <Info label="Email address" value={visit.email?.toLowerCase()} />
            <Info label="Source" value="Retail walk-in" />
            <Info label="Branch" value="Walk-in" />
            <Info label="Assigned executive" value={visit.assignedAgentName || 'Unassigned'} />
            <Info label="Customer type" value="Retail" />
            <Info label="Company" value={visit.companyName} />
            <Info label="Project / site" value={visit.projectName} />
            <Info label="Preferred contact" value={visit.preferredContactMethod} />
          </dl>
        </section>

        <section className="walkin-card walkin-assignment-column">
          <div className="walkin-card-heading"><div><h2>Sales assignment</h2><p>{visit.assignedAt ? `Assigned ${new Date(visit.assignedAt).toLocaleString()}` : 'Choose an agent or leave in the waiting queue.'}</p></div><span className="walkin-card-icon"><UserRound size={18} /></span></div>
          <div className="walkin-side-content">
            <div className="walkin-assignment-status"><span>Current status</span><strong className={`walkin-status status-${visit.status.toLowerCase().replaceAll(' ', '-')}`}>{visit.status}</strong></div>
            <div className="walkin-field walkin-agent-picker">
              <span>Sales agent</span>
              <button type="button" className="walkin-agent-picker-trigger" aria-haspopup="listbox" aria-expanded={agentMenuOpen} onClick={() => setAgentMenuOpen((open) => !open)} disabled={captureBusy || !canManageAgents}>
                <span>{selectedAgentDetails ? selectedAgentDetails.name : 'Unassigned / waiting queue'}</span>
                <span className="walkin-agent-picker-meta">{selectedAgentDetails ? selectedAgentDetails.availability : 'Waiting'}</span>
                <ChevronDown size={16} aria-hidden="true" />
              </button>
              {agentMenuOpen && <div className="walkin-agent-picker-menu" role="listbox" aria-label="Sales agents">
                <button type="button" className={`walkin-agent-picker-option${!selectedAgent ? ' is-selected' : ''}`} role="option" aria-selected={!selectedAgent} onClick={() => { setSelectedAgent(''); setAgentMenuOpen(false) }}>
                  <strong>Unassigned / waiting queue</strong><small>Waiting for assignment</small>
                </button>
                {agents.map((agent) => <button type="button" className={`walkin-agent-picker-option${selectedAgent === agent.id ? ' is-selected' : ''}`} role="option" aria-selected={selectedAgent === agent.id} key={agent.id} onClick={() => { setSelectedAgent(agent.id); setAgentMenuOpen(false) }}>
                  <strong>{agent.name}</strong><small>{agent.department || agent.specialization || 'Sales'} · <span className={`walkin-picker-availability availability-${agent.availability.toLowerCase()}`}>{agent.availability}</span></small>
                </button>)}
              </div>}
            </div>
            {agents.length === 0 && <p className="walkin-field-hint">No active workspace members are available. An administrator can add team members.</p>}
            {selectedAgentDetails && <p className="walkin-field-hint">{selectedAgentDetails.name} · {selectedAgentDetails.availability} · {selectedAgentDetails.openVisits ?? 0} open visits</p>}
            <label className="walkin-field">Assignment note<textarea rows={3} value={assignmentNote} onChange={(event) => setAssignmentNote(event.target.value)} disabled={captureBusy || !canManageAgents} placeholder="Optional handoff note" /></label>
            <button type="button" className="walkin-button primary full" onClick={() => void saveAssignment()} disabled={captureBusy || saving || !canManageAgents || (selectedAgent === (visit.assignedAgentId ?? '') && assignmentNote === visit.assignmentNote)}>
              {visit.assignedAgentId ? 'Save assignment' : 'Assign visitor'}
            </button>
          </div>
        </section>
      </div>
    </div>
  )
}

function Info({ label, value }: { label: string; value?: string }) {
  return <div className="walkin-info"><dt>{label}</dt><dd>{value || '—'}</dd></div>
}
