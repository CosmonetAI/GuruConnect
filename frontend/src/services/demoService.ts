import type { Enquiry } from '../types/enquiry'

let enquiries: Enquiry[] = [
  {
    id: 'ENQ-2026-0001',
    customerName: 'ABC Constructions',
    phone: '9876543210',
    email: 'info@abc.co',
    channel: 'Project',
    branch: 'Mumbai',
    assignedExecutive: 'Rajesh',
    product: 'Model X',
    quantity: 2,
    unit: 'nos',
    notes: 'Requires site visit',
    sourceEventId: undefined,
    createdAt: '2026-10-10',
  },
  {
    id: 'ENQ-2026-0002',
    customerName: 'Retail XYZ',
    phone: '9123456780',
    email: 'contact@retailxyz.com',
    channel: 'Retail walk-in',
    branch: 'Bengaluru',
    assignedExecutive: 'Sneha',
    product: 'Model A',
    quantity: 1,
    unit: 'nos',
    notes: '',
    sourceEventId: undefined,
    createdAt: '2026-10-09',
  },
]

export function getDashboardDemo() {
  return {
    totals: {
      totalEnquiries: enquiries.length,
      newEnquiries: enquiries.filter((e) => new Date(e.createdAt) > new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)).length,
      pendingApprovals: 3,
      sentQuotations: 98,
      overdueFollowups: 4,
      conversionRate: 21.3,
    },
    funnel: [
      { name: 'Contacted', value: 800 },
      { name: 'Interested', value: 400 },
      { name: 'Quoted', value: 150 },
      { name: 'Won', value: 32 },
    ],
    recent: enquiries.slice(0, 5).map((e) => ({ id: e.id, customer: e.customerName, channel: e.channel, date: e.createdAt })),
  }
}

export function listEnquiries(opts: { page?: number; pageSize?: number; query?: string; channel?: string; branch?: string } = {}) {
  const { page = 1, pageSize = 10, query = '', channel, branch } = opts
  let items = enquiries.slice().reverse()
  if (query) {
    const q = query.toLowerCase()
    items = items.filter((e) => e.customerName.toLowerCase().includes(q) || e.id.toLowerCase().includes(q) || (e.phone || '').includes(q))
  }
  if (channel) items = items.filter((e) => e.channel === channel)
  if (branch) items = items.filter((e) => e.branch === branch)
  const total = items.length
  const start = (page - 1) * pageSize
  const paged = items.slice(start, start + pageSize)
  return { items: paged, total }
}

export function getEnquiryById(id: string) {
  return enquiries.find((e) => e.id === id) || null
}

export function getDemoSalesOptions() {
  return {
    branches: [...new Set(enquiries.map((enquiry) => enquiry.branch).filter(Boolean))].sort(),
    executives: [...new Set(enquiries.map((enquiry) => enquiry.assignedExecutive).filter(Boolean))].sort(),
  }
}

export function createEnquiry(data: Omit<Enquiry, 'id' | 'createdAt'>) {
  const existing = data.sourceWalkinId
    ? enquiries.find((enquiry) => enquiry.sourceWalkinId === data.sourceWalkinId)
    : undefined
  if (existing) return existing
  const id = `ENQ-${new Date().getFullYear()}-${String(enquiries.length + 1).padStart(4, '0')}`
  const createdAt = new Date().toISOString().slice(0, 10)
  const enq: Enquiry = { id, createdAt, ...data }
  enquiries.push(enq)
  return enq
}
