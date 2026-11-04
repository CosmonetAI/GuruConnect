import { useEffect, useState } from 'react'
import { call } from '../zoho/shared/api.js'

export default function OrganisationPill() {
  const [organisationId, setOrganisationId] = useState('')

  useEffect(() => {
    let alive = true
    const refresh = () => {
      void call('/zoho/status').then(status => {
        if (alive) setOrganisationId(status?.organization_id ? String(status.organization_id) : '')
      }).catch(() => {
        if (alive) setOrganisationId('')
      })
    }
    refresh()
    window.addEventListener('guruconnect-zoho-organization-changed', refresh)
    return () => {
      alive = false
      window.removeEventListener('guruconnect-zoho-organization-changed', refresh)
    }
  }, [])

  return <div className="organisation-id-pill" role="status" aria-label="Selected platform organisation ID">
    <span>Organisation ID</span>
    <strong>{organisationId || 'Not selected'}</strong>
  </div>
}
