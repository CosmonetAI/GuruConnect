import pytest

from app.voice_ai.live import normalize_catalogue_item, search_catalogue
from recorder.demo_catalogue import LocalDemoCatalogueClient


@pytest.mark.asyncio
async def test_live_sample_catalogue_matches_fan_and_motor_separately_and_labels_every_card():
    result = await search_catalogue(LocalDemoCatalogueClient(), {
        'customer_requirements': [{'product': 'fan', 'features': ['BLDC', 'remote']}, {'product': 'motor'}],
        'preferred_features': ['BLDC', 'remote'],
    })
    assert result['status'] == 'ready'
    items = result['items']
    assert items[0]['sku'] == 'DEMO-FAN-BLDC1200'
    assert items[1]['category'] == 'motor'
    assert {'fan', 'motor'} == {item['category'] for item in items}
    assert all(item['catalogue_source'] == 'local-demo' and item['demo'] for item in items)
    assert all(item['image_url'].startswith('/demo-products/') for item in items)
    assert len({item['item_id'] for item in items}) == len(items)


@pytest.mark.asyncio
async def test_demo_search_does_not_invent_matches_and_results_are_independent():
    client = LocalDemoCatalogueClient()
    assert not (await client.search_books_items('local-demo', 'refrigerator'))['items']
    assert not (await client.search_books_items('local-demo', '', page=2))['items']
    first = (await client.search_books_items('local-demo', 'motor'))['items']
    assert {item['category'] for item in first} == {'motor'}
    first[0]['rate'] = 0
    second = (await client.search_books_items('local-demo', 'motor'))['items']
    assert second[0]['rate'] > 0


def test_hosted_catalogue_cannot_claim_local_asset_paths_or_change_its_provenance():
    item = normalize_catalogue_item({'id': '1', 'name': 'Fan', 'image_url': '/demo-products/fan.svg',
                                    'catalogue_source': 'local-demo'})
    assert item['catalogue_source'] == 'zoho'
    assert item['image_url'] is None
