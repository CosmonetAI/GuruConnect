export type RecordingSnapshot = {
  capture_state?: string | null
  upload_state?: string | null
  transcription_state?: string | null
  english_state?: string | null
  final_state?: string | null
  processing_error_code?: string | null
  captureState?: string | null
  uploadState?: string | null
  transcriptionState?: string | null
  englishState?: string | null
  finalState?: string | null
}

export type RecordingStatus = {
  key: 'ready' | 'recording' | 'uploading' | 'transcribing' | 'translating' | 'complete' | 'failed'
  label: string
  detail: string
}

const failureStates = new Set(['failed', 'blocked', 'uncertain'])
const activeStates = new Set(['pending', 'queued', 'running', 'processing'])

export function recordingStatus(snapshot?: RecordingSnapshot | null): RecordingStatus {
  if (!snapshot) return { key: 'ready', label: 'Ready to record', detail: 'No recording is active.' }
  const capture = snapshot.capture_state ?? snapshot.captureState
  const upload = snapshot.upload_state ?? snapshot.uploadState
  const transcription = snapshot.transcription_state ?? snapshot.transcriptionState
  const english = snapshot.english_state ?? snapshot.englishState
  const finalState = snapshot.final_state ?? snapshot.finalState
  if (capture === 'recording' || capture === 'paused') {
    return { key: 'recording', label: capture === 'paused' ? 'Recording paused' : 'Recording', detail: 'Audio capture is active independently of AI processing.' }
  }
  if (failureStates.has(transcription || '')) return { key: 'failed', label: 'Transcription failed', detail: snapshot.processing_error_code || 'Retry transcription when the provider is available.' }
  if (failureStates.has(english || '')) return { key: 'failed', label: 'Translation failed', detail: snapshot.processing_error_code || 'Retry translation when the provider is available.' }
  if (upload && upload !== 'saved') return { key: 'uploading', label: 'Uploading audio', detail: 'Audio is still being saved before processing.' }
  if (activeStates.has(transcription || '')) return { key: 'transcribing', label: 'Transcribing', detail: 'Preparing the latest conversation text.' }
  if (activeStates.has(english || '')) return { key: 'translating', label: 'Translating', detail: 'Preparing the English version of the conversation.' }
  if (finalState === 'complete' && english === 'complete') return { key: 'complete', label: 'Processing complete', detail: 'Transcript and processing results are available.' }
  if (capture === 'stopped' || capture === 'interrupted') return { key: 'transcribing', label: 'Processing latest window', detail: 'Recording has stopped; completed windows are still being processed.' }
  return { key: 'ready', label: 'Ready to record', detail: 'No recording is active.' }
}

export function recordingStatusFromEnquiry(recording?: RecordingSnapshot | null) {
  return recordingStatus(recording)
}
