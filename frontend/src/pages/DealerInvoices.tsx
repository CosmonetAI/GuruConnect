import { useState } from 'react'
import { useScopedResource } from '../hooks/useScopedResource'
import { requestStream } from '../services/officeApi'
import { useOffice } from '../contexts/OfficeContext'
import { Link, useNavigate } from 'react-router-dom'
import { Download, Pencil, ReceiptText } from 'lucide-react'
import '../styles/dealer.css'

type Invoice = { invoice_id: string; invoice_number?: string; status?: string; date?: string; total?: number; balance?: number; can_update?: boolean; state?: string }
type Response = { items: Invoice[]; unresolved_count?: number }
const money = (value: number) => new Intl.NumberFormat(undefined, { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(value || 0)

export default function DealerInvoices() {
  const { workspace } = useOffice()
  const navigate = useNavigate()
  const resource = useScopedResource<Response>('/dealer/invoices')
  const [downloadError, setDownloadError] = useState('')
  async function download(id: string) {
    if (!workspace) return
    setDownloadError('')
    try {
      const response = await requestStream(`/dealer/invoices/${encodeURIComponent(id)}/pdf`, workspace.id, {}, workspace.member_id)
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url; link.download = `invoice-${id}.pdf`; link.click()
      URL.revokeObjectURL(url)
    } catch (failure) { setDownloadError(failure instanceof Error ? failure.message : 'Invoice PDF could not be downloaded.') }
  }
  return <section className="dealer-page">
    <header className="dealer-header"><div><span className="dealer-kicker">BILLING</span><h1>Invoices</h1><p>Invoice details and current status are loaded from Zoho Books.</p></div></header>
    {resource.error && <p role="alert" className="dealer-error">{resource.error}</p>}
    {downloadError && <p role="alert" className="dealer-error">{downloadError}</p>}
    {!!resource.data?.unresolved_count && <p role="alert" className="dealer-error">Some invoice actions need reconciliation. Ask your administrator to check Zoho Books before retrying.</p>}
    <section className="dealer-panel">
      {resource.loading ? <p className="dealer-empty">Loading invoices…</p> : (resource.data?.items || []).length ?
        <div className="dealer-table-wrap"><table className="dealer-table"><thead><tr><th>Invoice</th><th>Date</th><th>Status</th><th>Total</th><th>Outstanding</th><th>Actions</th></tr></thead>
          <tbody>{resource.data!.items.map(invoice => <tr key={invoice.invoice_id}>
            <td>{invoice.can_update
              ? <Link className="dealer-invoice-number dealer-invoice-link" to={`/dealer/add-to-cart?invoice=${encodeURIComponent(invoice.invoice_id)}`}>{invoice.invoice_number || invoice.invoice_id}</Link>
              : <span className="dealer-invoice-number">{invoice.invoice_number || invoice.invoice_id}</span>}</td>
            <td>{invoice.date || '—'}</td><td><span className={`dealer-status dealer-status-${String(invoice.status || 'unknown').toLowerCase().replace(/[^a-z]+/g, '-')}`}>{invoice.status || invoice.state || 'Unavailable'}</span></td>
            <td>{money(Number(invoice.total) || 0)}</td><td>{money(Number(invoice.balance) || 0)}</td>
            <td className="dealer-actions"><button title="Download invoice PDF" aria-label={`Download ${invoice.invoice_number || invoice.invoice_id}`} onClick={() => void download(invoice.invoice_id)}><Download size={16} /></button>
              {invoice.can_update && <button title="Update invoice" aria-label={`Update ${invoice.invoice_number || invoice.invoice_id}`} onClick={() => navigate(`/dealer/add-to-cart?invoice=${encodeURIComponent(invoice.invoice_id)}`)}><Pencil size={16} /></button>}</td>
          </tr>)}</tbody></table></div> : <p className="dealer-empty"><ReceiptText size={20} />No confirmed invoices have been generated yet.</p>}
    </section>
  </section>
}
