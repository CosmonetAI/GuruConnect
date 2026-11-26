"""Real local AI orchestration uses durable facts without blocking live text reads."""
import asyncio
import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.voice_ai.orchestrator import VoiceConversationOrchestrator
from app.voice_ai.live import pending_turns, turn_fingerprint
from recorder.config import Settings
from recorder.local_store import LOCAL_USER_ID, LOCAL_WORKSPACE_ID, LocalStore
from recorder.local_voice_analysis import LocalVoiceAnalysis, _processing_status
from recorder.provider_bootstrap import bootstrap_local_managed_providers


def make_turn(text, sequence=0):
    return {'id': str(uuid4()), 'start_seconds': sequence * 10, 'end_seconds': (sequence + 1) * 10,
            'source_text': text, 'english_text': text, 'speaker_ref': 'speaker_0'}


def setup(tmp_path, turns=None):
    store = LocalStore(tmp_path)
    cid, sid = str(uuid4()), str(uuid4())
    record = {'id': cid, 'organisation_id': LOCAL_WORKSPACE_ID, 'owner_id': LOCAL_USER_ID,
              'capture_state': 'recording', 'upload_state': 'pending', 'transcription_state': 'available', 'english_state': 'available'}
    store.put('conversations', cid, record)
    store.put('sales_sessions', sid, {'id': sid, 'organisation_id': LOCAL_WORKSPACE_ID, 'member_id': LOCAL_USER_ID, 'conversation_id': cid})
    store.put('transcripts', cid, {'version': {'id': str(uuid4()), 'kind': 'progressive', 'coverage_samples': 160000}, 'turns': turns or [make_turn('I need a ceiling fan.')]})
    settings = Settings(_env_file=None, openai_api_key='dummy-local-analysis-key', local_data_dir=tmp_path,
                        environment='development', database_url=None, supabase_url='', supabase_service_role_key=None)
    return store, settings, sid, record


class Catalogue:
    calls = []

    def status(self):
        return {'connected': True, 'organization_id': 'local-demo', 'currency': 'INR', 'catalogue_source': 'local-demo', 'demo': True}

    async def search_books_items(self, organization, query='', date=None, page=1):
        self.calls.append(query)
        if not query:
            return {'items': [{'item_id': f'demo-{name}', 'name': name, 'rate': 1000,
                               'catalogue_source': 'local-demo', 'demo': True, 'status': 'active',
                               'can_be_sold': True, 'stock_on_hand': 10}
                              for name in ('Ceiling fan', 'Water pump motor')], 'has_more': False}
        name = 'Ceiling fan' if 'fan' in query.lower() else 'Water pump motor'
        return {'items': [{'item_id': f'demo-{name}', 'name': name, 'rate': 1000,
                           'catalogue_source': 'local-demo', 'demo': True, 'status': 'active'}]}


@pytest.mark.asyncio
async def test_live_snapshot_returns_before_ai_and_deduplicates_repeated_polls_and_restart(tmp_path):
    store, settings, sid, recording = setup(tmp_path)
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = []

    class Orchestrator:
        async def run(self, *, chunk, durable_context):
            calls.append(chunk)
            entered.set()
            await finish.wait()
            return {'context': {'customer_requirements': [{'product': 'ceiling fan', 'quantity': 1}]}, 'extraction': {}, 'route': {}}

    manager = LocalVoiceAnalysis(settings, store, orchestrator_factory=Orchestrator, catalogue_factory=Catalogue)
    turns = store.get_transcript(recording['id'])['turns']
    first = await manager.snapshot(sid, recording, turns)
    assert first['analysis_status'] == 'running' and first['recommendations'] == []
    await asyncio.wait_for(entered.wait(), 2)
    for _ in range(5):
        await manager.snapshot(sid, recording, turns)
    assert len(calls) == 1
    finish.set()
    await asyncio.gather(*list(manager.tasks.values()))
    result = await manager.snapshot(sid, recording, turns)
    assert result['analysis_status'] == 'complete'
    assert result['requirements'][0]['product'] == 'ceiling fan'
    assert result['recommendations'][0]['catalogue_source'] == 'local-demo'
    copied = [{**turns[0], 'id': str(uuid4())}]
    restarted = LocalVoiceAnalysis(settings, LocalStore(tmp_path), orchestrator_factory=Orchestrator, catalogue_factory=Catalogue)
    restored = await restarted.snapshot(sid, recording, copied)
    assert restored['analysis_status'] == 'complete'
    assert len(calls) == 1
    assert restored['requirements'] == result['requirements']
    await manager.stop(); await restarted.stop()


@pytest.mark.asyncio
async def test_continuing_recording_keeps_fan_then_two_bldc_then_motor_and_withdrawal(tmp_path):
    store, settings, sid, recording = setup(tmp_path)
    observed = []
    outputs = iter([
        {'requirements': [{'product': 'ceiling fan', 'quantity': 1, 'attributes': {'motor': 'regular'}}]},
        {'requirements': [{'product': 'ceiling fan', 'quantity': 2, 'attributes': {'motor': 'BLDC'}}]},
        {'requirements': [{'product': 'water pump motor', 'quantity': 1}]},
        {'removed_products': ['water pump motor']},
        {'removed_products': ['ceiling fan']},
    ])

    class Monitor:
        async def run(self, chunk, context, route):
            observed.append((copy.deepcopy(chunk), copy.deepcopy(context)))
            return next(outputs)

    def orchestrator():
        value = VoiceConversationOrchestrator()
        value.monitor = Monitor()
        return value

    manager = LocalVoiceAnalysis(settings, store, orchestrator_factory=orchestrator, catalogue_factory=Catalogue)
    turns = []
    for sequence, words in enumerate(['I need a ceiling fan.', 'Make that two with a BLDC motor.',
                                      'Also add one water pump motor.', 'Cancel the water pump motor.',
                                      'Cancel the ceiling fan too.']):
        turns.append(make_turn(words, sequence))
        store.put('transcripts', recording['id'], {'version': {'id': str(uuid4()), 'coverage_samples': (sequence + 1) * 160000}, 'turns': turns})
        await manager.snapshot(sid, recording, turns)
        await asyncio.gather(*list(manager.tasks.values()))
        if sequence == 3:
            before_cancel_all = await manager.snapshot(sid, recording, turns)
    value = await manager.snapshot(sid, recording, turns)
    assert recording['capture_state'] == 'recording'
    assert len(before_cancel_all['requirements']) == 1
    assert before_cancel_all['requirements'][0]['product'] == 'ceiling fan'
    assert before_cancel_all['requirements'][0]['quantity'] == 2
    assert before_cancel_all['requirements'][0]['attributes']['motor'] == 'BLDC'
    assert 'motor' in observed[-2][0]['recent_dialogue'][-1]['english_text']
    assert observed[1][1]['customer_requirements'][0]['quantity'] == 1
    assert all(item['name'] == 'Ceiling fan' for item in before_cancel_all['recommendations'])
    assert value['requirements'] == [] and value['recommendations'] == []
    assert len(store.list('sales_ai_extractions', {'conversation_id': recording['id']})) == 5
    await manager.stop()


@pytest.mark.asyncio
async def test_missing_key_never_invokes_ai_and_analysis_failure_backs_off_across_new_turns(tmp_path):
    store, settings, sid, recording = setup(tmp_path)
    settings.openai_api_key = None
    calls = []

    class Broken:
        async def run(self, **kwargs):
            calls.append(kwargs)
            raise RuntimeError('synthetic provider failure')

    manager = LocalVoiceAnalysis(settings, store, orchestrator_factory=Broken, catalogue_factory=Catalogue)
    turns = store.get_transcript(recording['id'])['turns']
    result = await manager.snapshot(sid, recording, turns)
    assert result['analysis_status'] == 'unavailable' and not manager.tasks and not calls
    settings.openai_api_key = 'dummy-key'
    await manager.snapshot(sid, recording, turns)
    await asyncio.gather(*list(manager.tasks.values()))
    extended = [*turns, make_turn('A second sentence.', 1)]
    result = await manager.snapshot(sid, recording, extended)
    assert result['analysis_status'] == 'failed' and len(calls) == 1 and not manager.tasks
    assert any(notice['code'] == 'voice_analysis_failed' for notice in result['notices'])
    await manager.stop()


@pytest.mark.asyncio
async def test_changed_customer_during_analysis_does_not_publish_old_results(tmp_path):
    store, settings, sid, recording = setup(tmp_path)
    entered, finish = asyncio.Event(), asyncio.Event()

    class Orchestrator:
        async def run(self, **kwargs):
            entered.set(); await finish.wait()
            return {'context': {'customer_requirements': [{'product': 'fan'}]}, 'extraction': {}, 'route': {}}

    manager = LocalVoiceAnalysis(settings, store, orchestrator_factory=Orchestrator, catalogue_factory=Catalogue)
    await manager.snapshot(sid, recording, store.get_turns(recording['id']))
    await asyncio.wait_for(entered.wait(), 2)
    store.patch('sales_sessions', sid, {'conversation_id': str(uuid4())})
    finish.set(); await asyncio.gather(*list(manager.tasks.values()))
    assert store.get('sales_ai_context', recording['id']) is None
    assert store.list('sales_ai_extractions') == []
    await manager.stop()


def test_provider_reconciliation_repairs_stale_missing_key_but_preserves_real_provider_failure(tmp_path):
    store, settings, _sid, _recording = setup(tmp_path)
    settings.elevenlabs_api_key = 'dummy-speech-key'
    for engine in ('elevenlabs', 'openai-translator'):
        store.put('connections', engine, {'id': str(uuid4()), 'engine_id': engine, 'generation': 11,
                  'state': 'configured', 'config': {'platform_managed': True}, 'credential_scope': None,
                  'last_error_code': 'engine_unconfigured' if engine == 'elevenlabs' else 'translator_unconfigured'})
    bootstrap_local_managed_providers(settings, store)
    assert store.connection('elevenlabs')['state'] == 'connected'
    assert store.connection('openai-translator')['state'] == 'connected'
    assert store.connection('elevenlabs')['generation'] == 12
    store.patch('connections', 'elevenlabs', {'last_error_code': 'provider_permission_denied'})
    bootstrap_local_managed_providers(settings, store)
    assert store.connection('elevenlabs')['last_error_code'] == 'provider_permission_denied'


def test_live_job_processing_status_prioritizes_current_work_over_old_failures():
    record = {'capture_state': 'recording', 'transcription_state': 'available', 'english_state': 'available'}
    assert _processing_status(record, [], [])['stage'] == 'waiting'
    assert _processing_status(record, [], [{'kind': 'live_transcribe', 'state': 'running'}])['stage'] == 'transcribing'
    assert _processing_status(record, [], [{'kind': 'live_translate', 'state': 'queued'}])['stage'] == 'translating'
    assert _processing_status(record, [make_turn('Available text.')], [])['stage'] == 'up_to_date'
    assert _processing_status({**record, 'processing_error_code': 'engine_unconfigured'}, [], []) == {'stage': 'blocked', 'code': 'engine_unconfigured'}


def test_connections_endpoint_reconciles_runtime_keys_and_never_exposes_them(tmp_path):
    from fastapi.testclient import TestClient
    from recorder.local_api import create_local_app
    settings = Settings(_env_file=None, environment='development', local_data_dir=tmp_path,
                        database_url=None, supabase_url='', supabase_service_role_key=None,
                        embedded_processing_enabled=False, openai_api_key=None, elevenlabs_api_key=None)
    app = create_local_app(settings)
    store = app.state.local_store
    for engine in ('elevenlabs', 'openai-translator'):
        store.put('connections', engine, {'id': str(uuid4()), 'engine_id': engine, 'generation': 11,
                  'state': 'configured', 'config': {'platform_managed': True}, 'credential_scope': None,
                  'last_error_code': 'engine_unconfigured' if engine == 'elevenlabs' else 'translator_unconfigured'})
    settings.elevenlabs_api_key = 'synthetic-speech-key'
    settings.openai_api_key = 'synthetic-openai-key'
    with TestClient(app, base_url='http://127.0.0.1:8010', client=('127.0.0.1', 15000)) as client:
        response = client.get('/api/v1/connections')
        assert response.status_code == 200
        assert all(row['runtime_configured'] for row in response.json()['items'])
        assert all(row['state'] == 'connected' for row in response.json()['items'])
        assert all(row['configuration_source'] == 'server_environment' for row in response.json()['items'])
        assert 'synthetic-speech-key' not in response.text and 'synthetic-openai-key' not in response.text
        assert 'credential_scope' not in response.text


def test_local_monitor_receives_runtime_key_without_changing_cloud_module_settings(tmp_path):
    from app.voice_ai.agents import settings as cloud_settings
    store, configuration, _sid, _recording = setup(tmp_path)
    previous = cloud_settings.openai_api_key
    manager = LocalVoiceAnalysis(configuration, store)
    orchestrator = manager._orchestrator()
    assert orchestrator.monitor.settings_override.openai_api_key == 'dummy-local-analysis-key'
    assert cloud_settings.openai_api_key == previous


@pytest.mark.parametrize(('status', 'catalogue_status'), [('unavailable', 'syncing'), ('failed', 'stale'), ('unavailable', 'semantic_unavailable')])
async def test_returned_analysis_failure_preserves_context_pending_fingerprints_and_retry_state(tmp_path, status, catalogue_status):
    store, settings, sid, recording = setup(tmp_path)
    previous = {'customer_requirements': [{'product': 'lamp', 'quantity': 2}], 'last_intent': 'customer_requirements'}
    store.put('sales_ai_context', recording['id'], {'context': previous, 'catalogue': {'status': catalogue_status}})
    calls = []

    class Orchestrator:
        async def run(self, **kwargs):
            calls.append(kwargs)
            return {'analysis_status': status, 'analysis_error': {'code': 'synthetic_failure', 'message': 'Analysis needs a retry.'},
                    'context': {'customer_requirements': [{'product': 'untrusted output'}]}, 'route': {}}

    def forbidden_catalogue():
        pytest.fail('A failed model result must not trigger catalogue search')

    manager = LocalVoiceAnalysis(settings, store, orchestrator_factory=Orchestrator, catalogue_factory=forbidden_catalogue)
    turns = store.get_transcript(recording['id'])['turns']
    await manager._analyse(sid, recording['id'])
    rows = store.list('sales_ai_extractions', {'conversation_id': recording['id']})
    assert rows[0]['status'] == 'failed'
    assert rows[0]['data']['analysis_status'] == status
    assert store.get('sales_ai_context', recording['id'])['context'] == previous
    assert pending_turns(turns, rows) == turns
    restored = LocalVoiceAnalysis(settings, LocalStore(tmp_path), orchestrator_factory=Orchestrator, catalogue_factory=forbidden_catalogue)
    snapshot = await restored.snapshot(sid, recording, turns)
    assert snapshot['analysis_status'] == status
    assert snapshot['analysis_error']['code'] == 'synthetic_failure'
    assert snapshot['current_intent'] == 'customer_requirements'
    assert not restored.tasks and len(calls) == 1
    await manager.stop()
    await restored.stop()


async def test_successful_local_snapshot_keeps_catalogue_assessments_and_agent_only_questions(tmp_path, monkeypatch):
    store, settings, sid, recording = setup(tmp_path)
    context = {'customer_requirements': [{'product': 'fan', 'quantity': 2, 'attributes': {'motor': 'BLDC'}}],
               'last_intent': 'quote_request', 'intent_confidence': .94, 'intent_evidence': ['Need a quote'],
               'document_kind': 'quotation', 'missing_critical_fields': ['size'],
               'suggested_questions': ['Which size is needed?', 'Is remote control required?']}
    catalogue = {'status': 'ready', 'items': [{'item_id': 'verified-fan', 'name': 'BLDC fan', 'match_status': 'needs_confirmation'}],
                 'queries': ['fan'], 'revision': 'indexed-version', 'notices': [],
                 'requirements': [{'requirement_id': 'requirement-0', 'product': 'fan', 'status': 'needs_confirmation',
                                   'missing_specs': ['size'], 'candidate_count': 1}],
                 'clarification_suggestions': [{'id': 'size-question', 'requirement_id': 'requirement-0',
                     'question': 'Which size is needed?', 'missing_fields': ['size'], 'evidence': ['1200 or 1400 mm?']}]}

    class Orchestrator:
        async def run(self, **kwargs):
            return {'analysis_status': 'complete', 'context': copy.deepcopy(context), 'route': {'intent': 'quote_request'}, 'extraction': {}}

    async def search(client, actual_context):
        assert actual_context['last_intent'] == 'quote_request'
        return catalogue

    monkeypatch.setattr('recorder.local_voice_analysis.search_catalogue', search)
    manager = LocalVoiceAnalysis(settings, store, orchestrator_factory=Orchestrator, catalogue_factory=Catalogue)
    await manager._analyse(sid, recording['id'])
    value = await manager.snapshot(sid, recording, store.get_turns(recording['id']))
    assert value['analysis_status'] == 'complete'
    assert value['analysis_error'] is None
    assert value['current_intent'] == 'quote_request' and value['intent_confidence'] == .94
    assert value['document_kind'] == 'quotation'
    assert value['requirements'][0]['quantity'] == 2
    assert value['requirements'][0]['status'] == 'needs_confirmation'
    assert value['requirements'][0]['missing_specs'] == ['size']
    assert value['catalogue']['revision'] == 'indexed-version'
    assert [question['question'] for question in value['clarification_suggestions']] == ['Which size is needed?', 'Is remote control required?']
    assert all(question['audience'] == 'agent' and question['auto_execute'] is False for question in value['clarification_suggestions'])
    assert value['clarification_suggestions'][0]['evidence'] == ['1200 or 1400 mm?']
    assert value['auto_execute'] is False
    await manager.stop()


async def test_corrected_requirement_cannot_keep_incompatible_previous_cards_when_catalogue_fails(tmp_path, monkeypatch):
    store, settings, sid, recording = setup(tmp_path)
    previous = {'customer_requirements': [{'product': 'fan', 'attributes': {'size': '1200 mm'}}],
                'recent_recommendations': [{'item_id': 'old-1200', 'name': '1200 mm fan', 'match_status': 'verified'}]}
    store.put('sales_ai_context', recording['id'], {'context': previous, 'catalogue': {}})

    class Orchestrator:
        async def run(self, *, durable_context, chunk):
            # Simulate the old merger retaining nested mapping references.
            durable_context['customer_requirements'][0]['attributes']['size'] = '1400 mm'
            return {'context': durable_context, 'route': {}, 'extraction': {}}

    async def search(*args):
        return {'status': 'unavailable', 'items': [], 'queries': [], 'notices': []}

    monkeypatch.setattr('recorder.local_voice_analysis.search_catalogue', search)
    manager = LocalVoiceAnalysis(settings, store, orchestrator_factory=Orchestrator, catalogue_factory=Catalogue)
    await manager._analyse(sid, recording['id'])
    value = await manager.snapshot(sid, recording, store.get_turns(recording['id']))
    assert value['analysis_status'] == 'complete'
    assert value['requirements'][0]['attributes']['size'] == '1400 mm'
    assert value['recommendations'] == []
    assert value['catalogue_status'] == 'unavailable'
    await manager.stop()


async def test_local_syncing_catalogue_refreshes_without_repeating_completed_model_work(tmp_path, monkeypatch):
    store, settings, sid, recording = setup(tmp_path)
    turns = store.get_turns(recording['id'])
    store.put('sales_ai_extractions', 'done', {'conversation_id': recording['id'], 'status': 'complete',
        'updated_at': datetime.now(UTC).isoformat(), 'data': {'_live': {'turn_fingerprints': [turn_fingerprint(turns[0])]}}})
    store.put('sales_ai_context', recording['id'], {'context': {'customer_requirements': [{'product': 'fan'}], 'last_intent': 'pricing'},
        'catalogue': {'status': 'syncing', 'items': [], 'queries': ['fan']},
        'catalogue_updated_at': (datetime.now(UTC) - timedelta(seconds=3)).isoformat()})

    class Forbidden:
        async def run(self, **kwargs):
            pytest.fail('Catalogue refresh must not repeat paid semantic extraction')

    async def search(*args):
        return {'status': 'ready', 'items': [{'item_id': 'fan-1', 'name': 'Fan'}], 'queries': ['fan'], 'notices': []}

    monkeypatch.setattr('recorder.local_voice_analysis.search_catalogue', search)
    manager = LocalVoiceAnalysis(settings, store, orchestrator_factory=Forbidden, catalogue_factory=Catalogue)
    first = await manager.snapshot(sid, recording, turns)
    assert first['analysis_status'] == 'complete' and first['catalogue_status'] == 'searching'
    await asyncio.gather(*list(manager.tasks.values()))
    restored = await manager.snapshot(sid, recording, turns)
    assert restored['analysis_status'] == 'complete' and restored['catalogue_status'] == 'ready'
    assert restored['recommendations'][0]['item_id'] == 'fan-1'
    assert len(store.list('sales_ai_extractions', {'conversation_id': recording['id']})) == 1
    await manager.stop()


async def test_catalogue_exception_does_not_discard_valid_model_facts_or_repeat_model_call(tmp_path, monkeypatch):
    store, settings, sid, recording = setup(tmp_path)
    calls = []

    class Orchestrator:
        async def run(self, **kwargs):
            calls.append(kwargs)
            return {'analysis_status': 'complete', 'context': {'customer_requirements': [{'product': 'fan', 'quantity': 2}]},
                    'extraction': {}, 'route': {}}

    async def broken_search(*args):
        raise RuntimeError('private-provider-error')

    monkeypatch.setattr('recorder.local_voice_analysis.search_catalogue', broken_search)
    manager = LocalVoiceAnalysis(settings, store, orchestrator_factory=Orchestrator, catalogue_factory=Catalogue)
    await manager._analyse(sid, recording['id'])
    value = await manager.snapshot(sid, recording, store.get_turns(recording['id']))
    assert value['analysis_status'] == 'complete'
    assert value['requirements'][0]['quantity'] == 2
    assert value['catalogue_status'] == 'unavailable'
    assert 'private-provider-error' not in str(value)
    assert not manager.tasks and len(calls) == 1
    assert pending_turns(store.get_turns(recording['id']), value['insights']) == []
    await manager.stop()
