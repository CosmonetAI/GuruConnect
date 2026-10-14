export interface Scope { userId: string; workspaceId: string }
export interface ChunkMeta { conversation_id: string; part_id: string; sequence: number; start_sample: number; sample_count: number; sample_rate: 16000; bytes: number; sha256: string; mime: 'audio/wav'; lease_token: string }
export interface StoredChunk { key: string; scope: string; meta: ChunkMeta; audio: ArrayBuffer }
export interface Recovery { key: string; scope: string; conversationId: string; partId: string; leaseToken: string; totalSamples: number; chunkCount: number; stopped: boolean; updatedAt: string }
export const scopeKey = (scope: Scope) => `${scope.userId}/${scope.workspaceId}`;
const database = () => new Promise<IDBDatabase>((resolve, reject) => {
  const open = indexedDB.open('guruconnect-recorder-outbox', 1);
  open.onupgradeneeded = () => {
    const chunks = open.result.createObjectStore('chunks', { keyPath: 'key' }); chunks.createIndex('scope', 'scope');
    const sessions = open.result.createObjectStore('sessions', { keyPath: 'key' }); sessions.createIndex('scope', 'scope');
  };
  open.onsuccess = () => resolve(open.result); open.onerror = () => reject(open.error);
});
const request = <T>(value: IDBRequest<T>): Promise<T> => new Promise((resolve, reject) => { value.onsuccess = () => resolve(value.result); value.onerror = () => reject(value.error); });
const done = (tx: IDBTransaction) => new Promise<void>((resolve, reject) => { tx.oncomplete = () => resolve(); tx.onabort = tx.onerror = () => reject(tx.error ?? new Error('Local audio persistence failed')); });
export async function pending(scope: Scope, conversationId?: string): Promise<StoredChunk[]> {
  const db = await database();
  try { const tx = db.transaction('chunks'); const completion = done(tx); const rows = await request<StoredChunk[]>(tx.objectStore('chunks').index('scope').getAll(scopeKey(scope))); await completion; return rows.filter(row => !conversationId || row.meta.conversation_id === conversationId).sort((a,b) => a.meta.start_sample - b.meta.start_sample); } finally { db.close(); }
}
export async function putChunk(scope: Scope, chunk: StoredChunk, recovery: Recovery, limitBytes = 64 * 1024 * 1024) {
  const scoped = scopeKey(scope);
  if (chunk.scope !== scoped || recovery.scope !== scoped || chunk.meta.conversation_id !== recovery.conversationId || chunk.meta.part_id !== recovery.partId) throw new Error('Invalid audio recording scope.');
  const db = await database();
  try {
    const tx = db.transaction(['chunks', 'sessions'], 'readwrite'); const completion = done(tx);
    const rows = await request<StoredChunk[]>(tx.objectStore('chunks').index('scope').getAll(scopeKey(scope)));
    if (rows.reduce((sum,row) => sum + row.meta.bytes, 0) + chunk.meta.bytes > limitBytes) { tx.abort(); await completion.catch(() => undefined); throw new Error('Local upload queue is full. Capture stopped; reconnect to save pending audio.'); }
    tx.objectStore('chunks').add(chunk); tx.objectStore('sessions').put(recovery); await completion;
  } finally { db.close(); }
}
/** A file import is recoverable only when every chunk and its final manifest commit together. */
export async function stageUpload(scope: Scope, chunks: StoredChunk[], recovery: Recovery, limitBytes = 256 * 1024 * 1024) {
  const scoped = scopeKey(scope);
  if (!chunks.length || recovery.scope !== scoped || chunks.some(chunk => chunk.scope !== scoped || chunk.meta.conversation_id !== recovery.conversationId || chunk.meta.part_id !== recovery.partId)) throw new Error('Invalid audio upload scope.');
  const bytes = chunks.reduce((sum, chunk) => sum + chunk.meta.bytes, 0);
  const db = await database();
  try {
    const tx = db.transaction(['chunks', 'sessions'], 'readwrite'); const completion = done(tx);
    const rows = await request<StoredChunk[]>(tx.objectStore('chunks').index('scope').getAll(scoped));
    if (rows.reduce((sum, row) => sum + row.meta.bytes, 0) + bytes > limitBytes) {
      tx.abort(); await completion.catch(() => undefined);
      throw new Error('There is not enough local space for this audio file. Recover your pending audio first, then choose the file again.');
    }
    for (const chunk of chunks) tx.objectStore('chunks').add(chunk);
    tx.objectStore('sessions').add(recovery);
    await completion;
  } finally { db.close(); }
}
export async function removeChunk(key: string) { const db = await database(); try { const tx = db.transaction('chunks', 'readwrite'); const completion = done(tx); tx.objectStore('chunks').delete(key); await completion; } finally { db.close(); } }
export async function saveRecovery(value: Recovery) { const db = await database(); try { const tx = db.transaction('sessions','readwrite'); const completion = done(tx); tx.objectStore('sessions').put(value); await completion; } finally { db.close(); } }
export async function listRecoveries(scope: Scope): Promise<Recovery[]> { const db = await database(); try { const tx = db.transaction('sessions'); const completion = done(tx); const rows = await request<Recovery[]>(tx.objectStore('sessions').index('scope').getAll(scopeKey(scope))); await completion; return rows; } finally { db.close(); } }
export async function finishRecovery(key: string) { const db = await database(); try { const tx = db.transaction('sessions', 'readwrite'); const completion = done(tx); tx.objectStore('sessions').delete(key); await completion; } finally { db.close(); } }
