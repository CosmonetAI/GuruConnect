export const walkinPurposes = [
  'Product enquiry',
  'New business enquiry',
  'Product demonstration',
  'Pricing or quotation request',
  'Existing order enquiry',
  'Service or support request',
  'Meeting with a specific employee',
  'Follow-up meeting',
  'Other',
] as const

export const walkinStatuses = [
  'Registered',
  'Waiting for Assignment',
  'Assigned',
  'Waiting for Sales Agent',
  'In Discussion',
  'Requirements Captured',
  'Follow-up Required',
  'Converted to Enquiry',
  'Completed',
  'Cancelled',
] as const

export type WalkinPurpose = (typeof walkinPurposes)[number]
export type WalkinStatus = (typeof walkinStatuses)[number]
export type WalkinAgent = {
  id: string
  name: string
  department: string
  specialization: string
  availability: 'Available' | 'Busy' | 'Unavailable'
  openVisits: number | null
}

export type WalkinVisit = {
  id: string
  visitNumber: string
  customerName: string
  mobileNumber: string
  email: string
  companyName: string
  purpose: WalkinPurpose
  purposeDescription: string
  productOrService: string
  department: string
  requestedEmployee: string
  appointmentStatus: 'Walk-in' | 'Scheduled Appointment'
  appointmentReference: string
  registeredBy: string
  assignedAgentId: string | null
  assignedAgentName: string | null
  assignmentNote: string
  status: WalkinStatus
  checkInAt: string
  assignedAt: string | null
  internalNotes: string
  convertedEnquiryReference: string | null
  createdAt: string
  updatedAt: string
}

export type WalkinRequirement = {
  detailedRequirement: string
  productOrService: string
  quantity: string
  budgetRange: string
  expectedPurchaseTimeline: string
  preferredSolution: string
  competitorOrAlternative: string
  specialRequests: string
  followUpAt: string
  agentNotes: string
  nextAction: string
  updatedAt: string | null
}

export type WalkinHistoryEvent = {
  id: string
  visitId: string
  eventType: string
  oldValue: string | null
  newValue: string | null
  notes: string
  performedBy: string
  createdAt: string
}

export type WalkinRecording = {
  id: string
  visitId: string
  transcript: string
  startedAt: string
  endedAt: string
  durationSeconds: number
  status: 'saved'
  audioStoragePath: string | null
  consentAt: string
  createdBy: string
  createdAt: string
  updatedAt: string
}

export type RegisterWalkinInput = Pick<
  WalkinVisit,
  | 'customerName'
  | 'mobileNumber'
  | 'email'
  | 'companyName'
  | 'purpose'
  | 'purposeDescription'
  | 'productOrService'
  | 'department'
  | 'requestedEmployee'
  | 'appointmentStatus'
  | 'appointmentReference'
  | 'internalNotes'
>

export type WalkinFilters = {
  query?: string
  purpose?: string
  status?: string
  from?: string
  to?: string
  assignedAgentId?: string
}
