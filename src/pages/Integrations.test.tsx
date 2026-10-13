import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../services/officeApi'
import Integrations from './Integrations'

const calls = vi.hoisted(() => ({ request: vi.fn(), reload: vi.fn(), role: 'owner', lastError: undefined as string | undefined }))
vi.mock('../services/officeApi', async original => ({ ...await original<typeof import('../services/officeApi')>(), request: calls.request }))
vi.mock('../contexts/OfficeContext', () => ({ useOffice: () => ({ user: { id: 'user-a' }, workspace: { id: 'workspace-a', role: calls.role } }) }))
vi.mock('../components/OfficeUi', async original => ({ ...await original<typeof import('../components/OfficeUi')>(), useOfficeResource: (path: string) => ({ loading: false, refreshing: false, error: '', reload: calls.reload, data: path === '/connections' ? { items: [
  { id: 'eleven-a', engine_id: 'elevenlabs', state: 'connected', generation: 3, last_error: calls.lastError },
  { id: 'openai-a', engine_id: 'openai-translator', state: 'configured', generation: 2 },
] } : { default_engine_id: 'elevenlabs', allowed_engine_ids: ['elevenlabs'], budget_policy: { max_audio_seconds_per_day: 6000 } } }) }))
function card(name: string) { return within(screen.getByRole('region', { name })) }
beforeEach(() => { calls.request.mockReset(); calls.reload.mockClear(); calls.role = 'owner'; calls.lastError = undefined })
afterEach(cleanup)

describe('secure provider configuration and readiness', () => {
  it('sends the displayed generation and trimmed new key without placing it in safe configuration', async () => {
    const user = userEvent.setup(); calls.request.mockResolvedValue({})
    render(<Integrations />)
    await user.click(card('English translation · OpenAI').getByRole('button', { name: 'Configure / rotate' }))
    const dialog = within(screen.getByRole('dialog', { name: 'Configure English translation · OpenAI' }))
    const input = dialog.getByLabelText('API key')
    expect(input).toHaveAttribute('type', 'password')
    expect(input).toHaveValue('')
    await user.type(input, '  dummy-new-project-key  ')
    await user.click(dialog.getByRole('button', { name: 'Save secure configuration' }))
    const body = JSON.parse(calls.request.mock.calls[0][2].body)
    expect(body).toMatchObject({ engine_id: 'openai-translator', expected_generation: 2, credentials: { api_key: 'dummy-new-project-key' }, config: { model: 'gpt-5.6-luna' } })
    expect(body.config).not.toHaveProperty('api_key')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue('dummy-new-project-key')).not.toBeInTheDocument()
  })

  it('requires review before retrying a stale-generation rotation and preserves its active input', async () => {
    const user = userEvent.setup()
    calls.request.mockRejectedValueOnce(new ApiError(409, 'stale_connection_generation', 'Connection changed. Review its latest state.'))
      .mockResolvedValueOnce({ items: [{ id: 'openai-a', engine_id: 'openai-translator', generation: 4 }] }).mockResolvedValueOnce({})
    render(<Integrations />)
    await user.click(card('English translation · OpenAI').getByRole('button', { name: 'Configure / rotate' }))
    const dialog = within(screen.getByRole('dialog', { name: 'Configure English translation · OpenAI' }))
    await user.type(dialog.getByLabelText('API key'), 'dummy-safe-ui-key')
    await user.click(dialog.getByRole('button', { name: 'Save secure configuration' }))
    expect(await dialog.findByRole('alert')).toHaveTextContent(/connection changed/i)
    expect(dialog.getByRole('button', { name: 'Save secure configuration' })).toBeDisabled()
    expect(dialog.getByLabelText('API key')).toHaveValue('dummy-safe-ui-key')
    expect(calls.request).toHaveBeenCalledTimes(1)
    await user.click(dialog.getByRole('button', { name: 'Review latest connection state' }))
    expect(dialog.getByRole('button', { name: 'Save secure configuration' })).toBeEnabled()
    expect(calls.request).toHaveBeenCalledTimes(2)
    await user.click(dialog.getByRole('button', { name: 'Save secure configuration' }))
    expect(JSON.parse(calls.request.mock.calls[2][2].body).expected_generation).toBe(4)
  })

  it('clears the entered key after a non-stale save failure', async () => {
    const user = userEvent.setup(); calls.request.mockRejectedValue(new Error('Service unavailable'))
    render(<Integrations />)
    await user.click(card('ElevenLabs Scribe v2').getByRole('button', { name: 'Configure / rotate' }))
    const dialog = within(screen.getByRole('dialog', { name: 'Configure ElevenLabs Scribe v2' }))
    await user.type(dialog.getByLabelText('API key'), 'dummy-safe-ui-key')
    await user.click(dialog.getByRole('button', { name: 'Save secure configuration' }))
    expect(await dialog.findByRole('alert')).toHaveTextContent('Service unavailable')
    expect(dialog.getByLabelText('API key')).toHaveValue('')
  })

  it('never claims English readiness from a metadata-only test and requires intent for the paid smoke test', async () => {
    const user = userEvent.setup()
    calls.request.mockResolvedValueOnce({ passed: true, state: 'configured', functional: false }).mockResolvedValueOnce({ passed: true, state: 'connected', functional: true })
    render(<Integrations />)
    const openai = card('English translation · OpenAI')
    await user.click(openai.getByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText(/Run the functional smoke test to verify text translation/)).toBeVisible()
    expect(JSON.parse(calls.request.mock.calls[0][2].body)).toEqual({ functional: false })
    await user.click(openai.getByRole('button', { name: 'Functional smoke test' }))
    expect(calls.request).toHaveBeenCalledTimes(1)
    const dialog = within(screen.getByRole('dialog', { name: 'Confirm functional smoke test' }))
    expect(dialog.getByText(/may be billed/)).toBeVisible()
    await user.click(dialog.getByRole('button', { name: 'Confirm provider usage and run test' }))
    expect(JSON.parse(calls.request.mock.calls[1][2].body)).toEqual({ functional: true })
    expect(await screen.findByText(/ready for English processing/)).toBeVisible()
  })

  it('leaves a failed test visible without a misleading success message', async () => {
    const user = userEvent.setup(); calls.request.mockResolvedValue({ passed: false, last_error_code: 'invalid_credentials' })
    render(<Integrations />)
    await user.click(card('ElevenLabs Scribe v2').getByRole('button', { name: 'Test connection' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('saved API key')
    expect(screen.queryByText(/connection check passed/)).not.toBeInTheDocument()
    expect(card('ElevenLabs Scribe v2').getByRole('button', { name: 'Test connection' })).toBeEnabled()
  })

  it('does not expose mutation controls to a salesperson or echo unknown provider tags', () => {
    calls.role = 'salesperson'; calls.lastError = 'unknown-tag-with-sensitive-provider-data'
    const { container } = render(<Integrations />)
    expect(screen.queryByRole('button', { name: 'Configure / rotate' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Test connection' })).not.toBeInTheDocument()
    expect(container).not.toHaveTextContent(calls.lastError)
    expect(screen.getByLabelText('Default speech engine')).toBeDisabled()
  })
})
