import { Outlet, useLocation } from 'react-router-dom'
import { useEffect } from 'react'
import { Eye } from 'lucide-react'
import Sidebar from '../components/Sidebar/Sidebar'
import OrganisationPill from '../components/OrganisationPill'
import AvailabilityPill from '../components/AvailabilityPill'
import { useOffice } from '../contexts/OfficeContext'

export default function AppLayout() {
  const { user, workspace, readOnly } = useOffice()
  const location = useLocation()
  useEffect(() => { window.scrollTo({ top: 0, behavior: 'auto' }) }, [location.pathname])
  return (
    <div className={`app-root${readOnly ? ' read-only-mode' : ''}`}>
      <a className="skip-navigation" href="#main-content">Skip to content</a>
      <Sidebar />
      <div className="app-main">
        <div className="platform-bar">{readOnly && <span className="read-only-pill"><span className="availability-pill-dot" />Read-only</span>}<AvailabilityPill />{workspace?.role !== 'receptionist' && <OrganisationPill />}</div>
        <main
          className="app-content"
          id="main-content"
          tabIndex={-1}
          onSubmitCapture={event => {
            if (readOnly) {
              const form = event.target as HTMLFormElement
              if (form.querySelector('input[type="search"], .recording-search, .product-search')) return
              event.preventDefault()
              event.stopPropagation()
            }
          }}
          onClickCapture={event => {
            if (!readOnly) return
            const target = event.target as HTMLElement
            const control = target.closest('button, a.button, [role="button"]') as HTMLElement | null
            if (!control) return
            const text = (control.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase()
            const blocked = /register visitor|add visitor|new enquiry|capture an enquiry|start recording|record again|recover audio|add another product|add product|add item|add contact|add opportunity|create (an? )?(invoice|quote|sales order|purchase order|document)|generate (an? )?(invoice|quote|sales order|purchase order|document)|delete|edit|save draft|save enquiry|update profile|assign|send invitation|add internal note|change stage|move opportunity/.test(text)
            if (blocked) {
              event.preventDefault()
              event.stopPropagation()
            }
          }}
          onChangeCapture={event => {
            if (!readOnly) return
            const target = event.target as HTMLElement
            if (target.tagName !== 'SELECT') return
            if (target.closest('.walkin-filters, .filter-bar, .recording-search, [data-review-filter]')) return
            event.preventDefault()
            event.stopPropagation()
          }}
        >
          {readOnly && <aside className="read-only-review-sheet" role="status"><Eye size={17} /><div><strong>Owner preview</strong><span>Browsing and opening records is available. Changes remain with this workspace member.</span></div></aside>}
          <Outlet key={`${user?.id}/${workspace?.id}/${workspace?.member_id ?? user?.id}`} />
        </main>
      </div>
    </div>
  )
}
