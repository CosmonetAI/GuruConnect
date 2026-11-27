import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Minus, Plus, Search, ShoppingCart, Trash2 } from 'lucide-react'
import { message, request, requestStream, type Workspace } from '../services/officeApi'
import { useOffice } from '../contexts/OfficeContext'
import '../styles/dealer.css'

type Product = { item_id: string; name?: string; item_name?: string; sku?: string; rate?: number; description?: string }
type CartRow = { product: Product; quantity: number }
type Preview = { lines: Array<{item_id: string; name: string; quantity: number; rate: number; subtotal: number; discount: number; gst: number; total: number}>; subtotal: number; discount: number; gst: number; total: number; gst_group?: { tax_name?: string; tax_display_name?: string } }
const money = (value: number) => new Intl.NumberFormat(undefined, { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(value || 0)
const call = <T,>(workspace: Workspace, path: string, method = 'GET', body?: unknown) =>
  request<T>(path, workspace.id, { method, body: body === undefined ? undefined : JSON.stringify(body) }, workspace.member_id)

export default function DealerOrder() {
  const { workspace } = useOffice()
  const [query, setQuery] = useState('')
  const [products, setProducts] = useState<Product[]>([])
  const [cart, setCart] = useState<Record<string, CartRow>>({})
  const [preview, setPreview] = useState<Preview | null>(null)
  const [loadingProducts, setLoadingProducts] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [params, setParams] = useSearchParams()
  const invoiceId = params.get('invoice')
  const [invoiceReady, setInvoiceReady] = useState(false)
  const [invoiceStatus, setInvoiceStatus] = useState('')
  const [invoiceLoading, setInvoiceLoading] = useState(false)
  const [invoiceError, setInvoiceError] = useState('')
  const [invoiceRetry, setInvoiceRetry] = useState(0)
  const [actionKey, setActionKey] = useState(() => crypto.randomUUID())
  const lines = useMemo(() => Object.values(cart).map(row => ({ item_id: row.product.item_id, quantity: row.quantity })), [cart])

  useEffect(() => {
    if (!workspace) return
    let active = true
    const timer = window.setTimeout(() => {
      setLoadingProducts(true)
      void call<{items: Product[]}>(workspace, `/dealer/products?search=${encodeURIComponent(query)}`)
        .then(result => { if (active) setProducts(result.items || []) })
        .catch(failure => { if (active) setError(message(failure)) })
        .finally(() => { if (active) setLoadingProducts(false) })
    }, 180)
    return () => { active = false; window.clearTimeout(timer) }
  }, [workspace?.id, workspace?.member_id, query])

  useEffect(() => {
    setInvoiceReady(false); setInvoiceStatus(''); setInvoiceError(''); setCart({}); setPreview(null)
    if (!invoiceId || !workspace) { setInvoiceLoading(false); return }
    let active = true
    setInvoiceLoading(true); setError(''); setNotice('')
    void call<{invoice: {status?: string; line_items?: Array<{item_id: string; item_name?: string; name?: string; sku?: string; rate?: number; quantity: number}>}}>(workspace, `/dealer/invoices/${encodeURIComponent(invoiceId)}`)
      .then(result => {
        if (!active) return
        const next: Record<string, CartRow> = {}
        for (const [index, line] of (result.invoice?.line_items || []).entries()) {
          if (!line.item_id || !Number.isFinite(Number(line.quantity)) || Number(line.quantity) <= 0) {
            throw new Error('This invoice contains items that cannot be edited in the cart. Contact your administrator.')
          }
          const item = { item_id: line.item_id, name: line.item_name || line.name, sku: line.sku, rate: line.rate }
          next[`invoice-${index}`] = { product: item, quantity: Number(line.quantity) }
        }
        if (!Object.keys(next).length) throw new Error('This invoice has no editable items. Contact your administrator.')
        setCart(next)
        setInvoiceStatus(String(result.invoice.status || '').toLowerCase())
        setInvoiceReady(true)
      }).catch(failure => { if (active) setInvoiceError(message(failure)) })
      .finally(() => { if (active) setInvoiceLoading(false) })
    return () => { active = false }
  }, [invoiceId, workspace?.id, workspace?.member_id, invoiceRetry])

  function add(product: Product) {
    setCart(current => {
      const key = Object.keys(current).find(id => current[id].product.item_id === product.item_id) || `product-${product.item_id}`
      return { ...current, [key]: { product, quantity: (current[key]?.quantity || 0) + 1 } }
    })
  }
  function quantity(id: string, delta: number) {
    setCart(current => {
      const row = current[id]
      if (!row) return current
      const next = row.quantity + delta
      if (next < 1) { const result = {...current}; delete result[id]; return result }
      return {...current, [id]: {...row, quantity: next}}
    })
  }
  async function submitPreview() {
    if (!workspace || !lines.length || (invoiceId && !invoiceReady)) return
    setBusy(true); setError(''); setNotice('')
    try { setPreview(await call<Preview>(workspace, '/dealer/preview', 'POST', { action_key: actionKey, lines })) }
    catch (failure) { setError(message(failure)) }
    finally { setBusy(false) }
  }
  async function confirmOrder() {
    if (!workspace || !preview) return
    setBusy(true); setError(''); setNotice('')
    try {
      const path = invoiceId ? `/dealer/invoices/${encodeURIComponent(invoiceId)}/replace` : '/dealer/invoices'
      const result = await call<{invoice_id: string}>(workspace, path, 'POST', { action_key: actionKey, lines })
      setActionKey(crypto.randomUUID()); setPreview(null); setCart({})
      try {
        const response = await requestStream(`/dealer/invoices/${encodeURIComponent(result.invoice_id)}/pdf`, workspace.id, {}, workspace.member_id)
        const url = URL.createObjectURL(await response.blob())
        const anchor = document.createElement('a'); anchor.href = url; anchor.download = `invoice-${result.invoice_id}.pdf`; anchor.click()
        URL.revokeObjectURL(url)
        setNotice(`Invoice ${invoiceId ? 'updated' : 'created'}. The PDF has been downloaded.`)
      } catch (failure) {
        setNotice(`Invoice ${invoiceId ? 'updated' : 'created'}, but the PDF could not be downloaded: ${message(failure)}. Download it from the Invoices screen.`)
      }
      if (invoiceId) setParams({}, { replace: true })
    } catch (failure) { setError(message(failure)) }
    finally { setBusy(false) }
  }
  return <section className="dealer-page dealer-order-page">
    <header className="dealer-header"><div><span className="dealer-kicker">ORDERING</span><h1>{invoiceId ? 'Update invoice' : 'Add to Cart'}</h1><p>Choose products, review the order, then generate an invoice.</p></div></header>
    {error && !preview && <p role="alert" className="dealer-error">{error}</p>}
    {notice && <p role="status" className="dealer-success">{notice}</p>}
    <div className="dealer-order-grid">
      <section className="dealer-panel dealer-product-panel"><div className="dealer-search"><Search size={17}/><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search products" aria-label="Search products"/></div>
        <div className="dealer-product-list">{loadingProducts ? <p className="dealer-empty">Loading products…</p> : products.map(product => <article className="dealer-product" key={product.item_id}>
          <div><strong>{product.name || product.item_name || 'Product'}</strong><span>{product.sku || product.description || 'Zoho Books item'}</span></div>
          <b>{money(Number(product.rate) || 0)}</b><button disabled={busy || (!!invoiceId && !invoiceReady)} aria-label={`Add ${product.name || product.item_name}`} onClick={() => add(product)}><Plus size={17}/></button>
        </article>)}</div></section>
      <section className="dealer-panel dealer-cart-panel"><div className="dealer-panel-heading"><div><h2><ShoppingCart size={18}/> Your cart</h2><p>{lines.length} product{lines.length === 1 ? '' : 's'}</p></div></div>
        {invoiceLoading && <p role="status">Loading invoice items…</p>}
        {invoiceError && <div role="alert" className="dealer-error">{invoiceError} <button type="button" onClick={() => setInvoiceRetry(value => value + 1)}>Retry</button></div>}
        <div className="dealer-cart-list">{Object.entries(cart).map(([key, row]) => <article className="dealer-cart-row" key={key}>
          <div className="dealer-cart-copy"><strong>{row.product.name || row.product.item_name || 'Product'}</strong><span>{money(Number(row.product.rate) || 0)} each</span></div>
          <div className="dealer-quantity"><button disabled={busy} aria-label="Decrease quantity" onClick={() => quantity(key, -1)}><Minus size={14}/></button><span>{row.quantity}</span><button disabled={busy} aria-label="Increase quantity" onClick={() => quantity(key, 1)}><Plus size={14}/></button></div>
          <button disabled={busy} className="dealer-remove" aria-label="Remove item" onClick={() => quantity(key, -row.quantity)}><Trash2 size={16}/></button>
        </article>)}</div>
        <button className="dealer-primary dealer-submit" disabled={!lines.length || busy || (!!invoiceId && !invoiceReady)} onClick={() => void submitPreview()}>{busy ? 'Please wait…' : 'Submit order'}</button>
      </section>
    </div>
    {preview && <div className="dealer-preview-backdrop"><section className="dealer-preview" role="dialog" aria-modal="true" aria-labelledby="dealer-preview-title">
      <div className="dealer-panel-heading"><div><span className="dealer-kicker">FINAL REVIEW</span><h2 id="dealer-preview-title">Order preview</h2><p>Discount and GST are applied from this Dealer’s settings.</p>{invoiceId && invoiceStatus === 'due' && <p>Updating creates a replacement invoice and voids the original.</p>}</div><button disabled={busy} className="dealer-close" onClick={() => setPreview(null)} aria-label="Close preview">×</button></div>
      {error && <p role="alert" className="dealer-error">{error}</p>}
      <div className="dealer-preview-lines">{preview.lines.map((line, index) => <div key={`${line.item_id}-${index}`}><span><strong>{line.name}</strong><small>{line.quantity} × {money(line.rate)} · Discount {money(line.discount)} · GST {money(line.gst)}</small></span><b>{money(line.total)}</b></div>)}</div>
      <dl className="dealer-totals"><div><dt>Subtotal</dt><dd>{money(preview.subtotal)}</dd></div><div><dt>Discount</dt><dd>− {money(preview.discount)}</dd></div><div><dt>GST{preview.gst_group ? ` · ${preview.gst_group.tax_display_name || preview.gst_group.tax_name}` : ''}</dt><dd>{money(preview.gst)}</dd></div><div className="dealer-total"><dt>Payable amount</dt><dd>{money(preview.total)}</dd></div></dl>
      <button className="dealer-primary dealer-confirm" disabled={busy} onClick={() => void confirmOrder()}>{busy ? 'Generating…' : invoiceId ? 'Update Invoice' : 'Generate Invoice'}</button>
    </section></div>}
  </section>
}
