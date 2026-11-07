import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate, useBlocker } from 'react-router-dom'
import { CheckCircle2 } from 'lucide-react'
import { z } from 'zod'
import { type RegisterWalkinInput, type WalkinAgent } from '../../types/walkin'
import { request } from '../../services/officeApi'
import { attachWalkinRecording, isWalkinDemoMode, listWalkinAgents, registerWalkin } from '../../services/walkinService'
import { createEnquiry } from '../../services/enquiryService'
import { useOffice } from '../../contexts/OfficeContext'
import ConversationRecorder from '../Enquiries/ConversationRecorder'
import VoiceFieldMic from './VoiceFieldMic'
import './Walkins.css'

const registrationSchema = z.object({
  customerName: z.string().trim().min(2, 'Enter the customer’s full name.').max(150, 'Name is too long.'),
  mobileNumber: z.string().trim().regex(/^\+?[0-9().\-\s]{7,20}$/, 'Enter a valid mobile number.').refine((value) => value.replace(/\D/g, '').length >= 7 && value.replace(/\D/g, '').length <= 15, 'Enter a valid mobile number.'),
  email: z.string().trim().min(1, 'Enter the visitor’s email address.').refine((value) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value), 'Enter a valid email address.'),
  companyName: z.string().trim().max(150, 'Company name is too long.'),
  projectName: z.string().trim().max(150, 'Project or site is too long.'),
  customerType: z.enum(['customer', 'vendor']),
  phoneCountryCode: z.string().regex(/^\+\d+(?:\s\d+)?$/, 'Choose a valid country code.'),
})

type FormValues = z.infer<typeof registrationSchema>
type FormErrors = Partial<Record<keyof FormValues, string>>
type WalkinVoicePreview = {
  conversation?: { transcription_state?: string; english_state?: string; final_state?: string }
  transcript?: Array<{ english_text?: string | null; source_text?: string | null }>
  fields?: Partial<Record<'customerName' | 'mobileNumber' | 'email' | 'companyName' | 'projectName' | 'customerType', string>>
}

const initialValues: FormValues = {
  customerName: '',
  mobileNumber: '',
  phoneCountryCode: '+91',
  email: '',
  companyName: '',
  projectName: '',
  customerType: 'customer',
}

const countryCodes = [
  ['+91', 'India'], ['+93', 'Afghanistan'], ['+355', 'Albania'], ['+213', 'Algeria'], ['+1', 'American Samoa'], ['+376', 'Andorra'], ['+244', 'Angola'], ['+1', 'Anguilla'], ['+672', 'Antarctica'], ['+1', 'Antigua and Barbuda'], ['+54', 'Argentina'], ['+374', 'Armenia'], ['+297', 'Aruba'], ['+61', 'Australia'], ['+43', 'Austria'], ['+994', 'Azerbaijan'],
  ['+1', 'Bahamas'], ['+973', 'Bahrain'], ['+880', 'Bangladesh'], ['+1', 'Barbados'], ['+375', 'Belarus'], ['+32', 'Belgium'], ['+501', 'Belize'], ['+229', 'Benin'], ['+1', 'Bermuda'], ['+975', 'Bhutan'], ['+591', 'Bolivia'], ['+387', 'Bosnia and Herzegovina'], ['+267', 'Botswana'], ['+55', 'Brazil'], ['+246', 'British Indian Ocean Territory'], ['+1', 'British Virgin Islands'], ['+673', 'Brunei'], ['+359', 'Bulgaria'], ['+226', 'Burkina Faso'], ['+257', 'Burundi'],
  ['+238', 'Cabo Verde'], ['+855', 'Cambodia'], ['+237', 'Cameroon'], ['+1', 'Canada'], ['+1', 'Cayman Islands'], ['+236', 'Central African Republic'], ['+235', 'Chad'], ['+56', 'Chile'], ['+86', 'China'], ['+61', 'Christmas Island'], ['+61', 'Cocos Islands'], ['+57', 'Colombia'], ['+269', 'Comoros'], ['+242', 'Congo'], ['+243', 'Congo, Democratic Republic'], ['+682', 'Cook Islands'], ['+506', 'Costa Rica'], ['+225', 'Côte d’Ivoire'], ['+385', 'Croatia'], ['+53', 'Cuba'], ['+599', 'Curaçao'], ['+357', 'Cyprus'], ['+420', 'Czechia'],
  ['+45', 'Denmark'], ['+253', 'Djibouti'], ['+1', 'Dominica'], ['+1', 'Dominican Republic'], ['+593', 'Ecuador'], ['+20', 'Egypt'], ['+503', 'El Salvador'], ['+240', 'Equatorial Guinea'], ['+291', 'Eritrea'], ['+372', 'Estonia'], ['+268', 'Eswatini'], ['+251', 'Ethiopia'], ['+500', 'Falkland Islands'], ['+298', 'Faroe Islands'], ['+679', 'Fiji'], ['+358', 'Finland'], ['+33', 'France'], ['+594', 'French Guiana'], ['+689', 'French Polynesia'], ['+241', 'Gabon'], ['+220', 'Gambia'], ['+995', 'Georgia'], ['+49', 'Germany'], ['+233', 'Ghana'], ['+350', 'Gibraltar'], ['+30', 'Greece'], ['+299', 'Greenland'], ['+1', 'Grenada'], ['+590', 'Guadeloupe'], ['+1', 'Guam'], ['+502', 'Guatemala'], ['+44', 'Guernsey'], ['+224', 'Guinea'], ['+245', 'Guinea-Bissau'], ['+592', 'Guyana'], ['+509', 'Haiti'], ['+504', 'Honduras'], ['+852', 'Hong Kong'], ['+36', 'Hungary'], ['+354', 'Iceland'], ['+62', 'Indonesia'], ['+98', 'Iran'], ['+964', 'Iraq'], ['+353', 'Ireland'], ['+44', 'Isle of Man'], ['+972', 'Israel'], ['+39', 'Italy'], ['+1', 'Jamaica'], ['+81', 'Japan'], ['+44', 'Jersey'], ['+962', 'Jordan'], ['+7', 'Kazakhstan'], ['+254', 'Kenya'], ['+686', 'Kiribati'], ['+383', 'Kosovo'], ['+965', 'Kuwait'], ['+996', 'Kyrgyzstan'], ['+856', 'Laos'], ['+371', 'Latvia'], ['+961', 'Lebanon'], ['+266', 'Lesotho'], ['+231', 'Liberia'], ['+218', 'Libya'], ['+423', 'Liechtenstein'], ['+370', 'Lithuania'], ['+352', 'Luxembourg'], ['+853', 'Macao'], ['+261', 'Madagascar'], ['+265', 'Malawi'], ['+60', 'Malaysia'], ['+960', 'Maldives'], ['+223', 'Mali'], ['+356', 'Malta'], ['+692', 'Marshall Islands'], ['+596', 'Martinique'], ['+222', 'Mauritania'], ['+230', 'Mauritius'], ['+262', 'Mayotte'], ['+52', 'Mexico'], ['+691', 'Micronesia'], ['+373', 'Moldova'], ['+377', 'Monaco'], ['+976', 'Mongolia'], ['+382', 'Montenegro'], ['+1', 'Montserrat'], ['+212', 'Morocco'], ['+258', 'Mozambique'], ['+95', 'Myanmar'], ['+264', 'Namibia'], ['+674', 'Nauru'], ['+977', 'Nepal'], ['+31', 'Netherlands'], ['+687', 'New Caledonia'], ['+64', 'New Zealand'], ['+505', 'Nicaragua'], ['+227', 'Niger'], ['+234', 'Nigeria'], ['+683', 'Niue'], ['+672', 'Norfolk Island'], ['+850', 'North Korea'], ['+389', 'North Macedonia'], ['+1', 'Northern Mariana Islands'], ['+47', 'Norway'], ['+968', 'Oman'], ['+92', 'Pakistan'], ['+680', 'Palau'], ['+970', 'Palestine'], ['+507', 'Panama'], ['+675', 'Papua New Guinea'], ['+595', 'Paraguay'], ['+51', 'Peru'], ['+63', 'Philippines'], ['+48', 'Poland'], ['+351', 'Portugal'], ['+1', 'Puerto Rico'], ['+974', 'Qatar'], ['+262', 'Réunion'], ['+40', 'Romania'], ['+7', 'Russia'], ['+250', 'Rwanda'], ['+590', 'Saint Barthélemy'], ['+290', 'Saint Helena'], ['+1', 'Saint Kitts and Nevis'], ['+1', 'Saint Lucia'], ['+590', 'Saint Martin'], ['+508', 'Saint Pierre and Miquelon'], ['+1', 'Saint Vincent and the Grenadines'], ['+685', 'Samoa'], ['+378', 'San Marino'], ['+239', 'São Tomé and Príncipe'], ['+966', 'Saudi Arabia'], ['+221', 'Senegal'], ['+381', 'Serbia'], ['+248', 'Seychelles'], ['+232', 'Sierra Leone'], ['+65', 'Singapore'], ['+1 721', 'Sint Maarten'], ['+421', 'Slovakia'], ['+386', 'Slovenia'], ['+677', 'Solomon Islands'], ['+252', 'Somalia'], ['+27', 'South Africa'], ['+82', 'South Korea'], ['+211', 'South Sudan'], ['+34', 'Spain'], ['+94', 'Sri Lanka'], ['+249', 'Sudan'], ['+597', 'Suriname'], ['+47', 'Svalbard and Jan Mayen'], ['+46', 'Sweden'], ['+41', 'Switzerland'], ['+963', 'Syria'], ['+886', 'Taiwan'], ['+992', 'Tajikistan'], ['+255', 'Tanzania'], ['+66', 'Thailand'], ['+670', 'Timor-Leste'], ['+228', 'Togo'], ['+690', 'Tokelau'], ['+676', 'Tonga'], ['+1', 'Trinidad and Tobago'], ['+216', 'Tunisia'], ['+90', 'Turkey'], ['+993', 'Turkmenistan'], ['+1', 'Turks and Caicos Islands'], ['+688', 'Tuvalu'], ['+256', 'Uganda'], ['+380', 'Ukraine'], ['+971', 'United Arab Emirates'], ['+44', 'United Kingdom'], ['+1', 'United States'], ['+598', 'Uruguay'], ['+998', 'Uzbekistan'], ['+678', 'Vanuatu'], ['+379', 'Vatican City'], ['+58', 'Venezuela'], ['+84', 'Vietnam'], ['+1', 'U.S. Virgin Islands'], ['+681', 'Wallis and Futuna'], ['+212', 'Western Sahara'], ['+967', 'Yemen'], ['+260', 'Zambia'], ['+263', 'Zimbabwe'],
] as const

const rotatingPrompts = {
  name: ['Ask: What is your name?', 'ಕೇಳಿ: ನಿಮ್ಮ ಹೆಸರು ಏನು?', 'पूछें: आपका नाम क्या है?'],
  phone: ['Ask: What is your phone number?', 'ಕೇಳಿ: ನಿಮ್ಮ ಫೋನ್ ಸಂಖ್ಯೆ ಏನು?', 'पूछें: आपका फ़ोन नंबर क्या है?'],
  email: ['Ask: What is your email address?', 'ಕೇಳಿ: ನಿಮ್ಮ ಇಮೇಲ್ ವಿಳಾಸ ಏನು?', 'पूछें: आपका ईमेल पता क्या है?'],
  organisation: ['Ask: What is your organisation name?', 'ಕೇಳಿ: ನಿಮ್ಮ ಸಂಸ್ಥೆಯ ಹೆಸರು ಏನು?', 'पूछें: आपके संगठन का नाम क्या है?'],
  requirement: ['Ask: What do you need?', 'ಕೇಳಿ: ನಿಮಗೆ ಏನು ಬೇಕು?', 'पूछें: आपको क्या चाहिए?'],
  customerType: ['Ask: Are you here to buy or sell?', 'ಕೇಳಿ: ನೀವು ಖರೀದಿಸಲು ಬಂದಿದ್ದೀರಾ ಅಥವಾ ಮಾರಾಟ ಮಾಡಲು?', 'पूछें: आप खरीदने आए हैं या बेचने?'],
} as const

function RotatingPrompt({ prompt }: { prompt: keyof typeof rotatingPrompts }) {
  const [index, setIndex] = useState(0)
  useEffect(() => {
    const timer = window.setInterval(() => setIndex((current) => (current + 1) % rotatingPrompts[prompt].length), 3200)
    return () => window.clearInterval(timer)
  }, [prompt])
  return <span className="walkin-rotating-prompt" key={`${prompt}-${index}`}>({rotatingPrompts[prompt][index]})</span>
}

export default function WalkinRegistration({ enquiryMode = false }: { enquiryMode?: boolean }) {
  const { user, workspace, setCaptureBusy } = useOffice()
  const navigate = useNavigate()
  const [form, setForm] = useState(initialValues)
  const [errors, setErrors] = useState<FormErrors>({})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveKey, setSaveKey] = useState(() => crypto.randomUUID())
  const [voiceBusy, setVoiceBusy] = useState(false)
  const [agents, setAgents] = useState<WalkinAgent[]>([])
  const [selectedAgentId, setSelectedAgentId] = useState('')
  const [agentMenuOpen, setAgentMenuOpen] = useState(false)
  const agentPickerRef = useRef<HTMLDivElement | null>(null)
  const voiceConversation = useRef<string | null>(null)
  const allowLeave = useRef(false)
  const [voiceId, setVoiceId] = useState<string | null>(null)
  const [voicePreview, setVoicePreview] = useState<WalkinVoicePreview | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [pendingValues, setPendingValues] = useState<FormValues | null>(null)
  const blocker = useBlocker(() => !allowLeave.current && (voiceBusy || saving))
  useEffect(() => { setCaptureBusy?.(voiceBusy || saving); return () => setCaptureBusy?.(false) }, [voiceBusy, saving, setCaptureBusy])
  useEffect(() => {
    if (!workspace) return
    let active = true
    const loadAgents = () => void listWalkinAgents(workspace.id).then((items) => {
      if (active) setAgents([...items].sort((left, right) => (left.role === 'owner' ? 1 : 0) - (right.role === 'owner' ? 1 : 0) || presenceRank(left.presenceStatus) - presenceRank(right.presenceStatus) || availabilityRank(left.availability) - availabilityRank(right.availability) || (left.openTickets ?? 0) - (right.openTickets ?? 0) || left.name.localeCompare(right.name)))
    }).catch(() => { if (active) setAgents([]) })
    loadAgents()
    const timer = window.setInterval(loadAgents, 15_000)
    return () => { active = false; window.clearInterval(timer) }
  }, [workspace])
  useEffect(() => { if (blocker.state === 'blocked') { setError('End the visitor recording before leaving this page.'); blocker.reset() } }, [blocker])
  useEffect(() => {
    if (!voiceId || !workspace) { setVoicePreview(null); return }
    let cancelled = false
    let timer: number | undefined
    const refresh = async () => {
      try {
        const result = await request<WalkinVoicePreview>(`/conversations/${encodeURIComponent(voiceId)}/walkin-preview`, workspace.id, {}, workspace.member_id)
        if (!cancelled) {
          setVoicePreview(result)
          const fields = result.fields || {}
          setForm(previous => {
            const next = { ...previous }
            for (const key of ['customerName', 'mobileNumber', 'email', 'companyName', 'projectName'] as const) {
              const value = fields[key]
              if (value && !next[key]) next[key] = value as never
            }
            if (fields.customerType === 'vendor' && next.customerType !== 'vendor') next.customerType = 'vendor'
            return next
          })
        }
      } catch { /* processing is asynchronous; the recorder remains usable */ }
      if (!cancelled) timer = window.setTimeout(() => void refresh(), 2500)
    }
    void refresh()
    return () => { cancelled = true; if (timer) window.clearTimeout(timer) }
  }, [voiceId, workspace])
  useEffect(() => {
    if (!agentMenuOpen) return
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (agentPickerRef.current && !agentPickerRef.current.contains(event.target as Node)) setAgentMenuOpen(false)
    }
    document.addEventListener('mousedown', closeOnOutsideClick)
    return () => document.removeEventListener('mousedown', closeOnOutsideClick)
  }, [agentMenuOpen])

  const update = <K extends keyof FormValues>(field: K, value: FormValues[K]) => {
    setForm((previous) => ({ ...previous, [field]: value }))
    setSaveKey(crypto.randomUUID())
    setErrors((previous) => ({ ...previous, [field]: undefined }))
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!workspace || saving) return
    setError('')
    if (voiceBusy) { setError('End the visitor recording before registering the visitor.'); return }
    const parsed = registrationSchema.safeParse(form)
    if (!parsed.success) {
      const next: FormErrors = {}
      for (const issue of parsed.error.issues) {
        const field = issue.path[0] as keyof FormValues | undefined
        if (field && !next[field]) next[field] = issue.message
      }
      setErrors(next)
      document.querySelector('.walkin-field.has-error input, .walkin-field.has-error select, .walkin-field.has-error textarea')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    setPendingValues(parsed.data)
    setPreviewOpen(true)
  }

  const confirmRegistration = async () => {
    if (!workspace || !pendingValues || saving) return
    setPreviewOpen(false)
    setSaving(true)
    try {
      const values = pendingValues
      const input: RegisterWalkinInput = {
      customerName: values.customerName,
      mobileNumber: `${values.phoneCountryCode}${values.mobileNumber}`,
      email: values.email || '',
      companyName: values.companyName || '',
      projectName: values.projectName || '',
      customerType: values.customerType || 'customer',
      assignedAgentId: selectedAgentId || undefined,
      // Provide defaults for fields not collected on the registration form
      purpose: 'Other',
      purposeDescription: '',
      productOrService: '',
      department: '',
      requestedEmployee: '',
      appointmentStatus: 'Walk-in',
      appointmentReference: '',
      internalNotes: '',
      }
      if (enquiryMode) {
        const assignedExecutive = user?.user_metadata?.display_name || user?.email || 'Salesperson'
        const created = await createEnquiry(workspace.id, {
          customerName: values.customerName,
          phone: input.mobileNumber,
          email: values.email || undefined,
          channel: 'Retail Walk-in',
          branch: 'Walk-in',
          assignedExecutive,
          product: values.projectName || '',
          quantity: 1,
          unit: 'nos',
          notes: '',
          customerNotes: '',
          salespersonNotes: '',
          requirementsSummary: values.projectName || '',
          customerType: 'Retail',
          company: values.companyName || '',
          projectName: values.projectName || '',
          preferredContactMethod: '',
          productRequirements: [],
          followUpDate: '',
          followUpOwner: assignedExecutive,
          priority: 'Normal',
        }, saveKey, workspace.member_id)
        allowLeave.current = true
        navigate(`/enquiries/${encodeURIComponent(created.id)}`, { replace: true })
        return
      }
      const visit = await registerWalkin(workspace.id, input, saveKey)
      const recordingId = voiceConversation.current
      if (recordingId) {
        let linked = false
        try { await attachWalkinRecording(workspace.id, visit.id, recordingId); linked = !isWalkinDemoMode() } catch { linked = false }
        if (!linked) { try { sessionStorage.setItem(`guruconnect:walkin-audio:${user?.id}:${workspace.id}:${visit.id}`, recordingId) } catch { /* best effort */ } }
      }
      allowLeave.current = true
      navigate('/walkins', { replace: true })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Visitor could not be registered.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="walkin-page">
      <header className="walkin-header">
        <div>
          <h1>Register a visitor</h1>
          <p>Capture just enough to direct the customer to the right person.</p>
        </div>
      </header>
      {error && <div className="walkin-alert error" role="alert">{error}</div>}

      <form className="walkin-card walkin-form" onSubmit={(event) => void submit(event)} noValidate>
        <section>
          <div className="walkin-section-heading">
            <span>01</span>
            <div><h2>Customer information</h2><p>Required fields are marked with an asterisk.</p></div>
            <div className="walkin-registration-recorder">
              <ConversationRecorder customerName={form.customerName} customerPhone={`${form.phoneCountryCode}${form.mobileNumber}`} customerEmail={form.email} title="Walk-in registration" onConversationChange={id => { voiceConversation.current = id; setVoiceId(id) }} onBusyChange={setVoiceBusy} chunkSeconds={8} audioBucket="walkin-audio" compact registrationOnly />
            </div>
          </div>
          {voiceId && <div className="walkin-live-results" aria-live="polite">
            <div className="walkin-live-results-header"><strong>Live registration details</strong><span>{voicePreview?.conversation?.english_state === 'complete' ? 'Translated' : 'Listening and extracting…'}</span></div>
            {voicePreview?.transcript?.slice(-1).map((turn, index) => <p key={`${turn.english_text || turn.source_text || 'turn'}-${index}`}>{turn.english_text || turn.source_text || 'Transcribing the latest answer…'}</p>)}
            {voicePreview?.fields && Object.keys(voicePreview.fields).length > 0 && <small>Form fields are being filled from the English translation as answers arrive.</small>}
          </div>}
          <div className="walkin-form-grid">
            <Field label={<>{form.customerType === 'vendor' ? 'Vendor Name' : 'Customer Full Name'} <RotatingPrompt prompt="name" /></>} name="customerName" required error={errors.customerName}><div className="walkin-voice-input"><input disabled={!!voiceId || voiceBusy} id="customerName" autoComplete="name" value={form.customerName} onChange={(event) => update('customerName', event.target.value)} placeholder={form.customerType === 'vendor' ? 'Vendor name' : 'Customer name'} /><VoiceFieldMic field="name" workspaceId={workspace?.id || ''} memberId={workspace?.member_id} disabled={!!voiceId || voiceBusy || !workspace} onValue={value => update('customerName', value)} /></div></Field>
            <Field label={<>{form.customerType === 'vendor' ? 'Vendor Phone No.' : 'Customer Phone No.'} <RotatingPrompt prompt="phone" /></>} name="mobileNumber" required error={errors.mobileNumber}><div className="walkin-phone-input"><select aria-label="Country code" value={form.phoneCountryCode} onChange={(event) => update('phoneCountryCode', event.target.value as FormValues['phoneCountryCode'])}>{countryCodes.map(([code, country], index) => <option key={`${code}-${country}-${index}`} value={code}>{code} {country}</option>)}</select><div className="walkin-voice-input"><input disabled={!!voiceId || voiceBusy} id="mobileNumber" type="tel" autoComplete="tel" inputMode="tel" value={form.mobileNumber} onChange={(event) => update('mobileNumber', event.target.value)} placeholder="Phone number" /><VoiceFieldMic field="phone" workspaceId={workspace?.id || ''} memberId={workspace?.member_id} disabled={!!voiceId || voiceBusy || !workspace} onValue={value => update('mobileNumber', value.replace(/\D/g, '').slice(-15))} /></div></div></Field>
            <Field label={<>{form.customerType === 'vendor' ? 'Vendor Email Address' : 'Customer Email Address'} <RotatingPrompt prompt="email" /></>} name="email" required error={errors.email}><div className="walkin-voice-input"><input disabled={!!voiceId || voiceBusy} id="email" type="email" autoComplete="email" value={form.email} onChange={(event) => update('email', event.target.value)} placeholder={form.customerType === 'vendor' ? 'vendor@example.com' : 'customer@example.com'} required /><VoiceFieldMic field="email" workspaceId={workspace?.id || ''} memberId={workspace?.member_id} disabled={!!voiceId || voiceBusy || !workspace} onValue={value => update('email', value)} /></div></Field>
            <Field label={form.customerType === 'vendor' ? 'Vendor Source type' : 'Customer Source type'} name="source"><div className="walkin-source-input" aria-disabled="true"><input id="source" value="Retail walk-in" disabled readOnly tabIndex={-1} aria-readonly="true" /></div></Field>
            <Field label="Assign executive" name="assignedExecutive" hint={enquiryMode ? 'Assigned to the salesperson who opened this enquiry.' : 'Handled automatically based on availability and current workload. The list is for visibility only.'}>{enquiryMode ? <input id="assignedExecutive" value={user?.user_metadata?.display_name || user?.email || 'Salesperson'} readOnly /> : <div className="walkin-agent-picker" ref={agentPickerRef}><button type="button" className="walkin-agent-picker-trigger" aria-haspopup="listbox" aria-expanded={agentMenuOpen} onClick={() => setAgentMenuOpen(open => !open)}><span>{selectedAgentId ? agents.find(agent => agent.id === selectedAgentId)?.name : 'Automatic assignment'}</span><span className={`walkin-agent-picker-chevron${agentMenuOpen ? ' is-open' : ''}`} aria-hidden="true" /></button>{agentMenuOpen && <div className="walkin-agent-picker-menu" role="listbox"><button type="button" className={`walkin-agent-option${selectedAgentId === '' ? ' is-selected' : ''}`} onClick={() => { setSelectedAgentId(''); setAgentMenuOpen(false) }}><span className="walkin-agent-option-main"><strong>Automatic assignment</strong><small>Recommended · system chooses the best available executive</small></span><span className="walkin-agent-status recommended">Auto</span></button>{agents.map((agent) => <button type="button" role="option" aria-selected={selectedAgentId === agent.id} key={agent.id} className={`walkin-agent-option${selectedAgentId === agent.id ? ' is-selected' : ''}`} onClick={() => { setSelectedAgentId(agent.id); setAgentMenuOpen(false) }}><span className="walkin-agent-option-main"><strong>{agent.name}</strong><small>{agent.availability} · {presenceLabel(agent.presenceStatus)} · {agent.openTickets ?? 0} open ticket{agent.openTickets === 1 ? '' : 's'}</small></span><span className={`walkin-agent-status ${agent.presenceStatus === 'active' ? 'active' : agent.presenceStatus === 'inactive' ? 'inactive' : 'away'}`}><i />{presenceLabel(agent.presenceStatus)}</span></button>)}</div>}</div>}</Field>
            <Field label={<>Customer type <RotatingPrompt prompt="customerType" /></>} name="customerType"><div className="walkin-voice-input"><select id="customerType" value={form.customerType} onChange={(event) => update('customerType', event.target.value as FormValues['customerType'])}><option value="customer">Retail customer</option><option value="vendor">Vendor</option></select><VoiceFieldMic field="customerType" workspaceId={workspace?.id || ''} memberId={workspace?.member_id} disabled={!!voiceId || voiceBusy || !workspace} onValue={value => update('customerType', value === 'vendor' ? 'vendor' : 'customer')} /></div></Field>
            <Field label={<>{form.customerType === 'vendor' ? 'Vendor Organisation Name' : 'Customer Organisation Name'} <RotatingPrompt prompt="organisation" /></>} name="companyName" error={errors.companyName}><div className="walkin-voice-input"><input id="companyName" autoComplete="organization" value={form.companyName} onChange={(event) => update('companyName', event.target.value)} placeholder="Not provided" /><VoiceFieldMic field="organisation" workspaceId={workspace?.id || ''} memberId={workspace?.member_id} disabled={!!voiceId || voiceBusy || !workspace} onValue={value => update('companyName', value)} /></div></Field>
            <Field label={<>{form.customerType === 'vendor' ? 'Vendor Purchase Requirement' : 'Customer Project Requirement'} <RotatingPrompt prompt="requirement" /></>} name="projectName" error={errors.projectName}><div className="walkin-voice-input"><input id="projectName" value={form.projectName} onChange={(event) => update('projectName', event.target.value)} placeholder="Not provided" /><VoiceFieldMic field="requirement" workspaceId={workspace?.id || ''} memberId={workspace?.member_id} disabled={!!voiceId || voiceBusy || !workspace} onValue={value => update('projectName', value)} /></div></Field>
          </div>
        </section>

        {error && <div className="walkin-alert error" role="alert">{error}</div>}
        <div className="walkin-form-actions"><button type="button" className="walkin-button secondary" disabled={voiceBusy || saving} onClick={() => navigate('/walkins')}>Cancel</button><button className="walkin-button primary" type="submit" disabled={saving || !workspace || voiceBusy}>{saving ? 'Saving visitor…' : <><CheckCircle2 size={17} /> Register visitor</>}</button></div>
      </form>
      {previewOpen && pendingValues && <div className="walkin-preview-backdrop" role="presentation">
        <section className="walkin-preview-modal" role="dialog" aria-modal="true" aria-labelledby="walkin-preview-title">
          <div className="walkin-preview-header"><div><span className="page-kicker">FINAL REVIEW</span><h2 id="walkin-preview-title">Confirm visitor details</h2><p>Review the captured details before creating the visitor.</p></div></div>
          <div className="walkin-preview-grid">
            <PreviewValue label={pendingValues.customerType === 'vendor' ? 'Vendor name' : 'Customer name'} value={pendingValues.customerName} />
            <PreviewValue label={pendingValues.customerType === 'vendor' ? 'Vendor phone' : 'Customer phone'} value={`${pendingValues.phoneCountryCode} ${pendingValues.mobileNumber}`} />
            <PreviewValue label={pendingValues.customerType === 'vendor' ? 'Vendor email' : 'Customer email'} value={pendingValues.email} />
            <PreviewValue label="Customer type" value={pendingValues.customerType === 'vendor' ? 'Vendor' : 'Retail customer'} />
            <PreviewValue label={pendingValues.customerType === 'vendor' ? 'Vendor organisation' : 'Customer organisation'} value={pendingValues.companyName || 'Not provided'} />
            <PreviewValue label={pendingValues.customerType === 'vendor' ? 'Purchase requirement' : 'Project requirement'} value={pendingValues.projectName || 'Not provided'} />
            <PreviewValue label="Assignment" value={selectedAgentId ? agents.find(agent => agent.id === selectedAgentId)?.name || 'Selected executive' : 'Automatic assignment'} />
            <PreviewValue label="Source" value="Retail walk-in" />
          </div>
          <div className="walkin-preview-actions"><button type="button" className="walkin-button secondary" disabled={saving} onClick={() => { setPreviewOpen(false); setPendingValues(null) }}>Back to form</button><button type="button" className="walkin-button primary" disabled={saving} onClick={() => void confirmRegistration()}>{saving ? 'Creating visitor…' : 'Confirm and register'}</button></div>
        </section>
      </div>}
    </div>
  )
}

function PreviewValue({ label, value }: { label: string; value: string }) {
  return <div className="walkin-preview-value"><small>{label}</small><strong>{value}</strong></div>
}

function availabilityRank(value: WalkinAgent['availability']) {
  return value === 'Available' ? 0 : value === 'Busy' ? 1 : 2
}

function presenceRank(value: WalkinAgent['presenceStatus']) {
  return value === 'active' ? 0 : value === 'inactive' ? 2 : 1
}

function presenceLabel(value: WalkinAgent['presenceStatus']) {
  return value === 'active' ? 'Active now' : value === 'on_leave' ? 'On leave' : value === 'be_right_back' ? 'Be right back' : 'Inactive'
}

function Field({
  label,
  name,
  required,
  error,
  hint,
  wide,
  ai,
  children,
}: {
  label: React.ReactNode
  name: string
  required?: boolean
  error?: string
  hint?: string
  wide?: boolean
  ai?: boolean
  children: React.ReactNode
}) {
  return (
    <div className={`walkin-field${error ? ' has-error' : ''}${wide ? ' wide' : ''}`}>
      <label htmlFor={name}>{label}{required && <span aria-label="required"> *</span>}{ai && <span className="walkin-ai-badge"> AI populated</span>}</label>
      {children}
      {error && <span className="walkin-field-error" role="alert">{error}</span>}
      {!error && hint && <span className="walkin-field-hint">{hint}</span>}
    </div>
  )
}
