import { Fragment, useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { Menu, X, BookOpen } from 'lucide-react'
import { useOffice } from '../../contexts/OfficeContext'
import { navigationItems, navigationTone, zohoBooksItems } from '../Sidebar/Sidebar'
export default function Topbar() {
  const location = useLocation()
  const { workspace } = useOffice()
  const [open, setOpen] = useState(false)
  const visibleItems = workspace?.role === 'owner' ? navigationItems : workspace?.role === 'receptionist' ? navigationItems.filter(item => item.to === '/walkins' || item.to === '/settings') : navigationItems.filter(item => !item.to.startsWith('/walkins'))
  const visibleBooksItems = workspace?.role === 'owner' || workspace?.role === 'salesperson' ? zohoBooksItems : []
  useEffect(() => { setOpen(false) }, [location.pathname])
  return <><button className="icon-btn mobile-menu-button" aria-label={open ? 'Close navigation' : 'Open navigation'} aria-expanded={open} aria-controls="mobile-navigation" onClick={() => setOpen(value => !value)}>{open ? <X size={20} /> : <Menu size={20} />}</button>
    {open && <><button type="button" className="mobile-navigation-scrim" aria-label="Close navigation" onClick={() => setOpen(false)} /><nav id="mobile-navigation" className="mobile-navigation" aria-label="Mobile navigation">{visibleItems.map(({ to, label, icon: Icon }) => <Fragment key={to}><NavLink to={to} onClick={() => setOpen(false)} className={({ isActive }) => `nav-link nav-link-${navigationTone(to)}${isActive ? ' active' : ''}`}><Icon size={18} />{label}</NavLink>{to === '/pipelines' && visibleBooksItems.length > 0 && <div className="mobile-navigation-group"><div className="mobile-navigation-group-title nav-link-books"><BookOpen size={18} />Zoho Books</div>{visibleBooksItems.map(({ to: booksTo, label: booksLabel, icon: BooksIcon }) => <NavLink key={booksTo} to={booksTo} onClick={() => setOpen(false)} className={({ isActive }) => `nav-link nav-link-child nav-link-${navigationTone(booksTo)}${isActive ? ' active' : ''}`}><BooksIcon size={17} />{booksLabel}</NavLink>)}</div>}</Fragment>)}</nav></>}</>
}
