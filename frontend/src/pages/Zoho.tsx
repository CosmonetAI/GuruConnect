import { useEffect, useState } from 'react'
import { call } from '../zoho/shared/api.js'
import { GlobalZohoDateFilter, GlobalZohoPager, ZohoSetup } from '../zoho/ZohoApp.jsx'
import '../zoho/styles.css'
import '../zoho/wizard-overrides.css'

export default function Zoho() {
  const [status, setStatus] = useState<any>(null)
  const [error, setError] = useState('')
  useEffect(() => { call('/zoho/status').then(setStatus).catch((e: Error) => setError(e.message)) }, [])
  return <>
    <GlobalZohoDateFilter />
    <GlobalZohoPager />
    {error && <div className="error" role="alert">{error}</div>}
    <ZohoSetup status={status} setStatus={setStatus} setError={setError} />
  </>
}
