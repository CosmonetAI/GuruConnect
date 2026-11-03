import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { ArrowLeft, CheckCircle2, ClipboardPlus, Mic, Save, Square } from 'lucide-react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import PageLoader from '../../components/PageLoader'
import { useOffice } from '../../contexts/OfficeContext'
import { useScopedResource } from '../../hooks/useScopedResource'
import { message } from '../../services/officeApi'
import { createEnquiry, updateEnquiry } from '../../services/enquiryService'
import type { Enquiry } from '../../types/enquiry'
import '../Walkins/Walkins.css'

const enquirySources = ['Incoming Call', 'Outgoing Call', 'Retail Walk-in', 'Dealership', 'Project', 'Supported WhatsApp', 'Email', 'Other'] as const
type Source = typeof enquirySources[number]
type FormValues = { customerName: string; phone: string; email: string; channel: Source; branch: string; assignedExecutive: string; customerType: 'Retail' | 'Dealer' | 'Project'; company: string; projectName: string; preferredContactMethod: string }
type FormErrors = Partial<Record<keyof FormValues, string>>
type WalkinConversion = { customerName?: string; phone?: string; email?: string; assignedExecutive?: string; company?: string; visitNumber?: string; conversationId?: string }

const blankForm: FormValues = { customerName: '', phone: '', email: '', channel: 'Incoming Call', branch: '', assignedExecutive: '', customerType: 'Retail', company: '', projectName: '', preferredContactMethod: '' }

function fieldError(values: FormValues): FormErrors {
  const errors: FormErrors = {}
  if (values.customerName.trim().length < 2) errors.customerName = 'Enter the customer’s full name.'
  if (!values.phone.trim() || values.phone.replace(/\D/g, '').length < 7) errors.phone = 'Enter a valid mobile number.'
  if (!values.email.trim() || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email)) errors.email = 'Enter a valid email address.'
  return errors
}

function Field({ label, htmlFor, required, error, children }: { label: string; htmlFor: string; required?: boolean; error?: string; children: ReactNode }) {
  return <div className={`walkin-field${error ? ' has-error' : ''}`}><label htmlFor={htmlFor}>{label}{required && <span aria-label="required"> *</span>}</label>{children}{error && <span id={`${htmlFor}-error`} className="walkin-field-error" role="alert">{error}</span>}</div>
}

function formFromEnquiry(enquiry: Enquiry): FormValues {
  const channel = enquirySources.includes(enquiry.channel as Source) ? enquiry.channel as Source : 'Other'
  return { customerName: enquiry.customerName, phone: enquiry.phone || '', email: enquiry.email || '', channel, branch: enquiry.branch || '', assignedExecutive: enquiry.assignedExecutive || '', customerType: enquiry.customerType || 'Retail', company: enquiry.company || '', projectName: enquiry.projectName || '', preferredContactMethod: enquiry.preferredContactMethod || '' }
}

export default function NewEnquiry() {
  const { user, workspace } = useOffice()
  const location = useLocation()
  const navigate = useNavigate()
  const { id } = useParams()
  const resource = useScopedResource<Enquiry>(id ? `/enquiries/${encodeURIComponent(id)}` : null)
  if (!user || !workspace) return <PageLoader kind="enquiries" label="Loading your workspace" detail="Preparing the enquiry capture workspace." />
  if (id && !resource.data) return resource.loading ? <PageLoader kind="enquiries" label="Loading enquiry" detail="Preparing the saved customer opportunity." /> : <p role="status">{resource.error || 'Enquiry unavailable.'}</p>
  const conversion = (location.state as { walkinConversion?: WalkinConversion } | null)?.walkinConversion
  return <EnquiryRegistration key={`${user.id}:${workspace.id}:${workspace.member_id ?? user.id}:${id || conversion?.visitNumber || 'new'}`} workspaceId={workspace.id} memberId={workspace.member_id} existing={resource.data} conversion={conversion} navigate={navigate} />
}

function EnquiryRegistration({ workspaceId, memberId, existing, conversion, navigate }: { workspaceId: string; memberId?: string; existing?: Enquiry; conversion?: WalkinConversion; navigate: ReturnType<typeof useNavigate> }) {
  const [form, setForm] = useState<FormValues>(() => existing ? formFromEnquiry(existing) : { ...blankForm, customerName: conversion?.customerName || '', phone: conversion?.phone || '', email: conversion?.email?.toLowerCase() || '', assignedExecutive: conversion?.assignedExecutive || '', company: conversion?.company || '', channel: conversion ? 'Retail Walk-in' : 'Incoming Call' })
  const [errors, setErrors] = useState<FormErrors>({})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [recordingBusy, setRecordingBusy] = useState(false)
  const conversationId = existing?.conversationId || conversion?.conversationId || null
  const [saveKey] = useState(() => crypto.randomUUID())

  useEffect(() => { if (form.email !== form.email.toLowerCase()) setForm(current => ({ ...current, email: current.email.toLowerCase() })) }, [form.email])

  const update = <K extends keyof FormValues>(key: K, value: FormValues[K]) => {
    setForm(current => ({ ...current, [key]: value }))
    setErrors(current => ({ ...current, [key]: undefined }))
    setError('')
  }

  const toggleRecording = () => {
    setRecordingBusy(current => !current)
    setError('')
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const nextErrors = fieldError(form)
    if (Object.keys(nextErrors).length) { setErrors(nextErrors); setError('Check the highlighted fields before saving this enquiry.'); return }
    if (saving || recordingBusy) { setError('Stop the recording before saving this enquiry.'); return }
    setSaving(true)
    setError('')
    try {
      const data = {
        customerName: form.customerName.trim(),
        phone: form.phone.trim(),
        email: form.email.trim().toLowerCase(),
        channel: form.channel,
        branch: form.branch.trim() || 'Walk-in',
        assignedExecutive: form.assignedExecutive.trim() || 'Unassigned',
        product: '', quantity: 1, unit: 'nos', notes: '', customerNotes: '', salespersonNotes: '', requirementsSummary: '',
        customerType: form.customerType, company: form.company.trim(), projectName: form.projectName.trim(), preferredContactMethod: form.preferredContactMethod.trim(),
        productRequirements: [], followUpDate: '', followUpOwner: '', priority: 'Normal' as const,
        ...(conversationId ? { conversationId } : {}),
        ...(conversion?.visitNumber ? { sourceEventId: `walkin:${conversion.visitNumber}` } : {}),
      }
      const saved = existing ? await updateEnquiry(workspaceId, existing.id, data, memberId) : await createEnquiry(workspaceId, data, saveKey, memberId)
      navigate(`/enquiries/${encodeURIComponent(saved.id)}`)
    } catch (reason) { setError(`${message(reason)} Your entries are preserved.`) } finally { setSaving(false) }
  }

  return <div className="walkin-page enquiry-registration-page">
    <div className="walkin-back-row"><Link to="/enquiries"><ArrowLeft size={16} /> Back to enquiries</Link></div>
    <header className="walkin-header"><div><div className="walkin-kicker"><ClipboardPlus size={15} /> SALES</div><h1>{existing ? 'Edit enquiry' : 'Register an enquiry'}</h1><p>Capture just enough to direct the customer to the right person.</p></div></header>
    {error && <div className="walkin-alert error" role="alert">{error}</div>}
    <fieldset disabled={saving} style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
      <form className="walkin-card walkin-form" noValidate onSubmit={event => void submit(event)}>
        <section aria-labelledby="customer-section-title">
          <div className="walkin-section-heading"><div className="walkin-section-heading-copy"><span>01</span><div><h2 id="customer-section-title">Customer information</h2><p>Required fields are marked with an asterisk.</p></div></div><DemoRecordingButton recording={recordingBusy} onClick={toggleRecording} /></div>
          <div className="walkin-form-grid">
            <Field label="Name (customer full name)" htmlFor="customerName" required error={errors.customerName}><input id="customerName" value={form.customerName} onChange={event => update('customerName', event.target.value)} autoComplete="name" placeholder="Visitor name" /></Field>
            <Field label="Phone (mobile number)" htmlFor="phone" required error={errors.phone}><input id="phone" type="tel" value={form.phone} onChange={event => update('phone', event.target.value)} autoComplete="tel" inputMode="tel" placeholder="Phone number" /></Field>
            <Field label="Email address" htmlFor="email" required error={errors.email}><input id="email" type="email" value={form.email} onChange={event => update('email', event.target.value.toLowerCase())} autoComplete="email" placeholder="visitor@example.com" /></Field>
            <Field label="Source" htmlFor="channel"><input id="channel" value={form.channel} readOnly /></Field>
            <Field label="Branch" htmlFor="branch"><input id="branch" value={form.branch || 'Walk-in'} readOnly /></Field>
            <Field label="Assigned executive" htmlFor="assignedExecutive"><input id="assignedExecutive" value={form.assignedExecutive || 'Unassigned'} readOnly /></Field>
            <Field label="Customer type" htmlFor="customerType"><input id="customerType" value={form.customerType} readOnly /></Field>
            <Field label="Company" htmlFor="company"><input id="company" value={form.company} onChange={event => update('company', event.target.value)} placeholder="Not provided" /></Field>
            <Field label="Project / site" htmlFor="projectName"><input id="projectName" value={form.projectName} onChange={event => update('projectName', event.target.value)} placeholder="Not provided" /></Field>
            <Field label="Preferred contact" htmlFor="preferredContactMethod"><input id="preferredContactMethod" value={form.preferredContactMethod} onChange={event => update('preferredContactMethod', event.target.value)} placeholder="Not provided" /></Field>
          </div>
        </section>
        <div className="walkin-form-actions"><button type="button" className="walkin-button secondary" disabled={recordingBusy || saving} onClick={() => navigate('/enquiries')}>Cancel</button><button type="submit" className="walkin-button primary" disabled={saving || recordingBusy}>{saving ? 'Saving enquiry…' : existing ? <><Save size={16} /> Save changes</> : <><CheckCircle2 size={17} /> Save enquiry</>}</button></div>
      </form>
    </fieldset>
  </div>
}

function DemoRecordingButton({ recording, onClick }: { recording: boolean; onClick: () => void }) {
  return <div className={`demo-recording${recording ? ' is-recording' : ''}`}><button type="button" className="demo-recording-button" onClick={onClick} aria-pressed={recording}>{recording ? <><Square size={15} fill="currentColor" /> Stop recording</> : <><Mic size={16} /> Start recording</>}</button></div>
}
