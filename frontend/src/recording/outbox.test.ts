import 'fake-indexeddb/auto';
import { describe, expect, it } from 'vitest';
import { pending, putChunk, removeChunk, saveRecovery, listRecoveries, finishRecovery, scopeKey, type Scope, type Recovery, type StoredChunk } from './outbox';
const scopeA: Scope = { userId: 'alice', workspaceId: 'workspace-a' };
const scopeB: Scope = { userId: 'bob', workspaceId: 'workspace-a' };
function fixture(scope: Scope, id: string, sequence = 0) {
  const recovery: Recovery = { key:`${scopeKey(scope)}/${id}`,scope:scopeKey(scope),conversationId:id,partId:'part-1',leaseToken:'token',totalSamples:80,chunkCount:1,stopped:true,updatedAt:new Date().toISOString() };
  const chunk: StoredChunk = {key:`${recovery.key}/${sequence}`,scope:recovery.scope,audio:new ArrayBuffer(204),meta:{conversation_id:id,part_id:recovery.partId,sequence,start_sample:0,sample_count:80,sample_rate:16000,bytes:204,sha256:'a'.repeat(64),mime:'audio/wav',lease_token:'token'}};
  return {recovery,chunk};
}
describe('durable account-scoped audio outbox',()=>{
  it('persists audio and recovery metadata atomically without exposing another account',async()=>{
    const {chunk,recovery}=fixture(scopeA,'conv-isolated');await putChunk(scopeA,chunk,recovery);
    expect((await pending(scopeA,'conv-isolated'))[0].audio.byteLength).toBe(204);
    expect(await pending(scopeB)).toEqual([]);
    expect(await listRecoveries(scopeB)).toEqual([]);
    expect(await listRecoveries({userId:'alice',workspaceId:'different-workspace'})).toEqual([]);
    await removeChunk(chunk.key);await finishRecovery(recovery.key);
  });
  it('aborts bounded persistence instead of silently evicting previously captured chunks',async()=>{
    const first=fixture(scopeA,'conv-capacity');await putChunk(scopeA,first.chunk,first.recovery,300);
    const second=fixture(scopeA,'conv-overflow');
    await expect(putChunk(scopeA,second.chunk,second.recovery,300)).rejects.toThrow('full');
    expect((await pending(scopeA)).some(row=>row.key===first.chunk.key)).toBe(true);
    expect((await listRecoveries(scopeA)).some(row=>row.key===second.recovery.key)).toBe(false);
    await removeChunk(first.chunk.key);await finishRecovery(first.recovery.key);
  });
  it('rejects conflicting chunk identities rather than upserting audio',async()=>{
    const {chunk,recovery}=fixture(scopeA,'conv-immutable');await putChunk(scopeA,chunk,recovery);
    await expect(putChunk(scopeA,{...chunk,audio:new ArrayBuffer(205)},recovery)).rejects.toThrow();
    expect((await pending(scopeA,'conv-immutable'))[0].audio.byteLength).toBe(204);
    await removeChunk(chunk.key);await finishRecovery(recovery.key);
  });
  it('keeps stopped-manifest metadata after successful chunk upload until finalization commits',async()=>{
    const {chunk,recovery}=fixture(scopeA,'conv-tail');await putChunk(scopeA,chunk,recovery);await removeChunk(chunk.key);
    await saveRecovery({...recovery,totalSamples:97,chunkCount:2});
    expect((await listRecoveries(scopeA)).find(row=>row.key===recovery.key)?.totalSamples).toBe(97);
    await finishRecovery(recovery.key);
  });
});
