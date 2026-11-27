import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { FilePlus2 } from 'lucide-react'
import { useScopedResource } from '../hooks/useScopedResource'
import '../styles/dealer.css'

type DealerInvoice = { invoice_id: string; invoice_number?: string; status?: string; total?: number; balance?: number }
type InvoiceResponse = { items: DealerInvoice[] }

const money = (value: number) => new Intl.NumberFormat(undefined, { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(value || 0)

export default function DealerDashboard() {
  const navigate = useNavigate()
  const invoices = useScopedResource<InvoiceResponse>('/dealer/invoices')
  const stats = useMemo(() => {
    const rows = invoices.data?.items || []
    return {
      count: rows.length,
      total: rows.reduce((sum, item) => sum + (Number(item.total) || 0), 0),
      outstanding: rows.filter(item => ['due', 'overdue', 'partially_paid'].includes(String(item.status).toLowerCase()))
        .reduce((sum, item) => sum + (Number(item.balance) || 0), 0),
    }
  }, [invoices.data])
  return <section className="dealer-page">
    <header className="dealer-header"><div><span className="dealer-kicker">DEALER WORKSPACE</span><h1>Dashboard</h1><p>Your invoices and outstanding balance at a glance.</p></div>
      <button className="dealer-primary" onClick={() => navigate('/dealer/add-to-cart')}><FilePlus2 size={17} />Create invoice</button>
    </header>
    {invoices.error && <p className="dealer-error" role="alert">{invoices.error}</p>}
    <div className="dealer-kpis" aria-label="Invoice summary">
      <article><span>Total invoices</span><strong>{invoices.loading ? '—' : stats.count}</strong></article>
      <article><span>Total invoice amount</span><strong>{invoices.loading ? '—' : money(stats.total)}</strong></article>
      <article><span>Outstanding amount</span><strong>{invoices.loading ? '—' : money(stats.outstanding)}</strong></article>
    </div>
  </section>
}
