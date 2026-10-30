"""Live session behavior with synthetic speech, catalogue and database fixtures."""
import asyncio
import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from app import main as sales
from app.db import sales_workspace
from app.voice_ai.context import VoiceConversationContext
from app.voice_ai.live import catalogue_queries, intent_keywords, pending_turns, search_catalogue, turn_fingerprint
from app.voice_ai.orchestrator import VoiceConversationOrchestrator
from app.zoho import ZohoClient


def turn(identity='turn-a', *, english='I need a fan and a motor.', version='active'):
    return {'id': identity, 'version_id': version, 'start_seconds': 0.0, 'end_seconds': 10.0,
            'source_text': 'original speech', 'english_text': english, 'speaker_ref': 'speaker-1'}


def test_progressive_version_copies_and_repeated_polls_do_not_repeat_extraction():
    original = turn()
    copied = {**original, 'id': 'copy-with-new-id', 'version_id': 'next-version'}
    assert turn_fingerprint(original) == turn_fingerprint(copied)
    extracted = [{'status': 'complete', 'fingerprint': 'batch',
                  'data': {'_live': {'turn_fingerprints': [turn_fingerprint(original)]}}}]
    assert pending_turns([copied], extracted) == []
    assert pending_turns([turn(english=None)], []) == []
    changed = {**copied, 'english_text': 'Actually I need two fans.'}
    assert pending_turns([changed], extracted) == [changed]


@pytest.mark.asyncio
async def test_continuous_context_updates_fan_then_adds_motor_and_preserves_corrections(monkeypatch):
    outputs = iter([
        {'focal_product': 'ceiling fan', 'requirements': [{'product': 'ceiling fan', 'quantity': 1, 'attributes': {'size': '1200 mm', 'motor': 'regular'}}]},
        {'requirements': [{'product': 'ceiling fan', 'quantity': 2, 'attributes': {'motor': 'BLDC'}}]},
        {'requirements': [{'product': 'water pump motor', 'quantity': 1, 'attributes': {'power': '1 HP'}}]},
        {'requirements': [{'product': 'ceiling fan', 'attributes': {'size': '1400 mm'}}], 'removed_products': ['water pump motor']},
        # Final refined transcript may repeat an earlier positive mention. It
        # cannot reinstate a product without an explicit reinstatement.
        {'requirements': [{'product': 'water pump motor', 'quantity': 1}]},
    ])
    observed_contexts = []

    async def extract(self, chunk, context, route):
        observed_contexts.append(context)
        return next(outputs)

    monkeypatch.setattr('app.voice_ai.orchestrator.ConversationMonitorAgent.run', extract)
    context = {}
    for index, text in enumerate(['Need a ceiling fan.', 'Make that two, BLDC.', 'Also a water pump motor.',
                                  '1400 mm instead, and cancel the pump motor.', 'Final conversation refinement.']):
        result = await VoiceConversationOrchestrator().run(chunk={
            'id': str(index), 'conversation_id': 'same-recording', 'fingerprint': str(index), 'english_text': text,
        }, durable_context=context)
        context = result['context']
    fan, motor = context['customer_requirements']
    assert fan['quantity'] == 2
    assert fan['attributes'] == {'size': '1400 mm', 'motor': 'BLDC'}
    assert motor['excluded'] is True
    assert catalogue_queries(context) == ['ceiling fan']
    assert 'BLDC' in intent_keywords(context)
    assert observed_contexts[1]['customer_requirements'][0]['product'] == 'ceiling fan'
    assert len(context['source_refs']) == 5


def test_feature_withdrawal_and_attribute_removal_are_durable():
    context = VoiceConversationContext()
    context.merge_extraction({'requirements': [{'product': 'fan', 'attributes': {'light': 'LED', 'motor': 'BLDC'}}],
                              'features': ['LED light', 'quiet']}, {'fingerprint': 'one'})
    context.merge_extraction({'requirements': [{'product': 'fan', 'removed_attributes': ['light']}],
                              'removed_features': ['LED light']}, {'fingerprint': 'two'})
    assert context.customer_requirements[0]['attributes'] == {'motor': 'BLDC'}
    assert context.preferred_features == ['quiet']


@pytest.mark.asyncio
async def test_real_model_customer_requirements_field_is_not_silently_discarded():
    # Shape observed from the real synthetic Hindi provider smoke. The model
    # mirrored the durable context field despite the prompt's requirements key.
    context = VoiceConversationContext()
    context.merge_extraction({'customer_requirements': [
        {'product': 'ceiling fan', 'quantity': 2, 'attributes': {'control_type': 'remote controlled'}},
        {'product': 'motor', 'quantity': 1},
    ]}, {'fingerprint': 'real-synthetic-output'})
    assert [row['quantity'] for row in context.customer_requirements] == [2, 1]
    from recorder.demo_catalogue import LocalDemoCatalogueClient
    matches = await search_catalogue(LocalDemoCatalogueClient(), context.as_dict())
    assert {'fan', 'motor'} == {item['category'] for item in matches['items']}


class Catalogue:
    def __init__(self, connected=True, fail=False):
        self.connected, self.fail, self.queries = connected, fail, []

    def status(self):
        return {'connected': self.connected, 'organization_id': 'books-org', 'currency': 'INR'}

    async def search_books_items(self, org, query, date, page):
        assert org == 'books-org' and page == 1
        self.queries.append(query)
        if self.fail:
            raise RuntimeError('private-oauth-token')
        if query == 'motor':
            return {'items': [{'item_id': 'motor-1', 'name': '1 HP motor', 'rate': '4500', 'stock_on_hand': 0}]}
        return {'items': [
            {'item_id': 'fan-1', 'name': 'Quiet BLDC fan', 'rate': '3999', 'stock_on_hand': 8, 'tax_percentage': 18},
            {'item_id': 'fan-1', 'name': 'Quiet BLDC fan', 'rate': '3999', 'stock_on_hand': 8},
            {'item_id': 'bad', 'name': 'Retired fan', 'status': 'inactive'},
        ]}


@pytest.fixture
def indexed_candidates(monkeypatch):
    async def indexed(client, context, embedding_client=None):
        rows = []
        for query in catalogue_queries(context):
            rows.extend({**row, 'currency_code': 'INR'} for row in
                        (await client.search_books_items('books-org', query, None, 1))['items'])
        return {'status': 'ready', 'items': rows, 'retrieval_mode': 'hybrid', 'notices': []}
    monkeypatch.setattr('app.voice_ai.live.retrieve_catalogue', indexed)


@pytest.mark.asyncio
async def test_indexed_candidates_verify_every_requested_product_without_duplicate_cards(indexed_candidates):
    catalogue = Catalogue()
    result = await search_catalogue(catalogue, {'customer_requirements': [{'product': 'fan'}, {'product': 'motor'}],
                                               'preferred_features': ['BLDC', 'quiet']})
    assert catalogue.queries == ['fan', 'motor']
    assert [item['item_id'] for item in result['items']] == ['fan-1', 'motor-1']
    assert result['items'][0]['rate'] == 3999
    assert result['items'][0]['tax_percentage'] == 18
    assert result['items'][1]['stock_on_hand'] == 0
    assert all(item['catalogue_source'] == 'zoho' and item['currency_code'] == 'INR' for item in result['items'])


@pytest.mark.asyncio
@pytest.mark.parametrize('client', [Catalogue(connected=False), Catalogue(fail=True)])
async def test_catalogue_failure_returns_nonblocking_notice_and_no_invented_products(client):
    result = await search_catalogue(client, {'customer_requirements': [{'product': 'fan'}]})
    assert result['status'] == 'unavailable'
    assert result['items'] == []
    assert result['notices']
    assert 'private-' not in str(result)


@pytest.mark.asyncio
async def test_catalogue_connector_uses_documented_books_search_and_active_filter():
    class Client(ZohoClient):
        async def request(self, method, path, **kwargs):
            return {'method': method, 'path': path, **kwargs}

    result = await Client().search_books_items('selected-org', 'f' * 120, None, 2)
    assert result['method'] == 'GET' and result['path'] == '/books/v3/items'
    assert result['params'] == {'organization_id': 'selected-org', 'filter_by': 'Status.Active',
                                'per_page': 200, 'page': 2, 'search_text': 'f' * 100}


@pytest.fixture
def scope():
    context = SimpleNamespace(id='00000000-0000-0000-0000-000000000001',
                              member_id='00000000-0000-0000-0000-000000000002', role='salesperson',
                              user=SimpleNamespace(id='00000000-0000-0000-0000-000000000002'), admin=False)
    token = sales_workspace.set(context)
    yield context
    sales_workspace.reset(token)


@pytest.mark.asyncio
async def test_session_warmup_uses_captured_verified_scope_and_restores_context(monkeypatch, scope):
    from app.zoho import get_member_id, get_organisation_id, set_member_id, set_organisation_id
    observed = []
    async def retrieve(client, context, embedding_client=None):
        observed.append((get_organisation_id(), get_member_id(), sales_workspace.get(), context))
        return {'status': 'syncing', 'items': []}
    monkeypatch.setattr('app.catalogue_index.retrieve_catalogue', retrieve)
    monkeypatch.setattr('app.catalogue_embeddings.get_embedding_client', lambda: None)
    before_org, before_member = get_organisation_id(), get_member_id()
    try:
        set_organisation_id('previous-org')
        set_member_id('previous-member')
        await sales._warm_voice_catalogue(scope)
        assert observed == [(scope.id, scope.member_id, scope, {})]
        assert get_organisation_id() == 'previous-org' and get_member_id() == 'previous-member'
        assert sales_workspace.get() is scope
    finally:
        set_organisation_id(before_org)
        set_member_id(before_member)


def snapshot(turns=None, extractions=None):
    return {'session_id': '00000000-0000-0000-0000-000000000003',
            'conversation_id': '00000000-0000-0000-0000-000000000004', 'revision': 'current-version',
            'recording': {'capture_state': 'recording'}, 'transcript_preview': turns or [],
            'insights': extractions or [], 'context': {}}


@pytest.mark.asyncio
async def test_live_poll_returns_text_without_waiting_for_ai_and_deduplicates_inflight_work(monkeypatch, scope):
    current = snapshot([turn()])
    monkeypatch.setattr(sales, '_voice_live_snapshot', lambda session_id: current)
    monkeypatch.setattr(sales.settings, 'openai_api_key', 'synthetic-key')
    gate = asyncio.Event()
    started = []

    async def work(session_id):
        started.append(session_id)
        await gate.wait()

    monkeypatch.setattr(sales, '_analyse_voice_live', work)
    session = UUID(current['session_id'])
    try:
        first = await asyncio.wait_for(sales.voice_session_live(session, None), timeout=.5)
        second = await asyncio.wait_for(sales.voice_session_live(session, None), timeout=.5)
        assert first['transcript_preview'][0]['english_text'] == 'I need a fan and a motor.'
        assert first['analysis_status'] == second['analysis_status'] == 'running'
        assert len(started) == 1
    finally:
        gate.set()
        await asyncio.gather(*list(sales._voice_live_tasks.values()))
        sales._voice_live_tasks.clear()


@pytest.mark.asyncio
async def test_source_only_transcript_does_not_start_ai_until_translation_arrives(monkeypatch, scope):
    monkeypatch.setattr(sales, '_voice_live_snapshot', lambda session_id: snapshot([turn(english=None)]))

    async def forbidden(session_id):
        pytest.fail('Source-only speech must wait for translated text')

    monkeypatch.setattr(sales, '_analyse_voice_live', forbidden)
    response = await sales.voice_session_live(UUID(snapshot()['session_id']), None)
    assert response['analysis_status'] == 'waiting_for_translation'
    assert response['recommendations'] == []
    assert not sales._voice_live_tasks


@pytest.mark.asyncio
async def test_hosted_shutdown_cancels_live_analysis_and_waits_for_transaction_cleanup():
    entered, released = asyncio.Event(), asyncio.Event()

    async def active_analysis():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    task = asyncio.create_task(active_analysis())
    sales._voice_live_tasks[('org', 'member', 'conversation')] = task
    await entered.wait()
    await sales.stop_voice_live_tasks()
    assert released.is_set() and task.cancelled()
    assert not sales._voice_live_tasks


class Row:
    def __init__(self, value):
        self._mapping = value

    def __getattr__(self, name):
        return self._mapping[name]

    def __getitem__(self, name):
        return list(self._mapping.values())[name] if isinstance(name, int) else self._mapping[name]


class Result:
    def __init__(self, values=()):
        self.values = [Row(value) for value in values]

    def first(self):
        return next(iter(self.values), None)

    def all(self):
        return self.values


@pytest.mark.parametrize('coverage', [0, 160000])
def test_live_snapshot_scopes_customer_and_only_reads_active_transcript_version(monkeypatch, scope, coverage):
    calls = []
    current = snapshot()
    timestamp = datetime.now(UTC)

    class Database:
        def __enter__(self): return self
        def __exit__(self, *args): pass

        def execute(self, statement, params):
            query = ' '.join(str(statement).split())
            calls.append((query, params))
            if 'select s.*,c.name' in query:
                assert params['org'] == scope.id and params['member'] == scope.member_id
                return Result([{'id': current['session_id'], 'conversation_id': current['conversation_id'], 'customer_id': 'customer'}])
            if 'from conversations where id=' in query:
                assert 'owner_id=cast(:member as uuid)' in query and 'customer_id=cast(:customer as uuid)' in query
                assert 'select coverage_samples from transcript_versions' in query
                return Result([{'id': current['conversation_id'], 'active_version_id': 'active', 'analysis_coverage_samples': coverage, 'updated_at': timestamp}])
            if 'from transcript_turns t' in query:
                assert 't.version_id=cast(:version as uuid)' in query
                versions = [turn('old', version='old'), turn('current', version='active')]
                return Result([row for row in versions if row['version_id'] == params['version']])
            if 'from conversation_extractions' in query:
                return Result()
            if 'select context from voice_ai_contexts' in query:
                return Result([{'context': {}}])
            raise AssertionError(query)

    monkeypatch.setattr(sales, 'require_db', Database)
    result = sales._voice_live_snapshot(UUID(current['session_id']))
    assert [row['id'] for row in result['transcript_preview']] == ['current']
    assert result['transcript_preview'][0]['translation_state'] == 'complete'
    assert result['transcript_preview'][0]['analysis_ready'] is (coverage > 0)
    assert pending_turns(result['transcript_preview'], []) == (result['transcript_preview'] if coverage else [])
    assert result['revision'] == 'active'


@pytest.mark.asyncio
async def test_live_analysis_persists_context_and_fingerprints_then_restart_poll_reuses_them(monkeypatch, scope, indexed_candidates):
    current = snapshot([turn()])
    model_calls = []

    async def extract(self, chunk, context, route):
        model_calls.append(chunk)
        return {'requirements': [{'product': 'fan', 'quantity': 2, 'attributes': {'motor': 'BLDC'}},
                                 {'product': 'motor', 'quantity': 1}]}

    class Database:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def commit(self): pass

        def execute(self, statement, params):
            query = ' '.join(str(statement).split())
            if 'pg_try_advisory_xact_lock' in query:
                assert params['key'] == f"voice-live:{current['conversation_id']}"
                return Result([{'acquired': True}])
            if query.startswith('insert into conversation_extractions'):
                current['insights'] = [{'id': 'analysis', 'fingerprint': params['fingerprint'], 'status': 'running',
                                        'data': {}, 'updated_at': datetime.now(UTC)}]
            elif query.startswith('insert into voice_ai_contexts'):
                current['context'] = json.loads(params['context'])
            elif query.startswith('update conversation_extractions'):
                current['insights'][0].update(status='complete', data=json.loads(params['data']))
            else:
                raise AssertionError(query)
            return Result()

    monkeypatch.setattr(sales, '_voice_live_snapshot', lambda session_id: copy.deepcopy(current))
    monkeypatch.setattr(sales, '_session', lambda db, session_id: {'conversation_id': current['conversation_id']})
    monkeypatch.setattr(sales, 'require_db', Database)
    monkeypatch.setattr(sales, 'zoho', Catalogue())
    monkeypatch.setattr('app.voice_ai.orchestrator.ConversationMonitorAgent.run', extract)
    monkeypatch.setattr(sales.settings, 'openai_api_key', 'synthetic-key')
    await sales._analyse_voice_live(UUID(current['session_id']))
    assert len(model_calls) == 1
    assert model_calls[0]['recent_dialogue'][0]['english_text'] == current['transcript_preview'][0]['english_text']
    assert current['insights'][0]['status'] == 'complete'
    assert [item['item_id'] for item in current['context']['recent_recommendations']] == ['fan-1', 'motor-1']
    # Simulate a process with no in-memory task registry and copied progressive
    # rows. Durable fingerprints suppress another paid extraction after restart.
    current['transcript_preview'][0]['id'] = 'new-copy-id'
    response = await sales.voice_session_live(UUID(current['session_id']), None)
    assert response['analysis_status'] == 'complete'
    assert response['keywords'] == ['fan', 'BLDC', 'Quantity: 2', 'motor', 'Quantity: 1']
    assert not sales._voice_live_tasks
    assert len(model_calls) == 1


@pytest.mark.asyncio
async def test_recent_failed_analysis_backs_off_without_hiding_transcript(monkeypatch, scope):
    from app.voice_ai.live import batch_fingerprint
    speech = turn()
    current = snapshot([speech], [{'id': 'failure', 'fingerprint': batch_fingerprint([speech]),
                                  'status': 'failed', 'data': {}, 'updated_at': datetime.now(UTC)}])
    monkeypatch.setattr(sales, '_voice_live_snapshot', lambda session_id: current)
    monkeypatch.setattr(sales.settings, 'openai_api_key', 'synthetic-key')

    async def forbidden(session_id):
        pytest.fail('A failed paid call must back off before retrying')

    monkeypatch.setattr(sales, '_analyse_voice_live', forbidden)
    response = await sales.voice_session_live(UUID(current['session_id']), None)
    assert response['analysis_status'] == 'failed'
    assert response['transcript_preview'] == [speech]
    assert response['notices'][0]['code'] == 'voice_analysis_failed'
    assert not sales._voice_live_tasks


def test_webhook_delivery_batches_unprocessed_dialogue_in_order(monkeypatch):
    earlier = {**turn('earlier', english='I need one fan.'), 'start_seconds': 0, 'end_seconds': 9}
    correction = {**turn('correction', english='Make that two BLDC fans.'), 'start_seconds': 10, 'end_seconds': 19}
    observed = []

    class Database:
        def execute(self, statement, params):
            query = ' '.join(str(statement).split())
            if 'union all' in query:
                return Result([earlier])  # An old event arrives after both turns exist.
            if query.startswith('select active_version_id'):
                return Result([{'active_version_id': 'active', 'analysis_coverage_samples': 320000}])
            if 'from transcript_turns t' in query:
                assert 'order by t.start_seconds,t.ordinal' in query
                return Result([earlier, correction])
            if 'select context from voice_ai_contexts' in query:
                return Result([{'context': {}}])
            return Result()

        def commit(self): pass

    async def analyse(self, *, chunk, durable_context):
        observed.append(chunk)
        return {'extraction': {'requirements': [{'product': 'fan', 'quantity': 2}]}, 'context': {}, 'route': {}}

    monkeypatch.setattr(sales.settings, 'openai_api_key', 'synthetic-test-key')
    monkeypatch.setattr(sales.VoiceConversationOrchestrator, 'run', analyse)
    result = sales._extract_voice_chunk(Database(), {'conversation_id': 'conversation', 'record_id': 'earlier'})
    assert result['status'] == 'complete'
    assert observed[0]['english_text'] == 'I need one fan.\nMake that two BLDC fans.'
    assert len(observed[0]['turn_fingerprints']) == 2
    assert observed[0]['recent_dialogue'][-1]['english_text'] == correction['english_text']


def test_prompt_context_retains_requirements_without_truncating_json_or_catalogue_metadata():
    from app.voice_ai.agents import prompt_context
    context = {'customer_requirements': [{'product': 'fan', 'quantity': 2, 'source_refs': [{'id': 'x'}] * 100}],
               'source_refs': [{'raw': 'x' * 20000}], 'budget_max': 5000,
               'recent_recommendations': [{'item_id': 'fan-1', 'name': 'Fan', 'sku': 'FAN', 'image_url': 'https://example.invalid/' + 'x' * 20000}]}
    compact = prompt_context(context)
    assert compact['customer_requirements'] == [{'product': 'fan', 'quantity': 2}]
    assert compact['budget_max'] == 5000
    assert compact['recent_recommendations'] == [{'item_id': 'fan-1', 'name': 'Fan', 'sku': 'FAN'}]
    assert 'source_refs' not in compact


@pytest.mark.parametrize('status', ['unavailable', 'failed'])
async def test_hosted_worker_does_not_complete_returned_model_failure(monkeypatch, scope, status):
    speech = turn()
    current = snapshot([speech])
    previous = {'customer_requirements': [{'product': 'lamp'}], 'last_intent': 'pricing'}
    current['context'] = previous
    writes = []

    class Database:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def commit(self): pass
        def execute(self, statement, params):
            query = ' '.join(str(statement).split())
            writes.append((query, params))
            if 'pg_try_advisory_xact_lock' in query:
                return Result([{'acquired': True}])
            if query.startswith('insert into conversation_extractions'):
                current['insights'] = [{'fingerprint': params['fingerprint'], 'status': 'running', 'updated_at': datetime.now(UTC), 'data': {}}]
            elif query.startswith('update conversation_extractions'):
                assert "status='failed'" in query
                current['insights'][0].update(status='failed', data=json.loads(params['data']), updated_at=datetime.now(UTC))
            else:
                pytest.fail(f'Failed analysis must not write durable context: {query}')
            return Result()

    async def analyze(self, **kwargs):
        return {'analysis_status': status, 'analysis_error': {'code': 'synthetic_failure', 'message': 'Analysis needs a retry.'},
                'context': {'customer_requirements': [{'product': 'untrusted output'}]}, 'route': {}}

    async def forbidden(*args):
        pytest.fail('Failed model output must not start catalogue search')

    monkeypatch.setattr(sales, '_voice_live_snapshot', lambda session_id: copy.deepcopy(current))
    monkeypatch.setattr(sales, '_session', lambda *args: {'conversation_id': current['conversation_id']})
    monkeypatch.setattr(sales, 'require_db', Database)
    monkeypatch.setattr(sales.VoiceConversationOrchestrator, 'run', analyze)
    monkeypatch.setattr(sales, 'search_catalogue', forbidden)
    monkeypatch.setattr(sales.settings, 'openai_api_key', 'synthetic-key')
    await sales._analyse_voice_live(UUID(current['session_id']))
    assert current['context'] == previous
    assert current['insights'][0]['data']['analysis_status'] == status
    assert pending_turns([speech], current['insights']) == [speech]
    result = await sales.voice_session_live(UUID(current['session_id']), None)
    assert result['analysis_status'] == status
    assert result['analysis_error']['code'] == 'synthetic_failure'
    assert result['current_intent'] == 'pricing'
    assert not sales._voice_live_tasks


async def test_hosted_snapshot_preserves_catalogue_assessments_and_merges_advisory_questions(monkeypatch, scope):
    speech = turn()
    catalogue = {'status': 'ready', 'items': [], 'queries': ['fan'], 'notices': [], 'revision': 'catalogue-version',
                 'requirements': [{'requirement_id': 'requirement-0', 'product': 'fan', 'status': 'no_match',
                                   'missing_specs': ['size'], 'candidate_count': 0}],
                 'clarification_suggestions': [{'id': 'size-question', 'question': 'Which size is needed?',
                     'requirement_id': 'requirement-0', 'missing_fields': ['size'], 'evidence': ['A large fan.']}]}
    current = snapshot([speech], [{'status': 'complete', 'updated_at': datetime.now(UTC), 'data': {
        '_catalogue': catalogue, '_live': {'turn_fingerprints': [turn_fingerprint(speech)]}}}])
    current['context'] = {'customer_requirements': [{'product': 'fan', 'quantity': 2}],
        'last_intent': 'quote_request', 'intent_confidence': .93, 'intent_evidence': ['Need a written offer.'],
        'document_kind': 'written_offer', 'missing_critical_fields': ['size'],
        'suggested_questions': ['Which size is needed?', 'Is remote control required?']}
    monkeypatch.setattr(sales, '_voice_live_snapshot', lambda session_id: current)
    result = await sales.voice_session_live(UUID(current['session_id']), None)
    assert result['analysis_status'] == 'complete'
    assert result['analysis_error'] is None
    assert result['current_intent'] == 'quote_request' and result['intent_confidence'] == .93
    assert result['document_kind'] == 'written_offer'
    assert result['requirements'][0]['quantity'] == 2
    assert result['requirements'][0]['status'] == 'no_match'
    assert result['requirements'][0]['missing_specs'] == ['size']
    assert result['catalogue']['revision'] == 'catalogue-version'
    assert [question['question'] for question in result['clarification_suggestions']] == ['Which size is needed?', 'Is remote control required?']
    assert all(question['audience'] == 'agent' and question['auto_execute'] is False for question in result['clarification_suggestions'])
    assert not sales._voice_live_tasks


async def test_hosted_missing_key_is_visibly_unavailable_without_consuming_transcript(monkeypatch, scope):
    speech = turn()
    current = snapshot([speech])
    monkeypatch.setattr(sales, '_voice_live_snapshot', lambda session_id: current)
    monkeypatch.setattr(sales.settings, 'openai_api_key', '')
    result = await sales.voice_session_live(UUID(current['session_id']), None)
    assert result['analysis_status'] == 'unavailable'
    assert result['analysis_error']['code'] == 'voice_analysis_not_configured'
    assert result['transcript_preview'] == [speech]
    assert pending_turns(result['transcript_preview'], result['insights']) == [speech]
    assert not sales._voice_live_tasks


@pytest.mark.parametrize('status', ['unavailable', 'failed'])
def test_webhook_returned_model_failure_only_persists_retry_marker(monkeypatch, status):
    speech = turn()
    writes = []

    class Database:
        def execute(self, statement, params):
            query = ' '.join(str(statement).split())
            writes.append((query, params))
            if 'union all' in query:
                return Result([speech])
            if query.startswith('select active_version_id'):
                return Result([{'active_version_id': 'active', 'analysis_coverage_samples': 160000}])
            if 'from transcript_turns t' in query:
                return Result([speech])
            if 'select context from voice_ai_contexts' in query:
                return Result([{'context': {'customer_requirements': [{'product': 'lamp'}]}}])
            if query.startswith('insert into voice_ai_contexts'):
                pytest.fail('A failed webhook analysis must not publish context')
            return Result()
        def commit(self): pass

    async def analyze(self, **kwargs):
        return {'analysis_status': status, 'analysis_error': {'code': 'synthetic_failure', 'message': 'Analysis needs a retry.'},
                'extraction': {}, 'context': {}, 'route': {}}

    monkeypatch.setattr(sales.settings, 'openai_api_key', 'synthetic-key')
    monkeypatch.setattr(sales.VoiceConversationOrchestrator, 'run', analyze)
    result = sales._extract_voice_chunk(Database(), {'conversation_id': 'conversation', 'record_id': 'event'})
    assert result['status'] == status
    updates = [(query, params) for query, params in writes if query.startswith('update conversation_extractions')]
    assert len(updates) == 1 and "status='failed'" in updates[0][0]
    assert json.loads(updates[0][1]['data'])['analysis_status'] == status


async def test_hosted_catalogue_exception_keeps_successful_model_facts_and_fingerprints(monkeypatch, scope):
    current = snapshot([turn()])
    calls = []

    class Database:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def commit(self): pass
        def execute(self, statement, params):
            query = ' '.join(str(statement).split())
            if 'pg_try_advisory_xact_lock' in query:
                return Result([{'acquired': True}])
            if query.startswith('insert into conversation_extractions'):
                current['insights'] = [{'fingerprint': params['fingerprint'], 'status': 'running', 'updated_at': datetime.now(UTC), 'data': {}}]
            elif query.startswith('insert into voice_ai_contexts'):
                current['context'] = json.loads(params['context'])
            elif query.startswith('update conversation_extractions'):
                assert "status='complete'" in query
                current['insights'][0].update(status='complete', data=json.loads(params['data']), updated_at=datetime.now(UTC))
            else:
                raise AssertionError(query)
            return Result()

    async def analyze(self, **kwargs):
        calls.append(kwargs)
        return {'analysis_status': 'complete', 'context': {'customer_requirements': [{'product': 'fan', 'quantity': 2}]},
                'route': {}, 'extraction': {}}

    async def broken(*args):
        raise RuntimeError('private-provider-error')

    monkeypatch.setattr(sales, '_voice_live_snapshot', lambda session_id: copy.deepcopy(current))
    monkeypatch.setattr(sales, '_session', lambda *args: {'conversation_id': current['conversation_id']})
    monkeypatch.setattr(sales, 'require_db', Database)
    monkeypatch.setattr(sales.VoiceConversationOrchestrator, 'run', analyze)
    monkeypatch.setattr(sales, 'search_catalogue', broken)
    monkeypatch.setattr(sales.settings, 'openai_api_key', 'synthetic-key')
    await sales._analyse_voice_live(UUID(current['session_id']))
    value = await sales.voice_session_live(UUID(current['session_id']), None)
    assert value['analysis_status'] == 'complete' and value['catalogue_status'] == 'unavailable'
    assert value['requirements'][0]['quantity'] == 2
    assert pending_turns(value['transcript_preview'], value['insights']) == []
    assert 'private-provider-error' not in str(value)
    assert len(calls) == 1 and not sales._voice_live_tasks


async def test_hosted_catalogue_only_refresh_preserves_completed_speech_without_calling_model(monkeypatch, scope):
    speech = turn()
    current = snapshot([speech], [{'status': 'complete', 'fingerprint': 'speech', 'updated_at': datetime.now(UTC),
        'data': {'_live': {'turn_fingerprints': [turn_fingerprint(speech)]}}}])
    current['context'] = {'customer_requirements': [{'product': 'fan'}], 'last_intent': 'pricing'}
    writes = []

    class Database:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def commit(self): pass
        def execute(self, statement, params):
            query = ' '.join(str(statement).split())
            writes.append((query, params))
            if 'pg_try_advisory_xact_lock' in query:
                return Result([{'acquired': True}])
            return Result()

    async def forbidden(*args, **kwargs):
        pytest.fail('Catalogue refresh must not repeat semantic extraction')

    async def search(*args):
        return {'status': 'ready', 'items': [{'item_id': 'fan-1'}]}

    monkeypatch.setattr(sales, '_voice_live_snapshot', lambda session_id: copy.deepcopy(current))
    monkeypatch.setattr(sales, '_session', lambda *args: {'conversation_id': current['conversation_id']})
    monkeypatch.setattr(sales, 'require_db', Database)
    monkeypatch.setattr(sales.VoiceConversationOrchestrator, 'run', forbidden)
    monkeypatch.setattr(sales, 'search_catalogue', search)
    await sales._analyse_voice_live(UUID(current['session_id']))
    extraction_writes = [(query, params) for query, params in writes if query.startswith('insert into conversation_extractions')]
    assert len(extraction_writes) == 1
    assert extraction_writes[0][1]['fingerprint'].startswith('catalogue:')
    assert json.loads(extraction_writes[0][1]['data'])['_live']['turn_fingerprints'] == []
