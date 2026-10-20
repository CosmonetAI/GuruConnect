import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useBlocker, useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  AlertCircle,
  ArrowLeft,
  AudioLines,
  Building2,
  CalendarDays,
  Check,
  ChevronDown,
  ClipboardList,
  Headphones,
  Lightbulb,
  Mic2,
  PackageSearch,
  Plus,
  Save,
  Sparkles,
  Trash2,
  UserRound,
  UsersRound,
} from 'lucide-react'
import { EnquiryDraftValuesSchema, NewEnquirySchema, productHasDetails, type NewEnquiryForm } from '../../schemas/enquirySchema'
import { createEnquiry, updateEnquiry } from '../../services/enquiryService'
import type { Enquiry } from '../../types/enquiry'
import { useScopedResource } from '../../hooks/useScopedResource'
import { message } from '../../services/officeApi'
import { useOffice } from '../../contexts/OfficeContext'
import ConversationRecorder from './ConversationRecorder'
import './NewEnquiry.css'

const enquirySources = [
  'Incoming Call',
  'Outgoing Call',
  'Retail Walk-in',
  'Dealership',
  'Project',
  'Supported WhatsApp',
  'Email',
  'Other',
] as const
const defaultValues: NewEnquiryForm = {
  customerName: '',
  phone: '',
  email: '',
  channel: 'Incoming Call',
  branch: '',
  assignedExecutive: '',
  customerType: 'Retail',
  company: '',
  projectName: '',
  preferredContactMethod: '',
  customerNotes: '',
  productRequirements: [
    {
      product: '',
      sku: '',
      quantity: 1,
      unit: 'nos',
      customerSegment: '',
      budget: '',
      paymentPreference: '',
      timeline: '',
      stockCheck: false,
      notes: '',
    },
  ],
  salespersonNotes: '',
  requirementsSummary: '',
  followUpDate: '',
  followUpOwner: '',
  priority: 'Normal',
}

type EnquiryField = keyof NewEnquiryForm
type FieldErrors = Record<string, string>

interface Draft { values: NewEnquiryForm; conversationId: string | null; saveKey: string }
const validId = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
function readDraft(storageKey: string): Draft & { warning: string; restored: boolean } {
  const fresh = () => ({ values: structuredClone(defaultValues), conversationId: null, saveKey: crypto.randomUUID(), warning: '', restored: false })
  try {
    const saved = sessionStorage.getItem(storageKey)
    if (!saved) return fresh()
    if (saved.length > 120_000) return { ...fresh(), warning: 'The saved draft is too large to restore. Start a new enquiry.' }
    const parsed = JSON.parse(saved) as Record<string, unknown>
    const values = EnquiryDraftValuesSchema.safeParse(parsed?.values)
    if (!values.success || !validId(parsed.saveKey) || (parsed.conversationId !== null && !validId(parsed.conversationId))) return { ...fresh(), warning: 'The saved draft could not be restored. Start a new enquiry or recover saved audio below.' }
    return { values: values.data, conversationId: parsed.conversationId as string | null, saveKey: parsed.saveKey,
      warning: 'Your draft was restored for this workspace. Attached audio is checked before you can save.', restored: true }
  } catch {
    return { ...fresh(), warning: 'The saved draft could not be read. You can continue, but browser recovery may be unavailable.', restored: false }
  }
}

function Field({
  label,
  htmlFor,
  required,
  hint,
  error,
  children,
  className,
}: {
  label: string
  htmlFor: string
  required?: boolean
  hint?: string
  error?: string
  children: ReactNode
  className?: string
}) {
  return (
    <div className={`enquiry-field${error ? ' has-error' : ''}${className ? ` ${className}` : ''}`}>
      <label htmlFor={htmlFor}>
        {label}
        {required && <span className="required-mark" aria-label="required"> *</span>}
      </label>
      {children}
      {error && <span id={`${htmlFor}-error`} className="field-error" role="alert">{error}</span>}
      {!error && hint && <span id={`${htmlFor}-hint`} className="field-hint">{hint}</span>}
    </div>
  )
}

function SectionHeader({
  icon,
  eyebrow,
  title,
  description,
  step,
  titleId,
}: {
  icon: ReactNode
  eyebrow: string
  title: string
  description?: string
  step?: string
  titleId: string
}) {
  return (
    <div className="enquiry-section-heading">
      <div className="section-icon">{icon}</div>
      <div className="section-heading-copy">
        <span className="section-eyebrow">{eyebrow}</span>
        <h2 id={titleId}>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {step && <span className="section-step">{step}</span>}
    </div>
  )
}

type WalkinConversion = { visitId: string; visitNumber: string; customerName: string; phone: string; email: string; company: string; assignedExecutive: string; purpose: string; productOrService: string; quantity: string; budget: string; timeline: string; requirements: string; notes: string; followUpDate: string; conversationId?: string }
function convertedValues(source: WalkinConversion): NewEnquiryForm {
  return { ...defaultValues, customerName: source.customerName, phone: source.phone, email: source.email, channel: 'Retail Walk-in', assignedExecutive: source.assignedExecutive, company: source.company,
    customerNotes: `${source.purpose} · Visit ${source.visitNumber}\n${source.notes}`.trim(), requirementsSummary: source.requirements, followUpDate: source.followUpDate,
    productRequirements: [{ ...defaultValues.productRequirements[0], product: source.productOrService, quantity: /^\d+$/.test(source.quantity) && Number(source.quantity) > 0 ? Number(source.quantity) : 0, budget: source.budget, timeline: source.timeline, notes: source.requirements }],
  }
}

function existingValues(source: Enquiry): NewEnquiryForm {
  return {
    ...defaultValues,
    customerName: source.customerName,
    phone: source.phone || '',
    email: source.email || '',
    channel: ['Incoming Call', 'Outgoing Call', 'Retail Walk-in', 'Dealership', 'Project', 'Supported WhatsApp', 'Email', 'Other'].includes(source.channel) ? source.channel as NewEnquiryForm['channel'] : 'Other',
    branch: source.branch,
    assignedExecutive: source.assignedExecutive,
    customerType: source.customerType || 'Retail',
    company: source.company || '',
    projectName: source.projectName || '',
    preferredContactMethod: source.preferredContactMethod || '',
    customerNotes: source.customerNotes || '',
    salespersonNotes: source.salespersonNotes || '',
    requirementsSummary: source.requirementsSummary || '',
    productRequirements: source.productRequirements?.length ? source.productRequirements : [{
      ...defaultValues.productRequirements[0],
      product: source.product || '',
      quantity: source.quantity || 1,
      unit: source.unit || 'nos',
      notes: source.notes || '',
    }],
    followUpDate: source.followUpDate || '',
    followUpOwner: source.followUpOwner || '',
    priority: source.priority || 'Normal',
  }
}

export default function NewEnquiry() {
  const { user, workspace } = useOffice()
  const location = useLocation()
  const { id } = useParams()
  const conversion = (location.state as { walkinConversion?: WalkinConversion } | null)?.walkinConversion
  const resource = useScopedResource<Enquiry>(id ? `/enquiries/${encodeURIComponent(id)}` : null)
  if (!user || !workspace) return <p role="status">Loading your workspace…</p>
  if (id && !resource.data) return <p role="status">{resource.loading ? 'Loading enquiry…' : resource.error || 'Enquiry unavailable.'}</p>
  return <ScopedNewEnquiry key={`${user.id}:${workspace.id}:${id || conversion?.visitId || "new"}`} storageKey={`guruconnect:enquiry-draft:${user.id}:${workspace.id}${id ? `:edit:${id}` : conversion ? `:walkin:${conversion.visitId}` : ""}`} conversion={conversion} existing={resource.data} />
}

function ScopedNewEnquiry({ storageKey, conversion, existing }: { storageKey: string; conversion?: WalkinConversion; existing?: Enquiry }) {
  const navigate = useNavigate()
  const { workspace, setCaptureBusy } = useOffice()
  const [draft] = useState(() => {
    const saved = readDraft(storageKey)
    if (saved.restored) return saved
    if (existing) return { ...saved, values: existingValues(existing), conversationId: existing.conversationId || null }
    return conversion ? { ...saved, values: convertedValues(conversion), conversationId: conversion.conversationId ?? null } : saved
  })
  const [form, setForm] = useState<NewEnquiryForm>(draft.values)
  const [errors, setErrors] = useState<FieldErrors>({})
  const [formMessage, setFormMessage] = useState(draft.warning)
  const [isSaving, setIsSaving] = useState(false)
  const [isDraftSaved, setIsDraftSaved] = useState(draft.restored)
  const savingRef = useRef(false)
  const [conversationId, setConversationId] = useState<string | null>(draft.conversationId)
  const conversationRef = useRef(draft.conversationId)
  const [savedConversationId, setSavedConversationId] = useState<string | null>(null)
  const [recordingBusy, setRecordingBusy] = useState(!!draft.conversationId)
  const [saveKey, setSaveKey] = useState(draft.saveKey)
  const allowLeave = useRef(false)
  const audioBusyRef = useRef(recordingBusy)
  audioBusyRef.current = recordingBusy
  const hasUnfinishedAudio = recordingBusy || !!(conversationId && conversationId !== savedConversationId)
  const blocker = useBlocker(() => !allowLeave.current && (audioBusyRef.current || savingRef.current || !!(conversationId && conversationId !== savedConversationId)))
  useEffect(() => {
    if (blocker.state === 'blocked') { setFormMessage('Finish saving or detach the audio before leaving. Your enquiry details stay on this page.'); blocker.reset() }
  }, [blocker])
  useEffect(() => { setCaptureBusy(hasUnfinishedAudio || isSaving); return () => setCaptureBusy(false) }, [hasUnfinishedAudio, isSaving, setCaptureBusy])
  useEffect(() => {
    try { sessionStorage.setItem(storageKey, JSON.stringify({ values: form, conversationId, saveKey })); setIsDraftSaved(true) }
    catch { setIsDraftSaved(false) }
  }, [form, conversationId, saveKey, storageKey])
  useEffect(() => {
    if (!isSaving) return
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', prevent)
    return () => window.removeEventListener('beforeunload', prevent)
  }, [isSaving])
  const changeConversation = useCallback((id: string | null) => {
    if (conversationRef.current !== id) { conversationRef.current = id; setSaveKey(crypto.randomUUID()) }
    setConversationId(id)
    if (!id) setSavedConversationId(null)
  }, [])
  const audioSaved = useCallback((id: string) => { setConversationId(id); setSavedConversationId(id) }, [])

  const update = <K extends EnquiryField>(field: K, value: NewEnquiryForm[K]) => {
    setForm((previous) => ({ ...previous, [field]: value }))
    setErrors((previous) => {
      const next = { ...previous }
      delete next[field]
      return next
    })
    setIsDraftSaved(false)
    setSaveKey(crypto.randomUUID())
    setFormMessage('')
  }

  const filledRequirements = form.productRequirements.filter(productHasDetails)
  const requiredValues = [form.customerName, form.phone, form.channel, form.branch, form.assignedExecutive]
  const completedRequired = requiredValues.filter((value) => value.trim() !== '').length
  const summaryProduct = filledRequirements.length
    ? filledRequirements.map((item) => item.product || item.sku || 'Product name or SKU needed').join(', ')
    : 'Not specified yet'

  const saveDraft = () => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify({ values: form, conversationId, saveKey }))
      setIsDraftSaved(true)
      setFormMessage('Draft saved for this browser session. No enquiry has been submitted.')
      setErrors({})
    } catch {
      setIsDraftSaved(false)
      setFormMessage('This browser could not save the draft. Keep this page open; your current entries are still here.')
    }
  }

  const submitEnquiry = async () => {
    if (savingRef.current || !workspace) return
    if (hasUnfinishedAudio) { setFormMessage('Finish saving or detach the audio before saving this enquiry.'); return }
    setFormMessage('')
    const parsed = NewEnquirySchema.safeParse({
      ...form,
      productRequirements: form.productRequirements,
    })
    if (!parsed.success) {
      const nextErrors: FieldErrors = {}
      for (const issue of parsed.error.issues) {
        const key = issue.path.join('.')
        if (!nextErrors[key]) nextErrors[key] = issue.message
      }
      setErrors(nextErrors)
      setFormMessage('Check the highlighted fields before saving this enquiry.')
      document.querySelector('.has-error input, .has-error select, .has-error textarea')?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      })
      return
    }

    savingRef.current = true
    setIsSaving(true)
    try {
      const data = parsed.data
      const walkinService = conversion ? await import('../../services/walkinService') : null
      if (conversion) {
        const visit = await walkinService!.getWalkinVisit(workspace.id, conversion.visitId)
        if (!visit) throw new Error('The source walk-in visit was not found. Your enquiry has not been submitted.')
        if (visit.convertedEnquiryReference) { allowLeave.current = true; navigate(`/enquiries/${encodeURIComponent(visit.convertedEnquiryReference)}`); return }
        if (!['Requirements Captured', 'Follow-up Required'].includes(visit.status)) throw new Error('Save and review the walk-in requirements before creating its enquiry.')
      }
      const requirements = data.productRequirements.filter(productHasDetails)
      const firstProduct = requirements[0]
      const enquiryData = {
        customerName: data.customerName,
        phone: data.phone,
        email: data.email || undefined,
        channel: data.channel,
        branch: data.branch,
        assignedExecutive: data.assignedExecutive,
        product: firstProduct?.product || firstProduct?.sku || '',
        quantity: firstProduct?.quantity ?? 1,
        unit: firstProduct?.unit ?? 'nos',
        notes: '',
        customerNotes: data.customerNotes, salespersonNotes: data.salespersonNotes, requirementsSummary: data.requirementsSummary,
        customerType: data.customerType,
        company: data.company,
        projectName: data.projectName,
        preferredContactMethod: data.preferredContactMethod,
        productRequirements: requirements,
        followUpDate: data.followUpDate,
        followUpOwner: data.followUpOwner,
        priority: data.priority,
        ...(conversationId ? { conversationId } : {}),
        ...(conversion ? { sourceEventId: `walkin:${conversion.visitId}` } : {}),
      }
      const created = existing
        ? await updateEnquiry(workspace.id, existing.id, enquiryData)
        : await createEnquiry(workspace.id, enquiryData, conversion ? `walkin:${conversion.visitId}` : saveKey)
      let draftCleanupMessage = ''
      if (conversion && walkinService!.isWalkinDemoMode()) { try { await walkinService!.markWalkinConverted(workspace.id, conversion.visitId, created.id) } catch { draftCleanupMessage = ' The enquiry is saved; the walk-in status could not be updated. Review the visit before converting again.' } }
      try {
        sessionStorage.removeItem(storageKey)
      } catch {
        draftCleanupMessage += ' The saved draft could not be removed from this browser tab.'
      }
      allowLeave.current = true
      navigate(`/enquiries/${encodeURIComponent(created.id)}`, {
        state: {
          confirmation: `Enquiry ${created.enquiryNumber || created.id} was ${existing ? 'updated' : 'saved'} in your workspace.${conversationId ? ' Transcription and English translation continue in the background.' : ''}${draftCleanupMessage}`,
        },
      })
    } catch (error) {
      setFormMessage(`${message(error)} Your entries are preserved. Retry without changing the form to check the same save.`)
    } finally {
      savingRef.current = false
      setIsSaving(false)
    }
  }

  const addProduct = () => {
    if (form.productRequirements.length >= 25) return
    update('productRequirements', [
      ...form.productRequirements,
      {
        product: '',
        sku: '',
        quantity: 1,
        unit: 'nos',
        customerSegment: '',
        budget: '',
        paymentPreference: '',
        timeline: '',
        stockCheck: false,
        notes: '',
      },
    ])
  }

  const updateProduct = (index: number, field: keyof NewEnquiryForm['productRequirements'][number], value: string | number | boolean) => {
    const updated = form.productRequirements.map((item, itemIndex) =>
      itemIndex === index ? { ...item, [field]: value } : item,
    )
    update('productRequirements', updated)
    setErrors((previous) => {
      const next = { ...previous }
      delete next[`productRequirements.${index}.${field}`]
      return next
    })
  }

  return (
    <form className="new-enquiry-page" noValidate onSubmit={(event) => { event.preventDefault(); void submitEnquiry() }}>
      <div className="enquiry-topline">
        <Link className="back-link" to="/enquiries"><ArrowLeft size={16} /> Back to enquiries</Link>
        <span className="enquiry-id-label"><span className="draft-dot" /> {existing ? `Edit ${existing.enquiryNumber || 'enquiry'}` : 'New enquiry'}</span>
      </div>

      <header className="enquiry-page-header">
        <div>
          <div className="page-kicker"><Sparkles size={14} /> SALES WORKSPACE</div>
          <h1>{existing ? 'Edit enquiry' : 'Capture a new enquiry'}</h1>
          <p>{existing ? 'Update customer details, requirements, and conversation audio.' : 'Start with the essentials. You can add more detail as the conversation develops.'}</p>
        </div>
        <div className="required-note"><span className="required-mark">*</span> Required fields</div>
      </header>

      <div className="demo-notice" role="status">
        <AudioLines size={17} />
        <p><strong>Customer → conversation → English transcript.</strong> Enter customer details, record or upload audio, then save the enquiry. Audio must finish saving first; transcription and translation can continue afterward.</p>
      </div>

      <fieldset disabled={isSaving} style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
      {conversion && <p className="flow-note" role="status">Review the customer and requirements transferred from {conversion.visitNumber}. Confirm the branch, product identifier and quantity before saving.</p>}
      <div className="enquiry-workspace">
        <div className="enquiry-main-column">
          <section className="enquiry-card" aria-labelledby="customer-section-title">
            <SectionHeader
                  titleId="customer-section-title"
              icon={<UserRound size={18} />}
              eyebrow="CUSTOMER"
              title="Customer information"
              description="Who are you speaking with?"
              step="01"
            />
            <div className="form-grid">
              <Field label="Customer full name" htmlFor="customerName" required error={errors.customerName} hint={conversationId ? 'This name is linked to the recording. Detach audio before changing the customer.' : undefined}>
                <input id="customerName" maxLength={200} disabled={!!conversationId} aria-describedby={conversationId ? 'customerName-hint' : errors.customerName ? 'customerName-error' : undefined} aria-invalid={!!errors.customerName} autoComplete="name" placeholder="e.g. Priya Sharma" value={form.customerName} onChange={(event) => update('customerName', event.target.value)} />
              </Field>
              <Field label="Phone number" htmlFor="phone" required error={errors.phone}>
                <input id="phone" type="tel" maxLength={20} aria-invalid={!!errors.phone} aria-describedby={errors.phone ? 'phone-error' : undefined} autoComplete="tel" inputMode="tel" placeholder="+91 98765 43210" value={form.phone} onChange={(event) => update('phone', event.target.value)} />
              </Field>
              <Field label="Email address" htmlFor="email" error={errors.email}>
                <input id="email" type="email" maxLength={320} aria-invalid={!!errors.email} aria-describedby={errors.email ? 'email-error' : undefined} autoComplete="email" placeholder="name@company.com" value={form.email} onChange={(event) => update('email', event.target.value)} />
              </Field>
              <Field label="Enquiry source" htmlFor="channel" required error={errors.channel}>
                <select id="channel" value={form.channel} onChange={(event) => update('channel', event.target.value as NewEnquiryForm['channel'])}>
                  {enquirySources.map((source) => <option key={source}>{source}</option>)}
                </select>
              </Field>
              <Field label="Branch / showroom" htmlFor="branch" required error={errors.branch}>
                <input id="branch" maxLength={160} aria-invalid={!!errors.branch} aria-describedby={errors.branch ? 'branch-error' : undefined} autoComplete="organization" placeholder="Enter the branch or showroom" value={form.branch} onChange={(event) => update('branch', event.target.value)} />
              </Field>
              <Field label="Assigned sales executive" htmlFor="assignedExecutive" required error={errors.assignedExecutive}>
                <input id="assignedExecutive" maxLength={160} aria-invalid={!!errors.assignedExecutive} aria-describedby={errors.assignedExecutive ? 'assignedExecutive-error' : undefined} autoComplete="off" placeholder="Enter an executive name" value={form.assignedExecutive} onChange={(event) => update('assignedExecutive', event.target.value)} />
              </Field>
              <Field label="Customer type" htmlFor="customerType">
                <select id="customerType" value={form.customerType} onChange={(event) => update('customerType', event.target.value as NewEnquiryForm['customerType'])}>
                  <option value="Retail">Retail</option>
                  <option value="Dealer">Dealer</option>
                  <option value="Project">Project</option>
                </select>
              </Field>
              <Field label="Preferred contact method" htmlFor="preferredContactMethod">
                <select id="preferredContactMethod" value={form.preferredContactMethod} onChange={(event) => update('preferredContactMethod', event.target.value)}>
                  <option value="">No preference</option>
                  <option>Phone</option><option>Email</option><option>WhatsApp</option>
                </select>
              </Field>
              {(form.customerType === 'Dealer' || form.customerType === 'Project') && (
                <Field label="Company / dealership name" htmlFor="company">
                  <input id="company" maxLength={200} autoComplete="organization" placeholder="Business or dealership name" value={form.company} onChange={(event) => update('company', event.target.value)} />
                </Field>
              )}
              {form.customerType === 'Project' && (
                <Field label="Project / site name" htmlFor="projectName">
                  <input id="projectName" maxLength={200} placeholder="Project or site name" value={form.projectName} onChange={(event) => update('projectName', event.target.value)} />
                </Field>
              )}
              <Field label="Additional customer notes" htmlFor="customerNotes" className="form-grid-wide">
                <textarea id="customerNotes" rows={3} maxLength={4000} placeholder="Anything useful to remember about this customer…" value={form.customerNotes} onChange={(event) => update('customerNotes', event.target.value)} />
              </Field>
            </div>
          </section>

          <section className="enquiry-card recording-card" aria-labelledby="recording-section-title">
            <SectionHeader
              titleId="recording-section-title"
              icon={<Mic2 size={18} />}
              eyebrow="CONVERSATION"
              title="Record or attach a conversation"
              description="Capture context now; review it later."
              step="02"
            />
            <ConversationRecorder customerName={form.customerName.trim().length >= 2 ? form.customerName : ''} title="Sales enquiry conversation" initialConversationId={conversationId} onConversationChange={changeConversation} onBusyChange={setRecordingBusy} onSaved={audioSaved} />
          </section>

          <section className="enquiry-card" aria-labelledby="products-section-title">
            <SectionHeader
              titleId="products-section-title"
              icon={<PackageSearch size={18} />}
              eyebrow="WHAT THEY NEED"
              title="Product requirements"
              description="Add one or more products the customer is interested in."
              step="03"
            />
            <div className="catalogue-notice"><AlertCircle size={15} /> Product catalogue unavailable. Capture the customer’s requested product; verify SKU, pricing, and stock separately.</div>
            {errors.productRequirements && <p className="field-error" role="alert">{errors.productRequirements}</p>}
            <div className="product-requirements">
              {form.productRequirements.map((item, index) => (
                <div className="product-row" key={index}>
                  <div className="product-row-heading">
                    <span>Product {String(index + 1).padStart(2, '0')}</span>
                    {form.productRequirements.length > 1 && (
                      <button type="button" className="text-button danger-button" onClick={() => {
                        update('productRequirements', form.productRequirements.filter((_, itemIndex) => itemIndex !== index))
                        setErrors((previous) => Object.fromEntries(Object.entries(previous).filter(([key]) => !key.startsWith('productRequirements.'))))
                      }}>
                        <Trash2 size={14} /> Remove
                      </button>
                    )}
                  </div>
                  <div className="form-grid product-grid">
                    <Field label="Product / model requested" htmlFor={`product-${index}`} error={errors[`productRequirements.${index}.product`]}>
                      <input id={`product-${index}`} maxLength={200} aria-invalid={!!errors[`productRequirements.${index}.product`]} aria-describedby={errors[`productRequirements.${index}.product`] ? `product-${index}-error` : undefined} placeholder="Enter the model or product name" value={item.product} onChange={(event) => updateProduct(index, 'product', event.target.value)} />
                    </Field>
                    <Field label="SKU (if known)" htmlFor={`sku-${index}`} error={errors[`productRequirements.${index}.sku`]}>
                      <input id={`sku-${index}`} maxLength={80} placeholder="Not verified" value={item.sku} onChange={(event) => updateProduct(index, 'sku', event.target.value)} />
                    </Field>
                    <Field label="Quantity" htmlFor={`quantity-${index}`} error={errors[`productRequirements.${index}.quantity`]}>
                      <input id={`quantity-${index}`} type="number" min="1" max="1000000" step="1" value={item.quantity} onChange={(event) => { const number = Number(event.target.value); updateProduct(index, 'quantity', Number.isFinite(number) ? number : 0) }} />
                    </Field>
                    <Field label="Unit" htmlFor={`unit-${index}`}>
                      <select id={`unit-${index}`} value={item.unit} onChange={(event) => updateProduct(index, 'unit', event.target.value)}>
                        <option value="nos">Nos</option><option value="sets">Sets</option><option value="units">Units</option><option value="sq ft">Sq ft</option><option value="other">Other</option>
                      </select>
                    </Field>
                    <Field label="Customer segment" htmlFor={`segment-${index}`}>
                      <input id={`segment-${index}`} maxLength={80} placeholder="e.g. Residential, hospitality" value={item.customerSegment} onChange={(event) => updateProduct(index, 'customerSegment', event.target.value)} />
                    </Field>
                    <Field label="Approximate budget (optional)" htmlFor={`budget-${index}`}>
                      <input id={`budget-${index}`} maxLength={80} inputMode="decimal" placeholder="As shared by customer" value={item.budget} onChange={(event) => updateProduct(index, 'budget', event.target.value)} />
                    </Field>
                    <Field label="Payment preference" htmlFor={`payment-${index}`}>
                      <select id={`payment-${index}`} value={item.paymentPreference} onChange={(event) => updateProduct(index, 'paymentPreference', event.target.value)}>
                        <option value="">Not discussed</option><option>Cash</option><option>Finance</option><option>Credit</option><option>Other</option>
                      </select>
                    </Field>
                    <Field label="Expected purchase timeline" htmlFor={`timeline-${index}`}>
                      <select id={`timeline-${index}`} value={item.timeline} onChange={(event) => updateProduct(index, 'timeline', event.target.value)}>
                        <option value="">Not discussed</option><option>Immediately</option><option>Within 1 month</option><option>1–3 months</option><option>More than 3 months</option><option>Not sure</option>
                      </select>
                    </Field>
                    <label className="checkbox-field form-grid-wide">
                      <input type="checkbox" checked={item.stockCheck} onChange={(event) => updateProduct(index, 'stockCheck', event.target.checked)} />
                      <span>Customer requested an availability / stock check</span>
                    </label>
                    <Field label="Additional product requirements" htmlFor={`product-notes-${index}`} className="form-grid-wide">
                      <textarea id={`product-notes-${index}`} rows={2} maxLength={2000} placeholder="Size, finish, application, or other requirements…" value={item.notes} onChange={(event) => updateProduct(index, 'notes', event.target.value)} />
                    </Field>
                  </div>
                </div>
              ))}
            </div>
            <button type="button" className="add-product-button" disabled={form.productRequirements.length >= 25} onClick={addProduct}><Plus size={16} /> Add another product</button>
          </section>

          <section className="enquiry-card" aria-labelledby="followup-section-title">
            <SectionHeader
              titleId="followup-section-title"
              icon={<ClipboardList size={18} />}
              eyebrow="NOTES & NEXT STEPS"
              title="Keep the conversation moving"
              description="Add the context and follow-up plan that will help your team."
              step="04"
            />
            <div className="form-grid">
              <Field label="Customer requirements summary" htmlFor="requirementsSummary" className="form-grid-wide">
                <textarea id="requirementsSummary" rows={3} maxLength={4000} placeholder="Summarise what matters most to the customer…" value={form.requirementsSummary} onChange={(event) => update('requirementsSummary', event.target.value)} />
              </Field>
              <Field label="Salesperson notes" htmlFor="salespersonNotes" className="form-grid-wide">
                <textarea id="salespersonNotes" rows={3} maxLength={4000} placeholder="Internal notes for your team…" value={form.salespersonNotes} onChange={(event) => update('salespersonNotes', event.target.value)} />
              </Field>
              <Field label="Next follow-up date" htmlFor="followUpDate" error={errors.followUpDate}>
                <input id="followUpDate" type="date" aria-invalid={!!errors.followUpDate} value={form.followUpDate} onChange={(event) => update('followUpDate', event.target.value)} />
              </Field>
              <Field label="Follow-up owner" htmlFor="followUpOwner">
                <input id="followUpOwner" maxLength={160} placeholder="Name of owner" value={form.followUpOwner} onChange={(event) => update('followUpOwner', event.target.value)} />
              </Field>
              <Field label="Priority" htmlFor="priority">
                <select id="priority" value={form.priority} onChange={(event) => update('priority', event.target.value as NewEnquiryForm['priority'])}>
                  <option>Normal</option><option>High</option><option>Urgent</option>
                </select>
              </Field>
            </div>
          </section>
        </div>

        <aside className="enquiry-side-column" aria-label="Enquiry summary and status">
          <section className="enquiry-card side-card">
            <div className="side-card-title"><span className="side-icon teal-side-icon"><ClipboardList size={16} /></span><h2>Enquiry at a glance</h2></div>
            <div className="summary-progress-label"><span>Required details</span><strong>{completedRequired} / 5</strong></div>
            <div className="summary-progress"><span style={{ width: `${completedRequired * 20}%` }} /></div>
            <div className="summary-person">
              <div className="summary-avatar"><UsersRound size={17} /></div>
              <div><strong>{form.customerName || 'Customer name'}</strong><span>{form.phone || 'Phone number'}</span></div>
            </div>
            <dl className="summary-list">
              <div><dt>Source</dt><dd>{form.channel}</dd></div>
              <div><dt>Branch</dt><dd>{form.branch || 'Not selected'}</dd></div>
              <div><dt>Assigned to</dt><dd>{form.assignedExecutive || 'Not assigned'}</dd></div>
              <div><dt>Interested in</dt><dd>{summaryProduct}</dd></div>
              <div><dt>Recording</dt><dd>{savedConversationId ? <span className="status-value"><Check size={13} /> Audio saved</span> : hasUnfinishedAudio ? 'Finish saving audio' : 'Not attached'}</dd></div>
            </dl>
          </section>

          <section className="enquiry-card side-card processing-card">
            <div className="side-card-title"><span className="side-icon purple-side-icon"><AudioLines size={16} /></span><h2>Conversation insights</h2></div>
            <div className="processing-state"><span className="state-indicator" /><div><strong>Original speech & speakers</strong><span>ElevenLabs transcribes saved conversation audio</span></div></div>
            <div className="processing-state"><span className="state-indicator" /><div><strong>English translation</strong><span>OpenAI translates the original transcript while preserving its speaker turns</span></div></div>
            <div className="insight-note"><Lightbulb size={15} /><span>{savedConversationId ? 'Save the enquiry to follow the actual transcript and processing status.' : 'Add consented audio to use transcription and translation. You can also save an enquiry without audio.'}</span></div>
          </section>

          <section className="enquiry-card side-card next-step-card">
            <div className="side-card-title"><span className="side-icon amber-side-icon"><CalendarDays size={16} /></span><h2>Suggested next step</h2></div>
            <p>{form.followUpDate ? `Follow up on ${new Date(`${form.followUpDate}T00:00:00`).toLocaleDateString()}` : 'Choose a follow-up date when you’re ready.'}</p>
            {form.followUpOwner && <span className="follow-up-owner"><Building2 size={13} /> {form.followUpOwner}</span>}
          </section>

          <div className="side-helper"><Headphones size={16} /><span>Need a hand? Your sales operations team can help with enquiry setup.</span></div>
        </aside>
      </div>
      </fieldset>

      <div className="enquiry-action-bar">
        <div className="action-context">
          <span className="action-status"><span className={`draft-dot${isDraftSaved ? ' saved' : ''}`} /> {isDraftSaved ? 'Draft saved in this tab' : 'Unsaved changes'}</span>
          {formMessage && <span className={`action-message${formMessage.startsWith('Check') ? ' error-message' : ''}`} role="status">{formMessage}</span>}
        </div>
        <div className="action-buttons">
          <button type="button" className="button button-quiet" disabled={isSaving || hasUnfinishedAudio} onClick={() => navigate('/enquiries')}>Cancel</button>
        <button type="button" className="button button-secondary" onClick={saveDraft} disabled={isSaving}><Save size={16} /> Save draft</button>
        <button type="submit" className="button button-primary" disabled={isSaving || hasUnfinishedAudio}>
          {isSaving ? <span className="button-spinner" /> : <Check size={16} />}
          {isSaving ? 'Saving…' : existing ? 'Save changes' : 'Save enquiry'}
          <ChevronDown className="button-arrow" size={15} />
        </button>
        </div>
      </div>
    </form>
  )
}
