"""Real disposable PostgreSQL API tests, with explicitly emulated Storage/Auth.

No hosted credentials, paid provider calls or fake production transcripts are used.
"""
import hashlib
import asyncio
import io
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
import wave
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi import Request
from fastapi.testclient import TestClient
from recorder.config import Settings
from recorder.main import create_app
from recorder.security import User, current_user
from recorder.db import one
from recorder.processing import publish_result
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from database.native_migrations import apply_native_migrations

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def database_url():
    if not all(shutil.which(command) for command in ("initdb", "pg_ctl", "psql")):
        pytest.skip("Native PostgreSQL binaries unavailable; run hosted integration separately")
    with tempfile.TemporaryDirectory(prefix="guru-api-test-") as temporary:
        path = Path(temporary)
        subprocess.run(["initdb", "-D", str(path/"data"), "-A", "trust", "--no-locale", "-E", "UTF8"], check=True, capture_output=True)
        # Unix socket only; the test never opens an unauthenticated network port.
        subprocess.run(["pg_ctl", "-D", str(path/"data"), "-l", str(path/"postgres.log"), "-o", f"-p 55441 -h '' -k {path} -c wal_level=logical", "-w", "start"], check=True, capture_output=True)
        url = f"postgresql:///postgres?host={path}&port=55441"
        try:
            with psycopg.connect(url, autocommit=True) as connection:
                connection.execute((ROOT/"frontend/tests/database/native_fixture.sql").read_text(), prepare=False)
                apply_native_migrations(connection, sorted((ROOT/"supabase/migrations").glob("*.sql")))
            yield url
        finally:
            subprocess.run(["pg_ctl", "-D", str(path/"data"), "-m", "fast", "-w", "stop"], check=True, capture_output=True)


@pytest.fixture
def api(database_url):
    settings = Settings(database_url=database_url, supabase_url="https://fixture.supabase.co", supabase_anon_key="public-test-key", supabase_service_role_key="service-test-key", credential_encryption_key=Fernet.generate_key().decode())
    app = create_app(settings)
    ids = [uuid4() for _ in range(3)]
    with psycopg.connect(database_url, autocommit=True) as connection:
        for index, user_id in enumerate(ids):
            connection.execute("insert into auth.users(id,email,email_confirmed_at,raw_user_meta_data) values(%s,%s,now(),%s::jsonb)", (user_id, f"u{index}@example.test", '{"display_name":"Salesperson"}'))
    async def test_user(request: Request):
        # Explicit fixture session; real JWT verification is covered independently.
        index = int(request.headers.get("X-Test-User", "0"))
        return User(ids[index], f"u{index}@example.test", f"Salesperson {index}")
    app.dependency_overrides[current_user] = test_user
    blobs = {}
    async def read(path, max_bytes):
        from recorder.errors import fail
        if path not in blobs:
            fail("audio_object_missing", 409)
        return blobs[path]
    app.state.storage.read = read
    with TestClient(app) as client:
        org = client.post("/api/v1/workspaces", json={"name": "Test workspace"}).json()["id"]
        with psycopg.connect(database_url, autocommit=True) as connection:
            for user_id in ids[1:]:
                connection.execute("insert into members(organisation_id,user_id,role) values(%s,%s,'salesperson')", (org, user_id))
            connection.execute("update engine_connections set state='connected',tested_at=now() where organisation_id=%s and engine_id='elevenlabs'", (org,))
        headers = {"X-Workspace-ID": org, "Authorization": "Bearer test-fixture-only", "X-Test-User": "1"}
        yield client, headers, ids, blobs, database_url
        # This module shares a disposable cluster, so leave no eligible jobs
        # for another test's globally scoped main-worker claim.
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute("update processing_jobs set state='cancelled',cancelled_at=now(),finish_in_flight=false where organisation_id=%s and state not in ('succeeded','cancelled')", (org,))


def new(client, headers, **fields):
    response = client.post("/api/v1/conversations", headers=headers, json={"customer_name": "Sam", "phone": "9876543210", "consent_confirmed": True, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def wav(samples=80000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(16000)
        file.writeframes(b"\x01\x00"*samples)
    return buffer.getvalue()


def reserve(client, headers, conv, lease, blobs, sequence=0, samples=80000, start_sample=0, audio=None):
    data = audio or wav(samples)
    body = {"conversation_id": conv["id"], "part_id": lease["part_id"], "lease_token": lease["lease_token"], "sequence": sequence, "sample_count": samples, "sample_rate": 16000, "start_sample": start_sample, "bytes": len(data), "mime": "audio/wav", "sha256": hashlib.sha256(data).hexdigest()}
    url = f"/api/v1/conversations/{conv['id']}/chunks"
    response = client.post(url+"/authorize", headers=headers, json=body)
    assert response.status_code == 200, response.text
    permit = response.json()
    assert permit["method"] == "POST"
    assert "token=" not in permit["upload_url"]
    blobs[permit["object_path"]] = data
    return body, url


def test_customers_conversations_isolation_and_role_escalation(api):
    client, headers, ids, _, _ = api
    a, again = new(client, headers), new(client, headers)
    assert a["customer_id"] == again["customer_id"]  # Same member and contact identity reuse the customer.
    assert a["owner_id"] == str(ids[1])
    b = {**headers, "X-Test-User": "2"}
    own_b = new(client, b, customer_name="Another customer")
    assert client.get(f"/api/v1/conversations/{a['id']}", headers=b).status_code == 404
    assert client.get(f"/api/v1/conversations/{a['id']}/export", headers=b).status_code == 404
    assert client.post("/api/v1/conversations", headers=b, json={"customer_id": a["customer_id"], "consent_confirmed": True}).status_code == 404
    assert client.post("/api/v1/conversations", headers=b, json={"customer_name": "Sam", "consent_confirmed": True, "owner_id": str(ids[1])}).status_code == 422
    assert client.patch("/api/v1/engine-settings", headers=b, json={"default_engine": "elevenlabs"}).status_code == 403
    assert client.post("/api/v1/invites", headers=b, json={"email": "x@example.test", "role": "admin"}).status_code == 403
    assert {row["id"] for row in client.get("/api/v1/conversations?state=ready", headers=b).json()["items"]} == {own_b["id"]}
    admin = {**headers, "X-Test-User": "0"}
    assert client.get(f"/api/v1/conversations/{a['id']}", headers=admin).status_code == 404
    assert client.get(f"/api/v1/conversations/{a['id']}", headers={**admin, "X-Workspace-Member-ID": str(ids[1])}).status_code == 200


def test_twenty_agents_record_in_parallel_and_share_four_processing_slots(api, monkeypatch):
    """Real DB/API/queue fences; in-memory private Storage and synthetic ASR only."""
    from recorder.providers import ElevenLabsAdapter
    from recorder.worker import MainWorker
    client, headers, ids, blobs, database_url = api
    with psycopg.connect(database_url, autocommit=True) as connection:
        for index in range(3, 21):
            user_id = uuid4()
            ids.append(user_id)
            connection.execute("insert into auth.users(id,email,email_confirmed_at) values(%s,%s,now())", (user_id, f"parallel-agent-{index}@example.invalid"))
            connection.execute("insert into members(organisation_id,user_id,role) values(%s,%s,'salesperson')", (headers['X-Workspace-ID'], user_id))
        connection.execute("update organisations set max_concurrent_jobs=4 where id=%s", (headers['X-Workspace-ID'],))
        connection.execute("update engine_connections set credential_scope='synthetic-shared-provider',max_concurrency=4 where organisation_id=%s and engine_id='elevenlabs'", (headers['X-Workspace-ID'],))

    def capture(index):
        agent = {**headers, 'X-Test-User': str(index)}
        conv = new(client, agent, customer_name=f'Parallel customer {index}', phone=f'900000{index:04d}')
        prefix = f"/api/v1/conversations/{conv['id']}"
        lease = client.post(prefix + '/lease', headers=agent, json={'tab_id': f'parallel-tab-{index:04d}'}).json()
        chunk, url = reserve(client, agent, conv, lease, blobs)
        confirmed = client.post(url + '/confirm', headers=agent, json=chunk)
        assert confirmed.status_code == 200, confirmed.text
        manifest = {'part_id': lease['part_id'], 'lease_token': lease['lease_token'], 'expected_chunks': 1, 'total_samples': chunk['sample_count']}
        saved = client.post(prefix + '/finalize', headers=agent, json=manifest)
        assert saved.status_code == 200, saved.text
        return agent, conv

    with ThreadPoolExecutor(max_workers=20) as pool:
        captures = list(pool.map(capture, range(1, 21)))
    assert len({conv['id'] for _, conv in captures}) == 20
    assert len({conv['customer_id'] for _, conv in captures}) == 20
    with psycopg.connect(database_url) as connection:
        planned_jobs = connection.execute("select count(*) from processing_jobs where organisation_id=%s and kind in ('transcribe','finalise')", (headers['X-Workspace-ID'],)).fetchone()[0]

    active = peak = calls = 0
    async def synthetic_asr(self, audio, language_hint, scope):
        nonlocal active, peak, calls
        active += 1
        peak = max(peak, active)
        calls += 1
        try:
            await asyncio.sleep(0.03)
            return {'raw': {'synthetic': True}, 'language': 'en', 'language_probability': .99,
                'source_segments': [{'start': 0., 'end': 4., 'text': f'Synthetic transcript {scope}', 'speaker': 'speaker-a'}],
                'diarization_status': 'completed', 'diarization_intervals': [], 'provenance': {'synthetic': True}}
        finally:
            active -= 1
    monkeypatch.setattr(ElevenLabsAdapter, 'process', synthetic_asr)

    async def process_jobs():
        settings = Settings(_env_file=None, database_url=database_url, supabase_url='https://fixture.supabase.co',
            supabase_service_role_key='synthetic-service-key', processing_concurrency=4)
        worker = MainWorker(settings)
        async def credentials(connection_id):
            return {'api_key': 'synthetic-provider-key'}
        async def read(path, maximum):
            assert path in blobs and len(blobs[path]) <= maximum
            return blobs[path]
        async def put(path, data, mime='application/json'):
            blobs[path] = data
        async def maintenance():
            await worker.stopping.wait()
        monkeypatch.setattr(worker, 'credentials', credentials)
        monkeypatch.setattr(worker.storage, 'read', read)
        monkeypatch.setattr(worker.storage, 'put', put)
        monkeypatch.setattr(worker, 'maintenance', maintenance)
        task = asyncio.create_task(worker.run())
        try:
            async with asyncio.timeout(15):
                async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as connection:
                    while True:
                        cursor = await connection.execute("select count(*) from processing_jobs where organisation_id=%s and state='succeeded'", (headers['X-Workspace-ID'],))
                        if (await cursor.fetchone())[0] == planned_jobs:
                            break
                        await asyncio.sleep(.05)
        finally:
            worker.stopping.set()
            await task
    asyncio.run(process_jobs())
    assert calls == planned_jobs and 1 < peak <= 4

    for index, (agent, conv) in enumerate(captures):
        prefix = f"/api/v1/conversations/{conv['id']}"
        transcript = client.get(prefix + '/transcript', headers=agent)
        assert transcript.status_code == 200 and transcript.json()['version']['english_state'] == 'complete'
        assert len(transcript.json()['turns']) == 1
        playback = client.get(prefix + '/playback/stream', headers=agent)
        assert playback.status_code == 200 and playback.content == wav()
        peer = captures[(index + 1) % len(captures)][0]
        assert client.get(prefix + '/transcript', headers=peer).status_code == 404
        assert client.get(prefix + '/playback/stream', headers=peer).status_code == 404
    with psycopg.connect(database_url) as connection:
        assert connection.execute("select count(*) from processing_jobs where organisation_id=%s and state='succeeded'", (headers['X-Workspace-ID'],)).fetchone()[0] == planned_jobs
        assert connection.execute("select count(*) from transcript_versions v join conversations c on c.id=v.conversation_id where c.organisation_id=%s", (headers['X-Workspace-ID'],)).fetchone()[0] == planned_jobs


def test_chunk_integrity_idempotency_gaps_stop_and_tail(api):
    client, headers, _, blobs, database_url = api
    conv = new(client, headers)
    prefix = f"/api/v1/conversations/{conv['id']}"
    lease = client.post(prefix+"/lease", headers=headers, json={"tab_id": "first-tab-unique-id"}).json()
    assert client.post(prefix+"/lease", headers=headers, json={"tab_id": "other-tab-unique-id"}).status_code == 409
    second, url = reserve(client, headers, conv, lease, blobs, sequence=1, start_sample=80000)
    assert client.post(url+"/confirm", headers=headers, json=second).status_code == 200
    manifest = {"part_id": lease["part_id"], "lease_token": lease["lease_token"], "expected_chunks": 2, "total_samples": 160000}
    missing = client.post(prefix+"/finalize", headers=headers, json=manifest)
    assert missing.status_code == 409 and missing.json()["detail"]["missing_sequences"] == [0]
    first, _ = reserve(client, headers, conv, lease, blobs)
    bad = {**first, "sha256": "a"*64}
    assert client.post(url+"/authorize", headers=headers, json=bad).status_code == 409
    original = next(path for path in blobs if path.endswith("/0.wav"))
    blobs[original] = b"invalid" * 100
    assert client.post(url+"/confirm", headers=headers, json=first).status_code == 422
    blobs[original] = wav()
    assert client.post(url+"/confirm", headers=headers, json=first).status_code == 200
    assert client.post(url+"/confirm", headers=headers, json=first).json()["confirmed"] is True
    tail, _ = reserve(client, headers, conv, lease, blobs, sequence=2, start_sample=160000, samples=1234)
    assert client.post(url+"/confirm", headers=headers, json=tail).status_code == 200
    event = {"part_id": lease["part_id"], "lease_token": lease["lease_token"], "kind": "stopped", "sample": 161234}
    assert client.post(prefix+"/events", headers=headers, json=event).status_code == 200
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("update recording_leases set expires_at=now()-interval '1 second' where conversation_id=%s", (conv["id"],))
    assert client.post(prefix+"/lease", headers=headers, json={"tab_id":"first-tab-unique-id","takeover":True}).status_code==409
    recovered = client.post(prefix+"/lease", headers=headers, json={"tab_id": "first-tab-unique-id", "takeover": True,"purpose":"recover"})
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["capture_state"] == "stopped"
    manifest.update(expected_chunks=3, total_samples=161234, lease_token=recovered.json()["lease_token"])
    assert client.post(prefix+"/finalize", headers=headers, json=manifest).status_code == 200
    assert client.post(prefix+"/finalize", headers=headers, json=manifest).status_code == 200
    assert client.post(url+"/authorize",headers=headers,json=first).json()['confirmed']
    assert client.post(url+"/confirm",headers=headers,json=first).json()['confirmed']
    assert client.post(prefix+"/finalize", headers=headers, json={**manifest, "total_samples": 161235}).status_code == 409
    with psycopg.connect(database_url) as connection:
        assert connection.execute("select count(*) from processing_jobs where conversation_id=%s and kind='finalise'", (conv["id"],)).fetchone()[0] == 1
        assert connection.execute("select count(*) from processing_jobs where conversation_id=%s and kind='assemble'", (conv["id"],)).fetchone()[0] == 0
    playback = client.get(prefix + "/playback/stream", headers=headers)
    assert playback.status_code == 200 and len(playback.content) == 44 + 161234 * 2


def test_revocation_preserves_history_and_blocks_upload(api):
    client, headers, ids, blobs, _ = api
    conv = new(client, headers)
    lease = client.post(f"/api/v1/conversations/{conv['id']}/lease", headers=headers, json={"tab_id": "revocation-test-tab"}).json()
    chunk, url = reserve(client, headers, conv, lease, blobs)
    admin = {**headers, "X-Test-User": "0"}
    assert client.delete(f"/api/v1/team/{ids[1]}", headers=admin).status_code == 200
    assert client.get(f"/api/v1/conversations/{conv['id']}", headers=headers).status_code == 403
    assert client.post(url+"/confirm", headers=headers, json=chunk).status_code == 403
    selected = {**admin, "X-Workspace-Member-ID": str(ids[1])}
    assert client.get(f"/api/v1/conversations/{conv['id']}", headers=selected).status_code == 200
    assert client.post(url + "/confirm", headers=selected, json=chunk).status_code == 403


def test_disconnect_never_reroutes_default_and_audio_only_is_explicit(api):
    client, headers, _, _, _ = api
    admin = {**headers, "X-Test-User": "0"}
    conn = next(item for item in client.get("/api/v1/connections", headers=admin).json()["items"] if item["engine_id"] == "elevenlabs")
    old = new(client, headers)
    assert client.post(f"/api/v1/connections/{conn['id']}/disconnect", headers=admin, json={"finish_in_flight": False}).status_code == 200
    response = client.post("/api/v1/conversations", headers=headers, json={"customer_name": "Sam", "phone": "9876543210", "consent_confirmed": True})
    assert response.status_code == 409
    audio = new(client, headers, audio_only=True)
    assert audio["runs"] == [] and audio["transcription_state"] == "blocked"
    assert client.get(f"/api/v1/conversations/{old['id']}", headers=headers).json()["runs"][0]["connection_generation"] == old["runs"][0]["connection_generation"]


def test_progressive_before_stop_versions_corrections_speaker_groups_and_deletion(api):
    client, headers, _, blobs, database_url = api
    conv = new(client, headers)
    prefix = f"/api/v1/conversations/{conv['id']}"
    lease = client.post(prefix+"/lease", headers=headers, json={"tab_id": "progressive-test-tab"}).json()
    for sequence in range(12):
        chunk, url = reserve(client, headers, conv, lease, blobs, sequence=sequence, start_sample=sequence*80000)
        assert client.post(url+"/confirm", headers=headers, json=chunk).status_code == 200
    async def commit_windows():
        async with await psycopg.AsyncConnection.connect(database_url, row_factory=dict_row) as connection:
            cursor = await connection.execute("select * from claim_jobs('fixture-main','main',2,180)")
            jobs = await cursor.fetchall()
        assert len(jobs) == 2
        for job in reversed(jobs):
            start = float(job["payload"]["ownership_start"]-job["payload"]["sample_start"]) / 16000
            segments = [{"start": start, "end": start+3, "text": "Fixture repeated repeated", "speaker": "voice-a"}, {"start": start+4, "end": start+6, "text": "Fixture reply", "speaker": "voice-b"}]
            result = {"source_segments": segments, "english_segments": segments, "language": "en", "diarization_status": "completed", "provenance": {"test_fixture": True}}
            async with await psycopg.AsyncConnection.connect(database_url, row_factory=dict_row) as connection:
                row = await one(connection, "select finish_job(%s,'fixture-main',%s,%s,%s) as accepted", (job["id"], job["lease_token"], job["fence"], Jsonb(result)))
                assert row["accepted"]
                await publish_result(connection, job, result)
    asyncio.run(commit_windows())
    before_stop = client.get(prefix+"/transcript", headers=headers).json()
    assert len(before_stop["turns"]) == 4
    assert before_stop["version"]["coverage_samples"] == 960000
    assert client.get(prefix, headers=headers).json()["capture_state"] == "recording"
    refs = list(dict.fromkeys(turn["speaker_ref"] for turn in before_stop["turns"]))
    assert len(refs) == 4  # Window speaker-a is not a persistent person identity.
    version = before_stop["version"]["id"]
    merge = client.post(prefix+"/speaker-groups", headers=headers, json={"version_id": version, "operation": "merge", "speaker_refs": refs[:2], "target_ref": refs[0]})
    assert merge.status_code == 200, merge.text
    merged = client.get(prefix+"/transcript", headers=headers).json()
    group_turns = [turn for turn in merged["turns"] if turn["speaker_ref"] == refs[0]]
    assert len(group_turns) == 2
    split = client.post(prefix+"/speaker-groups", headers=headers, json={"version_id": version, "operation": "split", "speaker_refs": [refs[0]], "turn_ids": [group_turns[0]["id"]]})
    assert split.status_code == 200, split.text
    assert client.post(prefix+"/speakers", headers=headers, json={"version_id": version, "speaker_ref": split.json()["target_ref"], "name": "Sam", "role": "customer"}).status_code == 200
    assert client.post(prefix+"/speaker-groups", headers=headers, json={"version_id": version, "operation": "split", "speaker_refs": [refs[0]], "turn_ids": [str(uuid4())]}).status_code == 422
    first = before_stop["turns"][0]
    assert client.post(prefix+"/revisions", headers=headers, json={"version_id": version, "turn_id": first["id"], "english_text": "Human correction"}).status_code == 201
    revised = client.get(prefix+"/transcript", headers=headers).json()["turns"][0]
    assert revised["english_text"] == "Human correction" and revised["original_english_text"] == "Fixture repeated repeated"
    exported = client.get(prefix+"/export?format=srt", headers=headers)
    assert exported.status_code == 200 and "Human correction" in exported.text
    assert client.delete(prefix, headers=headers).status_code == 200
    assert client.get(prefix+"/transcript", headers=headers).status_code == 404
    with psycopg.connect(database_url) as connection:
        assert connection.execute("select count(*) from transcript_turns where conversation_id=%s", (conv["id"],)).fetchone()[0] == 0
        assert connection.execute("select count(*) from processing_jobs where conversation_id=%s and result is not null", (conv["id"],)).fetchone()[0] == 0


def test_translation_dependency_can_be_explicitly_bound_after_source_capture(api):
    client,headers,_,_,database_url=api
    conv=new(client,headers);run=conv['runs'][0]
    with psycopg.connect(database_url,autocommit=True) as connection:
        job=connection.execute("select schedule_job(%s,%s,'translate','test-missing-translator','{}')",(conv['id'],run['id'])).fetchone()[0]
        connection.execute("update processing_jobs set state='blocked',connection_id=null,connection_generation=null where id=%s",(job,))
        translator=connection.execute("update engine_connections set state='connected',tested_at=now() where organisation_id=%s and engine_id='azure-translator' returning id",(headers['X-Workspace-ID'],)).fetchone()[0]
        connection.execute("update workspace_engine_settings set translator_connection_id=%s where organisation_id=%s",(translator,headers['X-Workspace-ID']))
    response=client.post(f"/api/v1/conversations/{conv['id']}/retry",headers=headers,json={'stage':'translation'})
    assert response.status_code==200,response.text
    with psycopg.connect(database_url) as connection:
        row=connection.execute("select connection_id,state from processing_jobs where id=%s",(job,)).fetchone()
        assert row==(translator,'queued')
        original=run.get('translator_connection_id')
        assert str(connection.execute("select translator_connection_id from processing_runs where id=%s",(run['id'],)).fetchone()[0])==str(original)


def test_retention_cleanup_deletes_immutable_machine_records_and_finishes(api):
    from recorder.worker import MainWorker
    client,headers,_,_,database_url=api
    conv=new(client,headers);run=conv['runs'][0]
    async def publish_and_expire():
        async with await psycopg.AsyncConnection.connect(database_url,row_factory=dict_row) as connection:
            await connection.execute("select schedule_job(%s,%s,'transcribe','retention-test',%s)",(conv['id'],run['id'],Jsonb({'sample_start':0,'sample_end':16000})))
        async with await psycopg.AsyncConnection.connect(database_url,row_factory=dict_row) as connection:
            cursor=await connection.execute("select * from claim_jobs('retention-fixture','main',1,180)");job=await cursor.fetchone()
            result={'source_segments':[{'start':0.,'end':1.,'text':'Private test text','speaker':'a'}],'english_segments':None,'language':'kn','diarization_status':'completed'}
            assert (await one(connection,"select finish_job(%s,'retention-fixture',%s,%s,%s) as ok",(job['id'],job['lease_token'],job['fence'],Jsonb(result))))['ok']
            await publish_result(connection,job,result)
        async with await psycopg.AsyncConnection.connect(database_url) as connection:
            await connection.execute("update conversations set created_at=now()-interval '400 days' where id=%s",(conv['id'],))
        class StorageFixture:
            async def list_paths(self,prefix):return []
            async def delete(self,paths):pass
        worker=MainWorker(Settings(database_url=database_url));worker.storage=StorageFixture()
        await worker.db.open()
        try:await worker.cleanup_deleted()
        finally:await worker.db.close()
    asyncio.run(publish_and_expire())
    with psycopg.connect(database_url) as connection:
        assert connection.execute("select count(*) from transcript_turns where conversation_id=%s",(conv['id'],)).fetchone()[0]==0
        assert connection.execute("select count(*) from transcript_versions where conversation_id=%s",(conv['id'],)).fetchone()[0]==0
        assert connection.execute("select deleted_at is not null,upload_state from conversations where id=%s",(conv['id'],)).fetchone()==(True,'saved')


def test_daily_audio_admission_reserves_running_jobs_before_usage_arrives(api):
    client,headers,_,_,database_url=api
    conv=new(client,headers);run=conv['runs'][0]
    with psycopg.connect(database_url,autocommit=True) as connection:
        connection.execute("update workspace_engine_settings set budget_policy='{"+'"max_audio_seconds_per_day":150'+"}' where organisation_id=%s",(headers['X-Workspace-ID'],))
        for index in range(2):connection.execute("select schedule_job(%s,%s,'transcribe',%s,%s)",(conv['id'],run['id'],f'budget-reservation-{index}',Jsonb({'sample_start':0,'sample_end':1600000})))
        claimed=connection.execute("select id from claim_jobs('budget-fixture','main',2,180)").fetchall()
        assert len(claimed)==1
        assert connection.execute("select count(*) from processing_jobs where conversation_id=%s and state='blocked' and error_code='workspace_budget_exhausted'",(conv['id'],)).fetchone()[0]==1
