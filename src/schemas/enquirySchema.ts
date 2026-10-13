import { z } from 'zod'

export const NewEnquirySchema = z.object({
  customerName: z.string().trim().min(2, 'Enter at least 2 characters'),
  phone: z.string().trim().min(1, 'Enter a phone number').refine(
    (value) => {
      const digits = value.replace(/\D/g, '')
      return /^\+?[0-9().\-\s]{7,20}$/.test(value) && digits.length >= 7 && digits.length <= 15
    },
    'Enter a valid phone number',
  ),
  email: z.string().trim().refine(
    (value) => value.length === 0 || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value),
    'Enter a valid email address',
  ),
  channel: z.enum([
    'Incoming Call',
    'Outgoing Call',
    'Retail Walk-in',
    'Dealership',
    'Project',
    'Supported WhatsApp',
    'Email',
    'Other',
  ]),
  branch: z.string().trim().min(1, 'Select branch'),
  assignedExecutive: z.string().trim().min(1, 'Assign an executive'),
  customerType: z.enum(['Retail', 'Dealer', 'Project']),
  company: z.string(),
  projectName: z.string(),
  preferredContactMethod: z.string(),
  customerNotes: z.string(),
  productRequirements: z.array(z.object({
    product: z.string(),
    sku: z.string(),
    quantity: z.number().int().positive('Quantity must be greater than 0'),
    unit: z.string(),
    customerSegment: z.string(),
    budget: z.string(),
    paymentPreference: z.string(),
    timeline: z.string(),
    stockCheck: z.boolean(),
    notes: z.string(),
  })),
  salespersonNotes: z.string(),
  requirementsSummary: z.string(),
  followUpDate: z.string(),
  followUpOwner: z.string(),
  priority: z.enum(['Normal', 'High', 'Urgent']),
})

export type NewEnquiryForm = z.infer<typeof NewEnquirySchema>
