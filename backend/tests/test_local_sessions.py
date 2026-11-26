"""Sessions use the real loopback API/storage, including restart and customer boundaries."""
from contextlib import contextmanager
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from recorder.config import Settings
from recorder.local_api import create_local_app
from recorder.local_store import LOCAL_WORKSPACE_ID


@contextmanager
def open_client(settings):
    app = create_local_app(settings)
    with TestClient(app, base_url='http://127.0.0.1:8010', client=('127.0.0.1', 15000),
                    headers={'Origin': 'http://127.0.0.1:5176', 'X-Workspace-ID': LOCAL_WORKSPACE_ID}) as client:
        yield client, app.state.local_store


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, environment='development', local_data_dir=tmp_path,
                    database_url=None, supabase_url='', supabase_service_role_key=None,
                    cors_origins=['http://127.0.0.1:5176'], embedded_processing_enabled=False)


def customer(**values):
    return {'name': 'Synthetic local customer', 'phone': '09674738429', 'email': 'customer@example.test', **values}


def create_session(client, **values):
    result = client.post('/api/voice-sessions', json=customer(**values))
    assert result.status_code == 200, result.text
    return result.json()


def recording(client, **values):
    response = client.post('/api/v1/conversations', json={
        'customer_name': 'Synthetic local customer', 'phone': '09674738429',
        'email': 'customer@example.test', 'consent_confirmed': True, 'language_hint': 'auto',
        'audio_only': False, **values,
    }, headers={'Idempotency-Key': str(uuid4())})
    assert response.status_code == 201, response.text
    return response.json()['id']


def test_session_open_and_equivalent_phone_reuse_survive_restart(settings):
    with open_client(settings) as (client, _store):
        assert client.get('/api/chats').json() == []
        opened = create_session(client)
        identity = opened['session']['id']
        assert opened['session']['phone'] == '9674738429'
        assert create_session(client, phone='+91 96747 38429')['session']['id'] == identity
        assert create_session(client, phone='9674738429')['session']['id'] == identity
        assert len(client.get('/api/chats').json()) == 1
    with open_client(settings) as (client, _store):
        assert client.get(f'/api/voice-sessions/{identity}/history').json()['session']['name'] == customer()['name']
        contacts = client.get('/api/session-contacts', params={'search': '96747'}).json()['contacts']
        assert len(contacts) == 1
        assert contacts[0]['session_id'] == identity
        assert client.get('/api/session-contacts', params={'search': 'different person'}).json()['contacts'] == []


def test_session_notes_and_cart_persist_without_cross_customer_leakage(settings):
    with open_client(settings) as (client, _store):
        first = create_session(client)['session']['id']
        second = create_session(client, name='Other synthetic customer', phone='9876543210', email='other@example.test')['session']['id']
        note = client.post(f'/api/voice-sessions/{first}/notes', json={'content': 'Needs two fans.'})
        assert note.status_code == 200
        assert client.get(f'/api/voice-sessions/{second}/notes').json()['notes'] == []
        context = {'product_requirements': [{'product': 'fan', 'quantity': 2}],
                   'cart': [{'item_id': 'synthetic-item', 'quantity': 2, 'rate': 4500}]}
        saved = client.put(f'/api/voice-sessions/{first}/context', json=context)
        assert saved.status_code == 200 and saved.json() == context
        assert client.get(f'/api/voice-sessions/{second}/context').json()['cart'] == []
    with open_client(settings) as (client, _store):
        assert client.get(f'/api/voice-sessions/{first}/context').json() == context
        assert client.get(f'/api/voice-sessions/{first}/notes').json()['notes'][0]['content'] == 'Needs two fans.'
        assert client.get(f'/api/voice-sessions/{second}/notes').json()['notes'] == []


def test_recording_link_uses_same_native_id_and_real_transcript(settings):
    with open_client(settings) as (client, store):
        session = create_session(client)['session']['id']
        identity = recording(client)
        response = client.post(f'/api/voice-sessions/{session}/conversation', json={'conversation_id': identity})
        assert response.status_code == 200
        assert store.get('conversations', identity)['live_session'] is True
        assert store.get('conversations', identity)['live_session_id'] == session
        version = {'id': str(uuid4()), 'kind': 'progressive'}
        turn = {'id': str(uuid4()), 'start_seconds': 0, 'end_seconds': 5, 'source_text': 'Synthetic source.',
                'english_text': 'Two fans, please.', 'speaker_ref': 'speaker_0'}
        store.put('transcripts', identity, {'version': version, 'turns': [turn], 'speakers': []})
        store.patch('conversations', identity, {'active_version_id': version['id'], 'lease_token': 'private-test-lease'})
        live = client.get(f'/api/voice-sessions/{session}/live')
        assert live.status_code == 200
        value = live.json()
        assert value['conversation_id'] == identity
        assert value['revision'] == version['id']
        assert value['transcript_preview'][0]['english_text'] == turn['english_text']
        assert value['transcript_preview'][0]['translation_state'] == 'complete'
        assert value['recording']['active_version_id'] == version['id']
        assert 'private-test-lease' not in live.text
        assert value['recommendations'] == [] and value['catalogue_status'] == 'waiting'
        assert any(item['code'] == 'local_demo_catalogue' for item in value['notices'])
        assert value['demo'] is True and value['catalogue_source'] == 'local-demo'
        detached = client.post(f'/api/voice-sessions/{session}/conversation', json={'conversation_id': None})
        assert detached.status_code == 200
        assert client.get(f'/api/voice-sessions/{session}/live').json()['transcript_preview'] == []


def test_recording_link_rejects_different_customer_even_with_same_email(settings):
    with open_client(settings) as (client, _store):
        session = create_session(client)['session']['id']
        other = recording(client, phone='9876543210')
        response = client.post(f'/api/voice-sessions/{session}/conversation', json={'conversation_id': other})
        assert response.status_code == 404
        assert client.get(f'/api/voice-sessions/{session}/history').json()['conversation_id'] is None


def test_live_waits_for_translation_and_never_invents_missing_provider_output(settings):
    with open_client(settings) as (client, store):
        session = create_session(client)['session']['id']
        identity = recording(client)
        client.post(f'/api/voice-sessions/{session}/conversation', json={'conversation_id': identity})
        store.patch('conversations', identity, {'processing_error_code': 'engine_unconfigured', 'transcription_state': 'blocked'})
        live = client.get(f'/api/voice-sessions/{session}/live').json()
        assert live['transcript_preview'] == [] and live['recommendations'] == []
        assert live['recording']['transcription_state'] == 'blocked'
        assert live['analysis_status'] == 'waiting_for_translation'
        assert live['processing_mode'] == 'progressive'
        assert live['provider_readiness']['speech_configured'] is False
        assert live['provider_readiness']['translation_configured'] is False
        assert any(item['code'] == 'engine_unconfigured' for item in live['notices'])
        assert client.get('/api/zoho/status').json()['connected'] is False
        catalogue = client.get('/api/products', params={'search': 'fan'}).json()
        assert catalogue['demo'] is True
        assert catalogue['items']
        assert client.get('/api/document-contacts').json()['contacts'] == []


def test_invalid_fields_unknown_session_and_wrong_workspace_are_rejected(settings):
    with open_client(settings) as (client, _store):
        for values in [{'name': ' '}, {'phone': 'letters-only'}, {'email': 'invalid@'}]:
            assert client.post('/api/voice-sessions', json=customer(**values)).status_code == 422
        unknown = str(uuid4())
        for suffix in ('history', 'live', 'notes', 'context'):
            assert client.get(f'/api/voice-sessions/{unknown}/{suffix}').status_code == 404
        assert client.get('/api/chats', headers={'X-Workspace-ID': str(uuid4())}).status_code == 403
        identity = create_session(client)['session']['id']
        assert client.post(f'/api/voice-sessions/{identity}/notes', json={'content': '   '}).status_code == 422
