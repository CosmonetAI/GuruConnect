import { useState } from 'react'
import { apiBase } from '../services/officeApi'

type Result = { ticketNumber: number; date: string; stage: string; salesperson: string; salespersonEmail?: string }

export default function TicketTracking() {
  const [token, setToken] = useState(''); const [result, setResult] = useState<Result | null>(null); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  async function track() { if (!token.trim() || busy) return; setBusy(true); setError(''); setResult(null); try { const response = await fetch(`${apiBase}/api/v1/enquiries/public/track?token=${encodeURIComponent(token.trim())}`); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Ticket not found or token expired'); setResult(data) } catch (failure) { setError(failure instanceof Error ? failure.message : 'Unable to find ticket.') } finally { setBusy(false) } }
  return <main className="flow-panel" style={{ maxWidth: 640, margin: '48px auto' }}><div className="page-kicker">GURUCONNECT TICKET</div><h1 className="page-title">Track your enquiry</h1><p>Enter the temporary token given by reception.</p><form className="settings-form" onSubmit={event => { event.preventDefault(); void track() }}><label>Temporary token<input value={token} onChange={event => setToken(event.target.value)} minLength={16} required placeholder="Paste your ticket token" /></label><button className="button button-primary" disabled={busy}>{busy ? 'Checking…' : 'Track ticket'}</button></form>{error && <p className="flow-error" role="alert">{error}</p>}{result && <div className="flow-note"><h2>Ticket #{result.ticketNumber}</h2><p>Date: {result.date}</p><p>Status: {result.stage === 'in_process' ? 'In process' : result.stage === 'closed' ? 'Closed' : 'New enquiry'}</p><p>Salesperson: {result.salesperson}{result.salespersonEmail ? ` · ${result.salespersonEmail}` : ''}</p></div>}</main>
}
