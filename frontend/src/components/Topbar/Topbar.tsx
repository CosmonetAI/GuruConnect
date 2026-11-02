import { Fragment, useEffect, useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { Menu, X, LogOut, Plus, BookOpen } from 'lucide-react'
import { useOffice } from '../../contexts/OfficeContext'
import { message, supabase } from '../../services/officeApi'
import { navigationItems, zohoBooksItems } from '../Sidebar/Sidebar'
export default function Topbar() {
  const location = useLocation()
  const { workspace, captureBusy, user, localMode } = useOffice()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const current = navigationItems.find(item => location.pathname.startsWith(item.to))
  const visibleItems = workspace?.role === 'owner' ? navigationItems : workspace?.role === 'receptionist' ? navigationItems.filter(item => item.to === '/walkins' || item.to === '/settings') : navigationItems.filter(item => !item.to.startsWith('/walkins'))
  const visibleBooksItems = workspace?.role === 'owner' || workspace?.role === 'salesperson' ? zohoBooksItems : []
  useEffect(() => { setOpen(false) }, [location.pathname])
  async function signOut() {
    if (!supabase || captureBusy || busy) return
    setBusy(true); setError('')
    try { const result = await supabase.auth.signOut(); if (result.error) throw result.error } catch (failure) { setError(message(failure)) } finally { setBusy(false) }
  }
  return <><header className="topbar"><div className="topbar-left"><button className="icon-btn mobile-menu-button" aria-label={open ? 'Close navigation' : 'Open navigation'} aria-expanded={open} aria-controls="mobile-navigation" onClick={() => setOpen(value => !value)}>{open ? <X size={20} /> : <Menu size={20} />}</button><div className="breadcrumbs">{current?.label ?? 'Workspace'}</div></div>
    <div className="topbar-right"><Link className="button button-primary topbar-create" to={workspace?.role === 'receptionist' ? '/walkins/new' : '/enquiries/new'}><Plus size={16} /> {workspace?.role === 'receptionist' ? 'Register visitor' : 'New enquiry'}</Link>
      {localMode ? <span className="local-mode-badge">Local mode</span> : <button className="icon-btn" aria-label="Sign out" title={captureBusy ? 'Finish saving your recording before signing out' : `Sign out ${user?.email ?? ''}`} disabled={captureBusy || busy} onClick={() => void signOut()}><LogOut size={18} /></button>}</div></header>
    {error && <p role="alert" className="flow-error">{error}</p>}{open && <nav id="mobile-navigation" className="mobile-navigation" aria-label="Mobile navigation">{visibleItems.map(({ to, label, icon: Icon }) => <Fragment key={to}><NavLink to={to} onClick={() => setOpen(false)}><Icon size={18} />{label}</NavLink>{to === '/pipelines' && visibleBooksItems.length > 0 && <div className="mobile-navigation-group"><div className="mobile-navigation-group-title"><BookOpen size={18} />Zoho Books</div>{visibleBooksItems.map(({ to: booksTo, label: booksLabel, icon: BooksIcon }) => <NavLink key={booksTo} to={booksTo} onClick={() => setOpen(false)} className="mobile-navigation-child"><BooksIcon size={17} />{booksLabel}</NavLink>)}</div>}</Fragment>)}</nav>}</>
}
