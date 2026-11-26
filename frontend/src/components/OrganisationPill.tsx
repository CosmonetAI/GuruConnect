import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { call } from '../zoho/shared/api.js'

export default function OrganisationPill() {
  const [organisationId, setOrganisationId] = useState('')
  const [mailAccountId, setMailAccountId] = useState('')
  const [showMailAccount, setShowMailAccount] = useState(false)

  useEffect(() => {
    let alive = true
    const refresh = () => {
      void call('/zoho/status').then(status => {
        if (alive) {
          setOrganisationId(status?.organization_id ? String(status.organization_id) : '')
          setMailAccountId(status?.mail_account_id ? String(status.mail_account_id) : '')
        }
      }).catch(() => {
        if (alive) { setOrganisationId(''); setMailAccountId('') }
      })
    }
    refresh()
    window.addEventListener('guruconnect-zoho-organization-changed', refresh)
    return () => {
      alive = false
      window.removeEventListener('guruconnect-zoho-organization-changed', refresh)
    }
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => setShowMailAccount(current => !current), 3000)
    return () => window.clearInterval(timer)
  }, [])

  return <Link className="organisation-id-pill organisation-slideshow-pill" to="/zoho" role="status" aria-live="polite" aria-label="Open Zoho setup for the selected organisation and Mail account">
    <span className="organisation-pill-slide" key={showMailAccount ? 'mail' : 'organisation'}>
      <span>{showMailAccount ? 'Mail account ID' : 'Organisation ID'}</span>
      <strong>{showMailAccount ? (mailAccountId || 'Not selected') : (organisationId || 'Not selected')}</strong>
    </span>
  </Link>
}
