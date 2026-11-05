import { useEffect, useRef, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { UsersRound, AudioLines, LayoutDashboard, ClipboardList, PlugZap, Settings, BarChart3, MessageSquare, PackageSearch, ContactRound, FileText, ShoppingCart, ClipboardCheck, ReceiptText, Columns3, Check, ChevronDown, Search, BookUser } from 'lucide-react'
import { useOffice } from '../../contexts/OfficeContext'
import { useScopedResource } from '../../hooks/useScopedResource'
import { supabase } from '../../services/officeApi'
export const navigationItems = [
  { to: '/dashboard', label: 'Overview', icon: LayoutDashboard }, { to: '/enquiries', label: 'Enquiries', icon: ClipboardList }, { to: '/walkins', label: 'Walk-in visits', icon: UsersRound }, { to: '/ai-chats', label: 'Sessions', icon: MessageSquare }, { to: '/products', label: 'Product catalogue', icon: PackageSearch }, { to: '/contacts', label: 'Customer directory', icon: ContactRound }, { to: '/recordings', label: 'Conversation history', icon: AudioLines }, { to: '/quotes', label: 'Quotes', icon: FileText }, { to: '/sales-orders', label: 'Sales orders', icon: ShoppingCart }, { to: '/purchase-orders', label: 'Purchase orders', icon: ClipboardCheck }, { to: '/invoices', label: 'Invoices', icon: ReceiptText }, { to: '/pipelines', label: 'Pipelines', icon: Columns3 }, { to: '/analytics', label: 'Analytics', icon: BarChart3 }, { to: '/zoho', label: 'Connections', icon: PlugZap }, { to: '/settings', label: 'Team & account', icon: Settings },
]
export default function Sidebar() {
  const { workspace, workspaces, setWorkspace, captureBusy, user, readOnly } = useOffice()
  const profile = useScopedResource<{ display_name?: string; avatar_path?: string }>('/profile')
  const unread = useScopedResource<{ count: number }>('/enquiries/unread-count')
  const [avatarUrl, setAvatarUrl] = useState(''), [avatarRefresh, setAvatarRefresh] = useState(0)
  useEffect(() => { const refresh = () => { setAvatarRefresh(value => value + 1); void profile.reload() }; window.addEventListener('guruconnect-profile-updated', refresh); return () => window.removeEventListener('guruconnect-profile-updated', refresh) }, [profile.reload])
  useEffect(() => {
    let active = true
    const path = profile.data?.avatar_path
    setAvatarUrl('')
    if (!path) return () => { active = false }
    if (path.startsWith('http')) { setAvatarUrl(path); return () => { active = false } }
    if (!supabase) return () => { active = false }
    void supabase.storage.from('profile-avatars').createSignedUrl(path, 3600).then(result => {
      if (active && !result.error && result.data?.signedUrl) setAvatarUrl(result.data.signedUrl)
    }).catch(() => {})
    return () => { active = false }
  }, [profile.data?.avatar_path, avatarRefresh])
  const roleItems = workspace?.role === 'owner' ? navigationItems : workspace?.role === 'receptionist' ? navigationItems.filter(item => item.to === '/walkins' || item.to === '/settings') : navigationItems.filter(item => item.to !== '/walkins' && !item.to.startsWith('/walkins/'))
  const booksRoutes = new Set(['/contacts', '/products', '/quotes', '/sales-orders', '/purchase-orders', '/invoices'])
  const booksItems = roleItems.filter(item => booksRoutes.has(item.to))
  const primaryItems = roleItems.filter(item => !booksRoutes.has(item.to))
  const booksIndex = primaryItems.findIndex(item => item.to === '/pipelines')
  const primaryBeforeBooks = booksIndex < 0 ? primaryItems : primaryItems.slice(0, booksIndex + 1)
  const primaryAfterBooks = booksIndex < 0 ? [] : primaryItems.slice(booksIndex + 1)
  const displayName = profile.data?.display_name || user?.user_metadata?.display_name || user?.email?.split('@')[0] || 'Team member'
  const email = String(user?.email ?? '').toLowerCase()
  const ownerWorkspace = workspaces.find(item => (item.member_id ?? item.id) === user?.id && item.role === 'owner')
  const canSwitchWorkspace = !!ownerWorkspace && workspaces.length > 1
  const handleAvatarError = () => setAvatarUrl('')
  const [workspaceMenuOpen, setWorkspaceMenuOpen] = useState(false)
  const [workspaceQuery, setWorkspaceQuery] = useState('')
  const workspaceMenuRef = useRef<HTMLDivElement>(null)
  const [booksOpen, setBooksOpen] = useState(true)
  useEffect(() => {
    if (!workspaceMenuOpen) return
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (workspaceMenuRef.current && !workspaceMenuRef.current.contains(event.target as Node)) setWorkspaceMenuOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setWorkspaceMenuOpen(false) }
    document.addEventListener('mousedown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => { document.removeEventListener('mousedown', closeOnOutsideClick); document.removeEventListener('keydown', closeOnEscape) }
  }, [workspaceMenuOpen])
  useEffect(() => { if (!canSwitchWorkspace) setWorkspaceMenuOpen(false) }, [canSwitchWorkspace])
  const activeWorkspaceId = workspace?.member_id ?? workspace?.id ?? ''
  const filteredWorkspaces = workspaces.filter(item => `${item.display_name || displayName} ${item.name} ${item.role} ${item.role === 'owner' ? 'full access' : 'read only'}`.toLowerCase().includes(workspaceQuery.trim().toLowerCase()))
  const workspaceInitial = (item: typeof workspaces[number]) => (item.display_name || item.name || displayName).slice(0, 2).toUpperCase()
  return <aside className="sidebar"><div className="sidebar-brand"><div className="brand-mark"><AudioLines size={23} /></div><div><div className="brand-title">GuruConnect</div><small>Conversation history & enquiries</small></div></div>
    <div className="sidebar-workspace" ref={workspaceMenuRef}><div className="workspace-person"><div className="workspace-avatar">{avatarUrl ? <img src={avatarUrl} alt="" onError={handleAvatarError} /> : <span>{displayName.slice(0, 1).toUpperCase()}</span>}</div><div className="workspace-person-copy">{canSwitchWorkspace ? <div className="workspace-switcher"><button type="button" className="workspace-trigger" aria-expanded={workspaceMenuOpen} aria-haspopup="listbox" onClick={() => { setWorkspaceMenuOpen(open => !open); setWorkspaceQuery('') }} disabled={captureBusy}><span>Switch workspace</span><ChevronDown size={15} /></button>{workspaceMenuOpen && <div className="workspace-menu" role="dialog" aria-label="Switch workspace"><div className="workspace-search"><Search size={16} /><input autoFocus value={workspaceQuery} onChange={event => setWorkspaceQuery(event.target.value)} placeholder="Search workspace..." aria-label="Search workspace" /></div><span className="workspace-menu-heading">MY WORKSPACES</span><div className="workspace-options" role="listbox" aria-label="Workspaces">{filteredWorkspaces.map(item => { const id = item.member_id ?? item.id; const selected = id === activeWorkspaceId; return <button type="button" role="option" aria-selected={selected} className={`workspace-option${selected ? ' selected' : ''}`} key={id} onClick={() => { setWorkspace(id); setWorkspaceMenuOpen(false) }}><span className={`workspace-option-avatar${selected ? ' active' : ''}`}>{workspaceInitial(item)}</span><span className="workspace-option-copy"><strong>{item.display_name || item.name || displayName}</strong><small>{item.role === 'owner' ? 'Full access' : `${item.role || 'Member'} · Read-only workspace`}</small></span>{selected && <Check size={18} />}</button> })}{!filteredWorkspaces.length && <span className="workspace-empty">No workspaces found</span>}</div></div>}</div> : <strong>{workspace?.name}</strong>}</div></div><div className="workspace-member"><span>{workspace?.display_name || displayName}</span><span className="workspace-role-badge">{workspace?.role}{readOnly ? ' · Read-only' : ''}</span></div><small className="workspace-email">{email}</small></div>
    <nav className="sidebar-nav" aria-label="Main navigation">{primaryBeforeBooks.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}><Icon size={18} />{label}{to === '/enquiries' && !!unread.data?.count && <span className="nav-count" aria-label={`${unread.data.count} unread enquiries`}>{unread.data.count > 99 ? '99+' : unread.data.count}</span>}</NavLink>)}{!!booksItems.length && <div className="nav-group"><button type="button" className="nav-group-trigger" aria-expanded={booksOpen} onClick={() => setBooksOpen(open => !open)}><span><BookUser size={18} />Zoho Books</span><ChevronDown size={17} className={booksOpen ? '' : 'collapsed'} /></button>{booksOpen && <div className="nav-group-items">{booksItems.map(item => { const Icon = item.icon; const label = item.to === '/contacts' ? 'Contacts' : item.to === '/products' ? 'Items' : item.label; return <NavLink key={item.to} to={item.to} className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}><Icon size={18} />{label}</NavLink> })}</div>}</div>}{primaryAfterBooks.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}><Icon size={18} />{label}</NavLink>)}</nav>
    <div className="sidebar-footer">Conversation capture · Sales follow-up</div></aside>
}
