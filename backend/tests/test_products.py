import pytest

from app import main


class FakeZoho:
    def __init__(self, payload=None):
        self.payload = payload or {'items': []}
        self.searches = []

    def status(self):
        return {
            'connected': True,
            'organization_id': '60096720982',
            'zoho_region': 'in',
        }

    async def search_books_items(self, organization_id, query, date, page):
        self.searches.append((organization_id, query, date, page))
        return self.payload


@pytest.mark.asyncio
async def test_product_search_fills_small_cache_match_from_zoho(monkeypatch):
    fake = FakeZoho()
    monkeypatch.setattr(main, 'zoho', fake)
    monkeypatch.setattr(main, '_recent_cache_rows', lambda dataset, limit=40: [
        {'item_id': 'pump-1', 'name': 'Water Pump', 'status': 'active', 'rate': 100},
    ])

    result = await main.products(search='pump')

    assert result['source'] == 'mixed'
    assert result['items'][0]['item_id'] == 'pump-1'
    assert fake.searches == [('60096720982', 'pump', None, 1)]


@pytest.mark.asyncio
async def test_product_search_falls_back_to_zoho_when_cache_misses(monkeypatch):
    fake = FakeZoho({'items': [
        {'item_id': 'pump-2', 'name': 'Submersible Pump', 'status': 'active'},
        {'item_id': 'pump-old', 'name': 'Old Pump', 'status': 'inactive'},
    ]})
    monkeypatch.setattr(main, 'zoho', fake)
    monkeypatch.setattr(main, '_recent_cache_rows', lambda dataset, limit=40: [])
    monkeypatch.setattr(main, '_cache_recent_rows', lambda dataset, rows: None)

    result = await main.products(search='pump')

    assert result['source'] == 'zoho'
    assert [item['item_id'] for item in result['items']] == ['pump-2', 'pump-old']
    assert fake.searches == [('60096720982', 'pump', None, 1)]


@pytest.mark.asyncio
async def test_product_search_includes_inactive_cache_rows_for_books_pages(monkeypatch):
    fake = FakeZoho()
    monkeypatch.setattr(main, 'zoho', fake)
    monkeypatch.setattr(main, '_recent_cache_rows', lambda dataset, limit=40: [
        {'item_id': 'pump-old', 'name': 'Old Pump', 'status': 'inactive'},
    ])
    monkeypatch.setattr(main, '_cache_recent_rows', lambda dataset, rows: None)

    result = await main.products(search='pump')

    assert result['source'] == 'mixed'
    assert [item['item_id'] for item in result['items']] == ['pump-old']
    assert fake.searches == [('60096720982', 'pump', None, 1)]


@pytest.mark.asyncio
async def test_product_search_uses_full_cache_as_first_batch_then_starts_zoho_at_page_one(monkeypatch):
    fake = FakeZoho({'items': [{'item_id': 'pump-live', 'name': 'Live Pump', 'status': 'inactive'}]})
    monkeypatch.setattr(main, 'zoho', fake)
    monkeypatch.setattr(main, '_recent_cache_rows', lambda dataset, limit=40: [
        {'item_id': f'pump-{index}', 'name': f'Pump {index}', 'status': 'active'} for index in range(40)
    ])

    result = await main.products(search='pump')

    assert result['source'] == 'supabase'
    assert len(result['items']) == 40
    assert result['has_more'] is True and result['next_provider_page'] == 1
    assert fake.searches == []
