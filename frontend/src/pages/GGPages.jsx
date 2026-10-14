import { useEffect, useState } from 'react'
import { call } from '../zoho/shared/api.js'
import { GlobalZohoDateFilter, GlobalZohoPager } from '../zoho/ZohoApp.jsx'
import EncodingRepair from '../zoho/EncodingRepair'
import { ProductsPage } from '../zoho/pages/ProductsPage.jsx'
import { ContactsPage } from '../zoho/pages/ContactsPage.jsx'
import { QuotesPage } from '../zoho/pages/QuotesPage.jsx'
import { OrdersPage } from '../zoho/pages/OrdersPage.jsx'
import { PurchaseOrdersPage } from '../zoho/pages/PurchaseOrdersPage.jsx'
import { InvoicesPage } from '../zoho/pages/InvoicesPage.jsx'
import '../zoho/styles.css'
import '../zoho/wizard-overrides.css'

function PageFrame({ eyebrow, title, pill, children }) {
  return <>
    <EncodingRepair />
    <GlobalZohoDateFilter />
    <GlobalZohoPager />
    <header className="gg-page-header"><div><small>{eyebrow}</small><h1>{title}</h1></div>{pill && <div className="pill">{pill}</div>}</header>
    {children}
  </>
}

function ZohoDataPage({ title, pill, children }) {
  const [error, setError] = useState('')
  return <PageFrame eyebrow="GURU GROUP / SALES DESK" title={title} pill={pill}>
    {error && <div className="error" role="alert">{error}</div>}
    {children(setError)}
  </PageFrame>
}

export function Products() { return <ZohoDataPage title="My Products" pill="Live Zoho Books search">{setError => <ProductsPage setError={setError} />}</ZohoDataPage> }
export function Contacts() { return <ZohoDataPage title="Contacts" pill="Live Zoho Bigin search">{setError => <ContactsPage setError={setError} />}</ZohoDataPage> }
export function Quotes() { return <ZohoDataPage title="Quotes" pill="Live Zoho Books search">{setError => <QuotesPage setError={setError} />}</ZohoDataPage> }
export function SalesOrders() { return <ZohoDataPage title="Sales Orders" pill="Live Zoho Books search">{setError => <OrdersPage setError={setError} />}</ZohoDataPage> }
export function PurchaseOrders() { return <ZohoDataPage title="Purchase Orders" pill="Live Zoho Books search">{setError => <PurchaseOrdersPage setError={setError} />}</ZohoDataPage> }
export function Invoices() { return <ZohoDataPage title="Invoices" pill="Live Zoho Books search">{setError => <InvoicesPage setError={setError} />}</ZohoDataPage> }

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
  return <PageFrame eyebrow="GURU GROUP / SALES DESK" title="Customer conversations" pill="AI Chats">
    {error && <div className="error" role="alert">{error}</div>}
    <div className="chat-layout"><section className="chat-list"><div className="list-head"><span>Sessions</span><button type="button" onClick={() => { setSession(null); setMessages([]) }}>＋</button></div>{chats.map(chat => <button type="button" className={`chat-row ${session === chat.id ? 'selected' : ''}`} onClick={() => open(chat.id)} key={chat.id}><b>{chat.name}</b><small>{chat.title}</small><em>{new Date(chat.updated_at).toLocaleString()}</em></button>)}</section><section className="conversation"><div className="messages">{!session && !messages.length && <div className="welcome"><span>✦</span><h2>Start a customer conversation</h2><p>Enter a name and valid 10-digit phone number to enable Send.</p><div className="identity"><input placeholder="Customer name" value={identity.name} onChange={e => setIdentity({ ...identity, name: e.target.value })} /><input inputMode="numeric" maxLength="10" placeholder="10-digit phone" value={identity.phone} onChange={e => setIdentity({ ...identity, phone: e.target.value.replace(/\D/g, '').slice(0, 10) })} /><input type="email" placeholder="Email (optional)" value={identity.email} onChange={e => setIdentity({ ...identity, email: e.target.value })} /></div></div>}{messages.map((message, index) => <div className={`bubble ${message.role}`} key={index}>{message.content}</div>)}</div><div className="composer"><textarea value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} placeholder="Ask about products, pricing, availability…" /><button type="button" className="primary" onClick={send} disabled={busy || (!session && !valid)}>{busy ? '…' : 'Send'}</button></div></section></div>
  </PageFrame>
}
