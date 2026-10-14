import { createHash, webcrypto } from 'node:crypto';
import { IDBFactory } from 'fake-indexeddb';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { listRecoveries, pending, putChunk, scopeKey, stageUpload, type Recovery, type Scope, type StoredChunk } from './outbox';
import { recoverRecording, type RecordingHooks } from './session';
import { MAX_AUDIO_FILE_BYTES, MAX_AUDIO_SECONDS, prepareAudioFile, uploadAudioFile, type AudioUploadOptions, type AudioUploadProgress } from './upload';

const scope: Scope = { userId: 'alice', workspaceId: 'office' };
const otherScope: Scope = { userId: 'bob', workspaceId: 'office' };
const decodeAudioData = vi.fn<(bytes: ArrayBuffer) => Promise<AudioBuffer>>();
const contextConstructor = vi.fn();
const requestLock = vi.fn(async (_name: string, _options: unknown, callback: (lock: object | null) => Promise<void>) => callback({}));

function audioBuffer(channels: number[][], overrides: Partial<AudioBuffer> = {}): AudioBuffer {
  return {
    numberOfChannels: channels.length, length: channels[0]?.length ?? 0, sampleRate: 16000,
    duration: (channels[0]?.length ?? 0) / 16000,
    getChannelData: (channel: number) => new Float32Array(channels[channel]),
    ...overrides,
  } as AudioBuffer;
}

function audioFile(name = 'meeting.m4a', size = 3): File {
  const file = new File([new Uint8Array(Math.min(size, 3))], name, { type: 'audio/mp4' });
  Object.defineProperty(file, 'size', { value: size });
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new Uint8Array([1, 2, 3]).buffer });
  return file;
}

function hooks() {
  return {
    authorize: vi.fn<RecordingHooks['authorize']>().mockResolvedValue({ confirmed: true }),
    confirm: vi.fn<RecordingHooks['confirm']>().mockResolvedValue(undefined),
    heartbeat: vi.fn<RecordingHooks['heartbeat']>().mockResolvedValue(undefined),
    event: vi.fn<RecordingHooks['event']>().mockResolvedValue(undefined),
    finalize: vi.fn<RecordingHooks['finalize']>().mockResolvedValue(undefined),
  };
}

function options(samples = new Float32Array([0, 0.5, -0.5]), id = 'uploaded-meeting'): AudioUploadOptions & { hooks: ReturnType<typeof hooks> } {
  return { scope, conversationId: id, partId: 'part-one', leaseToken: 'lease-one', audio: { name: 'meeting.m4a', samples }, hooks: hooks() };
}

function fixture(owner: Scope, id: string, sequence = 0, bytes = 46) {
  const key = `${scopeKey(owner)}/${id}`;
  const recovery: Recovery = { key, scope: scopeKey(owner), conversationId: id, partId: 'part-one', leaseToken: 'lease-one', totalSamples: 1, chunkCount: 1, stopped: true, updatedAt: new Date().toISOString() };
  const chunk: StoredChunk = { key: `${key}/${sequence}`, scope: recovery.scope, audio: new ArrayBuffer(46), meta: { conversation_id: id, part_id: recovery.partId, lease_token: recovery.leaseToken, sequence, start_sample: sequence, sample_count: 1, sample_rate: 16000, bytes, sha256: 'a'.repeat(64), mime: 'audio/wav' } };
  return { recovery, chunk };
}

beforeEach(() => {
  decodeAudioData.mockReset().mockResolvedValue(audioBuffer([[0, 0.5, -0.5]]));
  contextConstructor.mockClear();
  requestLock.mockReset().mockImplementation(async (_name, _options, callback) => callback({}));
  vi.stubGlobal('indexedDB', new IDBFactory());
  vi.stubGlobal('crypto', webcrypto);
  vi.stubGlobal('navigator', { locks: { request: requestLock } });
  vi.stubGlobal('OfflineAudioContext', class {
    constructor(...args: number[]) { contextConstructor(...args); }
    decodeAudioData = decodeAudioData;
  });
  vi.stubEnv('VITE_SUPABASE_URL', location.origin);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('audio file preparation', () => {
  it('decodes an M4A at 16 kHz and mixes every channel to bounded mono PCM', async () => {
    decodeAudioData.mockResolvedValue(audioBuffer([
      [0.9, -0.9, 2, -2, NaN], [0.3, -0.3, 2, -2, 0.6], [0, 0, 2, -2, 0.3],
    ]));
    const result = await prepareAudioFile(audioFile());
    expect(contextConstructor).toHaveBeenCalledWith(1, 1, 16000);
    expect(new Uint8Array(decodeAudioData.mock.calls[0]![0])).toEqual(new Uint8Array([1, 2, 3]));
    expect(result.name).toBe('meeting.m4a');
    expect(result.samples).toBeInstanceOf(Float32Array);
    [0.4, -0.4, 1, -1, 0.3].forEach((value, index) => expect(result.samples[index]).toBeCloseTo(value, 6));
  });

  it('rejects empty and oversized files before decoding them', async () => {
    await expect(prepareAudioFile(audioFile('empty.m4a', 0))).rejects.toThrow('contains sound');
    await expect(prepareAudioFile(audioFile('too-large.m4a', MAX_AUDIO_FILE_BYTES + 1))).rejects.toThrow('100 MB');
    expect(decodeAudioData).not.toHaveBeenCalled();
  });

  it('explains unsupported or corrupt audio without starting an import', async () => {
    decodeAudioData.mockRejectedValue(new DOMException('corrupt', 'EncodingError'));
    await expect(prepareAudioFile(audioFile())).rejects.toThrow('could not be decoded');
    expect(await listRecoveries(scope)).toEqual([]);
  });

  it.each([
    { duration: 0 }, { duration: NaN }, { length: 0 }, { numberOfChannels: 0 }, { sampleRate: 44100 },
  ])('rejects unusable decoded audio (%j)', async invalid => {
    decodeAudioData.mockResolvedValue(audioBuffer([[0.5]], invalid));
    await expect(prepareAudioFile(audioFile())).rejects.toThrow('usable audio');
  });

  it('limits decoded duration and sample count to two hours', async () => {
    decodeAudioData.mockResolvedValue(audioBuffer([[0.5]], { duration: MAX_AUDIO_SECONDS + 1 }));
    await expect(prepareAudioFile(audioFile())).rejects.toThrow('two hours');
    decodeAudioData.mockResolvedValue(audioBuffer([[0.5]], { length: MAX_AUDIO_SECONDS * 16000 + 1 }));
    await expect(prepareAudioFile(audioFile())).rejects.toThrow('two hours');
  });
});

describe('durable audio file upload', () => {
  it('stages complete 10-second WAV chunks and the short tail before sending, then finalizes the same recording manifest', async () => {
    const samples = new Float32Array(320003);
    samples[0] = -1; samples[159999] = 0.5; samples[160000] = -0.5;
    samples.set([1, 0, -1], 320000);
    const upload = options(samples);
    const progress: AudioUploadProgress[] = [];
    let staged: StoredChunk[] = [];
    upload.hooks.authorize.mockImplementation(async () => {
      if (!staged.length) staged = await pending(scope, upload.conversationId);
      return { confirmed: true };
    });
    await uploadAudioFile({ ...upload, onProgress: state => progress.push(state) });

    expect(staged.map(row => [row.meta.sequence, row.meta.start_sample, row.meta.sample_count, row.meta.bytes])).toEqual([
      [0, 0, 160000, 320044], [1, 160000, 160000, 320044], [2, 320000, 3, 50],
    ]);
    for (const row of staged) {
      const view = new DataView(row.audio);
      expect(new TextDecoder().decode(row.audio.slice(0, 4))).toBe('RIFF');
      expect(view.getUint16(20, true)).toBe(1);
      expect(view.getUint16(22, true)).toBe(1);
      expect(view.getUint32(24, true)).toBe(16000);
      expect(view.getUint16(34, true)).toBe(16);
      expect(view.getUint32(40, true)).toBe(row.meta.sample_count * 2);
      expect(row.meta.sha256).toBe(createHash('sha256').update(new Uint8Array(row.audio)).digest('hex'));
    }
    expect(new DataView(staged[0]!.audio).getInt16(44, true)).toBe(-32768);
    expect(new DataView(staged[0]!.audio).getInt16(44 + 159999 * 2, true)).toBe(16384);
    expect(new DataView(staged[1]!.audio).getInt16(44, true)).toBe(-16384);
    expect([44, 46, 48].map(offset => new DataView(staged[2]!.audio).getInt16(offset, true))).toEqual([32767, 0, -32768]);
    expect(requestLock).toHaveBeenCalledWith('guruconnect-recording:alice', { ifAvailable: true }, expect.any(Function));
    expect(upload.hooks.event.mock.calls).toEqual([['started', 0], ['stopped', 320003]]);
    expect(upload.hooks.finalize).toHaveBeenCalledWith({ part_id: 'part-one', lease_token: 'lease-one', expected_chunks: 3, total_samples: 320003 });
    expect(progress.filter(state => state.phase === 'uploading').map(state => state.confirmedSamples)).toEqual([0, 160000, 320000, 320003]);
    expect(progress.at(-1)).toEqual({ phase: 'finalizing', confirmedSamples: 320003, totalSamples: 320003 });
    expect(await pending(scope)).toEqual([]);
    expect(await listRecoveries(scope)).toEqual([]);
  });

  it('preserves the full manifest and remaining chunks when an upload fails after an earlier chunk was confirmed', async () => {
    const upload = options(new Float32Array(160003));
    const progress: AudioUploadProgress[] = [];
    upload.hooks.authorize.mockImplementation(async chunk => chunk.sequence === 0 ? { confirmed: true } : { upload_url: `${location.origin}/storage/v1/object/recorder-audio/tail.wav` });
    const fetchAudio = vi.fn().mockResolvedValue(new Response(null, { status: 403 }));
    vi.stubGlobal('fetch', fetchAudio);
    const uploading = uploadAudioFile({ ...upload, onProgress: state => progress.push(state) });
    await expect(uploading).rejects.toThrow('Recover audio');
    await expect(uploading).rejects.toMatchObject({ name: 'AudioUploadError', preserved: true });
    expect(upload.hooks.confirm.mock.calls.map(([chunk]) => chunk.sequence)).toEqual([0]);
    expect(upload.hooks.finalize).not.toHaveBeenCalled();
    expect(fetchAudio).toHaveBeenCalledTimes(1);
    expect((await pending(scope)).map(row => [row.meta.sequence, row.meta.sample_count])).toEqual([[1, 3]]);
    expect(await listRecoveries(scope)).toEqual([expect.objectContaining({ totalSamples: 160003, chunkCount: 2, stopped: true })]);
    expect(progress.at(-1)?.confirmedSamples).toBe(160000);
    expect(await pending(otherScope)).toEqual([]);
    expect(await listRecoveries(otherScope)).toEqual([]);
    expect(await listRecoveries({ ...scope, workspaceId: 'different-office' })).toEqual([]);

    const recoveryHooks = hooks();
    await recoverRecording(scope, upload.conversationId, recoveryHooks, 'renewed-lease');
    expect(recoveryHooks.confirm).toHaveBeenCalledWith(expect.objectContaining({ sequence: 1, lease_token: 'renewed-lease' }));
    expect(recoveryHooks.finalize).toHaveBeenCalledWith({ part_id: 'part-one', lease_token: 'renewed-lease', expected_chunks: 2, total_samples: 160003 });
    expect(await listRecoveries(scope)).toEqual([]);
  });

  it('retains a finalization-only recovery after every chunk was confirmed', async () => {
    const upload = options();
    upload.hooks.finalize.mockRejectedValue(new Error('Finalization rejected'));
    const uploading = uploadAudioFile(upload);
    await expect(uploading).rejects.toThrow('Recover audio');
    await expect(uploading).rejects.toMatchObject({ preserved: true });
    expect(await pending(scope)).toEqual([]);
    expect(await listRecoveries(scope)).toEqual([expect.objectContaining({ totalSamples: 3, chunkCount: 1 })]);
    const recoveryHooks = hooks();
    await recoverRecording(scope, upload.conversationId, recoveryHooks);
    expect(recoveryHooks.authorize).not.toHaveBeenCalled();
    expect(recoveryHooks.finalize).toHaveBeenCalledWith(expect.objectContaining({ expected_chunks: 1, total_samples: 3 }));
    expect(await listRecoveries(scope)).toEqual([]);
  });

  it('rejects a full scoped outbox without leaving a partial import or removing existing audio', async () => {
    const existing = fixture(scope, 'older-recording', 0, 256 * 1024 * 1024 - 10);
    await putChunk(scope, existing.chunk, existing.recovery, 256 * 1024 * 1024);
    const upload = options();
    const uploading = uploadAudioFile(upload);
    await expect(uploading).rejects.toThrow('could not be saved');
    await expect(uploading).rejects.toMatchObject({ name: 'AudioUploadError', preserved: false });
    expect(upload.hooks.authorize).not.toHaveBeenCalled();
    expect(upload.hooks.event).not.toHaveBeenCalled();
    expect((await pending(scope)).map(row => row.key)).toEqual([existing.chunk.key]);
    expect((await listRecoveries(scope)).map(row => row.key)).toEqual([existing.recovery.key]);
  });

  it('counts local capacity within the importing account and workspace', async () => {
    const existing = fixture(otherScope, 'another-account-recording', 0, 256 * 1024 * 1024);
    await putChunk(otherScope, existing.chunk, existing.recovery, 256 * 1024 * 1024);
    await uploadAudioFile(options());
    expect((await pending(otherScope)).map(row => row.key)).toEqual([existing.chunk.key]);
    expect((await listRecoveries(otherScope)).map(row => row.key)).toEqual([existing.recovery.key]);
    expect(await pending(scope)).toEqual([]);
  });

  it('rolls back the entire staging transaction if a later chunk conflicts', async () => {
    const first = fixture(scope, 'conflicting-import');
    const duplicate = { ...first.chunk, audio: new ArrayBuffer(48) };
    await expect(stageUpload(scope, [first.chunk, duplicate], { ...first.recovery, chunkCount: 2 })).rejects.toThrow();
    expect(await pending(scope)).toEqual([]);
    expect(await listRecoveries(scope)).toEqual([]);
  });

  it('rejects the whole staging batch when its combined bytes exceed the local quota', async () => {
    const first = fixture(scope, 'quota-import');
    const second = fixture(scope, 'quota-import', 1);
    await expect(stageUpload(scope, [first.chunk, second.chunk], { ...first.recovery, chunkCount: 2 }, 80)).rejects.toThrow('local space');
    expect(await pending(scope)).toEqual([]);
    expect(await listRecoveries(scope)).toEqual([]);
  });

  it('does not import while the microphone or another upload holds the recording lock', async () => {
    requestLock.mockImplementation(async (_name, _options, callback) => callback(null));
    const upload = options();
    await expect(uploadAudioFile(upload)).rejects.toThrow('another tab');
    expect(upload.hooks.heartbeat).not.toHaveBeenCalled();
    expect(await pending(scope)).toEqual([]);
    expect(await listRecoveries(scope)).toEqual([]);
  });

  it('does not acquire a recording lock or persist audio when the upload is already cancelled', async () => {
    const controller = new AbortController();
    controller.abort();
    const upload = options();
    await expect(uploadAudioFile({ ...upload, signal: controller.signal })).rejects.toMatchObject({ name: 'AudioUploadError', preserved: false });
    expect(requestLock).not.toHaveBeenCalled();
    expect(upload.hooks.heartbeat).not.toHaveBeenCalled();
    expect(upload.hooks.authorize).not.toHaveBeenCalled();
    expect(await pending(scope)).toEqual([]);
    expect(await listRecoveries(scope)).toEqual([]);
  });

  it('keeps the full manifest and remaining audio when cancelled after the first confirmed chunk', async () => {
    const controller = new AbortController();
    const upload = options(new Float32Array(320003));
    const uploading = uploadAudioFile({
      ...upload, signal: controller.signal,
      onProgress: state => { if (state.phase === 'uploading' && state.confirmedSamples === 160000) controller.abort(); },
    });
    await expect(uploading).rejects.toMatchObject({ name: 'AudioUploadError', preserved: true });
    expect(upload.hooks.authorize.mock.calls.map(([chunk]) => chunk.sequence)).toEqual([0]);
    expect(upload.hooks.confirm.mock.calls.map(([chunk]) => chunk.sequence)).toEqual([0]);
    expect(upload.hooks.finalize).not.toHaveBeenCalled();
    expect((await pending(scope)).map(row => [row.meta.sequence, row.meta.start_sample, row.meta.sample_count])).toEqual([[1, 160000, 160000], [2, 320000, 3]]);
    expect(await listRecoveries(scope)).toEqual([expect.objectContaining({ totalSamples: 320003, chunkCount: 3, stopped: true })]);
  });

  it('propagates cancellation to an in-flight storage fetch and retains its unconfirmed bytes', async () => {
    const controller = new AbortController();
    const upload = options();
    upload.hooks.authorize.mockResolvedValue({ upload_url: `${location.origin}/storage/v1/object/recorder-audio/chunk.wav` });
    let storageSignal: AbortSignal | undefined;
    let reachedStorage!: () => void;
    const storageReached = new Promise<void>(resolve => { reachedStorage = resolve; });
    const fetchAudio = vi.fn((_target: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      storageSignal = init?.signal ?? undefined;
      if (!storageSignal) { reject(new Error('Missing storage abort signal')); return; }
      storageSignal.addEventListener('abort', () => reject(storageSignal!.reason), { once: true });
      reachedStorage();
    }));
    vi.stubGlobal('fetch', fetchAudio);
    const uploading = uploadAudioFile({ ...upload, signal: controller.signal });
    const cancelled = expect(uploading).rejects.toMatchObject({ preserved: true });
    await storageReached;
    expect(storageSignal?.aborted).toBe(false);
    controller.abort();
    await cancelled;
    expect(storageSignal?.aborted).toBe(true);
    expect(fetchAudio).toHaveBeenCalledOnce();
    expect(upload.hooks.confirm).not.toHaveBeenCalled();
    expect(upload.hooks.finalize).not.toHaveBeenCalled();
    expect((await pending(scope)).map(row => row.meta.sample_count)).toEqual([3]);
    expect(await listRecoveries(scope)).toEqual([expect.objectContaining({ totalSamples: 3, chunkCount: 1 })]);
  });

  it('cancels a rate-limit backoff immediately without waiting a minute or retrying', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    const controller = new AbortController();
    const upload = options();
    let reachedRetry!: () => void;
    const retryReached = new Promise<void>(resolve => { reachedRetry = resolve; });
    upload.hooks.authorize.mockImplementationOnce(async () => {
      reachedRetry();
      throw Object.assign(new Error('Rate limited'), { status: 429 });
    });
    const uploading = uploadAudioFile({ ...upload, signal: controller.signal });
    const cancelled = expect(uploading).rejects.toMatchObject({ preserved: true });
    await retryReached;
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    await cancelled;
    expect(upload.hooks.authorize).toHaveBeenCalledOnce();
    expect(upload.hooks.confirm).not.toHaveBeenCalled();
    expect(upload.hooks.finalize).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
    expect(await listRecoveries(scope)).toEqual([expect.objectContaining({ totalSamples: 3, chunkCount: 1 })]);
  });

  it('retries a network failure using only remaining chunks and preserves confirmed progress', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    const upload = options(new Float32Array(160003));
    const progress: AudioUploadProgress[] = [];
    upload.hooks.authorize.mockImplementation(async chunk => chunk.sequence === 0 ? { confirmed: true } : { upload_url: `${location.origin}/storage/v1/object/recorder-audio/tail.wav` });
    let reachedRetry!: () => void;
    const retryReached = new Promise<void>(resolve => { reachedRetry = resolve; });
    const fetchAudio = vi.fn().mockImplementationOnce(async () => {
      reachedRetry();
      throw new TypeError('Network disconnected');
    }).mockResolvedValue(new Response(null, { status: 200 }));
    vi.stubGlobal('fetch', fetchAudio);
    const uploading = uploadAudioFile({ ...upload, onProgress: state => progress.push(state) });
    await retryReached;
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(500);
    await uploading;
    expect(fetchAudio).toHaveBeenCalledTimes(2);
    expect(upload.hooks.authorize.mock.calls.map(([chunk]) => chunk.sequence)).toEqual([0, 1, 1]);
    expect(upload.hooks.confirm.mock.calls.map(([chunk]) => chunk.sequence)).toEqual([0, 1]);
    expect(progress.filter(state => state.phase === 'uploading').map(state => state.confirmedSamples)).toEqual([0, 160000, 160003]);
    expect(upload.hooks.finalize).toHaveBeenCalledWith(expect.objectContaining({ total_samples: 160003, expected_chunks: 2 }));
    expect(await listRecoveries(scope)).toEqual([]);
  });

  it('gives each chunk its own rate-limit retry budget for a longer imported file', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    const upload = options(new Float32Array(5 * 160000));
    const firstAttempts = Array.from({ length: 5 }, () => {
      let resolve!: () => void;
      const promise = new Promise<void>(done => { resolve = done; });
      return { promise, resolve };
    });
    const attempted = new Set<number>();
    upload.hooks.authorize.mockImplementation(async chunk => {
      if (!attempted.has(chunk.sequence)) {
        attempted.add(chunk.sequence);
        firstAttempts[chunk.sequence]!.resolve();
        throw Object.assign(new Error('Rate limited'), { status: 429 });
      }
      return { confirmed: true };
    });
    const uploading = uploadAudioFile(upload);
    for (const attempt of firstAttempts) {
      await attempt.promise;
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(60000);
    }
    await uploading;
    expect(upload.hooks.authorize.mock.calls.map(([chunk]) => chunk.sequence)).toEqual([0, 0, 1, 1, 2, 2, 3, 3, 4, 4]);
    expect(upload.hooks.confirm.mock.calls.map(([chunk]) => chunk.sequence)).toEqual([0, 1, 2, 3, 4]);
    expect(upload.hooks.finalize).toHaveBeenCalledWith(expect.objectContaining({ total_samples: 800000, expected_chunks: 5 }));
    expect(await pending(scope)).toEqual([]);
    expect(await listRecoveries(scope)).toEqual([]);
  });

  it('waits a full rate-limit minute while renewing the lease and clears its heartbeat after success', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    const upload = options();
    let reachedRetry!: () => void;
    const retryReached = new Promise<void>(resolve => { reachedRetry = resolve; });
    upload.hooks.event.mockImplementationOnce(async () => {
      reachedRetry();
      throw Object.assign(new Error('Rate limited'), { status: 429 });
    });
    const uploading = uploadAudioFile(upload);
    await retryReached;
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(59999);
    expect(upload.hooks.event).toHaveBeenCalledTimes(1);
    expect(upload.hooks.heartbeat.mock.calls.length).toBeGreaterThanOrEqual(4);
    await vi.advanceTimersByTimeAsync(1);
    await uploading;
    expect(upload.hooks.event.mock.calls).toEqual([['started', 0], ['started', 0], ['stopped', 3]]);
    expect(upload.hooks.finalize).toHaveBeenCalledOnce();
    const heartbeatCount = upload.hooks.heartbeat.mock.calls.length;
    await vi.advanceTimersByTimeAsync(15000);
    expect(upload.hooks.heartbeat).toHaveBeenCalledTimes(heartbeatCount);
  });
});
