import { z } from 'zod'

const text = (maximum: number) => z.string().trim().max(maximum, `Use at most ${maximum} characters`)
export const ProductRequirementValuesSchema = z.object({
  product: text(200), sku: text(80),
  quantity: z.number().int().positive('Quantity must be greater than 0').max(1_000_000),
  unit: text(32).min(1, 'Select a unit'), customerSegment: text(80), budget: text(80),
  paymentPreference: text(80), timeline: text(120), stockCheck: z.boolean(), notes: text(2000),
})

/** The untouched optional row may be omitted; populated rows are never dropped. */
export function productHasDetails(item: z.infer<typeof ProductRequirementValuesSchema>) {
  return !!(item.product.trim() || item.sku.trim() || item.customerSegment.trim() || item.budget.trim()
    || item.paymentPreference.trim() || item.timeline.trim() || item.notes.trim() || item.stockCheck
    || item.quantity !== 1 || item.unit !== 'nos')
}
const ProductRequirementSchema = ProductRequirementValuesSchema.superRefine((item, context) => {
  if (productHasDetails(item) && !item.product && !item.sku) context.addIssue({ code: 'custom', path: ['product'], message: 'Enter a product name or SKU for these requirements' })
})
const ValuesSchema = z.object({
  customerName: text(200).min(2, 'Enter at least 2 characters'),
  phone: text(20).min(1, 'Enter a phone number').refine(
    value => /^\+?[0-9().\-\s]{7,20}$/.test(value) && value.replace(/\D/g, '').length >= 7 && value.replace(/\D/g, '').length <= 15,
    'Enter a valid phone number',
  ),
  email: text(320).refine(value => !value || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value), 'Enter a valid email address'),
  channel: z.enum(['Incoming Call', 'Outgoing Call', 'Retail Walk-in', 'Dealership', 'Project', 'Supported WhatsApp', 'Email', 'Other']),
  branch: text(160).min(1, 'Enter a branch'), assignedExecutive: text(160).min(1, 'Assign an executive'),
  customerType: z.enum(['Retail', 'Dealer', 'Project']), company: text(200), projectName: text(200),
  preferredContactMethod: text(80), customerNotes: text(4000),
  productRequirements: z.array(ProductRequirementSchema).max(25, 'Add at most 25 product requirements'),
  salespersonNotes: text(4000), requirementsSummary: text(4000),
  followUpDate: text(10).refine(value => {
    if (!value) return true
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith('0000')) return false
    const parsed = new Date(`${value}T00:00:00Z`)
    return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
  }, 'Enter a valid calendar date'),
  followUpOwner: text(160), priority: z.enum(['Normal', 'High', 'Urgent']),
})
export const NewEnquirySchema = ValuesSchema.superRefine((value, context) => {
  if (new TextEncoder().encode(JSON.stringify(value)).length > 90_000) context.addIssue({ code: 'custom', path: ['productRequirements'], message: 'These details are too large. Shorten notes or product requirements before saving.' })
})

// Incomplete drafts remain editable, with bounded text and a known structure.
export const EnquiryDraftValuesSchema = ValuesSchema.extend({
  customerName: text(200), phone: text(20), email: text(320), branch: text(160), assignedExecutive: text(160), followUpDate: text(10),
  productRequirements: z.array(ProductRequirementValuesSchema.extend({ quantity: z.number().finite() })).max(25),
}).strict()
export type NewEnquiryForm = z.infer<typeof NewEnquirySchema>
