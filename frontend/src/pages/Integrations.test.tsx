import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import Integrations from './Integrations'
vi.mock('../contexts/OfficeContext', () => ({ useOffice: () => ({ user: { id: 'u' }, workspace: { id: 'w', role: 'owner' } }) }))
vi.mock('../components/OfficeUi', async original => ({ ...await original<typeof import('../components/OfficeUi')>(), useOfficeResource: (path: string) => ({ reload: vi.fn(), data: path === '/connections' ? { items: [{ engine_id: 'elevenlabs', state: 'connected', safe_metadata: { platform_managed: true } }, { engine_id: 'openai-translator', state: 'connected', safe_metadata: { platform_managed: true } }] } : { default_engine_id: 'elevenlabs', server_managed_providers: true } }) }))
describe('server-managed processing status', () => {
  it('shows safe service readiness without browser credential or provider mutation controls', () => {
    const { container } = render(<Integrations />)
    expect(screen.getAllByText('connected')).toHaveLength(2)
    expect(screen.getByText(/managed by your administrator/i)).toBeInTheDocument()
    expect(container.querySelector('input[type="password"]')).toBeNull()
    expect(screen.queryByRole('button', { name: /Connect|Configure|Test connection|Disconnect/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refresh status' })).toBeInTheDocument()
  })
})
