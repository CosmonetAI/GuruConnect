import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { UsersRound, AudioLines, LayoutDashboard, ClipboardList, PlugZap, Settings, BarChart3, MessageSquare, PackageSearch, ContactRound, FileText, ShoppingCart, ClipboardCheck, ReceiptText, Columns3 } from 'lucide-react'
import { useOffice } from '../../contexts/OfficeContext'
import { useScopedResource } from '../../hooks/useScopedResource'
import { supabase } from '../../services/officeApi'
export const navigationItems = [
  { to: '/dashboard', label: 'Overview', icon: LayoutDashboard }, { to: '/enquiries', label: 'Enquiries', icon: ClipboardList }, { to: '/walkins', label: 'Walk-in visits', icon: UsersRound }, { to: '/ai-chats', label: 'Sessions', icon: MessageSquare }, { to: '/products', label: 'Product catalogue', icon: PackageSearch }, { to: '/contacts', label: 'Customer directory', icon: ContactRound }, { to: '/recordings', label: 'Conversation history', icon: AudioLines }, { to: '/quotes', label: 'Quotes', icon: FileText }, { to: '/sales-orders', label: 'Sales orders', icon: ShoppingCart }, { to: '/purchase-orders', label: 'Purchase orders', icon: ClipboardCheck }, { to: '/invoices', label: 'Invoices', icon: ReceiptText }, { to: '/pipelines', label: 'Pipelines', icon: Columns3 }, { to: '/analytics', label: 'Analytics', icon: BarChart3 }, { to: '/zoho', label: 'Connections', icon: PlugZap }, { to: '/settings', label: 'Team & account', icon: Settings },
]
export default function Sidebar() {
  const { workspace, user } = useOffice()
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
  const displayName = profile.data?.display_name || user?.user_metadata?.display_name || user?.email?.split('@')[0] || 'Team member'
  const email = user?.email?.toLowerCase() || ''
  const handleAvatarError = () => setAvatarUrl('')
  return <aside className="sidebar"><div className="sidebar-brand"><div className="brand-mark"><AudioLines size={23} /></div><div><div className="brand-title">GuruConnect</div><small>Conversation history & enquiries</small></div></div>
    <div className="sidebar-workspace"><div className="workspace-person"><div className="workspace-avatar">{avatarUrl ? <img src={avatarUrl} alt="" onError={handleAvatarError} /> : <span>{displayName.slice(0, 1).toUpperCase()}</span>}</div><div><span className="nav-eyebrow">WORKSPACE</span><strong>{workspace?.name}</strong></div></div><div className="workspace-member"><span>{displayName}</span><span className="workspace-role-badge">{workspace?.role}</span></div><small>{email}</small></div>
    <nav className="sidebar-nav" aria-label="Main navigation">{roleItems.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}><Icon size={18} />{label}{to === '/enquiries' && !!unread.data?.count && <span className="nav-count" aria-label={`${unread.data.count} unread enquiries`}>{unread.data.count > 99 ? '99+' : unread.data.count}</span>}</NavLink>)}</nav>
    <div className="sidebar-footer">Conversation capture · Sales follow-up</div></aside>
}
