from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from app.voice_ai.workspace import (analysis_outcome, catalogue_due, requirement_signature, retain_catalogue_recommendations,
                                    retry_failure, workspace_fields)


def test_workspace_assessments_use_exact_sku_for_same_name_variants():
    context = {'customer_requirements': [{'product': 'device', 'sku': 'A', 'quantity': 1}, {'product': 'device', 'sku': 'B', 'quantity': 2}]}
    catalogue = {'requirements': [{'product': 'device', 'sku': 'B', 'status': 'no_match'},
                                 {'product': 'device', 'sku': 'A', 'status': 'matched'}]}
    result = workspace_fields(context, catalogue)
    assert [item['status'] for item in result['requirements']] == ['matched', 'no_match']
    assert [item['quantity'] for item in result['requirements']] == [1, 2]


def test_workspace_omits_withdrawals_and_keeps_only_existing_advisory_question_text():
    context = {'customer_requirements': [{'product': 'motor', 'excluded': True}, {'product': 'fan'}],
               'suggested_questions': ['Which size?', 'Is a remote required?'], 'missing_critical_fields': ['size'],
               'intent_evidence': ['Need a fan.']}
    catalogue = {'clarification_suggestions': [{'question': 'Which size?', 'evidence': ['A large fan.'],
                   'audience': 'customer', 'auto_execute': True, 'open_modal': True}, {'question': ''}]}
    result = workspace_fields(context, catalogue)
    assert [item['product'] for item in result['requirements']] == ['fan']
    assert [item['question'] for item in result['clarification_suggestions']] == ['Which size?', 'Is a remote required?']
    for question in result['clarification_suggestions']:
        assert question['audience'] == 'agent' and question['auto_execute'] is False
        assert 'open_modal' not in question


def test_catalogue_outage_keeps_same_constraints_with_visible_price_and_stock_review():
    previous = {'customer_requirements': [{'product': 'fan'}],
                'recent_recommendations': [{'item_id': 'fan-1', 'rate': 3000, 'stock_on_hand': 4, 'match_status': 'verified'}]}
    context = deepcopy(previous)
    retain_catalogue_recommendations(context, {'status': 'unavailable', 'items': []}, previous)
    item = context['recent_recommendations'][0]
    assert item['rate'] == 3000 and item['stock_on_hand'] == 4
    assert item['match_status'] == 'needs_confirmation'
    assert item['price_status'] == item['availability_status'] == 'needs_review'
    assert item['missing_specs'] == ['catalogue_confirmation']


@pytest.mark.parametrize(('state', 'age', 'expected'), [
    ('syncing', 3, True), ('syncing', 1, False), ('stale', 6, True), ('stale', 4, False),
    ('unavailable', 29, False), ('unavailable', 31, True), ('ready', 61, True), ('ready', 59, False),
    ('semantic_unavailable', 29, False), ('semantic_unavailable', 31, True),
])
def test_catalogue_refresh_intervals_follow_lifecycle_without_hot_polling(state, age, expected):
    moment = datetime.now(UTC)
    assert catalogue_due({'status': state}, moment - timedelta(seconds=age), [{'product': 'fan'}], moment=moment) is expected
    assert catalogue_due({'status': state}, None, [], moment=moment) is False


def test_recent_failure_backoff_accepts_stored_iso_timestamps_and_preserves_pending():
    moment = datetime.now(UTC)
    failure = {'status': 'failed', 'updated_at': moment.isoformat()}
    assert retry_failure([failure], pending=True, moment=moment) == failure
    assert retry_failure([failure], pending=False, moment=moment) is None
    assert retry_failure([failure], pending=True, moment=moment + timedelta(seconds=31)) is None
    assert retry_failure([{'status': 'failed', 'updated_at': 'invalid'}], pending=True, moment=moment) is None


def test_nested_model_failure_cannot_be_overridden_by_a_complete_outer_status():
    status, error = analysis_outcome({'analysis_status': 'complete', 'extraction': {'analysis_status': 'unavailable',
        'analysis_error': {'code': 'provider_unavailable', 'message': 'Try later.'}}})
    assert status == 'unavailable'
    assert error['code'] == 'provider_unavailable'


def test_later_catalogue_refresh_does_not_clear_a_pending_model_failure_backoff():
    moment = datetime.now(UTC)
    failure = {'status': 'failed', 'updated_at': moment.isoformat()}
    catalogue = {'status': 'complete', 'fingerprint': 'catalogue:abc', 'updated_at': (moment + timedelta(seconds=1)).isoformat(),
                 'data': {'_catalogue': {'status': 'ready'}, '_live': {'turn_fingerprints': []}}}
    assert retry_failure([failure, catalogue], pending=True, moment=moment + timedelta(seconds=2)) == failure


def test_stale_catalogue_assessment_never_overwrites_newer_attributes_and_questions_for_withdrawals():
    context = {'customer_requirements': [{'product': 'fan', 'attributes': {'size': '1400 mm'}}, {'product': 'motor', 'excluded': True}]}
    catalogue = {'requirements': [{'product': 'fan', 'attributes': {'size': '1200 mm'}, 'status': 'matched'}],
                 'clarification_suggestions': [{'product': 'motor', 'question': 'Which motor phase?'}]}
    result = workspace_fields(context, catalogue)
    assert result['requirements'][0]['attributes'] == {'size': '1400 mm'}
    assert 'status' not in result['requirements'][0]
    assert result['clarification_suggestions'] == []


def test_global_preference_correction_invalidates_prior_catalogue_signature():
    context = {'customer_requirements': [{'product': 'fan'}], 'preferred_features': ['quiet']}
    changed = {**context, 'preferred_features': ['remote control']}
    assert requirement_signature(context) != requirement_signature(changed)
    assert requirement_signature(context) == requirement_signature({**context, 'source_refs': [{'fingerprint': 'new'}]})
