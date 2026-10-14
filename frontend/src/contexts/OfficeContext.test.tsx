import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Session } from '@supabase/supabase-js'

const mocks = vi.hoisted(() => ({
  configured: true, localMode: false, cloud: true, request: vi.fn(), getSession: vi.fn(), unsubscribe: vi.fn(),
  handler: null as ((event: string, session: unknown) => void) | null,
}))
vi.mock('../services/officeApi', () => ({
  get configured() { return mocks.configured }, get localMode() { return mocks.localMode },
  get supabase() { return mocks.cloud ? { auth: { getSession: mocks.getSession, onAuthStateChange: (callback: (event: string, session: unknown) => void) => { mocks.handler = callback; return { data: { subscription: { unsubscribe: mocks.unsubscribe } } } } } } : null },
  request: mocks.request, message: (error: unknown) => error instanceof Error ? error.message : 'Workspace error',
}))
import { OfficeProvider, useOffice } from './OfficeContext'

function Probe() {
  const state = useOffice()
  return <div><output data-testid="identity">{state.user?.id || 'anonymous'}</output><output data-testid="workspace">{state.workspace?.name || 'none'}</output><output data-testid="mode">{state.configured ? 'available' : 'unconfigured'}:{state.localMode ? 'local' : 'cloud'}</output><output data-testid="busy">{String(state.captureBusy)}</output><output data-testid="error">{state.error}</output>
    <button onClick={() => state.setCaptureBusy(true)}>Capture active</button><button onClick={() => state.setCaptureBusy(false)}>Capture stopped</button><button onClick={() => state.setWorkspace('workspace-b')}>Choose B</button>
  </div>
}
const session = (id: string): Session => ({ user: { id, email: `${id}@example.test`, user_metadata: { display_name: id }, app_metadata: {}, aud: 'authenticated', created_at: '2026-10-13T00:00:00Z' }, access_token: `fixture-${id}`, refresh_token: `fixture-refresh-${id}`, expires_in: 3600, token_type: 'bearer' })
function setup() { return render(<OfficeProvider><Probe /></OfficeProvider>) }
beforeEach(() => {
  vi.clearAllMocks(); mocks.configured = true; mocks.localMode = false; mocks.cloud = true; mocks.handler = null
  mocks.getSession.mockResolvedValue({ data: { session: null }, error: null })
  mocks.request.mockResolvedValue({ items: [{ id: 'workspace-a', name: 'Private workspace A', role: 'owner' }, { id: 'workspace-b', name: 'Private workspace B', role: 'owner' }] })
})
describe('workspace identity and explicit local mode', () => {
  it('provides only the fixed local account and local workspace when loopback local mode is enabled', () => {
    mocks.configured = false; mocks.localMode = true; mocks.cloud = false; setup()
    expect(screen.getByTestId('identity')).toHaveTextContent('00000000-0000-0000-0000-000000000001')
    expect(screen.getByTestId('workspace')).toHaveTextContent('Local workspace')
    expect(screen.getByTestId('mode')).toHaveTextContent('available:local')
    expect(mocks.getSession).not.toHaveBeenCalled(); expect(mocks.request).not.toHaveBeenCalled()
  })
  it('does not create a local identity when public production configuration is missing', () => {
    mocks.configured = false; mocks.localMode = false; mocks.cloud = false; setup()
    expect(screen.getByTestId('identity')).toHaveTextContent('anonymous'); expect(screen.getByTestId('workspace')).toHaveTextContent('none')
    expect(screen.getByTestId('mode')).toHaveTextContent('unconfigured:cloud')
    expect(mocks.request).not.toHaveBeenCalled()
  })
  it('ignores a stale initial session after a newer sign-out event', async () => {
    let resolveSession: (value: object) => void = () => undefined
    mocks.getSession.mockImplementation(() => new Promise(resolve => { resolveSession = resolve }))
    setup()
    act(() => { mocks.handler?.('SIGNED_OUT', null) })
    await act(async () => { resolveSession({ data: { session: session('alice') } }) })
    expect(screen.getByTestId('identity')).toHaveTextContent('anonymous'); expect(screen.getByTestId('workspace')).toHaveTextContent('none')
    expect(mocks.request).not.toHaveBeenCalled()
  })
  it('clears the previous private workspace immediately and ignores its delayed list on account switch', async () => {
    let resolveAlice: (value: object) => void = () => undefined
    mocks.request.mockImplementationOnce(() => new Promise(resolve => { resolveAlice = resolve })).mockResolvedValueOnce({ items: [{ id: 'workspace-b', name: 'Private Bob workspace', role: 'owner' }] })
    setup(); act(() => { mocks.handler?.('SIGNED_IN', session('alice')) })
    await waitFor(() => expect(mocks.request).toHaveBeenCalledOnce())
    act(() => { mocks.handler?.('SIGNED_IN', session('bob')) })
    expect(screen.getByTestId('identity')).toHaveTextContent('bob'); expect(screen.getByTestId('workspace')).toHaveTextContent('none')
    await waitFor(() => expect(screen.getByTestId('workspace')).toHaveTextContent('Private Bob workspace'))
    await act(async () => { resolveAlice({ items: [{ id: 'workspace-a', name: 'Private Alice workspace', role: 'owner' }] }) })
    expect(screen.getByTestId('workspace')).toHaveTextContent('Private Bob workspace')
  })
  it('prevents workspace switching while audio is active and clears access on sign-out', async () => {
    setup(); act(() => { mocks.handler?.('SIGNED_IN', session('alice')) })
    await waitFor(() => expect(screen.getByTestId('workspace')).toHaveTextContent('Private workspace A'))
    fireEvent.click(screen.getByRole('button', { name: 'Capture active' })); fireEvent.click(screen.getByRole('button', { name: 'Choose B' }))
    expect(screen.getByTestId('workspace')).toHaveTextContent('Private workspace A')
    fireEvent.click(screen.getByRole('button', { name: 'Capture stopped' })); fireEvent.click(screen.getByRole('button', { name: 'Choose B' }))
    expect(screen.getByTestId('workspace')).toHaveTextContent('Private workspace B')
    act(() => { mocks.handler?.('SIGNED_OUT', null) })
    expect(screen.getByTestId('identity')).toHaveTextContent('anonymous'); expect(screen.getByTestId('workspace')).toHaveTextContent('none'); expect(screen.getByTestId('busy')).toHaveTextContent('false')
  })
})
