import { useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  AlertCircle,
  ArrowLeft,
  AudioLines,
  Building2,
  CalendarDays,
  Check,
  ChevronDown,
  CircleHelp,
  ClipboardList,
  FileAudio2,
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
import { NewEnquirySchema, type NewEnquiryForm } from '../../schemas/enquirySchema'
import { createEnquiry, getDemoSalesOptions } from '../../services/demoService'
import { getWalkinVisit, isWalkinDemoMode, markWalkinConverted } from '../../services/walkinService'
import ConversationRecorder from './ConversationRecorder'
import './NewEnquiry.css'

const draftStorageKey = 'guru-group:new-enquiry-draft'
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

function isEnquiryDraft(value: unknown): value is NewEnquiryForm {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Record<string, unknown>
  const stringFields = [
    'customerName', 'phone', 'email', 'branch', 'assignedExecutive', 'company', 'projectName',
    'preferredContactMethod', 'customerNotes', 'salespersonNotes', 'requirementsSummary',
    'followUpDate', 'followUpOwner',
  ]
  if (stringFields.some((field) => typeof candidate[field] !== 'string')) return false
  if (!enquirySources.includes(candidate.channel as typeof enquirySources[number])) return false
  if (!['Retail', 'Dealer', 'Project'].includes(String(candidate.customerType))) return false
  if (!['Normal', 'High', 'Urgent'].includes(String(candidate.priority))) return false
  if (!Array.isArray(candidate.productRequirements)) return false
  return candidate.productRequirements.every((item: unknown) => {
    if (!item || typeof item !== 'object') return false
    const product = item as Record<string, unknown>
    return ['product', 'sku', 'unit', 'customerSegment', 'budget', 'paymentPreference', 'timeline', 'notes']
      .every((field) => typeof product[field] === 'string')
      && typeof product.quantity === 'number'
      && typeof product.stockCheck === 'boolean'
  })
}

function readDraft(): { values: NewEnquiryForm; warning: string; restored: boolean } {
  try {
    const saved = sessionStorage.getItem(draftStorageKey)
    if (!saved) return { values: defaultValues, warning: '', restored: false }
    const parsed: unknown = JSON.parse(saved)
    if (!isEnquiryDraft(parsed)) {
      return {
        values: defaultValues,
        warning: 'The saved draft could not be restored because its data is incomplete. You can start a new enquiry.',
        restored: false,
      }
    }
    return { values: parsed, warning: 'Your draft was restored from this browser tab.', restored: true }
  } catch (error) {
    const reason = error instanceof Error ? error.message : 'browser storage is unavailable'
    return { values: defaultValues, warning: `The saved draft could not be read (${reason}). You can continue with a new enquiry.`, restored: false }
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
      {error && <span className="field-error" role="alert">{error}</span>}
      {!error && hint && <span className="field-hint">{hint}</span>}
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

export default function NewEnquiry() {
  const navigate = useNavigate()
  const location = useLocation()
  const walkinConversion = (location.state as {
    walkinConversion?: {
      visitId: string
      visitNumber: string
      customerName: string
      phone: string
      email: string
      company: string
      assignedExecutive: string
      purpose: string
      productOrService: string
      quantity: string
      budget: string
      timeline: string
      requirements: string
      notes: string
      followUpDate: string
    }
  } | null)?.walkinConversion
  const [draft] = useState(readDraft)
  const [form, setForm] = useState<NewEnquiryForm>(() => {
    if (!walkinConversion) return draft.values
    const product = walkinConversion.productOrService
    return {
      ...draft.values,
      customerName: walkinConversion.customerName,
      phone: walkinConversion.phone,
      email: walkinConversion.email,
      channel: 'Retail Walk-in',
      assignedExecutive: walkinConversion.assignedExecutive,
      company: walkinConversion.company,
      customerNotes: `${walkinConversion.purpose} · Visit ${walkinConversion.visitNumber}\n${walkinConversion.notes}`.trim(),
      requirementsSummary: walkinConversion.requirements,
      followUpDate: walkinConversion.followUpDate,
      productRequirements: [{
        ...defaultValues.productRequirements[0],
        product,
        quantity: /^\d+$/.test(walkinConversion.quantity) && Number(walkinConversion.quantity) > 0
          ? Number(walkinConversion.quantity)
          : 0,
        budget: walkinConversion.budget,
        timeline: walkinConversion.timeline,
        notes: walkinConversion.requirements,
      }],
    }
  })
  const [errors, setErrors] = useState<FieldErrors>({})
  const [formMessage, setFormMessage] = useState(draft.warning)
  const [isSaving, setIsSaving] = useState(false)
  const [isDraftSaved, setIsDraftSaved] = useState(draft.restored)
  const savingRef = useRef(false)
  const [recordingClip, setRecordingClip] = useState<{ duration: number; name: string } | null>(null)
  const demoOptions = useMemo(() => getDemoSalesOptions(), [])
  const recorderStopRef = useRef<(() => void) | null>(null)
  const recorderIsRecordingRef = useRef<boolean | null>(null)
  const [isRecording, setIsRecording] = useState(false)

  const update = <K extends EnquiryField>(field: K, value: NewEnquiryForm[K]) => {
    setForm((previous) => ({ ...previous, [field]: value }))
    setErrors((previous) => {
      const next = { ...previous }
      delete next[field]
      return next
    })
    setIsDraftSaved(false)
    setFormMessage('')
  }

  const filledRequirements = form.productRequirements.filter((item) => item.product.trim() !== '')
  const requiredValues = [form.customerName, form.phone, form.channel, form.branch, form.assignedExecutive]
  const completedRequired = requiredValues.filter((value) => value.trim() !== '').length
  const summaryProduct = filledRequirements.length
    ? filledRequirements.map((item) => item.product).join(', ')
    : 'Not specified yet'

  const saveDraft = () => {
    try {
      sessionStorage.setItem(draftStorageKey, JSON.stringify(form))
      setIsDraftSaved(true)
      setFormMessage('Draft saved for this browser session. No enquiry has been submitted.')
      setErrors({})
    } catch (error) {
      setIsDraftSaved(false)
      const reason = error instanceof Error ? error.message : 'browser storage is unavailable'
      setFormMessage(`This browser could not save the draft (${reason}). Your current entries are still on this page.`)
    }
  }

  const submitEnquiry = async () => {
    if (savingRef.current) return
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
      if (walkinConversion) {
        if (!isWalkinDemoMode()) {
          throw new Error('The existing enquiry service is demo-only. Configure its production API before converting a walk-in.')
        }
        const visit = await getWalkinVisit(walkinConversion.visitId)
        if (!visit) throw new Error('The source walk-in visit could not be found. The enquiry was not created.')
        if (visit.convertedEnquiryReference) {
          navigate(`/walkins/${walkinConversion.visitId}`, { state: { confirmation: `This visit was already converted to enquiry ${visit.convertedEnquiryReference}.` } })
          return
        }
      }
      const created = createEnquiry({
        customerName: data.customerName,
        phone: data.phone,
        email: data.email || undefined,
        channel: data.channel,
        branch: data.branch,
        assignedExecutive: data.assignedExecutive,
        product: data.productRequirements.find((item) => item.product.trim())?.product ?? '',
        quantity: data.productRequirements.find((item) => item.product.trim())?.quantity ?? 1,
        unit: data.productRequirements.find((item) => item.product.trim())?.unit ?? 'nos',
        notes: [data.customerNotes, data.salespersonNotes, data.requirementsSummary]
          .filter(Boolean)
          .join('\n\n'),
        customerType: data.customerType,
        company: data.company,
        projectName: data.projectName,
        preferredContactMethod: data.preferredContactMethod,
        productRequirements: data.productRequirements.filter((item) => item.product.trim()),
        followUpDate: data.followUpDate,
        followUpOwner: data.followUpOwner,
        priority: data.priority,
        sourceWalkinId: walkinConversion?.visitId,
      })
      if (walkinConversion) await markWalkinConverted(walkinConversion.visitId, created.id)
      let draftCleanupMessage = ''
      try {
        sessionStorage.removeItem(draftStorageKey)
      } catch {
        draftCleanupMessage = ' The saved draft could not be removed from this browser tab.'
      }
      navigate('/enquiries', {
        state: {
          confirmation: `Enquiry ${created.id} was added to demo data in this browser session.${walkinConversion ? ` Linked to walk-in ${walkinConversion.visitNumber}.` : ''}${draftCleanupMessage}`,
        },
      })
    } catch (error) {
      setFormMessage(error instanceof Error ? error.message : 'The enquiry could not be saved. Your entries are preserved.')
    } finally {
      savingRef.current = false
      setIsSaving(false)
    }
  }

  const addProduct = () => {
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
    <form className="new-enquiry-page" onSubmit={(event) => { event.preventDefault(); void submitEnquiry() }}>
      <div className="enquiry-topline">
        <Link className="back-link" to="/enquiries"><ArrowLeft size={16} /> Back to enquiries</Link>
        <span className="enquiry-id-label"><span className="draft-dot" /> New enquiry</span>
      </div>

      <header className="enquiry-page-header">
        <div>
          <div className="page-kicker"><Sparkles size={14} /> SALES WORKSPACE</div>
          <h1>Capture a new enquiry</h1>
          <p>Start with the essentials. You can add more detail as the conversation develops.</p>
        </div>
        <div className="required-note"><span className="required-mark">*</span> Required fields</div>
      </header>

      <div className="demo-notice" role="status">
        <CircleHelp size={17} />
        <p><strong>Demo mode.</strong> Enquiries are kept in browser memory and reset on reload. Drafts are saved only for this browser tab. No database, audio storage, or transcription service is configured.</p>
      </div>
      {walkinConversion && <div className="demo-notice" role="status">
        <ClipboardList size={17} />
        <p><strong>Review before converting.</strong> Customer and requirement details were transferred from walk-in {walkinConversion.visitNumber}. Confirm the branch, customer type, product, quantity, and all requirements before saving. The conversion is linked to the source visit and duplicate submissions are prevented.</p>
      </div>}

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
              <Field label="Customer full name" htmlFor="customerName" required error={errors.customerName}>
                <input id="customerName" autoComplete="name" placeholder="e.g. Priya Sharma" value={form.customerName} onChange={(event) => update('customerName', event.target.value)} />
              </Field>
              <Field label="Phone number" htmlFor="phone" required error={errors.phone}>
                <input id="phone" type="tel" autoComplete="tel" inputMode="tel" placeholder="+91 98765 43210" value={form.phone} onChange={(event) => update('phone', event.target.value)} />
              </Field>
              <Field label="Email address" htmlFor="email" error={errors.email}>
                <input id="email" type="email" autoComplete="email" placeholder="name@company.com" value={form.email} onChange={(event) => update('email', event.target.value)} />
              </Field>
              <Field label="Enquiry source" htmlFor="channel" required error={errors.channel}>
                <select id="channel" value={form.channel} onChange={(event) => update('channel', event.target.value as NewEnquiryForm['channel'])}>
                  {enquirySources.map((source) => <option key={source}>{source}</option>)}
                </select>
              </Field>
              <Field label="Branch / showroom" htmlFor="branch" required hint={demoOptions.branches.length ? 'Options come from existing demo enquiry examples; you can enter another branch.' : undefined} error={errors.branch}>
                <input id="branch" list="branch-options" autoComplete="organization" placeholder="Search or enter a branch" value={form.branch} onChange={(event) => update('branch', event.target.value)} />
                <datalist id="branch-options">{demoOptions.branches.map((branch) => <option key={branch} value={branch} />)}</datalist>
              </Field>
              <Field label="Assigned sales executive" htmlFor="assignedExecutive" required hint={demoOptions.executives.length ? 'Options come from existing demo enquiry examples; you can enter another name.' : undefined} error={errors.assignedExecutive}>
                <input id="assignedExecutive" list="executive-options" autoComplete="off" placeholder="Search or enter a name" value={form.assignedExecutive} onChange={(event) => update('assignedExecutive', event.target.value)} />
                <datalist id="executive-options">{demoOptions.executives.map((executive) => <option key={executive} value={executive} />)}</datalist>
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
                  <input id="company" autoComplete="organization" placeholder="Business or dealership name" value={form.company} onChange={(event) => update('company', event.target.value)} />
                </Field>
              )}
              {form.customerType === 'Project' && (
                <Field label="Project / site name" htmlFor="projectName">
                  <input id="projectName" placeholder="Project or site name" value={form.projectName} onChange={(event) => update('projectName', event.target.value)} />
                </Field>
              )}
              <Field label="Additional customer notes" htmlFor="customerNotes" className="form-grid-wide">
                <textarea id="customerNotes" rows={3} placeholder="Anything useful to remember about this customer…" value={form.customerNotes} onChange={(event) => update('customerNotes', event.target.value)} />
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
            <ConversationRecorder onClipChange={setRecordingClip} stopRef={recorderStopRef} isRecordingRef={recorderIsRecordingRef} onRecordingChange={setIsRecording} />
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
                      <input id={`product-${index}`} placeholder="Enter the model or product name" value={item.product} onChange={(event) => updateProduct(index, 'product', event.target.value)} />
                    </Field>
                    <Field label="SKU (if known)" htmlFor={`sku-${index}`}>
                      <input id={`sku-${index}`} placeholder="Not verified" value={item.sku} onChange={(event) => updateProduct(index, 'sku', event.target.value)} />
                    </Field>
                    <Field label="Quantity" htmlFor={`quantity-${index}`} error={errors[`productRequirements.${index}.quantity`]}>
                      <input id={`quantity-${index}`} type="number" min="1" step="1" placeholder="Confirm with customer" value={item.quantity || ''} onChange={(event) => updateProduct(index, 'quantity', event.target.value === '' ? 0 : Number(event.target.value))} />
                    </Field>
                    <Field label="Unit" htmlFor={`unit-${index}`}>
                      <select id={`unit-${index}`} value={item.unit} onChange={(event) => updateProduct(index, 'unit', event.target.value)}>
                        <option value="nos">Nos</option><option value="sets">Sets</option><option value="units">Units</option><option value="sq ft">Sq ft</option><option value="other">Other</option>
                      </select>
                    </Field>
                    <Field label="Customer segment" htmlFor={`segment-${index}`}>
                      <input id={`segment-${index}`} placeholder="e.g. Residential, hospitality" value={item.customerSegment} onChange={(event) => updateProduct(index, 'customerSegment', event.target.value)} />
                    </Field>
                    <Field label="Approximate budget (optional)" htmlFor={`budget-${index}`}>
                      <input id={`budget-${index}`} inputMode="decimal" placeholder="As shared by customer" value={item.budget} onChange={(event) => updateProduct(index, 'budget', event.target.value)} />
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
                      <textarea id={`product-notes-${index}`} rows={2} placeholder="Size, finish, application, or other requirements…" value={item.notes} onChange={(event) => updateProduct(index, 'notes', event.target.value)} />
                    </Field>
                  </div>
                </div>
              ))}
            </div>
            <button type="button" className="add-product-button" onClick={addProduct}><Plus size={16} /> Add another product</button>
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
                <textarea id="requirementsSummary" rows={3} placeholder="Summarise what matters most to the customer…" value={form.requirementsSummary} onChange={(event) => update('requirementsSummary', event.target.value)} />
              </Field>
              <Field label="Salesperson notes" htmlFor="salespersonNotes" className="form-grid-wide">
                <textarea id="salespersonNotes" rows={3} placeholder="Internal notes for your team…" value={form.salespersonNotes} onChange={(event) => update('salespersonNotes', event.target.value)} />
              </Field>
              <Field label="Next follow-up date" htmlFor="followUpDate">
                <input id="followUpDate" type="date" min={new Date().toISOString().slice(0, 10)} value={form.followUpDate} onChange={(event) => update('followUpDate', event.target.value)} />
              </Field>
              <Field label="Follow-up owner" htmlFor="followUpOwner">
                <input id="followUpOwner" list="executive-options" placeholder="Name of owner" value={form.followUpOwner} onChange={(event) => update('followUpOwner', event.target.value)} />
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
              <div><dt>Recording</dt><dd>{recordingClip ? <span className="status-value"><Check size={13} /> Local preview ready</span> : 'Not attached'}</dd></div>
            </dl>
          </section>

          <section className="enquiry-card side-card processing-card">
            <div className="side-card-title"><span className="side-icon purple-side-icon"><AudioLines size={16} /></span><h2>Conversation insights</h2></div>
            <div className="processing-state"><span className="state-indicator unavailable" /><div><strong>Transcription unavailable</strong><span>No transcription service is configured</span></div></div>
            <div className="processing-state"><span className="state-indicator unavailable" /><div><strong>AI details unavailable</strong><span>Suggestions appear only after a real service response</span></div></div>
            <div className="insight-note"><Lightbulb size={15} /><span>No transcript or AI-extracted details will be fabricated. Your typed notes are always yours to review.</span></div>
          </section>

          <section className="enquiry-card side-card next-step-card">
            <div className="side-card-title"><span className="side-icon amber-side-icon"><CalendarDays size={16} /></span><h2>Suggested next step</h2></div>
            <p>{form.followUpDate ? `Follow up on ${new Date(`${form.followUpDate}T00:00:00`).toLocaleDateString()}` : 'Choose a follow-up date when you’re ready.'}</p>
            {form.followUpOwner && <span className="follow-up-owner"><Building2 size={13} /> {form.followUpOwner}</span>}
          </section>

          <div className="side-helper"><Headphones size={16} /><span>Need a hand? Your sales operations team can help with enquiry setup.</span></div>
          {recordingClip && <span className="sr-only"><FileAudio2 size={1} /> {recordingClip.name}, {recordingClip.duration} seconds</span>}
        </aside>
      </div>

      <div className="enquiry-action-bar">
        <div className="action-context">
          <span className="action-status"><span className={`draft-dot${isDraftSaved ? ' saved' : ''}`} /> {isDraftSaved ? 'Draft saved in this tab' : 'Unsaved changes'}</span>
          {formMessage && <span className={`action-message${formMessage.startsWith('Check') ? ' error-message' : ''}`} role="status">{formMessage}</span>}
        </div>
        <div className="action-buttons">
          <button type="button" className="button button-quiet" onClick={() => navigate('/enquiries')}>Cancel</button>
        {isRecording && (
          <button type="button" className="button button-stop" onClick={() => { recorderStopRef.current?.(); }} title="Stop recording">Stop recording</button>
        )}
        <button type="button" className="button button-secondary" onClick={saveDraft} disabled={isSaving}><Save size={16} /> Save draft</button>
        <button type="submit" className="button button-primary" disabled={isSaving}>
          {isSaving ? <span className="button-spinner" /> : <Check size={16} />}
          {isSaving ? 'Saving…' : 'Save enquiry'}
          <ChevronDown className="button-arrow" size={15} />
        </button>
        </div>
      </div>
    </form>
  )
}
