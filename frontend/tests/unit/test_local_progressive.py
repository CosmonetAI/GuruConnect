"""Real local store/worker regressions with synthetic provider responses only."""
import hashlib
import io
from uuid import uuid4
import wave

import pytest

from recorder.audio import SAMPLE_RATE, pcm_to_wav
from recorder.config import Settings
from recorder.local_store import LocalStore
from recorder.local_worker import LocalWorker
from recorder.openai_translator import OPENAI_TRANSLATION_MODEL, TranslationResult
from recorder.providers import ProviderError


def capture(tmp_path, live=True):
    store = LocalStore(tmp_path)
    cid, run_id = str(uuid4()), str(uuid4())
    store.put('conversations', cid, {'id': cid, 'run_id': run_id, 'live_session': live,
        'capture_state': 'recording', 'upload_state': 'pending', 'total_samples': 0,
        'language_hint': 'auto', 'transcription_state': 'pending', 'english_state': 'pending',
        'speaker_state': 'pending', 'final_state': 'pending'})
    store.put('runs', run_id, {'id': run_id, 'conversation_id': cid, 'kind': 'production', 'state': 'queued'})
    lease = store.acquire_lease(cid, 'test-tab')
    worker = LocalWorker(Settings(_env_file=None, local_data_dir=tmp_path, worker_lease_seconds=30), store)
    return store, worker, cid, lease


def add_chunk(store, cid, lease, sequence, seconds=5):
    data = pcm_to_wav(b'\x00\x00' * int(seconds * SAMPLE_RATE))
    chunk = {'conversation_id': cid, 'part_id': lease['part_id'], 'sequence': sequence,
             'start_sample': sequence * 5 * SAMPLE_RATE, 'sample_count': seconds * SAMPLE_RATE,
             'sample_rate': SAMPLE_RATE, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
             'mime': 'audio/wav', 'lease_token': lease['lease_token']}
    store.authorize_chunk(cid, chunk)
    path = store.chunk_path(cid, lease['part_id'], sequence)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    store.mark_chunk(cid, lease['part_id'], sequence, confirmed=True, lease_token=lease['lease_token'])
    return chunk


def connected(store, engine):
    store.configure_connection(engine, {'api_key': 'synthetic-key'},
        {'model': OPENAI_TRANSLATION_MODEL} if engine == 'openai-translator' else {}, str(uuid4()), None)
    store.patch('connections', engine, {'state': 'connected'})


def speech(text='Need a fan', language='en', start=.1, end=.8, words=None):
    return {'language': language, 'language_probability': .99, 'provenance': {},
        'source_segments': [{'start': start, 'end': end, 'text': text, 'speaker': 'speaker_0',
                             'words': words or [{'text': text, 'start': start, 'end': end}]}]}


def queue_window(store, cid, lease, start=0):
    add_chunk(store, cid, lease, start)
    add_chunk(store, cid, lease, start + 1)
    return store.plan_live_windows(cid)


def test_planner_is_opt_in_durable_and_deduplicates_overlapping_windows(tmp_path):
    store, _, cid, lease = capture(tmp_path, live=False)
    add_chunk(store, cid, lease, 0)
    add_chunk(store, cid, lease, 1)
    assert store.plan_live_windows(cid) == []
    store.patch('conversations', cid, {'live_session': True})
    first = store.plan_live_windows(cid)
    assert len(first) == 1
    assert first[0]['payload']['sample_start'] == 0
    assert store.plan_live_windows(cid) == []
    add_chunk(store, cid, lease, 2)
    assert store.plan_live_windows(cid) == []
    add_chunk(store, cid, lease, 3)
    second = store.plan_live_windows(cid)
    assert len(second) == 1
    assert second[0]['payload']['sample_start'] == 8 * SAMPLE_RATE
    assert second[0]['payload']['ownership_start'] == 10 * SAMPLE_RATE
    assert second[0]['payload']['ownership_end'] == 20 * SAMPLE_RATE
    assert LocalStore(tmp_path).plan_live_windows(cid) == []
    assert store.get('conversations', cid)['total_samples'] == 20 * SAMPLE_RATE


@pytest.mark.asyncio
async def test_source_and_english_appear_before_stop_without_final_audio_assembly(tmp_path, monkeypatch):
    store, worker, cid, lease = capture(tmp_path)
    connected(store, 'elevenlabs')
    queue_window(store, cid, lease)
    calls = []

    class Speech:
        def __init__(self, key): assert key == 'synthetic-key'
        async def process(self, audio, *_):
            with wave.open(io.BytesIO(audio.read()), 'rb') as wav:
                calls.append(wav.getnframes())
            return speech()

    monkeypatch.setattr('recorder.local_worker.ElevenLabsAdapter', Speech)
    assert await worker.run_once()
    assert calls == [10 * SAMPLE_RATE]
    assert store.get_turns(cid)[0]['english_text'] == 'Need a fan'
    row = store.get('conversations', cid)
    assert row['capture_state'] == 'recording' and row['upload_state'] == 'pending'
    assert row['final_state'] == 'pending'
    assert not store.audio_path(cid).exists()


@pytest.mark.asyncio
async def test_overlap_ownership_keeps_context_word_once_and_keeps_both_windows(tmp_path, monkeypatch):
    store, worker, cid, lease = capture(tmp_path)
    connected(store, 'elevenlabs')
    queue_window(store, cid, lease)
    queue_window(store, cid, lease, 2)
    outputs = iter([speech('quiet', start=9.4, end=9.8),
        speech('quiet fan', start=1.4, end=2.8, words=[{'text': 'quiet', 'start': 1.4, 'end': 1.8}, {'text': ' fan', 'start': 2.1, 'end': 2.8}])])
    lengths = []

    class Speech:
        def __init__(self, key): pass
        async def process(self, audio, *_):
            with wave.open(io.BytesIO(audio.read()), 'rb') as wav: lengths.append(wav.getnframes())
            return next(outputs)

    monkeypatch.setattr('recorder.local_worker.ElevenLabsAdapter', Speech)
    await worker.run_once(); await worker.run_once()
    turns = store.get_turns(cid)
    assert lengths == [10 * SAMPLE_RATE, 12 * SAMPLE_RATE]
    assert ''.join(turn['source_text'] for turn in turns) == 'quiet fan'
    assert [round(turn['start_seconds'], 1) for turn in turns] == [9.4, 10.1]
    assert store.get_transcript(cid)['version']['coverage_samples'] == 20 * SAMPLE_RATE


@pytest.mark.asyncio
async def test_late_translation_updates_its_window_without_erasing_newer_english(tmp_path, monkeypatch):
    store, worker, cid, lease = capture(tmp_path)
    connected(store, 'elevenlabs'); connected(store, 'openai-translator')
    queue_window(store, cid, lease)
    queue_window(store, cid, lease, 2)
    outputs = iter([speech('नमस्ते', language='hi'), speech(' motor', start=2.2, end=2.8)])

    class Speech:
        def __init__(self, key): pass
        async def process(self, *_): return next(outputs)

    class Translate:
        def __init__(self, *args, **kwargs): pass
        async def translate(self, text, *_):
            assert text == 'नमस्ते'
            return TranslationResult('Hello', 0, 0, OPENAI_TRANSLATION_MODEL, 'test')

    monkeypatch.setattr('recorder.local_worker.ElevenLabsAdapter', Speech)
    monkeypatch.setattr('recorder.local_worker.OpenAITranslator', Translate)
    await worker.run_once()
    assert store.get_turns(cid)[0]['english_text'] is None
    delayed_translation = store.claim_job('second-worker')
    assert delayed_translation['kind'] == 'live_translate'
    await worker.run_once()
    await worker.execute(delayed_translation)
    turns = store.get_turns(cid)
    assert [turn['english_text'] for turn in turns] == ['Hello', ' motor']
    assert store.get_transcript(cid)['version']['coverage_samples'] == 20 * SAMPLE_RATE


@pytest.mark.asyncio
async def test_final_version_fences_late_live_translation_before_paid_call(tmp_path, monkeypatch):
    store, worker, cid, lease = capture(tmp_path)
    connected(store, 'elevenlabs')
    queue_window(store, cid, lease)

    class Speech:
        def __init__(self, key): pass
        async def process(self, *_): return speech('नमस्ते', language='hi')

    monkeypatch.setattr('recorder.local_worker.ElevenLabsAdapter', Speech)
    await worker.run_once()
    final = {'id': str(uuid4()), 'kind': 'production', 'coverage_samples': 10 * SAMPLE_RATE}
    store.replace_turns(cid, [{'id': 'final', 'source_text': 'Final answer', 'english_text': 'Final answer'}], final)
    assert await worker.run_once()
    assert store.get_transcript(cid)['version']['id'] == final['id']
    assert store.get_turns(cid)[0]['english_text'] == 'Final answer'
    assert next(job for job in store.list('jobs') if job['kind'] == 'live_translate')['result']['superseded']


@pytest.mark.asyncio
async def test_missing_provider_preserves_capture_and_reports_real_blocked_state(tmp_path):
    store, worker, cid, lease = capture(tmp_path)
    queue_window(store, cid, lease)
    assert await worker.run_once()
    assert store.get('conversations', cid)['capture_state'] == 'recording'
    assert store.get('conversations', cid)['transcription_state'] == 'blocked'
    assert store.get_transcript(cid)['turns'] == []
    assert all(__import__('pathlib').Path(chunk['path']).is_file() for chunk in store.chunks(cid))


@pytest.mark.asyncio
async def test_bad_chunk_hash_stops_live_inference_before_provider_submission(tmp_path, monkeypatch):
    store, worker, cid, lease = capture(tmp_path)
    connected(store, 'elevenlabs')
    queue_window(store, cid, lease)
    chunk = store.chunks(cid)[0]
    __import__('pathlib').Path(chunk['path']).write_bytes(pcm_to_wav(b'\x01\x00' * 5 * SAMPLE_RATE))

    class Forbidden:
        def __init__(self, key): raise AssertionError('Corrupt audio reached provider')

    monkeypatch.setattr('recorder.local_worker.ElevenLabsAdapter', Forbidden)
    await worker.run_once()
    job = store.list('jobs')[0]
    assert job['state'] == 'failed' and job['error_code'] == 'input_hash_mismatch'
    assert job['submission_state'] == 'not_submitted'


@pytest.mark.asyncio
async def test_final_whole_recording_pass_remains_authoritative(tmp_path, monkeypatch):
    store, worker, cid, lease = capture(tmp_path)
    connected(store, 'elevenlabs')
    queue_window(store, cid, lease)
    calls = []

    class Speech:
        def __init__(self, key): pass
        async def process(self, *_):
            calls.append(1)
            return speech('Progressive' if len(calls) == 1 else 'Final refined text')

    monkeypatch.setattr('recorder.local_worker.ElevenLabsAdapter', Speech)
    await worker.run_once()
    store.finalize_conversation(cid, {'part_id': lease['part_id'], 'expected_chunks': 2, 'total_samples': 10 * SAMPLE_RATE}, lease['lease_token'])
    await worker.run_once(); await worker.run_once()
    assert store.get_turns(cid)[0]['english_text'] == 'Final refined text'
    assert store.get('conversations', cid)['final_state'] == 'complete'
    assert store.get_transcript(cid)['version']['kind'] == 'production'


@pytest.mark.asyncio
@pytest.mark.parametrize('link_after_upload', [False, True])
async def test_real_local_api_confirms_short_chunks_and_exposes_translation_before_end(tmp_path, monkeypatch, link_after_upload):
    import httpx
    from recorder.local_api import create_local_app
    from recorder.local_store import LOCAL_WORKSPACE_ID

    settings = Settings(_env_file=None, environment='development', local_data_dir=tmp_path,
                        embedded_processing_enabled=False, cors_origins=['http://127.0.0.1:5176'],
                        database_url=None, supabase_url='', supabase_service_role_key=None)
    app = create_local_app(settings)
    store = app.state.local_store
    connected(store, 'elevenlabs'); connected(store, 'openai-translator')
    worker = LocalWorker(settings, store)

    class Speech:
        def __init__(self, key): assert key == 'synthetic-key'
        async def process(self, audio, *_):
            assert audio.read(4) == b'RIFF'
            return speech('पंखा चाहिए', language='hi')

    class Translate:
        def __init__(self, *args, **kwargs): pass
        async def translate(self, *_):
            return TranslationResult('I need a fan.', 0, 0, OPENAI_TRANSLATION_MODEL, 'test')

    class Analysis:
        async def run(self, *, chunk, durable_context):
            return {'context': durable_context or {}, 'extraction': {}, 'route': {}, 'agent_result': {}}

    monkeypatch.setattr('recorder.local_worker.ElevenLabsAdapter', Speech)
    monkeypatch.setattr('recorder.local_worker.OpenAITranslator', Translate)
    app.state.local_voice_analysis.orchestrator_factory = Analysis
    transport = httpx.ASGITransport(app=app, client=('127.0.0.1', 1111))
    headers = {'Origin': 'http://127.0.0.1:5176', 'X-Workspace-ID': LOCAL_WORKSPACE_ID}
    async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:8010', headers=headers) as client:
        customer = {'name': 'Local live test', 'phone': '9876543210', 'email': 'qa@example.test'}
        opened = await client.post('/api/voice-sessions', json=customer)
        assert opened.status_code == 200, opened.text
        sid = opened.json()['session']['id']
        created = await client.post('/api/v1/conversations', headers={'Idempotency-Key': str(uuid4())},
            json={'customer_name': customer['name'], 'phone': customer['phone'], 'email': customer['email'],
                  'consent_confirmed': True, 'audio_only': False})
        assert created.status_code == 201, created.text
        cid = created.json()['id']
        if not link_after_upload:
            linked = await client.post(f'/api/voice-sessions/{sid}/conversation', json={'conversation_id': cid})
            assert linked.status_code == 200, linked.text
        leased = await client.post(f'/api/v1/conversations/{cid}/lease', json={'tab_id': 'synthetic-live-tab'})
        lease = leased.json()
        started = await client.post(f'/api/v1/conversations/{cid}/events', json={'part_id': lease['part_id'],
            'lease_token': lease['lease_token'], 'kind': 'started', 'sample': 0})
        assert started.status_code == 200, started.text
        for sequence in range(2):
            audio = pcm_to_wav(b'\0\0' * 5 * SAMPLE_RATE)
            metadata = {'part_id': lease['part_id'], 'lease_token': lease['lease_token'], 'sequence': sequence,
                        'sample_count': 5 * SAMPLE_RATE, 'start_sample': sequence * 5 * SAMPLE_RATE,
                        'sample_rate': SAMPLE_RATE, 'bytes': len(audio), 'sha256': hashlib.sha256(audio).hexdigest(), 'mime': 'audio/wav'}
            permit = await client.post(f'/api/v1/conversations/{cid}/chunks/authorize', json=metadata)
            assert permit.status_code == 200, permit.text
            uploaded = await client.put(permit.json()['upload_url'], content=audio, headers={'Content-Type': 'audio/wav'})
            assert uploaded.status_code == 200, uploaded.text
            confirmed = await client.post(f'/api/v1/conversations/{cid}/chunks/confirm', json=metadata)
            assert confirmed.status_code == 200, confirmed.text
        if link_after_upload:
            assert not store.list('jobs', {'conversation_id': cid})
            linked = await client.post(f'/api/voice-sessions/{sid}/conversation', json={'conversation_id': cid})
            assert linked.status_code == 200, linked.text
        assert await worker.run_once()
        pending = (await client.get(f'/api/voice-sessions/{sid}/live')).json()
        assert pending['transcript_preview'][0]['source_text'] == 'पंखा चाहिए'
        assert pending['transcript_preview'][0]['english_text'] is None
        assert pending['recording']['capture_state'] == 'recording'
        assert await worker.run_once()
        translated = (await client.get(f'/api/voice-sessions/{sid}/live')).json()
        assert translated['transcript_preview'][0]['english_text'] == 'I need a fan.'
        assert translated['recording']['capture_state'] == 'recording'
        assert translated['recording']['upload_state'] == 'pending'
    tasks = list(app.state.local_voice_analysis.tasks.values())
    if tasks:
        import asyncio
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_live_translation_retry_uses_cached_successful_turns(tmp_path, monkeypatch):
    store, worker, cid, lease = capture(tmp_path)
    connected(store, 'elevenlabs'); connected(store, 'openai-translator')
    queue_window(store, cid, lease)
    translated = []
    fail_second = True

    class Speech:
        def __init__(self, key): pass
        async def process(self, *_):
            result = speech('पहला', language='hi', start=.1, end=.3)
            result['source_segments'].append({'start': .4, 'end': .7, 'text': 'दूसरा', 'speaker': 'speaker_1',
                                             'words': [{'text': 'दूसरा', 'start': .4, 'end': .7}]})
            return result

    class Translate:
        def __init__(self, *args, **kwargs): pass
        async def translate(self, text, *_):
            translated.append(text)
            if text == 'दूसरा' and fail_second:
                raise ProviderError('provider_quota_exceeded')
            return TranslationResult('First' if text == 'पहला' else 'Second', 0, 0, OPENAI_TRANSLATION_MODEL, 'test')

    monkeypatch.setattr('recorder.local_worker.ElevenLabsAdapter', Speech)
    monkeypatch.setattr('recorder.local_worker.OpenAITranslator', Translate)
    await worker.run_once(); await worker.run_once()
    assert [turn['english_text'] for turn in store.get_turns(cid)] == ['First', None]
    fail_second = False
    resumed, uncertain = store.retry_jobs(cid, ['translate'])
    assert len(resumed) == 1 and not uncertain
    await worker.run_once()
    assert translated == ['पहला', 'दूसरा', 'दूसरा']
    assert [turn['english_text'] for turn in store.get_turns(cid)] == ['First', 'Second']


def test_deletion_removes_progressive_window_speech(tmp_path):
    store, _, cid, lease = capture(tmp_path)
    queue_window(store, cid, lease)
    job = store.claim_job('test')
    store.publish_live_window(job, [{'id': 'turn', 'start_seconds': 0, 'end_seconds': 1,
        'source_text': 'Private source', 'english_text': 'Private translation'}], [], {})
    store.complete_job(job['id'], lease_token=job['lease_token'])
    store.delete_conversation(cid)
    cleanup = store.claim_job('cleanup')
    assert cleanup['kind'] == 'delete'
    store.finish_deleted_conversation(cid, cleanup['id'], cleanup['lease_token'])
    assert store.list('live_windows') == []
    assert store.get_transcript(cid)['turns'] == []
