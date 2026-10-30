import { useEffect, useId, useRef } from 'react'
import { ArrowLeft, Check, Download, FileCheck2, FileText, Mail, Package, Phone, Receipt, Send, ShoppingBag, UserRound, X } from 'lucide-react'
import '../styles/sales-document-modal.css'

export type SalesDocumentType = 'invoice' | 'quote' | 'sales-order' | 'purchase-order'
type CartLine = { item_id?: string; id?: string; name?: string; product?: string; sku?: string; image_url?: string | null; rate?: number | string; quantity?: number | string; discount?: number | string; discount_type?: string; tax_percentage?: number | string | null; tax_id?: string | null }
type Contact = { name?: string; phone?: string; email?: string }
export type SalesDocumentModalProps = {
  open: boolean; contact: Contact | null; customerType: string; cart: CartLine[]; subtotal: number; discount: number
  documentType: SalesDocumentType; setDocumentType: (type: SalesDocumentType) => void
  email: string; setEmail: (email: string) => void; subject: string; setSubject: (subject: string) => void
  message: string; setMessage: (message: string) => void; busy: boolean; demo?: boolean; error?: string
  documentTypes?: SalesDocumentType[]; emailConfigured?: boolean; delivery?: 'email' | 'download'; setDelivery?: (delivery: 'email' | 'download') => void
  createdDocument?: { number?: string; deliveryStatus?: string } | null
  pendingCreation?: boolean
  onClose: () => void; onBack: () => void; onSubmit: (type: SalesDocumentType) => void; onDownload?: () => void
}

const money = (value: number) => `₹${value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const documents = [
  { value: 'invoice', title: 'Send invoice', copy: 'Create an invoice in Zoho Books', icon: Receipt },
  { value: 'quote', title: 'Send quote', copy: 'Create an estimate / quotation', icon: FileText },
  { value: 'sales-order', title: 'Create sales order', copy: 'Confirm the customer’s order', icon: ShoppingBag },
] as const

export default function SalesDocumentModal({ open, contact, customerType, cart, subtotal, discount, documentType, setDocumentType, email, setEmail, subject, setSubject, message, setMessage, busy, demo = false, error = '', documentTypes, emailConfigured = true, delivery = 'email', setDelivery, createdDocument, pendingCreation = false, onDownload, onClose, onBack, onSubmit }: SalesDocumentModalProps) {
  const titleId = useId()
  const dialog = useRef<HTMLElement>(null)
  const close = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    close.current?.focus()
    return () => { document.body.style.overflow = previousOverflow; if (previous?.isConnected) previous.focus() }
  }, [open])
  useEffect(() => { if (open && busy) dialog.current?.focus() }, [open, busy])
  if (!open || !contact) return null
  const vendor = customerType === 'dealer'
  const possible = [...documents, { value: 'purchase-order' as const, title: 'Create purchase order', copy: 'Create a purchase order for this vendor', icon: FileCheck2 }]
  const options = documentTypes ? possible.filter(option => documentTypes.includes(option.value)) : vendor ? possible.filter(option => option.value === 'purchase-order') : documents
  if (!options.length) return null
  const selected = options.find(option => option.value === documentType) || options[0]
  // The demo never calls an external mail service, but it should still exercise
  // the real workflow when the customer has an email address. The local API
  // records the document as sent and keeps the delivery entirely in-session.
  const hasRecipient = Boolean(email.trim())
  const downloadOnly = !hasRecipient || delivery === 'download'
  const deliveryUncertain = createdDocument?.deliveryStatus === 'uncertain'
  const beforeTax = Math.max(0, subtotal - discount)
  const initials = (contact.name || 'Customer').split(/\s+/).slice(0, 2).map(name => name[0]).join('').toUpperCase()

  return <div className="session-modal-backdrop sdm-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose() }}>
    <section className="sdm-dialog" ref={dialog} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-busy={busy} tabIndex={-1} onKeyDown={event => {
      if (event.key === 'Escape') { event.stopPropagation(); if (!busy) onClose() }
      if (event.key !== 'Tab') return
      const targets = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex="0"]')
      if (!targets?.length) { event.preventDefault(); return }
      const first = targets[0], last = targets[targets.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }}>
      <header className="sdm-header"><div className="sdm-title-icon"><FileText size={23} /></div><div><h2 id={titleId}>Create sales document</h2><p>Review the order, choose your document, and send it to your {vendor ? 'vendor' : 'customer'}.</p></div><button ref={close} type="button" className="sdm-close" aria-label="Close document modal" disabled={busy} onClick={onClose}><X size={21} /></button></header>
      <form className="sdm-form" noValidate onSubmit={event => { event.preventDefault(); if (!busy && cart.length) onSubmit(selected.value) }}>
        <div className="sdm-body">
          <section className="sdm-customer" aria-label="Document customer"><div className="sdm-avatar">{initials}</div><div className="sdm-customer-details"><strong>{contact.name || 'Customer'}</strong><div>{contact.phone && <span><Phone size={12} />{contact.phone}</span>}<span><Mail size={12} />{contact.email || 'Email not provided'}</span></div></div>{demo ? <span className="sdm-customer-tag sdm-demo-tag">Demo catalogue</span> : <label className="sdm-account-type">Account type<select aria-label="Account type" value={vendor ? 'dealer' : 'customer'} disabled><option value={vendor ? 'dealer' : 'customer'}>{vendor ? 'Vendor' : 'Customer'}</option></select></label>}</section>
          {demo && <p className="sdm-demo-notice">Demo document: products, prices and stock are sample data. Zoho Books creation and customer email delivery are simulated locally; no external service is called.</p>}
          <section className="sdm-summary" aria-labelledby={`${titleId}-summary`}><div className="sdm-section-title"><h3 id={`${titleId}-summary`}>Order summary <span>({cart.length} {cart.length === 1 ? 'product' : 'products'})</span></h3><button type="button" onClick={onBack} disabled={busy}>Edit cart</button></div><div className="sdm-table-scroll"><table><thead><tr><th>#</th><th>Product</th><th>Unit price</th><th>Qty</th><th>Discount</th><th>Line total</th></tr></thead><tbody>{cart.map((line, index) => {
            const gross = Number(line.rate || 0) * Number(line.quantity || 0)
            const reduction = line.discount_type === 'fixed' ? Number(line.discount || 0) : gross * Number(line.discount || 0) / 100
            return <tr key={line.item_id || line.id || line.sku || index}><td className="sdm-row-number">{index + 1}</td><td><div className="sdm-product"><span className="sdm-product-thumbnail"><Package size={19} aria-hidden="true" />{line.image_url && <img src={line.image_url} alt={`${line.product || line.name || 'Product'} product image`} onLoad={event => { event.currentTarget.dataset.loaded = 'true' }} onError={event => { event.currentTarget.hidden = true }} />}</span><span><strong>{line.product || line.name}</strong><small>SKU: {line.sku || '—'}</small></span></div></td><td>{money(Number(line.rate || 0))}</td><td>{line.quantity}</td><td className="sdm-line-discount">{reduction > 0 ? <><strong>{line.discount_type === 'fixed' ? `− ${money(reduction)}` : `${Number(line.discount)}%`}</strong>{line.discount_type !== 'fixed' && <small>− {money(reduction)}</small>}</> : '—'}</td><td>{money(Math.max(0, gross - reduction))}</td></tr>
          })}</tbody></table></div><dl className="sdm-totals"><div><dt>Subtotal</dt><dd>{money(subtotal)}</dd></div><div className="sdm-discount"><dt>Discount</dt><dd>− {money(discount)}</dd></div><div className="sdm-tax"><dt>Tax</dt><dd>{demo ? 'Not applied to sample document' : 'Calculated in Zoho Books'}</dd></div><div className="sdm-total"><dt>Total before tax</dt><dd>{money(beforeTax)}</dd></div></dl></section>
          <fieldset className="sdm-type-fieldset" disabled={busy || !!createdDocument || pendingCreation}><legend>Select document to create</legend><div className={`sdm-document-types${vendor ? ' is-vendor' : ''}`}>{options.map(option => {
            const Icon = option.icon
            return <label className={`sdm-type-card${selected.value === option.value ? ' is-selected' : ''}`} key={option.value}><input type="radio" name={`${titleId}-type`} checked={selected.value === option.value} onChange={() => setDocumentType(option.value)} /><span className="sdm-radio"><Check size={10} /></span><Icon size={23} className="sdm-document-icon" /><strong>{option.title}</strong><small>{demo ? 'Create in Zoho Books and email the customer' : option.copy}</small></label>
          })}</div></fieldset>
          <fieldset className="sdm-email-fieldset" disabled={busy || pendingCreation}><legend><span><Mail size={17} />Email details</span><small>{downloadOnly ? 'Add an email to send' : demo ? 'Local demo delivery' : emailConfigured ? 'Sent securely via SMTP2GO' : 'Local delivery simulation'}</small></legend><label className="sdm-recipient">To ({vendor ? 'vendor' : 'customer'} email)<span><UserRound size={15} /><input type="email" value={email} onChange={event => setEmail(event.target.value)} placeholder="Enter recipient email" /></span></label>{!email.trim() && <p className="sdm-email-hint"><Download size={13} />No email address? You can create and download the PDF.</p>}{!emailConfigured && email.trim() && <p className="sdm-email-hint">This demo records the send locally; no external email service is called.</p>}<div className="sdm-email-grid"><label>Email subject<input value={subject} onChange={event => setSubject(event.target.value)} maxLength={200} /></label><label>Email message<textarea value={message} onChange={event => setMessage(event.target.value)} rows={3} maxLength={10000} /></label></div>{setDelivery && <label className="sdm-download-choice"><input type="checkbox" checked={delivery === 'download'} onChange={event => setDelivery(event.target.checked ? 'download' : 'email')} />Download PDF instead of emailing</label>}</fieldset>
          {pendingCreation && <p className="sdm-existing-document" role="status">The previous request could not be confirmed. Check it using the same action before changing document details; this avoids creating a duplicate.</p>}
          {createdDocument && <p className="sdm-existing-document" role="status">Document {createdDocument.number || ''} has been created. {deliveryUncertain ? 'Email submission is uncertain; check its status before retrying.' : 'Retry email delivery or download this PDF without creating another document.'}</p>}
          {error && <p className="sdm-error" role="alert">{error}</p>}
        </div>
        <footer className="sdm-footer"><button type="button" className="sdm-back" onClick={onBack} disabled={busy}><ArrowLeft size={15} />Back to cart</button><div>{createdDocument && onDownload && <button type="button" className="sdm-back" disabled={busy} onClick={onDownload}><Download size={15} />Download PDF</button>}<button type="submit" className="sdm-submit" disabled={busy || !cart.length || (deliveryUncertain && !downloadOnly)}>{busy ? createdDocument ? 'Submitting email…' : 'Creating document…' : <>{downloadOnly ? <Download size={16} /> : <Send size={16} />}{pendingCreation ? 'Check document status' : createdDocument ? downloadOnly ? 'Download PDF' : deliveryUncertain ? 'Delivery status uncertain' : 'Retry email' : downloadOnly ? 'Create & download PDF' : 'Create & send'}</>}</button></div></footer>
      </form>
    </section>
  </div>
}
