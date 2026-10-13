import { useLocation } from 'react-router-dom'

export default function Topbar() {
  const loc = useLocation()
  const crumbs = loc.pathname.split('/').filter(Boolean)
  return (
    <header className="topbar">
      <div className="topbar-left">
        <div className="breadcrumbs">{crumbs.length ? crumbs.join(' / ') : 'Dashboard'}</div>
      </div>
      <div className="topbar-right">
        <input className="search" placeholder="Search enquiries, customers..." />
        <button className="icon-btn">🔔</button>
        <div className="avatar">SA</div>
      </div>
    </header>
  )
}
