import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from 'react'
import { useNavigate, useBlocker } from 'react-router-dom'
import { CheckCircle2, ClipboardPlus, Mic, Square } from 'lucide-react'
import { z } from 'zod'
import { type RegisterWalkinInput } from '../../types/walkin'
import { attachWalkinRecording, isWalkinDemoMode, registerWalkin } from '../../services/walkinService'
import { useOffice } from '../../contexts/OfficeContext'
import './Walkins.css'

const registrationSchema = z.object({
  customerName: z.string().trim().min(2, 'Enter the customer’s full name.').max(150, 'Name is too long.'),
  mobileNumber: z.string().trim().regex(/^\+?[0-9().\-\s]{7,20}$/, 'Enter a valid mobile number.').refine((value) => value.replace(/\D/g, '').length >= 7 && value.replace(/\D/g, '').length <= 15, 'Enter a valid mobile number.'),
  email: z.string().trim().min(1, 'Enter the visitor’s email address.').refine((value) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value), 'Enter a valid email address.'),
  companyName: z.string().trim().max(150, 'Company name is too long.'),
  projectName: z.string().trim().max(150, 'Project or site is too long.'),
  preferredContactMethod: z.string().trim().max(80, 'Preferred contact is too long.'),
})

type FormValues = z.infer<typeof registrationSchema>
type FormErrors = Partial<Record<keyof FormValues, string>>

const initialValues: FormValues = {
  customerName: '',
  mobileNumber: '',
  email: '',
  companyName: '',
  projectName: '',
  preferredContactMethod: '',
}

export default function WalkinRegistration() {
  const { user, workspace, setCaptureBusy } = useOffice()
  const navigate = useNavigate()
  const [form, setForm] = useState(initialValues)
  const [errors, setErrors] = useState<FormErrors>({})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveKey, setSaveKey] = useState(() => crypto.randomUUID())
  const [voiceBusy, setVoiceBusy] = useState(false)
  const voiceConversation = useRef<string | null>(null)
  const [voiceId, setVoiceId] = useState<string | null>(null)
  const voiceFillStep = useRef(0)
  const blocker = useBlocker(voiceBusy || saving)
  useEffect(() => { setCaptureBusy?.(voiceBusy || saving); return () => setCaptureBusy?.(false) }, [voiceBusy, saving, setCaptureBusy])
  useEffect(() => { if (blocker.state === 'blocked') { setError('End the visitor recording before leaving this page.'); blocker.reset() } }, [blocker])
  useEffect(() => {
    if (!voiceBusy) return
    const demoFields: Array<[keyof FormValues, string]> = [
      ['customerName', 'Akshat Saxena'], ['mobileNumber', '9311854768'], ['email', 'akshat@example.com'],
      ['companyName', 'Cosmonet AI'], ['projectName', 'Bengaluru Central showroom'], ['preferredContactMethod', 'Phone'],
    ]
    const timer = window.setInterval(() => {
      const next = demoFields[voiceFillStep.current % demoFields.length]
      voiceFillStep.current += 1
      setForm(previous => ({ ...previous, [next[0]]: next[1] }))
      setErrors(previous => ({ ...previous, [next[0]]: undefined }))
      setSaveKey(crypto.randomUUID())
    }, 850)
    return () => window.clearInterval(timer)
  }, [voiceBusy])

  const toggleDemoRecording = () => {
    if (voiceBusy) { setVoiceBusy(false); return }
    voiceFillStep.current = 0
    const recordingId = `demo-recording-${Date.now()}`
    voiceConversation.current = recordingId
    setVoiceId(recordingId)
    setVoiceBusy(true)
    setError('')
  }

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
    setSaving(true)
    try {
      const values = parsed.data
      const input: RegisterWalkinInput = {
      customerName: values.customerName,
      mobileNumber: values.mobileNumber,
      email: values.email || '',
      companyName: values.companyName || '',
      projectName: values.projectName || '',
      preferredContactMethod: values.preferredContactMethod || '',
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
      const visit = await registerWalkin(workspace.id, input, saveKey)
      const recordingId = voiceConversation.current
      if (recordingId) {
        let linked = false
        try { await attachWalkinRecording(workspace.id, visit.id, recordingId); linked = !isWalkinDemoMode() } catch { linked = false }
        if (!linked) { try { sessionStorage.setItem(`guruconnect:walkin-audio:${user?.id}:${workspace.id}:${visit.id}`, recordingId) } catch { /* best effort */ } }
      }
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
          <div className="walkin-kicker"><ClipboardPlus size={15} /> RECEPTION</div>
          <h1>Register a visitor</h1>
          <p>Capture just enough to direct the customer to the right person.</p>
        </div>
      </header>
      {error && <div className="walkin-alert error" role="alert">{error}</div>}

      <form className="walkin-card walkin-form" onSubmit={(event) => void submit(event)} noValidate>
        <section>
          <div className="walkin-section-heading"><div className="walkin-section-heading-copy"><span>01</span><div><h2>Customer information</h2><p>Required fields are marked with an asterisk.</p></div></div><DemoRecordingButton recording={voiceBusy} onClick={toggleDemoRecording} /></div>
          <div className="walkin-form-grid">
            <Field label="Name (customer full name)" name="customerName" required error={errors.customerName}><input disabled={!!voiceId || voiceBusy} id="customerName" autoComplete="name" value={form.customerName} onChange={(event) => update('customerName', event.target.value)} placeholder="Visitor name" /></Field>
            <Field label="Phone (mobile number)" name="mobileNumber" required error={errors.mobileNumber}><input disabled={!!voiceId || voiceBusy} id="mobileNumber" type="tel" autoComplete="tel" inputMode="tel" value={form.mobileNumber} onChange={(event) => update('mobileNumber', event.target.value)} placeholder="Phone number" /></Field>
            <Field label="Email address" name="email" required error={errors.email}><input disabled={!!voiceId || voiceBusy} id="email" type="email" autoComplete="email" value={form.email} onChange={(event) => update('email', event.target.value)} placeholder="visitor@example.com" required /></Field>
            <Field label="Source" name="source"><input id="source" value="Retail walk-in" readOnly /></Field>
            <Field label="Branch" name="branch"><input id="branch" value="Walk-in" readOnly /></Field>
            <Field label="Assigned executive" name="assignedExecutive"><input id="assignedExecutive" value="Unassigned" readOnly /></Field>
            <Field label="Customer type" name="customerType"><input id="customerType" value="Retail" readOnly /></Field>
            <Field label="Company" name="companyName" error={errors.companyName}><input id="companyName" autoComplete="organization" value={form.companyName} onChange={(event) => update('companyName', event.target.value)} placeholder="Not provided" /></Field>
            <Field label="Project / site" name="projectName" error={errors.projectName}><input id="projectName" value={form.projectName} onChange={(event) => update('projectName', event.target.value)} placeholder="Not provided" /></Field>
            <Field label="Preferred contact" name="preferredContactMethod" error={errors.preferredContactMethod}><input id="preferredContactMethod" value={form.preferredContactMethod} onChange={(event) => update('preferredContactMethod', event.target.value)} placeholder="Not provided" /></Field>
          </div>
        </section>

        {error && <div className="walkin-alert error" role="alert">{error}</div>}
        <div className="walkin-form-actions"><button type="button" className="walkin-button secondary" disabled={voiceBusy || saving} onClick={() => navigate('/walkins')}>Cancel</button><button className="walkin-button primary" type="submit" disabled={saving || !workspace || voiceBusy}>{saving ? 'Saving visitor…' : <><CheckCircle2 size={17} /> Register visitor</>}</button></div>
      </form>
    </div>
  )
}

function DemoRecordingButton({ recording, onClick }: { recording: boolean; onClick: () => void }) {
  return <div className={`demo-recording${recording ? ' is-recording' : ''}`}>
    <button type="button" className="demo-recording-button" onClick={onClick}>{recording ? <><Square size={15} fill="currentColor" /> Stop recording</> : <><Mic size={16} /> Start recording</>}</button>
    {recording && <div className="demo-recording-live"><span className="demo-waveform" aria-label="Recording waveform">{Array.from({ length: 22 }, (_, index) => <i key={index} style={{ '--wave-delay': `${index * 45}ms`, '--wave-height': `${10 + ((index * 13) % 20)}px` } as CSSProperties} />)}</span><small>Listening and filling the form...</small></div>}
  </div>
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
  label: string
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
