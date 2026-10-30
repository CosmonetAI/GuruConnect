"""Present stored analysis as advisory workspace data without taking actions."""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import re
from typing import Any


def analysis_outcome(result: dict[str, Any]) -> tuple[str, dict[str, str] | None]:
    extraction = result.get('extraction') or {}
    statuses = [value for value in (result.get('analysis_status'), extraction.get('analysis_status')) if value]
    status = next((value for value in statuses if value != 'complete'), 'complete')
    if status == 'complete':
        return status, None
    status = status if status in {'unavailable', 'failed'} else 'failed'
    error = result.get('analysis_error') or extraction.get('analysis_error') or {
        'code': 'voice_analysis_failed', 'message': 'AI analysis is unavailable. Your transcript and existing requirements are preserved.',
    }
    if not isinstance(error, dict):
        error = {'code': 'voice_analysis_failed', 'message': 'AI analysis is temporarily unavailable.'}
    return status, {'code': str(error.get('code') or 'voice_analysis_failed')[:100],
                    'message': str(error.get('message') or 'AI analysis is temporarily unavailable.')[:500]}


def failed_analysis_data(result: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    status, error = analysis_outcome(result)
    return {'analysis_status': status, 'analysis_error': error, '_route': result.get('route') or {},
            '_live': live, 'auto_execute': False}


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return None


def retry_failure(rows: list[dict[str, Any]], *, pending: bool, moment: datetime | None = None) -> dict[str, Any] | None:
    """Back off failures across new speech without consuming pending fingerprints."""
    if not pending:
        return None
    latest = max((row for row in rows if row.get('status') in {'failed', 'unavailable'}),
                 key=lambda row: _timestamp(row.get('updated_at')) or datetime.min.replace(tzinfo=UTC), default=None)
    if latest is None:
        return None
    failed_at = _timestamp(latest.get('updated_at'))
    completed_at = max((_timestamp(row.get('updated_at')) or datetime.min.replace(tzinfo=UTC)
                        for row in rows if row.get('status') == 'complete'
                        and not str(row.get('fingerprint') or '').startswith('catalogue:')
                        and not ((row.get('data') or {}).get('_catalogue')
                                 and ((row.get('data') or {}).get('_live') or {}).get('turn_fingerprints') == [])),
                       default=datetime.min.replace(tzinfo=UTC))
    if failed_at and failed_at > completed_at and ((moment or datetime.now(UTC)) - failed_at).total_seconds() < 30:
        return latest
    return None


def row_analysis_error(row: dict[str, Any] | None) -> tuple[str, dict[str, str] | None]:
    if not row:
        return 'complete', None
    data = row.get('data') or {}
    return analysis_outcome({**data, 'analysis_status': data.get('analysis_status') or 'failed'})


def catalogue_due(catalogue: dict[str, Any], updated_at: Any, requirements: list[dict[str, Any]], *, moment: datetime | None = None) -> bool:
    if not requirements:
        return False
    updated = _timestamp(updated_at)
    if not catalogue or not updated:
        return True
    interval = {'syncing': 2, 'stale': 5, 'unavailable': 30, 'semantic_unavailable': 30}.get(catalogue.get('status'), 60)
    return ((moment or datetime.now(UTC)) - updated).total_seconds() >= interval


def requirement_signature(context: dict[str, Any]) -> str:
    # Exclude audit/confidence data so polling does not make valid items stale.
    keys = ('id', 'requirement_id', 'product', 'name', 'category', 'sku', 'attributes', 'quantity', 'budget',
            'excluded', 'status', 'search_terms', 'category_terms', 'accessory_terms', 'required_terms', 'excluded_terms')
    facts = [{key: item[key] for key in keys if key in item} for item in context.get('customer_requirements') or []
             if isinstance(item, dict)]
    constraints = {key: context.get(key) for key in ('budget_max', 'preferred_features', 'brand_preferences')}
    return hashlib.sha256(json.dumps({'requirements': facts, 'constraints': constraints}, sort_keys=True, default=str).encode()).hexdigest()


def retain_catalogue_recommendations(context: dict[str, Any], catalogue: dict[str, Any], previous: dict[str, Any]) -> None:
    """Keep failures honest when the customer's constraints changed during a call."""
    if catalogue.get('status') in {'ready', 'waiting'}:
        context['recent_recommendations'] = catalogue.get('items') or []
    elif catalogue.get('status') in {'syncing', 'stale'} and catalogue.get('items'):
        context['recent_recommendations'] = [{**item, 'price_status': 'needs_review', 'availability_status': 'needs_review'}
                                             for item in catalogue['items'] if isinstance(item, dict)]
    elif requirement_signature(context) != requirement_signature(previous):
        context['recent_recommendations'] = []
    else:
        context['recent_recommendations'] = [
            {**item, 'match_status': 'needs_confirmation', 'price_status': 'needs_review',
             'availability_status': 'needs_review',
             'missing_specs': [*(item.get('missing_specs') or []),
                               *([] if 'catalogue_confirmation' in (item.get('missing_specs') or []) else ['catalogue_confirmation'])]}
            for item in previous.get('recent_recommendations') or [] if isinstance(item, dict)
        ]


def _identity_score(item: dict[str, Any], assessment: dict[str, Any]) -> int:
    def value(row, key):
        return str(row.get(key) or '').strip().casefold()
    identities = {value(item, key) for key in ('id', 'requirement_id')} - {''}
    if identities & ({value(assessment, key) for key in ('id', 'requirement_id')} - {''}):
        return 100
    if value(item, 'sku') and value(assessment, 'sku'):
        return 80 if value(item, 'sku') == value(assessment, 'sku') else 0
    names = {value(item, key) for key in ('product', 'name')} - {''}
    return 10 if names & ({value(assessment, key) for key in ('product', 'name')} - {''}) else 0


def workspace_fields(context: dict[str, Any], catalogue: dict[str, Any]) -> dict[str, Any]:
    """Merge existing questions and match assessments; never invent a question."""
    requirements = []
    signature = catalogue.get('requirements_signature')
    catalogue_current = not signature or signature == requirement_signature(context)
    assessments = [item for item in catalogue.get('requirements') or [] if isinstance(item, dict)] if catalogue_current else []
    for item in context.get('customer_requirements') or []:
        if not isinstance(item, dict) or item.get('excluded') or item.get('status') == 'removed':
            continue
        best = max((_identity_score(item, assessment) for assessment in assessments), default=0)
        matches = [assessment for assessment in assessments if best and _identity_score(item, assessment) == best]
        match = matches[0] if len(matches) == 1 else None
        if match and isinstance(match.get('attributes'), dict) and isinstance(item.get('attributes'), dict) and match['attributes'] != item['attributes']:
            match = None  # A webhook correction may be newer than this lookup.
        enriched = {**item, **match} if match else dict(item)
        for key in ('product', 'name', 'sku', 'attributes', 'required_specs', 'quantity', 'budget', 'excluded',
                    'search_terms', 'category_terms', 'accessory_terms', 'required_terms', 'excluded_terms'):
            if key in item:
                enriched[key] = item[key]
        requirements.append(enriched)
    questions, seen = [], set()
    model_questions = [{'question': question, 'missing_fields': context.get('missing_critical_fields') or [],
                        'evidence': context.get('intent_evidence') or []}
                       for question in context.get('suggested_questions') or [] if isinstance(question, str)]
    active_ids = {str(item.get('requirement_id') or item.get('id')) for item in requirements if item.get('requirement_id') or item.get('id')}
    active_names = {str(item.get('product') or item.get('name') or '').strip().casefold() for item in requirements}
    catalogue_questions = [value for value in catalogue.get('clarification_suggestions') or [] if isinstance(value, dict)
                           and requirements and catalogue_current
                           and (not value.get('requirement_id') or not active_ids or str(value['requirement_id']) in active_ids)
                           and (not value.get('product') or str(value['product']).strip().casefold() in active_names)]
    for value in [*catalogue_questions, *model_questions]:
        if not isinstance(value, dict) or not isinstance(value.get('question'), str):
            continue
        question = value['question'].strip()
        key = re.sub(r'\W+', ' ', question).strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        identity = str(value.get('requirement_id') or '')
        questions.append({'id': value.get('id') or hashlib.sha256(f'{identity}:{key}'.encode()).hexdigest()[:16],
                          'requirement_id': value.get('requirement_id'), 'question': question,
                          'missing_fields': value.get('missing_fields') or [], 'evidence': value.get('evidence') or [],
                          'audience': 'agent', 'auto_execute': False})
    guidance = list(context.get('agent_guidance') or [])
    document_labels = {'quotation': 'Quotation', 'written_offer': 'Written offer', 'boq': 'Bill of quantities',
                       'invoice': 'Invoice', 'sales_order': 'Sales order', 'purchase_order': 'Purchase order'}
    document = document_labels.get(context.get('document_kind'))
    if document:
        guidance.append(f'{document} requested. Review the cart and choose the document in Complete cart; creation and sending require your action.')
    return {'requirements': requirements, 'clarification_suggestions': questions,
            'current_intent': context.get('last_intent'), 'intent_confidence': context.get('intent_confidence'),
            'intent_evidence': context.get('intent_evidence') or [], 'document_kind': context.get('document_kind'),
            'agent_guidance': guidance, 'clarification_audience': 'agent',
            'catalogue': catalogue, 'auto_execute': False}
