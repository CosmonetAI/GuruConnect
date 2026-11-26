import { useEffect, useState } from 'react'
import { call } from '../zoho/shared/api.js'
import { GlobalZohoDateFilter, GlobalZohoPager } from '../zoho/ZohoApp.jsx'
import EncodingRepair from '../zoho/EncodingRepair'
import { ProductsPage } from './ProductsPageWithCreate.jsx'
import { ContactsPage } from './ContactsPage.jsx'
import BooksDocumentsPage from './BooksDocumentsPage.jsx'
import '../zoho/styles.css'
import '../zoho/wizard-overrides.css'

function PageFrame({ sectionLabel, title, description, pill, headerActions, children }) {
  return <>
    <EncodingRepair />
    <GlobalZohoDateFilter />
    <GlobalZohoPager />
    <header className="gg-page-header"><div><div className="gg-page-header-kicker">{sectionLabel && <small>{sectionLabel}</small>}</div><h1>{title}</h1>{description && <p>{description}</p>}</div>{(headerActions || pill) && <div className="gg-page-header-actions">{headerActions}{pill && <div className="pill">{pill}</div>}</div>}</header>
    {children}
  </>
}

function ZohoDataPage({ title, description, sectionLabel = 'SALES CATALOGUE', pill, headerActions, children }) {
  const [error, setError] = useState('')
  return <PageFrame sectionLabel={sectionLabel} title={title} description={description} pill={pill} headerActions={headerActions}>
    {error && <div className="error" role="alert">{error}</div>}
    {children(setError)}
  </PageFrame>
}

export function Products() {
  const [showCreate, setShowCreate] = useState(false)
  return <ZohoDataPage title="Product Catalogue" description="Find approved products, pricing, and availability while preparing a customer quotation or order." headerActions={<div className="gg-header-action-group"><button className="primary" type="button" onClick={() => setShowCreate(true)}>+ Add item</button><span className="live-badge">● Catalogue connected</span></div>}>
    {setError => <ProductsPage setError={setError} showCreate={showCreate} setShowCreate={setShowCreate} />}
  </ZohoDataPage>
}

export function Contacts() {
  const [showCreate, setShowCreate] = useState(false)
  return <ZohoDataPage title="Customer Directory" description="Keep customer and supplier details ready for conversations, quotations, orders, and follow-up." headerActions={<button className="primary" type="button" onClick={() => setShowCreate(true)}>Add contact</button>} pill="Connected records">
    {setError => <ContactsPage setError={setError} showCreate={showCreate} setShowCreate={setShowCreate} />}
  </ZohoDataPage>
}

function DocumentPage({ type }) {
  const [showCreate, setShowCreate] = useState(false)
  const labels = { quote: 'Quote', 'sales-order': 'Sales Order', 'purchase-order': 'Purchase Order', invoice: 'Invoice' }
  const label = labels[type]
  const title = label === 'Quote' ? 'Quotes' : `${label}s`
  return <ZohoDataPage title={title} description={documentDescription(type)} headerActions={<button className="primary" type="button" onClick={() => setShowCreate(true)}>Create {label.toLowerCase()}</button>} pill="Connected transactions">
    {setError => <BooksDocumentsPage type={type} setError={setError} showCreate={showCreate} setShowCreate={setShowCreate} />}
  </ZohoDataPage>
}

function documentDescription(type) {
  return { quote: 'Prepare clear, accurate quotations from approved products and customer details.', 'sales-order': 'Turn confirmed customer requirements into traceable sales orders.', 'purchase-order': 'Coordinate supplier purchasing with clear items, quantities, and delivery terms.', invoice: 'Create and track customer invoices with the right commercial terms.' }[type]
}

export function Quotes() { return <DocumentPage type="quote" /> }
export function SalesOrders() { return <DocumentPage type="sales-order" /> }
export function PurchaseOrders() { return <DocumentPage type="purchase-order" /> }
export function Invoices() { return <DocumentPage type="invoice" /> }

export function AIChats() {
  const [chats, setChats] = useState([])
  const [session, setSession] = useState(null)
  const [messages, setMessages] = useState([])
  const [text, setText] = useState('')
  const [identity, setIdentity] = useState({ name: '', phone: '', email: '', phone_extension: '+91' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const valid = Boolean(identity.name.trim() && /^[1-9]\d{9}$/.test(identity.phone) && (!identity.email || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(identity.email)))
  const load = () => call('/chats').then(setChats).catch(e => setError(e.message))
  useEffect(() => { load() }, [])
  const open = id => { setSession(id); call(`/chats/${id}/messages`).then(setMessages).catch(e => setError(e.message)) }
  const send = async () => {
    if (!text.trim() || busy || (!session && !valid)) return
    setBusy(true); setError('')
    try {
      const data = await call('/chat', { method: 'POST', body: JSON.stringify({ message: text, session_id: session, customer: session ? undefined : identity }) })
      setSession(data.session_id); setMessages(items => [...items, { role: 'user', content: text }, { role: 'assistant', content: data.answer }]); setText(''); load()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <PageFrame title="Customer conversations" pill="AI Chats">
    {error && <div className="error" role="alert">{error}</div>}
    <div className="chat-layout"><section className="chat-list"><div className="list-head"><span>Sessions</span><button type="button" onClick={() => { setSession(null); setMessages([]) }}>＋</button></div>{chats.map(chat => <button type="button" className={`chat-row ${session === chat.id ? 'selected' : ''}`} onClick={() => open(chat.id)} key={chat.id}><b>{chat.name}</b><small>{chat.title}</small><em>{new Date(chat.updated_at).toLocaleString()}</em></button>)}</section><section className="conversation"><div className="messages">{!session && !messages.length && <div className="welcome"><span>✦</span><h2>Start a customer conversation</h2><p>Enter a name and valid 10-digit phone number to enable Send.</p><div className="identity"><input placeholder="Customer name" value={identity.name} onChange={e => setIdentity({ ...identity, name: e.target.value })} /><input inputMode="numeric" maxLength="10" placeholder="10-digit phone" value={identity.phone} onChange={e => setIdentity({ ...identity, phone: e.target.value.replace(/\D/g, '').slice(0, 10) })} /><input type="email" placeholder="Email (optional)" value={identity.email} onChange={e => setIdentity({ ...identity, email: e.target.value })} /></div></div>}{messages.map((message, index) => <div className={`bubble ${message.role}`} key={index}>{message.content}</div>)}</div><div className="composer"><textarea value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} placeholder="Ask about products, pricing, availability…" /><button type="button" className="primary" onClick={send} disabled={busy || (!session && !valid)}>{busy ? '…' : 'Send'}</button></div></section></div>
  </PageFrame>
}
