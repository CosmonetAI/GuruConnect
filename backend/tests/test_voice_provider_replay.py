"""Replay synthetic conversation outputs captured from the provider; no live calls."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.voice_ai.agents import ConversationMonitorAgent, _Understanding, _attribute_supported, _normalize_understanding
from app.voice_ai.context import VoiceConversationContext


CONFIG = SimpleNamespace(openai_api_key='synthetic-key', voice_extraction_model='gpt-4o-mini', openai_model='gpt-4o-mini')
CAPTURE = json.loads((Path(__file__).parent / 'fixtures' / 'voice_semantic_provider.json').read_text())


def response(value):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=json.dumps(value), refusal=None))])


@pytest.mark.parametrize(('spoken', 'value', 'name', 'valid'), [
    ('one horsepower', '1 HP', 'power', True), ('two HP', '2HP', 'horsepower', True),
    ('single phase', '1 phase', 'phase', True), ('double pole', 'DP', 'poles', True),
    ('ten amps', '10A', 'current', True), ('sixteen amps', '10 A', 'current', False),
    ('110 amps', '10 A', 'current', False), ('one horsepower', '2 HP', 'power', False),
    ('not single phase', '1 phase', 'phase', False), ('No fan.', 'none', 'fan', False),
])
def test_narrow_number_unit_equivalence_keeps_mismatches_and_negations_rejected(spoken, value, name, valid):
    assert _attribute_supported({'name': name, 'value': value, 'evidence': [spoken]}) is valid


def test_captured_fabricated_compound_sentence_is_still_rejected():
    captured = CAPTURE[0]
    with pytest.raises(ValueError, match='unsupported_evidence'):
        _normalize_understanding(_Understanding.model_validate_json(captured['raw']), captured['payload'])


def test_paired_quote_wrapper_is_safe_but_fabricated_inner_quote_still_rejected():
    captured = copy.deepcopy(CAPTURE[1])
    output = json.loads(captured['raw'])
    output['requirements'] = []
    result = _normalize_understanding(_Understanding.model_validate(output), captured['payload'])
    assert result['intent_evidence'] == [captured['payload']['english_text']]
    output['intent_evidence'] = ['"Prepare and send the quotation."']
    with pytest.raises(ValueError, match='unsupported_intent_evidence'):
        _normalize_understanding(_Understanding.model_validate(output), captured['payload'])


def corrected(index):
    output = json.loads(CAPTURE[index]['raw'])
    text = CAPTURE[index]['payload']['english_text']
    output['intent_evidence'] = [text]
    if index == 0:
        for requirement in output['requirements']:
            requirement['evidence'] = [text]
            requirement['product']['evidence'] = [text]
            for attribute in requirement['attributes']:
                attribute['evidence'] = [text]
    elif index == 1:
        requirement = output['requirements'][0]
        requirement['product'] = {'value': 'motor', 'evidence': ['motor']}
        requirement['attributes'] = [requirement['attributes'][0]]
        requirement['attributes'][0]['evidence'] = ['Make the motor two HP instead.']
        requirement['evidence'] = ['Make the motor two HP instead.']
        fan = copy.deepcopy(requirement)
        fan.update(product={'value': 'fan', 'evidence': ['No fan.']}, category='fan', attributes=[],
                   excluded={'value': True, 'evidence': ['No fan.']}, evidence=['No fan.'], search_terms=['fan'], category_terms=['fan'])
        output['requirements'].append(fan)
    else:
        requirement = output['requirements'][0]
        requirement['product'] = {'value': 'motor', 'evidence': ['keep the motor']}
        requirement['attributes'] = []
        requirement['evidence'] = [text]
        breaker = copy.deepcopy(requirement)
        breaker.update(product={'value': 'MCB', 'evidence': ['MCB']}, category='circuit breaker',
                       excluded={'value': True, 'evidence': ['cancel the breaker']}, evidence=['cancel the breaker'],
                       search_terms=['MCB'], category_terms=['circuit breaker'])
        output['requirements'].append(breaker)
    return output


async def test_captured_bad_outputs_get_one_structured_repair_and_preserve_chronological_facts(monkeypatch):
    calls = []
    context = VoiceConversationContext()
    for index, captured in enumerate(CAPTURE):
        replies = iter([json.loads(captured['raw']), corrected(index)])

        async def fake(configuration, payload):
            calls.append(copy.deepcopy(payload))
            return response(next(replies))

        monkeypatch.setattr('app.voice_ai.agents._structured_completion', fake)
        chunk = {**captured['payload'], 'fingerprint': str(index)}
        result = await ConversationMonitorAgent(CONFIG).run(chunk, context.as_dict(), captured['payload']['fallback_route_hint'])
        assert result['analysis_status'] == 'complete', result
        assert result['analysis_repaired'] is True and result['analysis_attempts'] == 2
        context.merge_extraction(result, {'fingerprint': str(index)})
        if index == 0:
            assert len(context.customer_requirements) == 2
            assert context.customer_requirements[0]['product'] == 'motor'
            assert context.customer_requirements[0]['required_specs'] == {'power_hp': 1.0, 'phases': 1}
            assert context.customer_requirements[1]['required_specs'] == {'current_a': 10.0, 'poles': 2, 'breaker_curve': 'C'}
        if index == 1:
            assert context.customer_requirements[0]['attributes']['power'] == '2 hp'
            assert 'horsepower' not in context.customer_requirements[0]['attributes']
            assert context.customer_requirements[0]['required_specs']['power_hp'] == 2
            assert result['intent'] == 'quote_request' and result['auto_execute'] is False
    motor, breaker, fan = context.customer_requirements
    assert motor['excluded'] if 'excluded' in motor else True
    assert breaker['excluded'] is True and fan['excluded'] is True
    assert motor['attributes']['power'] == '2 hp'
    assert len(calls) == 6
    assert all('validation_repair' in calls[index] for index in (1, 3, 5))


async def test_repair_is_bounded_and_never_accepts_fabricated_evidence(monkeypatch):
    calls = []
    async def fake(configuration, payload):
        calls.append(payload)
        return response(json.loads(CAPTURE[0]['raw']))
    monkeypatch.setattr('app.voice_ai.agents._structured_completion', fake)
    captured = CAPTURE[0]
    result = await ConversationMonitorAgent(CONFIG).run(captured['payload'], {}, captured['payload']['fallback_route_hint'])
    assert result['analysis_status'] == 'failed'
    assert result['analysis_error']['code'] == 'voice_ai_invalid_output'
    assert len(calls) == 2
