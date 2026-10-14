import { Outlet, useLocation } from 'react-router-dom'
import { useEffect } from 'react'
import Sidebar from '../components/Sidebar/Sidebar'
import Topbar from '../components/Topbar/Topbar'
import { useOffice } from '../contexts/OfficeContext'

export default function AppLayout() {
  const { user, workspace } = useOffice()
  const location = useLocation()
  useEffect(() => { window.scrollTo({ top: 0, behavior: 'auto' }) }, [location.pathname])
  return (
    <div className="app-root">
      <a className="skip-navigation" href="#main-content">Skip to content</a>
      <Sidebar />
      <div className="app-main">
        <Topbar />
        <main className="app-content" id="main-content" tabIndex={-1}>
          <Outlet key={`${user?.id}/${workspace?.id}`} />
        </main>
      </div>
    </div>
  )
}
