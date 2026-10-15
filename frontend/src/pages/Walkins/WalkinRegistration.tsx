import { useCallback, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, ClipboardPlus } from 'lucide-react'
import { z } from 'zod'
import { type RegisterWalkinInput, walkinPurposes } from '../../types/walkin'
import { attachWalkinRecording, isWalkinDemoMode, registerWalkin } from '../../services/walkinService'
import { useOffice } from '../../contexts/OfficeContext'
import WalkinVoiceAssistant from './WalkinVoiceAssistant'
import { VISITOR_EXTRACTION_CONFIDENCE_THRESHOLD, type VisitorField, type VisitorFieldSuggestion } from './visitorExtraction'
import './Walkins.css'

const registrationSchema = z.object({
  customerName: z.string().trim().min(2, 'Enter the customer’s full name.').max(150, 'Name is too long.'),
  mobileNumber: z.string().trim().regex(/^\+?[0-9().\-\s]{7,20}$/, 'Enter a valid mobile number.').refine((value) => value.replace(/\D/g, '').length >= 7 && value.replace(/\D/g, '').length <= 15, 'Enter a valid mobile number.'),
  email: z.string().trim().refine((value) => !value || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value), 'Enter a valid email address.'),
  companyName: z.string().trim().max(150, 'Company name is too long.'),
  purpose: z.enum(walkinPurposes),
  purposeDescription: z.string().trim().max(2000, 'Visit description is too long.'),
  department: z.string().trim().max(150, 'Department is too long.'),
  requestedEmployee: z.string().trim().max(150, 'Employee name is too long.'),
  internalNotes: z.string().trim().max(2000, 'Reception notes are too long.'),
})

type FormValues = z.infer<typeof registrationSchema>
type FormErrors = Partial<Record<keyof FormValues, string>>

const initialValues: FormValues = {
  customerName: '',
  mobileNumber: '',
  email: '',
  companyName: '',
  purpose: 'Other',
  purposeDescription: '',
  department: '',
  requestedEmployee: '',
  internalNotes: '',
}

export default function WalkinRegistration() {
  const { workspace } = useOffice()
  const navigate = useNavigate()
  const [form, setForm] = useState(initialValues)
  const formRef = useRef(initialValues)
  const [errors, setErrors] = useState<FormErrors>({})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [voiceBusy, setVoiceBusy] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [autoFields, setAutoFields] = useState<Set<VisitorField>>(() => new Set())
  const autoFieldsRef = useRef(new Set<VisitorField>())
  const manualFields = useRef(new Set<keyof FormValues>())
  const [saveKey, setSaveKey] = useState(() => crypto.randomUUID())

  const update = <K extends keyof FormValues>(field: K, value: FormValues[K]) => {
    formRef.current = { ...formRef.current, [field]: value }
    manualFields.current.add(field)
    autoFieldsRef.current.delete(field as VisitorField)
    setAutoFields(new Set(autoFieldsRef.current))
    setForm((previous) => ({ ...previous, [field]: value }))
    setSaveKey(crypto.randomUUID())
    setErrors((previous) => ({ ...previous, [field]: undefined }))
  }

  const applySuggestions = useCallback((suggestions: Partial<Record<VisitorField, VisitorFieldSuggestion>>) => {
    const next = { ...formRef.current }
    const added = new Set(autoFieldsRef.current)
    let changed = false
    for (const [field, suggestion] of Object.entries(suggestions) as [VisitorField, VisitorFieldSuggestion][]) {
      if (suggestion.conflict || suggestion.confidence < VISITOR_EXTRACTION_CONFIDENCE_THRESHOLD || manualFields.current.has(field)) continue
      if (next[field] === suggestion.value) continue
      next[field] = suggestion.value as never
      added.add(field)
      changed = true
    }
    if (!changed) return
    formRef.current = next
    autoFieldsRef.current = added
    setForm(next)
    setAutoFields(new Set(added))
    setSaveKey(crypto.randomUUID())
    setErrors((previous) => {
      const updated = { ...previous }
      for (const field of added) delete updated[field]
      return updated
    })
  }, [])

  const acceptSuggestion = useCallback((field: VisitorField, suggestion: VisitorFieldSuggestion) => {
    manualFields.current.add(field)
    autoFieldsRef.current.delete(field)
    const next = { ...formRef.current, [field]: suggestion.value } as FormValues
    formRef.current = next
    setForm(next)
    setAutoFields(new Set(autoFieldsRef.current))
    setSaveKey(crypto.randomUUID())
    setErrors((previous) => ({ ...previous, [field]: undefined }))
  }, [])

  const updateConversation = useCallback((id: string | null) => setConversationId(id), [])
  const updateVoiceBusy = useCallback((busy: boolean) => setVoiceBusy(busy), [])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!workspace || saving || voiceBusy) return
    setError('')
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
    setSaving(true)
    try {
      const values = parsed.data
      const input: RegisterWalkinInput = {
      customerName: values.customerName,
      mobileNumber: values.mobileNumber,
      email: values.email || '',
      companyName: values.companyName || '',
      purpose: values.purpose,
      purposeDescription: values.purposeDescription,
      productOrService: '',
      department: values.department,
      requestedEmployee: values.requestedEmployee,
      appointmentStatus: 'Walk-in',
      appointmentReference: '',
      internalNotes: values.internalNotes,
      }
      const visit = await registerWalkin(workspace.id, input, saveKey)
      if (conversationId) {
        try {
          await attachWalkinRecording(workspace.id, visit.id, conversationId)
        } catch (reason) {
          navigate(`/walkins/${visit.id}`, { state: {
            confirmation: `Visitor registered as ${visit.visitNumber}.`,
            warning: reason instanceof Error ? `The visitor was registered, but the recording could not be linked: ${reason.message}` : 'The visitor was registered, but its recording could not be linked.',
          } })
          return
        }
      }
      navigate(`/walkins/${visit.id}`, { state: { confirmation: `Visitor registered as ${visit.visitNumber}.` } })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Visitor could not be registered.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="walkin-page">
      <div className="walkin-back-row"><Link to="/walkins"><ArrowLeft size={16} /> Reception queue</Link></div>
      <header className="walkin-header">
        <div>
          <div className="walkin-kicker"><ClipboardPlus size={15} /> RECEPTION</div>
          <h1>Register a visitor</h1>
          <p>Capture just enough to direct the customer to the right person.</p>
        </div>
      </header>
      {isWalkinDemoMode() && <div className="walkin-notice">Demo mode — records are saved to this browser. Check-in time and visit number are generated when you save.</div>}
      {error && <div className="walkin-alert error" role="alert">{error}</div>}

      <form className="walkin-form" onSubmit={(event) => void submit(event)} noValidate>
        <div className="walkin-registration-layout">
          <WalkinVoiceAssistant
            customerName={form.customerName}
            onConversationChange={updateConversation}
            onBusyChange={updateVoiceBusy}
            onSuggestions={applySuggestions}
            onAcceptSuggestion={acceptSuggestion}
          />
          <div className="walkin-card walkin-form-fields">
            <section>
              <div className="walkin-section-heading"><span>01</span><div><h2>Customer information</h2><p>Required fields are marked with an asterisk.</p></div></div>
              <div className="walkin-form-grid">
                <Field label="Customer full name" name="customerName" required error={errors.customerName} aiDetected={autoFields.has('customerName')} hint={autoFields.has('customerName') ? 'AI detected · review before registering' : undefined}><input id="customerName" autoComplete="name" value={form.customerName} onChange={(event) => update('customerName', event.target.value)} placeholder="Customer name" /></Field>
                <Field label="Mobile number" name="mobileNumber" required error={errors.mobileNumber} aiDetected={autoFields.has('mobileNumber')} hint={autoFields.has('mobileNumber') ? 'AI detected · review before registering' : undefined}><input id="mobileNumber" type="tel" autoComplete="tel" inputMode="tel" value={form.mobileNumber} onChange={(event) => update('mobileNumber', event.target.value)} placeholder="+91 98765 43210" /></Field>
                <Field label="Company or organization" name="companyName" error={errors.companyName} aiDetected={autoFields.has('companyName')} hint={autoFields.has('companyName') ? 'AI detected · review before registering' : undefined}><input id="companyName" autoComplete="organization" value={form.companyName} onChange={(event) => update('companyName', event.target.value)} placeholder="Optional" /></Field>
                <Field label="Email address" name="email" error={errors.email} aiDetected={autoFields.has('email')} hint={autoFields.has('email') ? 'AI detected · review before registering' : undefined}><input id="email" type="email" autoComplete="email" value={form.email} onChange={(event) => update('email', event.target.value)} placeholder="name@company.com" /></Field>
              </div>
            </section>
            <section>
              <div className="walkin-section-heading"><span>02</span><div><h2>Visit information</h2><p>Details can be completed during or after the conversation.</p></div></div>
              <div className="walkin-form-grid">
                <Field label="Purpose of visit" name="purpose" error={errors.purpose} aiDetected={autoFields.has('purpose')} hint={autoFields.has('purpose') ? 'AI detected · review before registering' : undefined}>
                  <select id="purpose" value={form.purpose} onChange={(event) => update('purpose', event.target.value as FormValues['purpose'])}>{walkinPurposes.map(purpose => <option key={purpose}>{purpose}</option>)}</select>
                </Field>
                <Field label="Person or department to meet" name="requestedEmployee" error={errors.requestedEmployee} aiDetected={autoFields.has('requestedEmployee')} hint={autoFields.has('requestedEmployee') ? 'AI detected · review before registering' : undefined}><input id="requestedEmployee" value={form.requestedEmployee} onChange={(event) => update('requestedEmployee', event.target.value)} placeholder="Optional" /></Field>
                <Field label="Department" name="department" error={errors.department} aiDetected={autoFields.has('department')} hint={autoFields.has('department') ? 'AI detected · review before registering' : undefined}><input id="department" value={form.department} onChange={(event) => update('department', event.target.value)} placeholder="Optional" /></Field>
                <Field label="Visit description" name="purposeDescription" error={errors.purposeDescription} wide aiDetected={autoFields.has('purposeDescription')} hint={autoFields.has('purposeDescription') ? 'AI detected · review before registering' : undefined}><textarea id="purposeDescription" rows={3} value={form.purposeDescription} onChange={(event) => update('purposeDescription', event.target.value)} placeholder="Optional" /></Field>
                <Field label="Reception notes" name="internalNotes" error={errors.internalNotes} wide><textarea id="internalNotes" rows={2} value={form.internalNotes} onChange={(event) => update('internalNotes', event.target.value)} placeholder="Optional" /></Field>
              </div>
            </section>
          </div>
        </div>
        {error && <div className="walkin-alert error" role="alert">{error}</div>}
        {voiceBusy && <p className="walkin-field-hint" role="status">Stop recording and finish saving its audio before registering. The form remains editable while you record.</p>}
        <div className="walkin-form-actions"><Link className="walkin-button secondary" to="/walkins">Cancel</Link><button className="walkin-button primary" type="submit" disabled={saving || voiceBusy || !workspace}>{saving ? 'Saving visitor…' : <><CheckCircle2 size={17} /> Register visitor</>}</button></div>
      </form>
    </div>
  )
}

function Field({
  label,
  name,
  required,
  error,
  hint,
  wide,
  aiDetected,
  children,
}: {
  label: string
  name: string
  required?: boolean
  error?: string
  hint?: string
  wide?: boolean
  aiDetected?: boolean
  children: React.ReactNode
}) {
  return (
    <div className={`walkin-field${error ? ' has-error' : ''}${wide ? ' wide' : ''}${aiDetected ? ' is-ai-detected' : ''}`}>
      <label htmlFor={name}>{label}{required && <span aria-label="required"> *</span>}</label>
      {children}
      {error && <span className="walkin-field-error" role="alert">{error}</span>}
      {!error && hint && <span className="walkin-field-hint">{hint}</span>}
    </div>
  )
}
