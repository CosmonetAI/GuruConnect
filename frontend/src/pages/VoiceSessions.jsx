import { useEffect, useState } from 'react'
import { BookUser, Headphones, Mic2, Plus, Search } from 'lucide-react'
import { call } from '../zoho/shared/api.js'
import ConversationRecorder from './Enquiries/ConversationRecorder'
import ConversationTranscript from '../components/ConversationTranscript'
import '../zoho/styles.css'
import '../styles/voice-sessions.css'

export default function VoiceSessions() {
  const [chats, setChats] = useState([]), [contacts, setContacts] = useState([]), [contactSearch, setContactSearch] = useState('')
  const [session, setSession] = useState(null), [messages, setMessages] = useState([]), [identity, setIdentity] = useState({ name: '', phone: '', email: '' }), [conversationId, setConversationId] = useState(null), [selectedContact, setSelectedContact] = useState(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [contactBusy, setContactBusy] = useState(false)
  const load = () => call('/chats').then(setChats).catch(e => setError(e.message))
  const loadContacts = value => { setContactBusy(true); call(`/session-contacts${value ? `?search=${encodeURIComponent(value)}` : ''}`).then(data => setContacts(data.contacts || [])).catch(e => setError(e.message)).finally(() => setContactBusy(false)) }
  useEffect(() => { load(); loadContacts('') }, [])
  const open = id => { setSession(id); call(`/voice-sessions/${id}/history`).then(data => { setMessages(data.messages || []); setConversationId(data.conversation_id || null); setIdentity({ name: data.session?.name || '', phone: data.session?.phone || '', email: data.session?.email || '' }) }).catch(e => setError(e.message)) }
  const prepareSession = async (contact = selectedContact) => {
    const next = contact ? { name: contact.name, phone: contact.phone || '', email: contact.email || '' } : identity
    if (!next.name.trim() || (!next.phone.trim() && !next.email.trim())) { setError('Enter a customer name and phone number or email first.'); return }
    setBusy(true); setError(''); setIdentity(next)
    try { const data = await call('/voice-sessions', { method: 'POST', body: JSON.stringify(next) }); setSession(data.session.id); setMessages(data.messages || []); setConversationId(data.conversation_id || null); await load(); await loadContacts(contactSearch) } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const chooseContact = contact => { setSelectedContact(contact); setIdentity({ name: contact.name, phone: contact.phone || '', email: contact.email || '' }); if (contact.session_id) open(contact.session_id); else void prepareSession(contact) }
  const reset = () => { setSession(null); setMessages([]); setConversationId(null); setSelectedContact(null); setIdentity({ name: '', phone: '', email: '' }); setError('') }
  const linkConversation = async id => { setConversationId(id); if (session) await call(`/voice-sessions/${session}/conversation`, { method: 'POST', body: JSON.stringify({ conversation_id: id }) }) }
  return <section className="voice-sessions-page">
    {error && <div className="error" role="alert">{error}</div>}
    <div className="chat-layout voice-sessions-layout">
      <section className="chat-list">
        <div className="list-head"><span>Contacts & sessions</span><button type="button" onClick={reset}><Plus size={20} /></button></div>
        <div className="contact-search"><Search size={15} /><input placeholder="Search Bigin, Books, enquiries…" value={contactSearch} onChange={e => { setContactSearch(e.target.value); loadContacts(e.target.value) }} /></div>
        <div className="contact-heading"><BookUser size={15} /> Recent contacts {contactBusy && '…'}</div>
        {contacts.map(contact => <button type="button" className={`contact-row ${selectedContact?.id === contact.id ? 'selected' : ''}`} onClick={() => chooseContact(contact)} key={contact.id}><b>{contact.name}</b><small>{contact.phone || contact.email || 'No phone or email'}</small><em>{contact.sources.join(' · ')}</em></button>)}
        <div className="contact-heading session-heading"><Headphones size={15} /> Recent sessions</div>
        {chats.map(chat => <button type="button" className={`chat-row ${session === chat.id ? 'selected' : ''}`} onClick={() => open(chat.id)} key={chat.id}><b>{chat.name}</b><small>{chat.phone || chat.email || 'Voice session'}</small><em>{new Date(chat.updated_at).toLocaleString()}</em></button>)}
      </section>
      <section className="conversation">
        {selectedContact && <div className="contact-record"><div><span>CONTACT RECORD</span><strong>{selectedContact.name}</strong><small>{selectedContact.email || selectedContact.phone || 'No contact details'}</small></div><aside>{selectedContact.sources.map(source => <i key={source}>{source}</i>)}{selectedContact.records && Object.entries(selectedContact.records).map(([source, id]) => <small key={source}>{source} ID: {id}</small>)}</aside></div>}
        <div className="messages">{!session && <div className="welcome"><Headphones size={34} /><h2>Start a voice session</h2><p>Select a contact from Bigin, Books, enquiries, or recent sessions. Audio uses GuruConnect’s managed WAV pipeline for ElevenLabs transcription and English translation.</p><div className="identity"><input placeholder="Customer name" value={identity.name} onChange={e => setIdentity({ ...identity, name: e.target.value })} /><input inputMode="numeric" maxLength="15" placeholder="Phone number" value={identity.phone} onChange={e => setIdentity({ ...identity, phone: e.target.value.replace(/\D/g, '').slice(0, 15) })} /><input type="email" placeholder="Email fallback" value={identity.email} onChange={e => setIdentity({ ...identity, email: e.target.value })} /><button type="button" className="primary voice-open-button" onClick={() => void prepareSession()} disabled={busy}>{busy ? 'Opening…' : 'Open recorder'}</button></div></div>}{session && messages.map((message, index) => <div className={`bubble ${message.role}`} key={index}>{message.content}</div>)}{session && <div className="voice-processing-note"><Mic2 size={15} /><span>Saved audio is sent to ElevenLabs Scribe v2. Non-English source turns are translated to English by the configured OpenAI Luna path.</span></div>}</div>
        {session && <div className="voice-composer"><ConversationRecorder customerName={identity.name} title="Voice session" initialConversationId={conversationId} onConversationChange={linkConversation} onBusyChange={setBusy} />{conversationId && <div className="session-transcript"><ConversationTranscript conversationId={conversationId} /></div>}</div>}
      </section>
    </div>
  </section>
}
