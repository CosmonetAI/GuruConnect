import type { Enquiry } from '../types/enquiry'
import { request, json } from './officeApi'
export interface EnquiryPage { items: Enquiry[]; total: number; page: number; pageSize: number; totalPages: number }
export type EnquiryInput = Omit<Enquiry, 'id' | 'enquiryNumber' | 'createdAt' | 'recording'>
export function createEnquiry(workspaceId: string, data: EnquiryInput, idempotencyKey: string) {
  return request<Enquiry>('/enquiries', workspaceId, json('POST', data, idempotencyKey))
}
export function updateEnquiry(workspaceId: string, id: string, data: EnquiryInput) {
  return request<Enquiry>(`/enquiries/${encodeURIComponent(id)}`, workspaceId, json('PATCH', data))
}
export function enquiryListPath(options: { page?: number; pageSize?: number; query?: string; channel?: string; branch?: string } = {}) {
  const params = new URLSearchParams({ page: String(options.page ?? 1), pageSize: String(options.pageSize ?? 10) })
  for (const key of ['query', 'channel', 'branch'] as const) if (options[key]) params.set(key, options[key]!)
  return `/enquiries?${params}`
}
