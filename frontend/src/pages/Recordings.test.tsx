import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ path: vi.fn() }))
vi.mock('../contexts/OfficeContext', () => ({ useOffice: () => ({ workspace: { id: 'w' } }) }))
vi.mock('../hooks/useScopedResource', () => ({ useScopedResource: (path: string) => { mocks.path(path); return { data: { items: [], next_cursor: path.includes('cursor=') ? null : 'older-recordings' }, reload: vi.fn() } } }))
import Recordings from './Recordings'
beforeEach(() => mocks.path.mockReset())
describe('complete conversation history', () => {
  it('requests older cursor pages and searches through the server rather than only a loaded page', async () => {
    render(<MemoryRouter><Recordings /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: 'Next recordings' }))
    expect(mocks.path).toHaveBeenLastCalledWith('/conversations?limit=100&cursor=older-recordings')
    expect(screen.getByRole('button', { name: 'Next recordings' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Previous recordings' }))
    expect(mocks.path).toHaveBeenLastCalledWith('/conversations?limit=100')
    fireEvent.change(screen.getByPlaceholderText('Search by name, phone, or email'), { target: { value: 'Old customer' } })
    await waitFor(() => expect(mocks.path).toHaveBeenLastCalledWith('/conversations?limit=100&q=Old+customer'))
  })
})
