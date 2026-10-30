"""Grounded live-session extraction and catalogue matching helpers.

These helpers never turn model output into catalogue facts. The member's
Zoho Books items supply inventory; local checks use an explicitly labelled
sample adapter with its own provenance.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from ..catalogue_embeddings import get_embedding_client
from ..catalogue_index import retrieve_catalogue
from ..catalogue_matching import match_catalogue


def turn_fingerprint(turn: dict[str, Any]) -> str:
    # Progressive versions copy earlier turns with new IDs. Content and audio
    # offsets identify the same speech across those versions.
    content = {key: turn.get(key) for key in ('start_seconds', 'end_seconds', 'source_text', 'english_text')}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def untranslated(turn: dict[str, Any]) -> bool:
    return not str(turn.get('english_text') or '').strip()


def analysis_turns(turns: list[dict], coverage_samples: int | None) -> list[dict]:
    """Attach AI-only frontier flags while preserving every UI preview row.

    Native translation may publish separate English and source passes. Those
    optional source rows are context, not a missing English translation job.
    """
    coverage_seconds = coverage_samples / 16000 if coverage_samples is not None else None
    marked = []
    for turn in turns:
        end = turn.get('end_seconds')
        ready = (coverage_seconds is not None and end is not None and math.isfinite(float(end))
                 and float(end) <= coverage_seconds + 1e-9)
        skip = untranslated(turn) and (turn.get('provenance') or {}).get('native_english') is True
        marked.append({**turn, 'analysis_ready': ready, 'analysis_skip': skip})
    return marked


def pending_turns(turns: list[dict], extractions: list[dict]) -> list[dict]:
    """Analyse an ordered translated prefix, never speech beyond a missing window.

    Provider jobs may finish out of order. Applying a later correction before
    an earlier turn would let the delayed earlier statement overwrite it when
    its translation arrives. UI previews may show every available turn; AI
    context advances only through the contiguous, translated audio frontier.
    """
    processed = set()
    for extraction in extractions:
        if extraction.get('status') != 'complete':
            continue
        processed.add(str(extraction.get('fingerprint') or ''))
        processed.update(((extraction.get('data') or {}).get('_live') or {}).get('turn_fingerprints') or [])
    pending = []
    for turn in sorted(turns, key=lambda row: (row.get('start_seconds') or 0, row.get('end_seconds') or 0, row.get('ordinal') or 0)):
        if turn.get('analysis_skip') is True:
            continue
        if turn.get('analysis_ready') is False or untranslated(turn):
            break
        if turn_fingerprint(turn) not in processed:
            pending.append(turn)
    return pending


def batch_fingerprint(turns: list[dict]) -> str:
    return hashlib.sha256('|'.join(turn_fingerprint(turn) for turn in turns).encode()).hexdigest()


def catalogue_queries(context: dict) -> list[str]:
    """Search every requested product, using attributes to rank rather than narrow out stock."""
    queries = []
    for requirement in context.get('customer_requirements') or []:
        if not isinstance(requirement, dict) or requirement.get('excluded') or requirement.get('status') == 'removed':
            continue
        # SKU searches are precise; category searches find options when a long
        # spoken description would match no catalogue names.
        values = [requirement.get('sku'), requirement.get('product') or requirement.get('name'), requirement.get('category')]
        for key in ('search_terms', 'category_terms'):
            terms = requirement.get(key) or []
            values.extend([terms] if isinstance(terms, str) else terms if isinstance(terms, list) else [])
        for value in values:
            clean = re.sub(r'\s+', ' ', str(value or '')).strip()[:100]
            if clean and clean.casefold() not in {query.casefold() for query in queries}:
                queries.append(clean)
    focal = str(context.get('focal_product') or '').strip()[:100]
    excluded_names = {str(item.get('product') or item.get('name') or '').casefold()
                      for item in context.get('customer_requirements') or [] if isinstance(item, dict) and item.get('excluded')}
    if focal and focal.casefold() not in excluded_names and focal.casefold() not in {query.casefold() for query in queries}:
        queries.append(focal)
    return queries


def intent_keywords(context: dict) -> list[str]:
    values = []
    for requirement in context.get('customer_requirements') or []:
        if not isinstance(requirement, dict) or requirement.get('excluded') or requirement.get('status') == 'removed':
            continue
        values.extend([requirement.get('product') or requirement.get('name'), requirement.get('sku')])
        attributes = requirement.get('attributes') or []
        values.extend(attributes.values() if isinstance(attributes, dict) else attributes if isinstance(attributes, list) else [attributes])
        if requirement.get('quantity') is not None:
            values.append(f"Quantity: {requirement['quantity']}")
        if requirement.get('budget') is not None:
            values.append(f"Budget: {requirement['budget']}")
    values.extend(context.get('preferred_features') or [])
    values.extend(context.get('brand_preferences') or [])
    if context.get('budget_max') is not None:
        values.append(f"Budget: {context['budget_max']}")
    clean = []
    for value in values:
        label = str(value or '').strip()[:100]
        if label and label.casefold() not in {item.casefold() for item in clean}:
            clean.append(label)
    return clean[:60]


def _number(value) -> float | None:
    try:
        number = float(str(value).replace(',', '').replace('%', '').strip())
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def normalize_catalogue_item(row: dict, *, currency: str | None = None, catalogue_source: str = 'zoho') -> dict | None:
    item_id = row.get('item_id') or row.get('id')
    name = row.get('name') or row.get('item_name')
    if not item_id or not name or row.get('status') == 'inactive' or row.get('can_be_sold') is False:
        return None
    image = str(row.get('image_url') or row.get('image') or '').strip()
    local_image = catalogue_source == 'local-demo' and re.fullmatch(r'/demo-products/[a-z0-9-]+\.svg', image)
    if not image.startswith(('https://', 'http://')) and not local_image:
        image = ''
    return {
        **row, 'item_id': str(item_id), 'name': str(name), 'sku': str(row.get('sku') or ''),
        'description': str(row.get('description') or ''), 'rate': _number(row.get('rate')),
        'stock_on_hand': _number(row.get('stock_on_hand')),
        'available_stock': _number(row.get('available_stock')),
        'tax_percentage': _number(row.get('tax_percentage')), 'image_url': image or None,
        'currency_code': row.get('currency_code') or currency,
        'catalogue_source': catalogue_source,
    }


async def search_catalogue(client, context: dict) -> dict:
    """Retrieve indexed candidates, then verify their actual catalogue facts.

    Semantic similarity broadens retrieval; it cannot verify a technical rating,
    invent a price or publish a partial inventory snapshot as a complete search.
    """
    queries = catalogue_queries(context)
    if not queries:
        return {'status': 'waiting', 'items': [], 'queries': [], 'notices': []}
    try:
        retrieved = await retrieve_catalogue(client, context, embedding_client=get_embedding_client())
        source = 'local-demo' if retrieved.get('retrieval_mode') == 'sample' else 'zoho'
        items = [item for raw in retrieved.get('items') or []
                 if isinstance(raw, dict) and (item := normalize_catalogue_item(raw, catalogue_source=source))]
        if not items and retrieved.get('status') not in {'ready', 'stale'}:
            return {**retrieved, 'queries': queries, 'requirements': [], 'clarification_suggestions': []}
        deferred = retrieved.get('deferred_requirements') or []
        deferred_ids = {row.get('requirement_id') for row in deferred}
        matching_context = context
        if deferred_ids:
            active = [dict(row) for row in context.get('customer_requirements') or []
                      if isinstance(row, dict) and not row.get('excluded') and row.get('status') != 'removed']
            for index, requirement in enumerate(active):
                requirement.setdefault('id', requirement.get('requirement_id') or f'requirement-{index + 1}')
            matching_context = {**context, 'focal_product': None,
                                'customer_requirements': [row for row in active if row['id'] not in deferred_ids]}
        matched = match_catalogue(items, matching_context)
        matched['requirements'].extend({**row, 'candidate_count': 0, 'verified_count': 0, 'missing_specs': []}
                                       for row in deferred)
        return {**retrieved, **matched, 'queries': queries}
    except Exception:
        return {'status': 'unavailable', 'items': [], 'queries': queries, 'notices': [{
            'code': 'catalogue_lookup_failed', 'message': 'Product suggestions are temporarily unavailable. Transcript updates continue.',
        }]}
