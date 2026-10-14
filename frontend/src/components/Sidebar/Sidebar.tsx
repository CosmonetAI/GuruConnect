import { NavLink } from 'react-router-dom'
import { UsersRound, AudioLines, LayoutDashboard, ClipboardList, PlugZap, Settings, BarChart3 } from 'lucide-react'
import { useOffice } from '../../contexts/OfficeContext'
export const navigationItems = [
  { to: '/dashboard', label: 'Overview', icon: LayoutDashboard },
  { to: '/enquiries', label: 'Enquiries', icon: ClipboardList },
  { to: '/walkins', label: 'Walk-in visits', icon: UsersRound },
  { to: '/recordings', label: 'Recordings', icon: AudioLines },
  { to: '/zoho', label: 'Zoho integration', icon: PlugZap },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/settings', label: 'Team & account', icon: Settings },
]
export default function Sidebar() {
  const { workspace } = useOffice()
  return <aside className="sidebar"><div className="sidebar-brand"><div className="brand-mark"><AudioLines size={23} /></div><div><div className="brand-title">GuruConnect</div><small>Conversations & enquiries</small></div></div>
    <div className="sidebar-workspace"><span className="nav-eyebrow">WORKSPACE</span><strong>{workspace?.name}</strong><small>{workspace?.role}</small></div>
    <nav className="sidebar-nav" aria-label="Main navigation">{navigationItems.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}><Icon size={18} />{label}</NavLink>)}</nav>
    <div className="sidebar-footer">Private audio. Source-linked English.</div></aside>
}
