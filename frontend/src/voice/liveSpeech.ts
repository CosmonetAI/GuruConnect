export interface LiveSegment { id: string; text: string; final: boolean; speaker: 'Unknown' | 'Receptionist' | 'Visitor'; manual?: boolean }
export type LiveSpeechError = 'permission' | 'unsupported' | 'network' | 'no-microphone' | 'other'

interface RecognitionResult { readonly isFinal: boolean; readonly length: number; [index: number]: { transcript: string } }
interface RecognitionEvent { resultIndex: number; results: ArrayLike<RecognitionResult> }
interface Recognition {
  continuous: boolean; interimResults: boolean; lang: string
  onresult: ((event: RecognitionEvent) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
  start(): void; stop(): void; abort(): void
}
type RecognitionConstructor = new () => Recognition

function recognitionConstructor(): RecognitionConstructor | null {
  const scope = globalThis as unknown as { SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor }
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null
}

export function liveSpeechSupported() { return recognitionConstructor() !== null }

export function classifySpeechError(code: string): LiveSpeechError | null {
  if (code === 'no-speech' || code === 'aborted') return null
  if (code === 'not-allowed' || code === 'service-not-allowed') return 'permission'
  if (code === 'audio-capture') return 'no-microphone'
  if (code === 'network') return 'network'
  return 'other'
}

export interface LiveSpeech { stop(): void }

// Browser-native recognition only. Speaker identity is never inferred here: every segment starts as Unknown.
export function startLiveSpeech(handlers: { onSegment(segment: LiveSegment): void; onError(error: LiveSpeechError): void }, lang = typeof navigator === 'undefined' ? 'en-US' : navigator.language || 'en-US'): LiveSpeech | null {
  const Ctor = recognitionConstructor()
  if (!Ctor) { handlers.onError('unsupported'); return null }
  const recognition = new Ctor()
  let active = true
  let session = 0
  recognition.continuous = true
  recognition.interimResults = true
  recognition.lang = lang
  recognition.onresult = event => {
    for (let index = event.resultIndex; index < event.results.length; index++) {
      const result = event.results[index]
      const text = result[0]?.transcript.trim()
      if (text) handlers.onSegment({ id: `live-${session}-${index}`, text, final: result.isFinal, speaker: 'Unknown' })
    }
  }
  recognition.onerror = event => {
    const kind = classifySpeechError(event.error)
    if (!kind) return
    active = false
    handlers.onError(kind)
  }
  // Browsers end continuous recognition after silence; restart while still active.
  recognition.onend = () => {
    if (!active) return
    session++
    try { recognition.start() } catch { active = false; handlers.onError('other') }
  }
  try { recognition.start() } catch { active = false; handlers.onError('other'); return null }
  return {
    stop() {
      active = false
      recognition.onresult = recognition.onerror = recognition.onend = null
      try { recognition.abort() } catch { /* already stopped */ }
    },
  }
}