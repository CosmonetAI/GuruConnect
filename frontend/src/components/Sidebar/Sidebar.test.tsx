import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ role: 'owner' }))
vi.mock('../../contexts/OfficeContext', () => ({
  useOffice: () => ({ workspace: { id: 'workspace-a', name: 'Workspace', role: mocks.role }, workspaces: [], setWorkspace: vi.fn(), captureBusy: false, user: { id: 'user-a', email: 'user@example.com' } }),
}))
vi.mock('../../hooks/useScopedResource', () => ({
  useScopedResource: () => ({ data: undefined, reload: vi.fn() }),
}))
vi.mock('../../services/officeApi', () => ({ supabase: null }))

import Sidebar from './Sidebar'

beforeEach(() => { mocks.role = 'owner' })

describe('role-based sidebar navigation', () => {
  it('shows owners the walk-in screen while keeping it hidden from salespeople', () => {
    const { rerender } = render(<MemoryRouter><Sidebar /></MemoryRouter>)
    expect(screen.getByRole('link', { name: 'Walk-in visits' })).toBeInTheDocument()

    mocks.role = 'salesperson'
    rerender(<MemoryRouter><Sidebar /></MemoryRouter>)
    expect(screen.queryByRole('link', { name: 'Walk-in visits' })).not.toBeInTheDocument()
  })

  it('keeps the receptionist navigation limited to walk-ins and settings', () => {
    mocks.role = 'receptionist'
    render(<MemoryRouter><Sidebar /></MemoryRouter>)
    expect(screen.getByRole('link', { name: 'Walk-in visits' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Team & account' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Overview' })).not.toBeInTheDocument()
  })

  it('keeps the owner Zoho Books screens available from navigation', () => {
    render(<MemoryRouter><Sidebar /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: /Zoho Books/ }))
    expect(screen.getByRole('link', { name: 'Contacts' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Invoices' })).toBeInTheDocument()
  })
})
