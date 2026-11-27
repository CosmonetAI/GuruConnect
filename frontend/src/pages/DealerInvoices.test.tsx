import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import DealerInvoices from './DealerInvoices'

vi.mock('../contexts/OfficeContext', () => ({ useOffice: () => ({ workspace: { id: 'workspace', member_id: 'dealer' } }) }))
vi.mock('../hooks/useScopedResource', () => ({
  useScopedResource: () => ({ data: { items: [
    { invoice_id: 'draft-123', invoice_number: 'INV-1', status: 'draft', can_update: true },
    { invoice_id: 'paid-123', invoice_number: 'INV-2', status: 'paid', can_update: false },
  ] } }),
}))

function CartLocation() {
  const location = useLocation()
  return <p>Cart: {location.search}</p>
}

it('opens an editable invoice in the cart but does not link a paid invoice', () => {
  render(<MemoryRouter initialEntries={['/dealer/invoices']}><Routes>
    <Route path="/dealer/invoices" element={<DealerInvoices />} />
    <Route path="/dealer/add-to-cart" element={<CartLocation />} />
  </Routes></MemoryRouter>)
  expect(screen.queryByRole('link', { name: 'INV-2' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('link', { name: 'INV-1' }))
  expect(screen.getByText('Cart: ?invoice=draft-123')).toBeInTheDocument()
})
