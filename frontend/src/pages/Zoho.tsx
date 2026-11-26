import { useEffect, useState } from 'react'
import { call } from '../zoho/shared/api.js'
import { GlobalZohoDateFilter, GlobalZohoPager, ZohoSetup } from '../zoho/ZohoApp.jsx'
import ToastHost from '../zoho/shared/ToastHost.jsx'
import { notifyToast } from '../zoho/shared/toast.js'
import EncodingRepair from '../zoho/EncodingRepair'
import '../zoho/styles.css'
import '../zoho/wizard-overrides.css'

export default function Zoho() {
  const [status, setStatus] = useState<any>(null)
  const [error, setError] = useState('')
  useEffect(() => { call('/zoho/status').then(setStatus).catch((e: Error) => setError(e.message)) }, [])
  return <div className="zoho-feedback-scope" onClickCapture={event => {
    const target = event.target as HTMLElement
    const button = target.closest('button')
    if (!button || button.disabled || button.dataset.toast === 'false') return
    const label = (button.innerText || button.getAttribute('aria-label') || 'Action').replace(/\s+/g, ' ').trim()
    if (label) notifyToast(`${label} clicked`, 'info', 1400)
  }}>
    <EncodingRepair />
    <GlobalZohoDateFilter />
    <GlobalZohoPager />
    <ToastHost />
    {error && <div className="error" role="alert">{error}</div>}
    <ZohoSetup status={status} setStatus={setStatus} setError={setError} />
  </div>
}
