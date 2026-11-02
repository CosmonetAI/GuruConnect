import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Download, Link2, Mail, MessageCircle, Printer, CheckCircle2 } from 'lucide-react'
import '../styles/invoice-review.css'

// Placeholder data only: this screen is a design preview and calls no service.
const customer = { name: 'Akshat Saxena', phone: '9311854768', email: 'akshat@example.com' }
const lines = [
  { sku: 'FAN-1200', product: 'Ceiling Fan 1200mm', price: 5999, qty: 1, discount: 500 },
  { sku: 'LED-9W-4', product: 'LED Bulb 9W (Pack of 4)', price: 1999, qty: 3, discount: 449 },
]
const invoice = { number: 'INV-DEMO-0001', date: '31 Oct 2026', gstRate: 18 }
const inr = (value: number) => `₹${value.toLocaleString('en-IN')}`
const lineTotal = (line: (typeof lines)[number]) => line.price * line.qty - line.discount
const subtotal = lines.reduce((sum, line) => sum + line.price * line.qty, 0)
const discount = lines.reduce((sum, line) => sum + line.discount, 0)
const gst = Math.round((subtotal - discount) * invoice.gstRate / 100)
const total = subtotal - discount + gst

function buildPdf() {
  const rows = [
    'ConnectCosmos - Invoice (sample)',
    `Invoice ${invoice.number}    Date ${invoice.date}`,
    `Bill to: ${customer.name}, ${customer.phone}, ${customer.email}`,
    '',
    ...lines.map((line, index) => `${index + 1}. ${line.product} (${line.sku})  Rs.${line.price} x ${line.qty}  -Rs.${line.discount}  = Rs.${lineTotal(line)}`),
    '',
    `Subtotal: Rs.${subtotal}`,
    `Discount: -Rs.${discount}`,
    `GST (${invoice.gstRate}%): Rs.${gst}`,
    `Total amount: Rs.${total}`,
  ]
  const escape = (text: string) => text.replace(/[\\()]/g, '\\$&')
  const content = `BT /F1 12 Tf 50 780 Td 20 TL ${rows.map(row => `(${escape(row)}) Tj T*`).join(' ')} ET`
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
    `<< /Length ${content.length} >>\nstream\n${content}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ]
  let pdf = '%PDF-1.4\n'
  const offsets = objects.map((body, index) => { const offset = pdf.length; pdf += `${index + 1} 0 obj\n${body}\nendobj\n`; return offset })
  const xref = pdf.length
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`
  return new Blob([pdf], { type: 'application/pdf' })
}

export default function InvoiceReview() {
  const navigate = useNavigate()
  const [email, setEmail] = useState(customer.email)
  const [subject, setSubject] = useState('Your invoice from ConnectCosmos')
  const [message, setMessage] = useState(`Hi ${customer.name.split(' ')[0]},\n\nPlease find your invoice for the selected items. Let us know if you have any questions.`)
  const [notice, setNotice] = useState('')
  const [emailError, setEmailError] = useState('')

  const download = () => {
    const url = URL.createObjectURL(buildPdf())
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${invoice.number}.pdf`
    anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    setNotice('Sample invoice downloaded as PDF.')
  }
  const sendEmail = () => {
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) { setEmailError('Enter a valid customer email.'); return }
    setEmailError('')
    setNotice(`Sample invoice emailed to ${email.trim()} (demo; nothing was sent).`)
  }
  const copyLink = async () => {
    const link = `${window.location.origin}/invoices/${invoice.number}`
    try { await navigator.clipboard.writeText(link); setNotice('Sample invoice link copied.') } catch { setNotice(`Copy this link manually: ${link}`) }
  }
  const whatsappHref = `https://wa.me/91${customer.phone}?text=${encodeURIComponent(`Hi ${customer.name}, your invoice ${invoice.number} for ${inr(total)} is ready.`)}`

  return <div className="invoice-review">
    <header className="invoice-review-header">
      <button type="button" className="invoice-back" onClick={() => navigate(-1)}><ArrowLeft size={16} />Back to cart</button>
      <div><h1>Invoice ready</h1><p>Review, share or download the invoice for this customer. Sample data shown.</p></div>
    </header>
    {notice && <p role="status" className="invoice-notice"><CheckCircle2 size={16} />{notice}</p>}
    <div className="invoice-review-grid">
      <section className="invoice-card" aria-labelledby="invoice-summary-title">
        <div className="invoice-customer"><span className="invoice-avatar">AS</span><div><strong>{customer.name}</strong><span>{customer.phone}</span><span>{customer.email}</span></div><div className="invoice-meta"><strong>{invoice.number}</strong><span>{invoice.date}</span></div></div>
        <h2 id="invoice-summary-title">Order summary ({lines.length} products)</h2>
        <table>
          <thead><tr><th>#</th><th>Product</th><th>SKU</th><th>Price (₹)</th><th>Qty</th><th>Discount</th><th>Total (₹)</th></tr></thead>
          <tbody>{lines.map((line, index) => <tr key={line.sku}><td>{index + 1}</td><td>{line.product}</td><td>{line.sku}</td><td>{line.price.toLocaleString('en-IN')}</td><td>{line.qty}</td><td>{inr(line.discount)}</td><td>{lineTotal(line).toLocaleString('en-IN')}</td></tr>)}</tbody>
        </table>
        <dl className="invoice-totals">
          <div><dt>Subtotal</dt><dd>{inr(subtotal)}</dd></div>
          <div><dt>Discount (Total)</dt><dd className="invoice-discount">- {inr(discount)}</dd></div>
          <div><dt>GST ({invoice.gstRate}%)</dt><dd>{inr(gst)}</dd></div>
          <div className="invoice-grand"><dt>Total amount</dt><dd>{inr(total)}</dd></div>
        </dl>
      </section>
      <aside className="invoice-card invoice-actions" aria-label="Share invoice">
        <h2>Share &amp; download</h2>
        <div className="invoice-button-row">
          <button type="button" className="invoice-primary" onClick={download}><Download size={16} />Download PDF</button>
          <button type="button" onClick={() => window.print()}><Printer size={16} />Print</button>
          <button type="button" onClick={() => void copyLink()}><Link2 size={16} />Copy link</button>
          <a className="invoice-whatsapp" href={whatsappHref} target="_blank" rel="noreferrer"><MessageCircle size={16} />WhatsApp</a>
        </div>
        <h3>Email invoice</h3>
        <label>To (customer email)<input type="email" value={email} onChange={event => { setEmail(event.target.value); setEmailError('') }} aria-invalid={!!emailError} /></label>
        {emailError && <p role="alert" className="invoice-error">{emailError}</p>}
        <label>Email subject<input value={subject} onChange={event => setSubject(event.target.value)} /></label>
        <label>Email message<textarea rows={5} value={message} onChange={event => setMessage(event.target.value)} /></label>
        <button type="button" className="invoice-primary" onClick={sendEmail}><Mail size={16} />Send invoice</button>
      </aside>
    </div>
  </div>
}
