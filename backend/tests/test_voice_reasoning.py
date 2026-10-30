"""Semantic extraction contracts use synthetic provider responses; no paid calls."""
import copy
import json
from types import SimpleNamespace

import pytest

from app.voice_ai.agents import (ConversationMonitorAgent, _Understanding, _bounded_dialogue, _number_supported,
                                 _quantity_supported, _reference_schema, _resolve_evidence)
from app.voice_ai.context import VoiceConversationContext
from app.voice_ai.intents import classify_voice_intent
from app.voice_ai.orchestrator import VoiceConversationOrchestrator


CONFIG = SimpleNamespace(openai_api_key='synthetic-provider-key', voice_extraction_model='gpt-4o-mini', openai_model='gpt-4o-mini')


def fact(value, quote):
    return {'value': value, 'evidence': [quote]}


def understanding(text, intent='customer_requirements', **changes):
    return {'intent': intent, 'intent_confidence': .96, 'intent_evidence': [text], 'document_kind': None,
            'requirements': [], 'facts': [], 'budget': None, 'summary': text,
            'missing_critical_fields': [], 'suggested_questions': [], **changes}


def requirement(product_name, quote, **changes):
    return {'product': fact(product_name, quote), 'category': product_name, 'sku': None, 'attributes': [],
            'quantity': None, 'budget': None, 'excluded': None, 'removed_attributes': [],
            'search_terms': [product_name], 'category_terms': [product_name], 'accessory_terms': [],
            'excluded_terms': [], 'required_terms': [], 'removed_excluded_terms': [], 'evidence': [quote], **changes}


def completion(value, finish_reason='stop', refusal=None):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish_reason,
        message=SimpleNamespace(content=json.dumps(value), refusal=refusal))])


def provider(monkeypatch, value):
    seen = []

    async def run(configuration, payload):
        seen.append(payload)
        return completion(value)

    monkeypatch.setattr('app.voice_ai.agents._structured_completion', run)
    return seen


@pytest.mark.parametrize(('text', 'intent', 'kind'), [
    ('No quotation, just prices.', 'pricing', None),
    ('Could you send a written offer for these?', 'quote_request', 'written_offer'),
    ('We need a BOQ for this project.', 'quote_request', 'boq'),
    ('Please make that two.', 'customer_requirements', None),
    ('Cancel the motor; keep the fan.', 'customer_requirements', None),
    ('That one.', 'clarification', None),
    ('A formal commercial proposal would help us get approval.', 'quote_request', 'quotation'),
])
async def test_semantic_model_route_survives_fallback_and_has_no_automatic_action(monkeypatch, text, intent, kind):
    value = understanding(text, intent, document_kind=kind,
                          missing_critical_fields=['product reference'] if intent == 'clarification' else [],
                          suggested_questions=['Which option does the customer mean?'] if intent == 'clarification' else [])
    provider(monkeypatch, value)
    orchestrator = VoiceConversationOrchestrator()
    orchestrator.monitor = ConversationMonitorAgent(CONFIG)
    result = await orchestrator.run(chunk={'english_text': text, 'fingerprint': text}, durable_context={})
    assert result['route']['intent'] == intent
    assert result['route']['source'] == 'model'
    assert result['route']['confidence'] == .96
    assert result['route']['evidence'] == [text]
    assert result['route']['auto_execute'] is False
    assert result['context']['last_intent'] == intent
    assert result['context']['document_kind'] == kind
    assert result['context']['clarification_audience'] == 'agent'
    if intent == 'clarification':
        assert result['context']['suggested_questions'] == ['Which option does the customer mean?']


async def test_multiple_products_normalized_specs_sku_search_terms_and_followup_corrections(monkeypatch):
    text = 'Need one 1200 mm BLDC ceiling fan, SKU F-42, and a 1 HP motor. No white finish.'
    value = understanding(text, requirements=[
        requirement('ceiling fan', text, category='fan', sku=fact('F-42', 'SKU F-42'),
                    quantity=fact(1, 'one'), search_terms=['ceiling fan', 'fan', 'F-42'],
                    category_terms=['ceiling fan', 'fan'], accessory_terms=['fan regulator'],
                    required_terms=[fact('BLDC', 'BLDC')], excluded_terms=[fact('white', 'No white finish')],
                    attributes=[{'name': 'size', **fact('1200 mm', '1200 mm')}, {'name': 'motor', **fact('BLDC', 'BLDC')}]),
        requirement('motor', text, attributes=[{'name': 'power', **fact('1 HP', '1 HP')}]),
    ], facts=[{'field': 'focal_product', **fact('ceiling fan', text)}])
    seen = provider(monkeypatch, value)
    orchestrator = VoiceConversationOrchestrator()
    orchestrator.monitor = ConversationMonitorAgent(CONFIG)
    first = await orchestrator.run(chunk={'english_text': text, 'fingerprint': 'first'}, durable_context={})
    assert len(first['context']['customer_requirements']) == 2
    assert first['context']['customer_requirements'][0]['sku'] == 'F-42'
    assert first['context']['customer_requirements'][0]['quantity'] == 1
    assert first['context']['customer_requirements'][0]['search_terms'] == ['ceiling fan', 'fan', 'F-42']
    assert first['context']['customer_requirements'][0]['attributes'] == {'size': '1200 mm', 'motor': 'BLDC'}
    assert first['context']['customer_requirements'][0]['category_terms'] == ['ceiling fan', 'fan']
    assert first['context']['customer_requirements'][0]['accessory_terms'] == ['fan regulator']
    assert first['context']['customer_requirements'][0]['required_terms'] == ['BLDC']
    assert first['context']['customer_requirements'][0]['excluded_terms'] == ['white']
    followup = 'Make that two, 1400 mm instead, and cancel the motor.'
    update = understanding(followup, requirements=[
        requirement('ceiling fan', followup, product=fact('ceiling fan', 'ceiling fan'), sku=fact('F-42', 'F-42'),
                    quantity=fact(2, 'Make that two'), attributes=[{'name': 'size', **fact('1400 mm', '1400 mm')}]),
        requirement('motor', followup, excluded=fact(True, 'cancel the motor')),
    ])
    provider(monkeypatch, update)
    second = await orchestrator.run(chunk={'english_text': followup, 'fingerprint': 'second',
        'recent_dialogue': [{'english_text': text}]}, durable_context=first['context'])
    fan, motor = second['context']['customer_requirements']
    assert fan['quantity'] == 2
    assert fan['attributes'] == {'size': '1400 mm', 'motor': 'BLDC'}
    assert motor['excluded'] is True
    assert len(fan['source_refs']) == 2
    assert seen[0]['fallback_route_hint']['intent'] == 'customer_requirements'


@pytest.mark.parametrize('mutation', [
    lambda value: value.update(intent='invented_intent'),
    lambda value: value.update(intent_confidence='0.99'),
    lambda value: value.update(intent_confidence=1.1),
    lambda value: value.update(intent_evidence=['Customer said something else']),
    lambda value: value.update(extra='discarding extra keys would hide an invalid contract'),
    lambda value: value['requirements'][0].update(sku=fact('MADE-UP-SKU', 'Need a fan')),
    lambda value: value['requirements'][0].update(quantity=fact(100, 'Need a fan')),
    lambda value: value['requirements'][0].update(attributes=[{'name': 'power', **fact('900 W', 'Need a fan')}]),
    lambda value: value['requirements'][0].update(excluded_terms=[fact('invented feature', 'Need a fan')]),
    lambda value: value.update(facts=[{'field': 'contact_email', **fact('invented@example.com', 'Need a fan')}]),
])
async def test_invalid_or_unsupported_output_never_completes_or_marks_fingerprints(monkeypatch, mutation):
    text = 'Need a fan'
    value = understanding(text, requirements=[requirement('fan', text)])
    mutation(value)
    provider(monkeypatch, value)
    orchestrator = VoiceConversationOrchestrator()
    orchestrator.monitor = ConversationMonitorAgent(CONFIG)
    prior = {'focal_product': 'lamp', 'source_refs': [{'fingerprint': 'old'}]}
    result = await orchestrator.run(chunk={'english_text': text, 'fingerprint': 'new'}, durable_context=prior)
    assert result['analysis_status'] == 'failed'
    assert result['analysis_error']['code'] == 'voice_ai_invalid_output'
    assert result['context']['focal_product'] == 'lamp'
    assert result['context']['source_refs'] == [{'fingerprint': 'old'}]
    assert result['route']['source'] == 'rule_fallback'
    assert result['route']['auto_execute'] is False


async def test_missing_key_is_visible_without_losing_facts():
    orchestrator = VoiceConversationOrchestrator()
    orchestrator.monitor = ConversationMonitorAgent(SimpleNamespace(openai_api_key=''))
    prior = {'customer_requirements': [{'product': 'fan', 'quantity': 2}]}
    result = await orchestrator.run(chunk={'english_text': 'Need a fan', 'fingerprint': 'one'}, durable_context=prior)
    assert result['analysis_status'] == 'unavailable'
    assert result['analysis_error']['code'] == 'voice_ai_not_configured'
    assert result['context']['customer_requirements'] == prior['customer_requirements']
    assert result['context']['source_refs'] == []


async def test_old_dialogue_cannot_reapply_a_superseded_quantity(monkeypatch):
    text = 'We are still discussing that fan.'
    value = understanding(text, requirements=[requirement('fan', text, quantity=fact(1, 'one fan'))])
    provider(monkeypatch, value)
    orchestrator = VoiceConversationOrchestrator()
    orchestrator.monitor = ConversationMonitorAgent(CONFIG)
    prior = {'customer_requirements': [{'product': 'fan', 'quantity': 2}]}
    result = await orchestrator.run(chunk={'english_text': text, 'fingerprint': 'new',
        'recent_dialogue': [{'english_text': 'Need one fan.'}, {'english_text': 'Actually make it two.'}]}, durable_context=prior)
    assert result['analysis_status'] == 'failed'
    assert result['context']['customer_requirements'][0]['quantity'] == 2
    assert result['context']['source_refs'] == []


async def test_provider_error_preserves_facts_without_exposing_private_details(monkeypatch):
    async def broken(configuration, payload):
        raise RuntimeError('private-provider-key')

    monkeypatch.setattr('app.voice_ai.agents._structured_completion', broken)
    orchestrator = VoiceConversationOrchestrator()
    orchestrator.monitor = ConversationMonitorAgent(CONFIG)
    prior = {'customer_requirements': [{'product': 'fan', 'quantity': 2}]}
    result = await orchestrator.run(chunk={'english_text': 'Need a fan', 'fingerprint': 'one'}, durable_context=prior)
    assert result['analysis_status'] == 'unavailable'
    assert 'private-provider-key' not in str(result)
    assert result['context']['source_refs'] == []


@pytest.mark.parametrize(('finish_reason', 'refusal', 'code'), [
    ('length', None, 'voice_ai_incomplete'), ('stop', 'refused', 'voice_ai_refused'),
])
async def test_incomplete_and_refused_responses_are_not_empty_success(monkeypatch, finish_reason, refusal, code):
    async def run(configuration, payload):
        return completion(understanding('Need a fan'), finish_reason, refusal)
    monkeypatch.setattr('app.voice_ai.agents._structured_completion', run)
    result = await ConversationMonitorAgent(CONFIG).run({'english_text': 'Need a fan'}, {}, {})
    assert result['analysis_status'] != 'complete'
    assert result['analysis_error']['code'] == code


async def test_sdk_call_uses_strict_schema_bounded_timeout_and_preserves_model(monkeypatch):
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            captured['client'] = kwargs
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def create(self, **kwargs):
            captured['request'] = kwargs
            return completion(understanding('Need a fan'))

    monkeypatch.setattr('openai.AsyncOpenAI', Client)
    result = await ConversationMonitorAgent(CONFIG).run({'english_text': 'Need a fan'}, {}, {})
    assert result['analysis_status'] == 'complete'
    assert captured['client']['timeout'] == 35
    assert captured['client']['max_retries'] == 1
    assert captured['request']['model'] == CONFIG.voice_extraction_model
    schema = captured['request']['response_format']['json_schema']
    assert schema['strict'] is True
    for definition in [schema['schema'], *schema['schema']['$defs'].values()]:
        if definition.get('type') == 'object':
            assert definition['additionalProperties'] is False
            assert set(definition['required']) == set(definition['properties'])


async def test_oversized_durable_facts_are_not_silently_clipped_or_sent(monkeypatch):
    async def forbidden(*args):
        pytest.fail('Oversized input should not reach the paid provider')
    monkeypatch.setattr('app.voice_ai.agents._structured_completion', forbidden)
    result = await ConversationMonitorAgent(CONFIG).run({'english_text': 'Need a fan'}, {'commitments': ['x' * 100000]}, {})
    assert result['analysis_status'] == 'unavailable'
    assert result['analysis_error']['code'] == 'voice_context_limit'


async def test_one_bounded_repair_uses_original_evidence_and_never_accepts_fabricated_quotes(monkeypatch):
    text = 'Need a fan and motor.'
    bad = understanding(text, requirements=[requirement('motor', 'Need a motor.')])
    good = understanding(text, requirements=[requirement('motor', 'current:english_text')],
                         intent_evidence=['current:english_text'])
    seen = []
    async def run(configuration, payload):
        seen.append(payload)
        return completion(bad if len(seen) == 1 else good)
    monkeypatch.setattr('app.voice_ai.agents._structured_completion', run)
    result = await ConversationMonitorAgent(CONFIG).run({'english_text': text}, {}, {})
    assert result['analysis_status'] == 'complete' and result['analysis_repaired'] is True
    assert len(seen) == 2 and seen[1]['validation_repair']['error'] == 'unsupported_evidence'
    assert result['requirements'][0]['evidence'] == [text]
    assert result['intent_evidence'] == [text]
    assert _reference_schema(seen[0]['evidence_sources'])['$defs']['VoiceEvidenceSource']['enum']


async def test_repair_is_limited_to_two_attempts_with_no_successful_fingerprint(monkeypatch):
    text = 'Need a fan.'
    calls = provider(monkeypatch, understanding(text, requirements=[requirement('motor', 'Need a motor.')]))
    result = await ConversationMonitorAgent(CONFIG).run({'english_text': text}, {}, {})
    assert result['analysis_status'] == 'failed' and len(calls) == 2


async def test_unchanged_context_replay_cannot_prevent_grounded_power_correction(monkeypatch):
    text = 'Make the motor two HP instead.'
    prior = {'customer_requirements': [{'product': 'motor', 'category': 'motor', 'quantity': 1,
              'attributes': {'power': '1 HP', 'phase': 'single phase'},
              'required_specs': {'power_hp': 1, 'phases': 1}}]}
    original = copy.deepcopy(prior)
    value = understanding(text, requirements=[requirement('motor', text,
        attributes=[{'name': 'horsepower', **fact('2 HP', text)}, {'name': 'phases', **fact('1 phase', 'motor')}],
        quantity=fact(1, 'motor'))])
    provider(monkeypatch, value)
    orchestrator = VoiceConversationOrchestrator()
    orchestrator.monitor = ConversationMonitorAgent(CONFIG)
    result = await orchestrator.run(chunk={'english_text': text, 'fingerprint': 'new'}, durable_context=prior)
    assert result['analysis_status'] == 'complete'
    item = result['context']['customer_requirements'][0]
    assert item['attributes'] == {'power': '2 hp', 'phase': 'single phase'}
    assert item['required_specs'] == {'power_hp': 2, 'phases': 1}
    assert item['quantity'] == 1 and prior == original


@pytest.mark.parametrize(('value', 'quote', 'supported'), [
    (10, 'Need a ten amp MCB.', False), (1, 'Need a one horsepower motor.', True),
    (2, 'Make that two.', True), (10, 'Need ten 1 HP motors.', True),
    (1, 'One horsepower.', False), (2, 'Need double pole MCB.', False),
])
def test_ratings_do_not_become_product_counts(value, quote, supported):
    assert _quantity_supported(value, [quote]) is supported


def test_removing_an_attribute_also_removes_its_derived_requirement_specification():
    context = VoiceConversationContext.from_mapping({'customer_requirements': [
        {'product': 'motor', 'attributes': {'power': '1 HP', 'phase': 'single phase'},
         'required_specs': {'power_hp': 1, 'phases': 1}}]})
    context.merge_extraction({'requirements': [{'product': 'motor', 'removed_attributes': ['power']}]}, {'fingerprint': 'remove'})
    assert context.customer_requirements[0]['required_specs'] == {'phases': 1}


def test_source_reference_expansion_only_changes_citations():
    source = {'current:english_text': 'Need a fan.'}
    value = {'value': 'current:english_text', 'evidence': ['current:english_text'],
             'intent_evidence': ['invented-reference']}
    assert _resolve_evidence(value, source) == {'value': 'current:english_text', 'evidence': ['Need a fan.'],
                                                'intent_evidence': ['invented-reference']}


async def test_semantic_target_reference_withdraws_saved_device_without_a_duplicate_name(monkeypatch):
    text = 'Cancel the breaker; keep the motor.'
    prior = {'customer_requirements': [{'product': 'motor', 'category': 'motor'},
                                      {'product': 'MCB', 'category': 'circuit protection', 'quantity': 2}]}
    value = understanding(text, requirements=[requirement('breaker', text, target='requirement:1',
                                                        excluded=fact(True, text))])
    provider(monkeypatch, value)
    orchestrator = VoiceConversationOrchestrator()
    orchestrator.monitor = ConversationMonitorAgent(CONFIG)
    result = await orchestrator.run(chunk={'english_text': text, 'fingerprint': 'cancel'}, durable_context=prior)
    assert result['analysis_status'] == 'complete'
    assert len(result['context']['customer_requirements']) == 2
    assert result['context']['customer_requirements'][1]['product'] == 'MCB'
    assert result['context']['customer_requirements'][1]['excluded'] is True
    assert result['context']['customer_requirements'][1]['quantity'] == 2


async def test_unknown_target_reference_cannot_update_a_saved_requirement(monkeypatch):
    text = 'Cancel the breaker.'
    provider(monkeypatch, understanding(text, requirements=[requirement('breaker', text, target='requirement:99',
                                                                      excluded=fact(True, text))]))
    result = await ConversationMonitorAgent(CONFIG).run({'english_text': text}, {}, {})
    assert result['analysis_status'] == 'failed'


def test_recent_dialogue_is_bounded_chronological_and_keeps_whole_turns():
    turns = [{'english_text': f'Turn {index} ' + 'x' * 1500} for index in range(50)]
    result = _bounded_dialogue({'recent_dialogue': turns})
    assert result[-1] == turns[-1]
    assert len(result) < 24
    assert all(turn in turns for turn in result)
    assert result == turns[-len(result):]


def test_sku_introduction_merges_same_product_and_explicit_reinstatement_is_required():
    context = VoiceConversationContext()
    context.merge_extraction({'requirements': [{'product': 'fan', 'quantity': 1}]}, {'fingerprint': 'one'})
    context.merge_extraction({'requirements': [{'product': 'fan', 'sku': 'F-42', 'excluded': True}]}, {'fingerprint': 'two'})
    context.merge_extraction({'requirements': [{'product': 'fan', 'quantity': 2}]}, {'fingerprint': 'three'})
    assert len(context.customer_requirements) == 1
    assert context.customer_requirements[0]['excluded'] is True
    context.merge_extraction({'requirements': [{'product': 'fan', 'excluded': False}]}, {'fingerprint': 'four'})
    assert context.customer_requirements[0]['excluded'] is False
    before = copy.deepcopy(context.as_dict())
    context.merge_extraction({'requirements': [{'product': 'fan', 'quantity': 999}]}, {'fingerprint': 'four'})
    assert context.as_dict() == before


def test_dynamic_taxonomy_exclusions_and_preferences_preserve_newest_correction():
    context = VoiceConversationContext()
    context.merge_extraction({'requirements': [{'product': 'device', 'required_terms': ['remote'],
        'category_terms': ['device', 'unit'], 'accessory_terms': ['controller']}]}, {'fingerprint': 'one'})
    context.merge_extraction({'requirements': [{'product': 'device', 'excluded_terms': ['remote']}]}, {'fingerprint': 'two'})
    item = context.customer_requirements[0]
    assert item['required_terms'] == []
    assert item['excluded_terms'] == ['remote']
    assert item['category_terms'] == ['device', 'unit']
    assert item['accessory_terms'] == ['controller']
    context.merge_extraction({'requirements': [{'product': 'device', 'required_terms': ['remote']}]}, {'fingerprint': 'three'})
    assert item['required_terms'] == ['remote']
    assert item['excluded_terms'] == []


def test_long_conversation_does_not_discard_earlier_requested_products():
    context = VoiceConversationContext()
    for index in range(40):
        context.merge_extraction({'requirements': [{'product': f'item {index}'}]}, {'fingerprint': str(index)})
    assert len(context.customer_requirements) == 40
    assert context.customer_requirements[0]['product'] == 'item 0'


@pytest.mark.parametrize('text', ['No quotation, just prices', 'No quotation just prices',
                                 "Don't create an invoice; just give prices", 'Without an estimate, show the price'])
def test_fallback_does_not_turn_negated_document_mentions_into_document_requests(text):
    assert classify_voice_intent(text, {}).intent == 'pricing'


@pytest.mark.parametrize(('value', 'text', 'supported'), [
    (2, 'Make that two', True), (5000, 'Budget five thousand', True), (1200, '1200 mm', True),
    (100, 'one hundred', True), (1, 'one hundred', False), (0, 'Need a fan', False),
    (7, 'Need a fan', False), (900, '700 W', False),
])
def test_number_grounding(value, text, supported):
    assert _number_supported(value, [text]) is supported


def test_schema_rejects_unrecognized_keys():
    with pytest.raises(ValueError):
        _Understanding.model_validate({**understanding('Hello'), 'unexpected': True})
