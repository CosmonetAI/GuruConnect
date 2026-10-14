export type TranscriptionResult = {
  transcript: string
  language?: string
  reference?: string
}

export interface TranscriptionService {
  transcribe(input: {
    audio: Blob
    mimeType: string
    consentTimestamp: string
  }): Promise<TranscriptionResult>
}
