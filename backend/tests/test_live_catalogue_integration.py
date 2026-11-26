import pytest

from app.voice_ai.live import search_catalogue


@pytest.mark.asyncio
async def test_syncing_inventory_never_claims_product_absence(monkeypatch):
    async def retrieve(*args, **kwargs):
        return {'status': 'syncing', 'items': [], 'notices': [{'code': 'catalogue_syncing'}]}
    monkeypatch.setattr('app.voice_ai.live.retrieve_catalogue', retrieve)
    result = await search_catalogue(object(), {'customer_requirements': [{'product': 'pump'}]})
    assert result['status'] == 'syncing'
    assert result['requirements'] == [] and result['clarification_suggestions'] == []


@pytest.mark.asyncio
async def test_deferred_products_are_visible_without_false_no_match_or_clarification(monkeypatch):
    async def retrieve(*args, **kwargs):
        return {'status': 'ready', 'items': [], 'retrieval_exhaustive': False,
                'deferred_requirements': [{'requirement_id': 'requirement-2', 'product': 'pump',
                                           'status': 'lookup_deferred', 'reason': 'query_budget'}]}
    monkeypatch.setattr('app.voice_ai.live.retrieve_catalogue', retrieve)
    result = await search_catalogue(object(), {'customer_requirements': [{'product': 'fan'}, {'product': 'pump'}]})
    assert result['requirements'][0]['product'] == 'fan'
    assert result['requirements'][0]['status'] == 'no_match'
    assert result['requirements'][1]['status'] == 'lookup_deferred'
    assert not any(row.get('product') == 'pump' for row in result['clarification_suggestions'])


@pytest.mark.asyncio
async def test_semantic_candidate_does_not_override_known_specification_conflict(monkeypatch):
    async def retrieve(*args, **kwargs):
        return {'status': 'ready', 'retrieval_mode': 'hybrid', 'items': [
            {'item_id': 'right', 'name': 'Single phase pump motor 1 HP', 'category': 'pump motor', 'rate': 4000, 'stock_on_hand': 3, 'can_be_sold': True},
            {'item_id': 'wrong', 'name': 'Single phase pump motor 10 HP', 'category': 'pump motor', 'rate': 40000, 'stock_on_hand': 8, 'can_be_sold': True},
        ]}
    monkeypatch.setattr('app.voice_ai.live.retrieve_catalogue', retrieve)
    result = await search_catalogue(object(), {'customer_requirements': [{'product': 'pump motor',
                                             'attributes': {'power': '1 HP', 'phase': 'single phase'}}]})
    assert [row['item_id'] for row in result['items']] == ['right']
    assert result['items'][0]['match_status'] == 'verified'
    assert result['requirements'][0]['rejected_reasons']['spec_mismatch:power_hp'] == 1
