export type Enquiry = {
  id: string
  enquiryNumber?: string
  conversationId?: string
  customerNotes?: string
  salespersonNotes?: string
  requirementsSummary?: string
  recording?: { captureState: string; uploadState: string; transcriptionState: string; englishState: string; speakerState: string; finalState: string; durationSeconds?: number }
  customerName: string
  phone?: string
  email?: string
  channel: 'Call' | 'Dealership' | 'Retail walk-in' | 'Project' | 'Chat' | string
  branch: string
  assignedExecutive: string
  product: string
  quantity: number
  unit: string
  notes?: string
  sourceEventId?: string
  customerType?: 'Retail' | 'Dealer' | 'Project'
  company?: string
  projectName?: string
  preferredContactMethod?: string
  productRequirements?: ProductRequirement[]
  followUpDate?: string
  followUpOwner?: string
  priority?: 'Normal' | 'High' | 'Urgent'
  createdAt: string
}

export type ProductRequirement = {
  product: string
  sku: string
  quantity: number
  unit: string
  customerSegment: string
  budget: string
  paymentPreference: string
  timeline: string
  stockCheck: boolean
  notes: string
}
