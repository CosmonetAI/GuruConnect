import { encodeWav } from './dsp';
import { scopeKey, stageUpload, finishRecovery, type Scope, type Recovery, type StoredChunk } from './outbox';
import { retryAudioOperation, transientAudioError, uploadPending, type RecordingHooks } from './session';

export const MAX_AUDIO_FILE_BYTES = 100 * 1024 * 1024;
export const MAX_AUDIO_SECONDS = 7200;
const SAMPLE_RATE = 16000;
const DEFAULT_CHUNK_SECONDS = 10;
export interface PreparedAudioFile { samples: Float32Array; name: string }
export interface AudioUploadProgress { phase: 'preparing' | 'saving' | 'uploading' | 'finalizing'; confirmedSamples: number; totalSamples: number }
export type UploadProgress = AudioUploadProgress;
export class AudioUploadError extends Error {
  constructor(message: string, public readonly preserved: boolean) { super(message); this.name = 'AudioUploadError'; }
}
export interface AudioUploadOptions {
  scope: Scope; conversationId: string; partId: string; leaseToken: string;
  audio: PreparedAudioFile; hooks: RecordingHooks;
  chunkSeconds?: number;
  signal?: AbortSignal;
  onProgress?(progress: AudioUploadProgress): void;
}

/** Decode at the recording pipeline's sample rate without accessing the microphone. */
export async function prepareAudioFile(file: File): Promise<PreparedAudioFile> {
  if (!file.size) throw new Error('Choose an audio file that contains sound.');
  if (file.size > MAX_AUDIO_FILE_BYTES) throw new Error('Choose an audio file no larger than 100 MB.');
  if (typeof OfflineAudioContext === 'undefined') throw new Error('Audio upload requires a browser with audio decoding support. Use a current browser.');
  let decoded: AudioBuffer;
  try {
    const context = new OfflineAudioContext(1, 1, SAMPLE_RATE);
    decoded = await context.decodeAudioData(await file.arrayBuffer());
  } catch {
    throw new Error('This audio file could not be decoded. Try an M4A, MP3, WAV, OGG, or WebM file supported by your browser.');
  }
  if (!Number.isFinite(decoded.duration) || decoded.duration <= 0 || !decoded.length || decoded.numberOfChannels < 1 || decoded.sampleRate !== SAMPLE_RATE) throw new Error('This audio file does not contain usable audio.');
  if (decoded.duration > MAX_AUDIO_SECONDS || decoded.length > MAX_AUDIO_SECONDS * SAMPLE_RATE) throw new Error('Choose an audio file that is two hours or shorter.');
  const samples = new Float32Array(decoded.length);
  for (let channel = 0; channel < decoded.numberOfChannels; channel++) {
    const values = decoded.getChannelData(channel);
    for (let index = 0; index < samples.length; index++) samples[index] = samples[index]! + (Number.isFinite(values[index]) ? values[index]! : 0) / decoded.numberOfChannels;
  }
  for (let index = 0; index < samples.length; index++) samples[index] = Math.max(-1, Math.min(1, samples[index]!));
  return { samples, name: file.name };
}

const delay = (milliseconds: number) => new Promise<void>(resolve => setTimeout(resolve, milliseconds));

/** Stage the whole file before upload so Recover audio always has a complete manifest. */
export async function uploadAudioFile(options: AudioUploadOptions): Promise<void> {
  const { audio, scope, conversationId, partId, leaseToken, hooks } = options;
  const chunkSamples = Math.max(1, Math.round((options.chunkSeconds ?? DEFAULT_CHUNK_SECONDS) * SAMPLE_RATE));
  try { options.signal?.throwIfAborted(); }
  catch { throw new AudioUploadError('The audio upload was cancelled before it was saved.', false); }
  if (!audio.samples.length || audio.samples.length > MAX_AUDIO_SECONDS * SAMPLE_RATE) throw new AudioUploadError('Choose an audio file between a moment and two hours long.', false);
  if (!navigator.locks) throw new AudioUploadError('This browser cannot safely coordinate recording tabs. Use a current browser.', false);
  await navigator.locks.request(`guruconnect-recording:${scope.userId}`, { ifAvailable: true }, async lock => {
    if (!lock) throw new AudioUploadError('A recording or upload is already active in another tab. Finish it before uploading audio.', false);
    const totalSamples = audio.samples.length;
    let confirmedSamples = 0; let staged = false; let leaseFailure: unknown;
    const progress = (phase: AudioUploadProgress['phase']) => options.onProgress?.({ phase, confirmedSamples, totalSamples });
    const checkLease = () => { options.signal?.throwIfAborted(); if (leaseFailure) throw leaseFailure; };
    let renewing = false;
    const heartbeat = setInterval(() => {
      if (renewing || leaseFailure || options.signal?.aborted) return;
      renewing = true;
      void hooks.heartbeat().catch(error => { if (!transientAudioError(error)) leaseFailure = error; }).finally(() => { renewing = false; });
    }, 15000);
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', unload);
    try {
      await retryAudioOperation(() => hooks.heartbeat(), checkLease, options.signal); checkLease(); progress('preparing');
      const key = `${scopeKey(scope)}/${conversationId}`;
      const recovery: Recovery = { key, scope: scopeKey(scope), conversationId, partId, leaseToken, totalSamples, chunkCount: Math.ceil(totalSamples / chunkSamples), stopped: true, updatedAt: new Date().toISOString() };
      const chunks: StoredChunk[] = [];
      for (let start = 0, sequence = 0; start < totalSamples; start += chunkSamples, sequence++) {
        checkLease();
        const end = Math.min(start + chunkSamples, totalSamples);
        const buffer = encodeWav(audio.samples.subarray(start, end));
        const digest = await crypto.subtle.digest('SHA-256', buffer);
        const sha256 = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
        chunks.push({ key: `${key}/${sequence}`, scope: recovery.scope, audio: buffer, meta: { conversation_id: conversationId, part_id: partId, lease_token: leaseToken, sequence, start_sample: start, sample_count: end - start, sample_rate: SAMPLE_RATE, mime: 'audio/wav', bytes: buffer.byteLength, sha256 } });
        if (sequence % 16 === 15) await delay(0);
      }
      checkLease(); progress('saving');
      await stageUpload(scope, chunks, recovery); staged = true; chunks.length = 0;
      checkLease();
      await retryAudioOperation(() => hooks.event('started', 0), checkLease, options.signal);
      await retryAudioOperation(() => hooks.event('stopped', totalSamples), checkLease, options.signal);
      checkLease();
      progress('uploading');
      await uploadPending(scope, conversationId, hooks, leaseToken, chunk => { confirmedSamples += chunk.sample_count; progress('uploading'); }, { retry: true, checkLease, signal: options.signal });
      checkLease();
      progress('finalizing');
      await retryAudioOperation(() => hooks.finalize({ part_id: partId, lease_token: leaseToken, expected_chunks: recovery.chunkCount, total_samples: totalSamples }), checkLease, options.signal);
      await finishRecovery(key);
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Please try again.';
      throw new AudioUploadError(staged ? `Your audio is preserved. Use Recover audio to finish saving this conversation. ${detail}` : `The audio file could not be saved. Retry the upload with the selected file. ${detail}`, staged);
    } finally {
      clearInterval(heartbeat); window.removeEventListener('beforeunload', unload);
    }
  });
}
