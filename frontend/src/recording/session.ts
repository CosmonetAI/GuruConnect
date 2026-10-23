import workletUrl from './capture.worklet.ts?worker&url';
import { apiBase, localMode } from '../services/officeApi';
import { discardRecovery, finishRecovery, listRecoveries, pending, putChunk, removeChunk, saveRecovery, scopeKey, type ChunkMeta, type Recovery, type Scope } from './outbox';
export { discardRecovery, listRecoveries } from './outbox';
export type { Scope, ChunkMeta } from './outbox';
export interface Manifest { part_id: string; expected_chunks: number; total_samples: number; lease_token: string }
export interface CaptureState { capture: 'idle' | 'recording' | 'paused' | 'stopped' | 'interrupted'; samples: number; elapsedSeconds: number; level: number; pendingChunks: number; pendingBytes: number; confirmedSamples: number; saving: boolean; error?: string }
export interface RecordingHooks {
  authorize(chunk: ChunkMeta): Promise<{ upload_url?: string; headers?: Record<string, string>; method?: 'POST' | 'PUT'; confirmed?: boolean }>;
  confirm(chunk: ChunkMeta): Promise<void>;
  heartbeat(): Promise<void>;
  event(kind: string, sample: number): Promise<void>;
  finalize(manifest: Manifest): Promise<void>;
}
export interface RecordingOptions { scope: Scope; conversationId: string; partId: string; leaseToken: string; deviceId?: string; hooks: RecordingHooks; onState(state: CaptureState): void; maxSeconds?: number; chunkSeconds?: number; outboxLimitBytes?: number }
const delay = (ms: number, signal?: AbortSignal) => new Promise<void>((resolve, reject) => {
  signal?.throwIfAborted();
  const aborted = () => { clearTimeout(timer); signal?.removeEventListener('abort', aborted); reject(signal?.reason ?? new DOMException('The audio upload was cancelled.', 'AbortError')); };
  const timer = setTimeout(() => { signal?.removeEventListener('abort', aborted); resolve(); }, ms);
  signal?.addEventListener('abort', aborted, { once: true });
});
const sha256 = async (buffer: ArrayBuffer) => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', buffer))).map(value => value.toString(16).padStart(2, '0')).join('');
const errorStatus = (error: unknown) => typeof error === 'object' && error !== null && 'status' in error ? Number(error.status) : undefined;
export function transientAudioError(error: unknown): boolean {
  const status = errorStatus(error);
  return status === 429 || (status !== undefined && status >= 500) || error instanceof TypeError || (error instanceof DOMException && ['AbortError', 'TimeoutError', 'NetworkError'].includes(error.name));
}
export async function retryAudioOperation<T>(operation: () => Promise<T>, checkLease: () => void = () => undefined, signal?: AbortSignal): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    signal?.throwIfAborted(); checkLease();
    try { return await operation(); }
    catch (error) {
      signal?.throwIfAborted(); checkLease();
      if (attempt >= 4 || !transientAudioError(error)) throw error;
      // The API uses one-minute request buckets. Heartbeats continue during this wait.
      await delay(errorStatus(error) === 429 ? 60000 : Math.min(5000, 500 * 2 ** attempt), signal);
    }
  }
}

export async function prepareMicrophone(deviceId?: string, onLevel?: (level: number) => void) {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('Recording requires HTTPS and a browser with microphone support.');
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: deviceId ? { exact: deviceId } : undefined, channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false }, video: false });
  let context: AudioContext | undefined; let animation = 0;
  try {
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === 'audioinput');
    if (onLevel) {
      context = new AudioContext(); await context.resume(); const analyzer = context.createAnalyser(); analyzer.fftSize = 256; context.createMediaStreamSource(stream).connect(analyzer);
      const values = new Float32Array(analyzer.fftSize);
      const tick = () => { analyzer.getFloatTimeDomainData(values); onLevel(Math.sqrt(values.reduce((sum,x) => sum + x*x, 0) / values.length)); animation = requestAnimationFrame(tick); }; tick();
    }
    return { stream, devices, stop: () => { cancelAnimationFrame(animation); stream.getTracks().forEach(track => track.stop()); void context?.close(); } };
  } catch (error) {
    cancelAnimationFrame(animation); stream.getTracks().forEach(track => track.stop()); await context?.close(); throw error;
  }
}

export async function uploadPending(scope: Scope, conversationId: string, hooks: RecordingHooks, leaseToken?: string, onConfirmed?: (chunk: ChunkMeta) => void, options: { retry?: boolean; checkLease?: () => void; signal?: AbortSignal } = {}): Promise<number> {
  const checkTransfer = () => { options.signal?.throwIfAborted(); options.checkLease?.(); };
  checkTransfer();
  const rows = await pending(scope, conversationId); let confirmed = 0;
  for (const row of rows) {
    if (leaseToken) row.meta={...row.meta,lease_token:leaseToken};
    const transfer = async () => {
      checkTransfer();
      const permit = await hooks.authorize(row.meta);
      checkTransfer();
      if (permit.confirmed) { await hooks.confirm(row.meta); await removeChunk(row.key); return; }
      if (!permit.upload_url) throw new Error('Upload authorization did not include a storage destination.');
      const target = new URL(permit.upload_url, location.origin);
      const supabaseOrigin = import.meta.env.VITE_SUPABASE_URL ? new URL(import.meta.env.VITE_SUPABASE_URL).origin : undefined;
      const localOrigin = new URL(apiBase).origin;
      const localDestination = localMode && target.origin === localOrigin && ['localhost', '127.0.0.1', '[::1]'].includes(target.hostname) && target.pathname.startsWith('/api/v1/local-audio/');
      const hostedDestination = target.origin === supabaseOrigin && ['/storage/v1/object/session-audio/', '/storage/v1/object/walkin-audio/'].some(prefix => target.pathname.startsWith(prefix));
      if (target.username || target.password || (!localDestination && !hostedDestination)) throw new Error('Upload authorization has an invalid storage destination.');
      const timeout = AbortSignal.timeout(30000);
      const signal = options.signal ? AbortSignal.any([timeout, options.signal]) : timeout;
      const response = await fetch(target, { method: permit.method ?? 'POST', headers: { 'Content-Type': 'audio/wav', ...permit.headers }, body: row.audio, credentials: 'omit', redirect: 'error', signal });
      checkTransfer();
      // An immutable retry may find the already-uploaded object; server confirmation proves bytes.
      if (!response.ok && response.status !== 409 && response.status !== 400) throw Object.assign(new Error(`Audio upload failed (${response.status}). Pending audio is retained.`), { status: response.status });
      await hooks.confirm(row.meta); await removeChunk(row.key);
    };
    if (options.retry) await retryAudioOperation(transfer, checkTransfer, options.signal);
    else await transfer();
    confirmed += row.meta.sample_count; onConfirmed?.(row.meta);
  }
  return confirmed;
}

export async function recoverRecording(scope: Scope, conversationId: string, hooks: RecordingHooks, leaseToken?: string, signal?: AbortSignal) {
  signal?.throwIfAborted();
  const recoveries = await listRecoveries(scope); const recovery = recoveries.find(row => row.conversationId === conversationId);
  if (!recovery) throw new Error('No pending recording belongs to this account and workspace.');
  if (!navigator.locks) throw new Error('This browser cannot safely recover recording tabs. Use a current browser.');
  await navigator.locks.request(`guruconnect-recording:${scope.userId}`,{ifAvailable:true},async lock=>{
    if (!lock) throw new Error('A recording is active in another tab. Stop it before recovering audio.');
    const heartbeat=setInterval(()=>{void hooks.heartbeat().catch(()=>undefined);},15000);
    try {
      if(leaseToken){recovery.leaseToken=leaseToken;await saveRecovery(recovery);}
      await uploadPending(scope, conversationId, hooks, leaseToken, undefined, { retry: true, signal });
      signal?.throwIfAborted();
      await hooks.event('interrupted', recovery.totalSamples).catch(()=>undefined);
      await retryAudioOperation(() => hooks.finalize({ part_id: recovery.partId, expected_chunks: recovery.chunkCount, total_samples: recovery.totalSamples, lease_token: recovery.leaseToken }), undefined, signal);
      await finishRecovery(recovery.key);
    } finally{clearInterval(heartbeat);}
  });
}

export class RecorderSession {
  private context?: AudioContext;
  private stream?: MediaStream;
  private node?: AudioWorkletNode;
  private releaseLock?: () => void;
  private heartbeatTimer?: ReturnType<typeof setInterval>;
  private uploading = false;
  private persisting: Promise<void> = Promise.resolve();
  private commandId = 0;
  private commands = new Map<number, { resolve: () => void; reject: (error: Error) => void; timeout: ReturnType<typeof setTimeout> }>();
  private recovery: Recovery;
  private state: CaptureState = { capture: 'idle', samples: 0, elapsedSeconds: 0, level: 0, pendingChunks: 0, pendingBytes: 0, confirmedSamples: 0, saving: false };
  private closing = false;
  private disposed = false;
  private finalized = false;
  private listeners = new Set<(state: CaptureState) => void>();
  constructor(private readonly options: RecordingOptions) {
    this.recovery = { key: `${scopeKey(options.scope)}/${options.conversationId}`, scope: scopeKey(options.scope), conversationId: options.conversationId, partId: options.partId, leaseToken: options.leaseToken, totalSamples: 0, chunkCount: 0, stopped: false, updatedAt: new Date().toISOString() };
  }
  private update(values: Partial<CaptureState>) { this.state = { ...this.state, ...values }; const snapshot = { ...this.state }; this.options.onState(snapshot); this.listeners.forEach(listener => listener(snapshot)); }
  snapshot() { return { ...this.state }; }
  subscribe(listener: (state: CaptureState) => void) { this.listeners.add(listener); listener(this.snapshot()); return () => this.listeners.delete(listener); }
  private unload = (event: BeforeUnloadEvent) => { if (this.state.capture === 'recording' || this.state.pendingChunks) { event.preventDefault(); event.returnValue = ''; } };
  private async lock() {
    if (!navigator.locks) throw new Error('This browser cannot safely coordinate recording tabs. Use a current browser.');
    await new Promise<void>((resolve, reject) => {
      void navigator.locks.request(`guruconnect-recording:${this.options.scope.userId}`, { ifAvailable: true }, async lock => {
        if (!lock) { reject(new Error('A recording is already active in another tab. Stop it or recover its recording first.')); return; }
        const held = new Promise<void>(release => { this.releaseLock = release; }); resolve(); await held;
      }).catch(reject);
    });
  }
  async start() {
    if (this.state.capture !== 'idle') throw new Error('This recording has already started.');
    await this.lock();
    try {
      this.checkActive();
      const outstanding = await pending(this.options.scope);
      this.checkActive();
      if (outstanding.reduce((sum,row) => sum + row.meta.bytes, 0) > (this.options.outboxLimitBytes ?? 64*1024*1024) - 320088) throw new Error('Save your pending audio before starting another recording.');
      await this.options.hooks.heartbeat(); this.checkActive(); await saveRecovery(this.recovery); this.checkActive();
      const prepared = await prepareMicrophone(this.options.deviceId); this.stream = prepared.stream;
      this.checkActive();
      this.context = new AudioContext(); await this.context.audioWorklet.addModule(workletUrl); this.checkActive(); await this.context.resume(); this.checkActive();
      this.node = new AudioWorkletNode(this.context, 'guru-pcm-capture', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1], processorOptions: { maxSamples: (this.options.maxSeconds ?? 7200) * 16000, chunkSamples: (this.options.chunkSeconds ?? 180) * 16000 } });
      this.node.port.onmessage = (event: MessageEvent) => this.message(event.data);
      const mute = this.context.createGain(); mute.gain.value = 0;
      this.context.createMediaStreamSource(this.stream).connect(this.node).connect(mute).connect(this.context.destination);
      this.stream.getTracks().forEach(track => track.addEventListener('ended', () => { if (!this.closing) void this.interrupt('The microphone disconnected. Saved audio can be recovered.'); }));
      this.context.addEventListener('statechange', () => { if (!this.closing && this.context?.state === 'suspended' && this.state.capture === 'recording') void this.interrupt('The browser suspended microphone execution. Capture was interrupted.'); });
      window.addEventListener('beforeunload', this.unload);
      this.update({ capture: 'recording' });
      await this.options.hooks.event('started', 0);
      this.checkActive();
      this.heartbeatTimer = setInterval(() => { void this.options.hooks.heartbeat().catch(() => this.interrupt('Recording authorization expired or access changed. Pending audio stays with this account.')); }, 15000);
    } catch (error) {
      await this.cleanup();
      if (!this.recovery.totalSamples && !(await pending(this.options.scope, this.options.conversationId).catch(() => [])).length) await finishRecovery(this.recovery.key).catch(() => undefined);
      this.update({ capture: 'interrupted', error: error instanceof Error ? error.message : 'Microphone preparation failed' }); throw error;
    }
  }
  private checkActive() { if (this.disposed) throw new DOMException('The recording page was closed. Recover any saved audio before recording again.', 'AbortError'); }
  /** Cancel pending permission as well as active capture without losing committed chunks. */
  async dispose() {
    if (this.disposed) return;
    this.disposed = true;
    if (!this.closing && ['recording', 'paused'].includes(this.state.capture)) await this.interrupt('Capture stopped when the recording page closed. Recover the saved audio.');
    else { this.closing = true; await this.cleanup(); }
  }
  private message(data: { type: string; id: number; sequence: number; startSample: number; sampleCount: number; buffer: ArrayBuffer; level: number; samples: number }) {
    if (data.type === 'ack') { const command = this.commands.get(data.id); if (command) { clearTimeout(command.timeout); command.resolve(); this.commands.delete(data.id); } return; }
    if (data.type === 'limit') { void this.stop().catch(error => this.update({error: String(error)})); return; }
    if (data.type === 'meter') {
      this.update({ level: data.level, samples: data.samples, elapsedSeconds: data.samples / 16000 });
      if (data.samples >= (this.options.maxSeconds ?? 7200) * 16000 && !this.closing) void this.stop().catch(error => this.update({ error: String(error) }));
      return;
    }
    if (data.type !== 'chunk') return;
    this.persisting = this.persisting.then(async () => {
      const meta: ChunkMeta = { conversation_id: this.options.conversationId, part_id: this.options.partId, sequence: data.sequence, start_sample: data.startSample, sample_count: data.sampleCount, sample_rate: 16000, bytes: data.buffer.byteLength, sha256: await sha256(data.buffer), mime: 'audio/wav', lease_token: this.options.leaseToken };
      const next: Recovery = { ...this.recovery, totalSamples: data.startSample + data.sampleCount, chunkCount: data.sequence + 1, updatedAt: new Date().toISOString() };
      await putChunk(this.options.scope, { key: `${next.key}/${meta.sequence}`, scope: next.scope, meta, audio: data.buffer }, next, this.options.outboxLimitBytes);
      this.recovery = next; await this.refreshQueue(); void this.pump();
      if (this.state.pendingBytes >= (this.options.outboxLimitBytes ?? 64*1024*1024) - 320088 && !this.closing) void this.interrupt('Local upload queue is nearly full. Capture stopped so pending audio can be saved.');
    }).catch(error => { this.update({ error: error instanceof Error ? error.message : 'Local audio persistence failed' }); void this.interrupt('Local storage could not save new audio. Capture stopped immediately.'); throw error; });
    // The failure remains visible to Stop, with an attached handler to avoid an unhandled rejection.
    void this.persisting.catch(() => undefined);
  }
  private async refreshQueue() { const rows = await pending(this.options.scope, this.options.conversationId); this.update({ pendingChunks: rows.length, pendingBytes: rows.reduce((sum,row) => sum + row.meta.bytes, 0), confirmedSamples: this.recovery.totalSamples - rows.reduce((sum,row) => sum + row.meta.sample_count, 0) }); }
  private async pump() {
    if (this.uploading) return;
    this.uploading = true; let attempts = 0;
    try {
      while ((await pending(this.options.scope, this.options.conversationId)).length) {
        try { const count = await uploadPending(this.options.scope, this.options.conversationId, this.options.hooks); this.update({ confirmedSamples: this.state.confirmedSamples + count, error: undefined }); attempts = 0; }
        catch (error) { this.update({ error: error instanceof Error ? error.message : 'Upload interrupted' }); if (++attempts >= 5) break; await delay(Math.min(5000, 300 * 2**attempts) + Math.random()*150); }
        await this.refreshQueue();
      }
    } finally { this.uploading = false; await this.refreshQueue(); }
  }
  private command(type: string) {
    return new Promise<void>((resolve,reject) => {
      if (!this.node) { resolve(); return; }
      const id = ++this.commandId;
      const timeout = setTimeout(() => { this.commands.delete(id); reject(new Error('Microphone execution stopped responding. Recover the durably saved audio.')); }, 3000);
      this.commands.set(id, { resolve,reject,timeout }); this.node.port.postMessage({ type, id });
    });
  }
  async pause() { if (this.state.capture !== 'recording') return; await this.command('pause'); await this.persisting; this.update({ capture: 'paused', level: 0 }); await this.options.hooks.event('paused', this.recovery.totalSamples); }
  async resume() { if (this.state.capture !== 'paused') return; await this.options.hooks.heartbeat(); await this.context?.resume(); await this.command('resume'); this.update({ capture: 'recording' }); await this.options.hooks.event('resumed', this.recovery.totalSamples); }
  async stop() {
    if (this.closing) {
      if (this.finalized) return;
      throw new Error('This recording is stopping or interrupted. Recover its saved audio before finishing.');
    }
    this.closing = true; this.update({ capture: 'stopped', level: 0, saving: true });
    try {
      await this.command('stop'); await this.persisting;
      this.recovery.stopped = true; await saveRecovery(this.recovery);
      await this.cleanup(true); await this.options.hooks.event('stopped', this.recovery.totalSamples);
      while (this.uploading) await delay(100);
      await this.pump();
      if ((await pending(this.options.scope, this.options.conversationId)).length) throw new Error('Recording stopped. Audio remains saved on this device; reconnect and use Recover audio.');
      await this.options.hooks.finalize({ part_id: this.recovery.partId, expected_chunks: this.recovery.chunkCount, total_samples: this.recovery.totalSamples, lease_token: this.recovery.leaseToken });
      await finishRecovery(this.recovery.key); await this.cleanup(); this.finalized = true; this.update({ saving: false, error: undefined, samples: this.recovery.totalSamples, elapsedSeconds: this.recovery.totalSamples / 16000 });
    } catch (error) { await this.cleanup(); this.update({ saving: false, error: error instanceof Error ? error.message : 'Finalization needs recovery' }); throw error; }
  }
  async discard() {
    if (this.finalized) return;
    this.closing = true;
    await this.command('stop').catch(() => undefined);
    await this.persisting.catch(() => undefined);
    await this.cleanup();
    await discardRecovery(this.options.scope, this.options.conversationId).catch(() => undefined);
    this.finalized = true;
    this.update({ capture: 'stopped', saving: false, level: 0, error: undefined });
  }
  private async interrupt(reason: string) {
    if (this.closing) return; this.closing = true; this.update({ capture: 'interrupted', error: reason, level: 0 });
    await this.command('stop').catch(() => undefined); await this.persisting.catch(() => undefined);
    await saveRecovery({ ...this.recovery, stopped: true }).catch(() => undefined); await this.cleanup();
    await this.options.hooks.event('interrupted', this.recovery.totalSamples).catch(() => undefined); void this.pump();
  }
  private async cleanup(keepLease = false) {
    if (!keepLease && this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.stream?.getTracks().forEach(track => track.stop()); this.node?.disconnect();
    if (this.context && this.context.state !== 'closed') await this.context.close();
    this.releaseLock?.(); this.releaseLock = undefined;
    window.removeEventListener('beforeunload', this.unload);
  }
}
