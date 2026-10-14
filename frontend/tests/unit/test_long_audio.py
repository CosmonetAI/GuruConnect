import hashlib
import tracemalloc
from contextlib import asynccontextmanager
import wave
import pytest
from recorder.audio import pcm_to_wav
from recorder.config import Settings
from recorder.worker import MainWorker
import recorder.worker as module


@pytest.mark.asyncio
async def test_two_hour_final_assembly_is_bounded_and_preserves_every_sample(monkeypatch):
    raw=pcm_to_wav(b'\0\0'*80000);digest=hashlib.sha256(raw).hexdigest()
    chunks=[{'sample_start':i*80000,'sample_count':80000,'bytes':len(raw),'sha256':digest,'object_path':f'test/{i}.wav'} for i in range(1440)]
    class DB:
        @asynccontextmanager
        async def connection(self):yield object()
    async def one(*args):return {'id':'conversation','deleted_at':None}
    async def many(*args):return chunks
    class Storage:
        async def read(self,*args):return raw
    monkeypatch.setattr(module,'one',one);monkeypatch.setattr(module,'many',many)
    worker=MainWorker(Settings());worker.db=DB();worker.storage=Storage()
    tracemalloc.start()
    audio,_=await worker.audio_file({'conversation_id':'conversation','payload':{'sample_start':0,'sample_end':115200000}})
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    try:
        with wave.open(audio,'rb') as wav:assert wav.getnframes()==115200000
        assert peak<10*1024*1024
    finally:audio.close()


@pytest.mark.asyncio
async def test_long_assembly_rejects_a_single_corrupted_immutable_chunk(monkeypatch):
    raw=pcm_to_wav(b'\0\0'*80000)
    class DB:
        @asynccontextmanager
        async def connection(self):yield object()
    async def one(*args):return {'id':'conversation'}
    async def many(*args):return [{'sample_start':0,'sample_count':80000,'bytes':len(raw),'sha256':'0'*64,'object_path':'test.wav'}]
    class Storage:
        async def read(self,*args):return raw
    monkeypatch.setattr(module,'one',one);monkeypatch.setattr(module,'many',many)
    worker=MainWorker(Settings());worker.db=DB();worker.storage=Storage()
    with pytest.raises(module.ProviderError,match='input_hash_mismatch'):await worker.audio_file({'conversation_id':'conversation','payload':{'sample_end':80000}})
