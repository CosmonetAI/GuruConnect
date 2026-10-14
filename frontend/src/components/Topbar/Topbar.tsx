import { useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { Menu, X, LogOut, Plus } from 'lucide-react'
import { useOffice } from '../../contexts/OfficeContext'
import { message, supabase } from '../../services/officeApi'
import { navigationItems } from '../Sidebar/Sidebar'
export default function Topbar() {
  const location = useLocation()
  const { workspaces, workspace, setWorkspace, captureBusy, user, localMode } = useOffice()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const current = navigationItems.find(item => location.pathname.startsWith(item.to))
  async function signOut() {
    if (!supabase || captureBusy || busy) return
    setBusy(true); setError('')
    try { const result = await supabase.auth.signOut(); if (result.error) throw result.error } catch (failure) { setError(message(failure)) } finally { setBusy(false) }
  }
  return <><header className="topbar"><div className="topbar-left"><button className="icon-btn mobile-menu-button" aria-label={open ? 'Close navigation' : 'Open navigation'} aria-expanded={open} aria-controls="mobile-navigation" onClick={() => setOpen(value => !value)}>{open ? <X size={20} /> : <Menu size={20} />}</button><div className="breadcrumbs">{current?.label ?? 'Workspace'}</div></div>
    <div className="topbar-right"><label className="workspace-select"><span className="sr-only">Active workspace</span><select value={workspace?.id ?? ''} onChange={event => setWorkspace(event.target.value)} disabled={captureBusy}>{workspaces.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <Link className="button button-primary topbar-create" to="/enquiries/new"><Plus size={16} /> New enquiry</Link>
      {localMode ? <span className="local-mode-badge">Local mode</span> : <button className="icon-btn" aria-label="Sign out" title={captureBusy ? 'Finish saving your recording before signing out' : `Sign out ${user?.email ?? ''}`} disabled={captureBusy || busy} onClick={() => void signOut()}><LogOut size={18} /></button>}</div></header>
    {error && <p role="alert" className="flow-error">{error}</p>}{open && <nav id="mobile-navigation" className="mobile-navigation" aria-label="Mobile navigation">{navigationItems.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} onClick={() => setOpen(false)}><Icon size={18} />{label}</NavLink>)}</nav>}</>
}
