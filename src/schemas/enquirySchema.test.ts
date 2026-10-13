import { describe, expect, it } from 'vitest'
import { NewEnquirySchema, productHasDetails } from './enquirySchema'

const blank = { product: '', sku: '', quantity: 1, unit: 'nos', customerSegment: '', budget: '', paymentPreference: '', timeline: '', stockCheck: false, notes: '' }
const form = { customerName: 'Synthetic Customer', phone: '9876543210', email: '', channel: 'Incoming Call', branch: 'Branch', assignedExecutive: 'Salesperson', customerType: 'Retail', company: '', projectName: '', preferredContactMethod: '', customerNotes: '', productRequirements: [blank], salespersonNotes: '', requirementsSummary: '', followUpDate: '', followUpOwner: '', priority: 'Normal' }
describe('enquiry limits and incomplete product rows', () => {
  it('keeps SKU-only rows while distinguishing a untouched optional row', () => {
    expect(productHasDetails(blank)).toBe(false)
    const parsed = NewEnquirySchema.parse({ ...form, productRequirements: [{ ...blank, sku: 'SKU', quantity: 3 }] })
    expect(parsed.productRequirements[0].sku).toBe('SKU')
    expect(productHasDetails(parsed.productRequirements[0])).toBe(true)
  })
  it.each([{ notes: 'Required finish' }, { quantity: 3 }, { budget: '10000' }, { stockCheck: true }, { unit: 'sets' }])('rejects populated unidentified rows: %j', changes => {
    expect(NewEnquirySchema.safeParse({ ...form, productRequirements: [{ ...blank, ...changes }] }).success).toBe(false)
  })
  it.each(['2026-02-30', '2026-13-01', 'not-a-date', '0000-01-01'])('rejects an invalid calendar date: %s', followUpDate => {
    expect(NewEnquirySchema.safeParse({ ...form, followUpDate }).success).toBe(false)
  })
  it('rejects large/malformed input before server submission', () => {
    expect(NewEnquirySchema.safeParse({ ...form, customerName: 'x'.repeat(201) }).success).toBe(false)
    expect(NewEnquirySchema.safeParse({ ...form, productRequirements: Array(26).fill({ ...blank, sku: 'SKU' }) }).success).toBe(false)
    expect(NewEnquirySchema.safeParse({ ...form, productRequirements: [{ ...blank, sku: 'SKU', quantity: 1_000_001 }] }).success).toBe(false)
    expect(NewEnquirySchema.safeParse({ ...form, productRequirements: Array(25).fill({ ...blank, sku: 'SKU', notes: '漢'.repeat(2000) }) }).success).toBe(false)
  })
})
