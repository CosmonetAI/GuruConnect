import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, ClipboardPlus } from 'lucide-react'
import { z } from 'zod'
import { type RegisterWalkinInput } from '../../types/walkin'
import { isWalkinDemoMode, registerWalkin } from '../../services/walkinService'
import './Walkins.css'

const registrationSchema = z.object({
  customerName: z.string().trim().min(2, 'Enter the customer’s full name.').max(150, 'Name is too long.'),
  mobileNumber: z.string().trim().regex(/^\+?[0-9().\-\s]{7,20}$/, 'Enter a valid mobile number.').refine((value) => value.replace(/\D/g, '').length >= 7 && value.replace(/\D/g, '').length <= 15, 'Enter a valid mobile number.'),
  email: z.string().trim().refine((value) => !value || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value), 'Enter a valid email address.'),
  companyName: z.string().trim().max(150, 'Company name is too long.'),
})

type FormValues = z.infer<typeof registrationSchema>
type FormErrors = Partial<Record<keyof FormValues, string>>

const initialValues: FormValues = {
  customerName: '',
  mobileNumber: '',
  email: '',
  companyName: '',
}

export default function WalkinRegistration() {
  const navigate = useNavigate()
  const [form, setForm] = useState(initialValues)
  const [errors, setErrors] = useState<FormErrors>({})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const update = <K extends keyof FormValues>(field: K, value: FormValues[K]) => {
    setForm((previous) => ({ ...previous, [field]: value }))
    setErrors((previous) => ({ ...previous, [field]: undefined }))
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
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
      const visit = await registerWalkin(input)
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

      <form className="walkin-card walkin-form" onSubmit={(event) => void submit(event)} noValidate>
        <section>
          <div className="walkin-section-heading"><span>01</span><div><h2>Customer information</h2><p>Required fields are marked with an asterisk.</p></div></div>
          <div className="walkin-form-grid">
            <Field label="Customer full name" name="customerName" required error={errors.customerName}><input id="customerName" autoComplete="name" value={form.customerName} onChange={(event) => update('customerName', event.target.value)} placeholder="Customer name" /></Field>
            <Field label="Mobile number" name="mobileNumber" required error={errors.mobileNumber}><input id="mobileNumber" type="tel" autoComplete="tel" inputMode="tel" value={form.mobileNumber} onChange={(event) => update('mobileNumber', event.target.value)} placeholder="+91 98765 43210" /></Field>
            <Field label="Company or organization" name="companyName" error={errors.companyName}><input id="companyName" autoComplete="organization" value={form.companyName} onChange={(event) => update('companyName', event.target.value)} placeholder="Optional" /></Field>
            <Field label="Email address" name="email" error={errors.email}><input id="email" type="email" autoComplete="email" value={form.email} onChange={(event) => update('email', event.target.value)} placeholder="name@company.com" /></Field>
          </div>
        </section>

        {error && <div className="walkin-alert error" role="alert">{error}</div>}
        <div className="walkin-form-actions"><Link className="walkin-button secondary" to="/walkins">Cancel</Link><button className="walkin-button primary" type="submit" disabled={saving}>{saving ? 'Saving visitor…' : <><CheckCircle2 size={17} /> Register visitor</>}</button></div>
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
  children,
}: {
  label: string
  name: string
  required?: boolean
  error?: string
  hint?: string
  wide?: boolean
  children: React.ReactNode
}) {
  return (
    <div className={`walkin-field${error ? ' has-error' : ''}${wide ? ' wide' : ''}`}>
      <label htmlFor={name}>{label}{required && <span aria-label="required"> *</span>}</label>
      {children}
      {error && <span className="walkin-field-error" role="alert">{error}</span>}
      {!error && hint && <span className="walkin-field-hint">{hint}</span>}
    </div>
  )
}
