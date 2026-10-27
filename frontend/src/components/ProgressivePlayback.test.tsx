import { createRef } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ProgressivePlayback, { type PlaybackHandle } from './ProgressivePlayback'
const mocks = vi.hoisted(() => ({ stream: vi.fn() }))
vi.mock('../services/officeApi', () => ({ requestStream: mocks.stream }))
beforeEach(() => {
  mocks.stream.mockReset(); mocks.stream.mockResolvedValue({ blob: async () => new Blob(['synthetic wav'], { type: 'audio/wav' }) })
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:audio'), revokeObjectURL: vi.fn() }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
describe('authenticated source playback', () => {
  it('seeks to original speech using native playback without double counting its offset', async () => {
    const ref = createRef<PlaybackHandle>(); const onTimeChange = vi.fn()
    render(<ProgressivePlayback ref={ref} conversationId="recording" workspaceId="workspace" onTimeChange={onTimeChange} />)
    expect(mocks.stream).not.toHaveBeenCalled()
    await act(async () => ref.current!.playAt(12))
    const audio = await screen.findByLabelText('Conversation source audio') as HTMLAudioElement
    fireEvent.loadedMetadata(audio)
    expect(audio.currentTime).toBe(12)
    fireEvent.timeUpdate(audio); expect(onTimeChange).toHaveBeenLastCalledWith(12)
    expect(mocks.stream).toHaveBeenCalledWith('/conversations/recording/playback/stream?start_sample=0', 'workspace', expect.objectContaining({ signal: expect.any(AbortSignal) }), undefined)
    await act(async () => ref.current!.playAt(30)); expect(audio.currentTime).toBe(30)
    expect(mocks.stream).toHaveBeenCalledOnce()
    expect(audio.controls).toBe(true)
  })
  it('cancels a previous workspace fetch and never publishes its late audio', async () => {
    let finish!: (response: Response) => void
    mocks.stream.mockImplementation(() => new Promise<Response>(resolve => { finish = resolve }))
    const view = render(<ProgressivePlayback conversationId="a" workspaceId="first" />)
    fireEvent.click(screen.getByRole('button', { name: 'Play recording' }))
    await waitFor(() => expect(mocks.stream).toHaveBeenCalledOnce())
    const signal = mocks.stream.mock.calls[0][2].signal as AbortSignal
    view.rerender(<ProgressivePlayback conversationId="b" workspaceId="second" />)
    expect(signal.aborted).toBe(true)
    await act(async () => finish({ blob: async () => new Blob(['late']) } as Response))
    expect(screen.queryByLabelText('Conversation source audio')).not.toBeInTheDocument()
    expect(URL.createObjectURL).not.toHaveBeenCalled()
  })
})
