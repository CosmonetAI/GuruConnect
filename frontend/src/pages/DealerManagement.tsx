import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useScopedResource } from '../hooks/useScopedResource'
import { json, message, request, type Member } from '../services/officeApi'
import { useOffice } from '../contexts/OfficeContext'
import '../styles/dealer.css'

type Account = { member_id: string; name: string; role: string; connected: boolean }
type Tax = { tax_id: string; tax_name?: string; tax_display_name?: string; tax_percentage?: number }
type Mapping = { configured: boolean; account_member_id?: string; discount_percent?: number; gst_group_id?: string | null }

export default function DealerManagement() {
  const { workspace } = useOffice()
  const team = useScopedResource<{items: Member[]}>('/team')
  const [accounts, setAccounts] = useState<Account[]>([])
  const [taxes, setTaxes] = useState<Tax[]>([])
  const [dealerId, setDealerId] = useState('')
  const [accountId, setAccountId] = useState('')
  const [discount, setDiscount] = useState('0')
  const [gstId, setGstId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const mappingAccountId = useRef('')
  useEffect(() => {
    let active = true
    void request<{items: Account[]}>('/dealer/accounts', workspace?.id, {}, workspace?.member_id)
      .then(result => { if (active) setAccounts(result.items || []) })
      .catch(failure => { if (active) setError(message(failure)) })
    return () => { active = false }
  }, [workspace?.id, workspace?.member_id])
  useEffect(() => {
    if (!dealerId || !workspace) return
    let active = true
    void request<Mapping>(`/dealer/${encodeURIComponent(dealerId)}/mapping`, workspace.id, {}, workspace.member_id)
      .then(result => {
        if (!active) return
        if (result.configured) {
          mappingAccountId.current = result.account_member_id || ''
          setAccountId(result.account_member_id || '')
          setDiscount(String(result.discount_percent ?? 0))
          setGstId(result.gst_group_id || '')
        } else {
          mappingAccountId.current = ''
          setAccountId('')
          setDiscount('0')
          setGstId('')
          setTaxes([])
        }
      }).catch(failure => { if (active) setError(message(failure)) })
    return () => { active = false }
  }, [dealerId, workspace?.id, workspace?.member_id])
  useEffect(() => {
    if (!accountId || !workspace || !dealerId) { 
      setTaxes([])
      setGstId('')
      return 
    }
    let active = true
    const preserveGstSelection = mappingAccountId.current === accountId
    mappingAccountId.current = ''
    setTaxes([])
    if (!preserveGstSelection) setGstId('')
    setError('')
    request<{items: Tax[]}>(`/dealer/${encodeURIComponent(dealerId)}/accounts/${encodeURIComponent(accountId)}/gst-groups`, workspace.id, {}, workspace.member_id)
      .then(taxResult => { 
        if (active) {
          if (taxResult.items && taxResult.items.length > 0) {
            setTaxes(taxResult.items)
            setError('')
          } else {
            setTaxes([])
            setGstId('')
            setError('No GST groups found for the selected account')
          }
        }
      })
      .catch(failure => { 
        if (active) {
          const errorMsg = message(failure)
          setError(`Failed to load GST groups: ${errorMsg}`)
          setTaxes([])
        }
      })
    return () => { active = false }
  }, [dealerId, accountId, workspace?.id, workspace?.member_id])
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!workspace || !dealerId || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      await request(`/dealer/${encodeURIComponent(dealerId)}/mapping`, workspace.id,
        json('PUT', { account_member_id: accountId,
          discount_percent: Number(discount), gst_group_id: gstId || null }), workspace.member_id)
      setNotice('Dealer account mapping saved.')
    } catch (failure) { setError(message(failure)) }
    finally { setBusy(false) }
  }
  const dealers = (team.data?.items || []).filter(member => member.role === 'dealer' && member.status === 'active')
  return <section className="dealer-page">
    <header className="dealer-header"><div><span className="dealer-kicker">ACCOUNT MAPPING</span><h1>Dealer setup</h1><p>Connect each Dealer to a Zoho Books account and configure its invoice defaults.</p></div></header>
    {(error || team.error) && <p role="alert" className="dealer-error">{error || team.error}</p>}
    {notice && <p role="status" className="dealer-success">{notice}</p>}
    <section className="dealer-panel dealer-setup-panel">
      <label>Dealer<select value={dealerId} onChange={event => setDealerId(event.target.value)}><option value="">Select a Dealer</option>
        {dealers.map(member => <option key={member.user_id} value={member.user_id}>{member.display_name || member.email || member.user_id}</option>)}</select></label>
      {dealerId && <form onSubmit={event => void save(event)}>
        <label>Connected account<select required value={accountId} onChange={event => { setAccountId(event.target.value) }}><option value="">Select an Owner or Sales Rep</option>
          {accounts.map(account => <option key={account.member_id} value={account.member_id} disabled={!account.connected}>{account.name} · {account.role}{account.connected ? '' : ' · Zoho Books not connected'}</option>)}</select></label>
        <label>Dealer-wide discount (%)<input type="number" min="0" max="100" step="0.01" value={discount} onChange={event => setDiscount(event.target.value)} required /></label>
        <label>GST Group<select value={gstId} onChange={event => setGstId(event.target.value)}><option value="">Select GST</option>
          {taxes.map(tax => <option key={tax.tax_id} value={tax.tax_id}>{tax.tax_display_name || tax.tax_name} ({tax.tax_percentage ?? 0}%)</option>)}</select></label>
        <button className="dealer-primary" type="submit" disabled={busy || !accountId}>{busy ? 'Saving…' : 'Save Dealer setup'}</button>
      </form>}
    </section>
  </section>
}
