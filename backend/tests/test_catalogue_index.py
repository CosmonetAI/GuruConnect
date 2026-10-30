"""Snapshot synchronization uses synthetic providers and isolated scoped stores."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import catalogue_index as index
from app.catalogue_index import CatalogueLease, CatalogueLeaseLost, CatalogueScope, reciprocal_rank_fusion, retrieve_catalogue, sync_catalogue
from app.catalogue_matching import normalize_item
from app.db import sales_workspace
from app.zoho import set_member_id, set_organisation_id


def inventory(name='Motor 1HP', identity='motor-1', **data):
    return {'item_id': identity, 'name': name, 'sku': identity, 'rate': 1500, 'stock_on_hand': 8,
            'can_be_sold': True, 'status': 'active', **data}


class MemoryStore:
    def __init__(self, *, vectors=True):
        self.clock = datetime.now(timezone.utc)
        self.headers, self.snapshots = {}, {}
        self.lock = threading.RLock()
        self.vectors = vectors
        self.searches, self.semantic_queries, self.claims = [], [], 0

    def vector_available(self): return self.vectors

    def state(self, scope):
        with self.lock:
            header = deepcopy(self.headers.get(scope.key, {}))
            snapshot = self.snapshots.get(header.get('snapshot_id'))
            if snapshot:
                header.update({key: snapshot[key] for key in ('snapshot_id','item_count','completed_at','inventory_observed_at','semantic_status','embedding_model')})
            return header

    def claim(self, scope, *, force=False, semantic_only=False, model=None):
        with self.lock:
            header = self.headers.setdefault(scope.key, {'fence': 0, 'next_refresh_at': self.clock})
            if header.get('lease_expires_at') and header['lease_expires_at'] > self.clock:
                return None
            semantic_due = header.get('requested_embedding_model') != model or header.get('next_semantic_retry_at', self.clock) <= self.clock
            if not force and (not semantic_only and header['next_refresh_at'] > self.clock or semantic_only and not semantic_due):
                return None
            header.update(fence=header['fence'] + 1, lease_token=str(uuid4()), lease_expires_at=self.clock + timedelta(seconds=120),
                          requested_embedding_model=model)
            lease = CatalogueLease(str(uuid4()), header['lease_token'], header['fence'])
            self.snapshots[lease.snapshot_id] = {'scope': scope.key, 'fence': lease.fence, 'state': 'building', 'items': {},
                                                 'vectors': {}, 'snapshot_id': lease.snapshot_id}
            self.claims += 1
            return lease

    def _guard(self, scope, lease):
        header = self.headers[scope.key]
        snapshot = self.snapshots[lease.snapshot_id]
        if (header['fence'] != lease.fence or header.get('lease_token') != lease.token
                or not header.get('lease_expires_at') or header['lease_expires_at'] <= self.clock
                or snapshot['scope'] != scope.key or snapshot['fence'] != lease.fence or snapshot['state'] != 'building'):
            raise CatalogueLeaseLost('Fenced')

    def renew(self, scope, lease):
        with self.lock:
            self._guard(scope, lease)
            self.headers[scope.key]['lease_expires_at'] = self.clock + timedelta(seconds=120)

    def reusable_embeddings(self, scope, hashes, model):
        state = self.state(scope)
        snapshot = self.snapshots.get(state.get('snapshot_id'), {})
        return {key: value for key, value in snapshot.get('vectors', {}).items()
                if key in hashes and snapshot.get('embedding_model') == model}

    def stage(self, scope, lease, items, embeddings, model):
        with self.lock:
            self._guard(scope, lease)
            snapshot = self.snapshots[lease.snapshot_id]
            snapshot['items'].update({item['item_id']: deepcopy(item) for item in items})
            snapshot['vectors'].update(embeddings)
            snapshot['embedding_model'] = model

    def publish(self, scope, lease, *, item_count, page_count, semantic_status, model, observed_at=None):
        with self.lock:
            self._guard(scope, lease)
            snapshot = self.snapshots[lease.snapshot_id]
            assert item_count == len(snapshot['items']) and page_count >= 1
            snapshot.update(state='complete', completed_at=self.clock, item_count=item_count,
                            inventory_observed_at=observed_at or self.clock, semantic_status=semantic_status, embedding_model=model)
            self.headers[scope.key].update(snapshot_id=lease.snapshot_id, lease_token=None, lease_expires_at=None,
                                          next_refresh_at=(observed_at or self.clock) + timedelta(seconds=900), last_error_code=None,
                                          next_semantic_retry_at=self.clock + timedelta(seconds=900 if semantic_status == 'ready' else 60),
                                          requested_embedding_model=model)

    def fail(self, scope, lease, code):
        with self.lock:
            snapshot = self.snapshots[lease.snapshot_id]
            if snapshot['state'] == 'building':
                snapshot['state'] = 'failed'
            header = self.headers[scope.key]
            if header['fence'] == lease.fence and header.get('lease_token') == lease.token:
                header.update(lease_token=None, lease_expires_at=None,
                              next_refresh_at=max(header['next_refresh_at'], self.clock + timedelta(seconds=60)),
                              next_semantic_retry_at=self.clock + timedelta(seconds=60), last_error_code=code)

    def read_snapshot_page(self, scope, snapshot, cursor='', *, limit=200):
        selected = self.snapshots[snapshot]
        assert selected['scope'] == scope.key and selected['state'] == 'complete'
        return [deepcopy(selected['items'][identity]) for identity in sorted(selected['items']) if identity > cursor][:limit]

    def lexical_search(self, scope, snapshot, queries, *, limit=48):
        self.searches.append((scope.key, snapshot, list(queries)))
        selected = self.snapshots[snapshot]
        assert selected['scope'] == scope.key and selected['state'] == 'complete'
        rows = list(selected['items'].values())
        return [[deepcopy(row) for row in rows if query.lower() in row['name'].lower() or query.lower() == row['sku'].lower()][:limit]
                for query in queries]

    def semantic_search(self, scope, snapshot, vectors, model, *, limit=48):
        self.semantic_queries.append((scope.key, snapshot, model))
        selected = self.snapshots[snapshot]
        assert selected['scope'] == scope.key and selected['embedding_model'] == model
        return [list(reversed(list(selected['items'].values())))[:limit] for _ in vectors]


class Books:
    def __init__(self, pages=None, *, books='books-1'):
        self.pages = pages if pages is not None else [[inventory()]]
        self.books, self.calls = books, []
        self.fail_page, self.gate = None, None

    def status(self): return {'connected': True, 'organization_id': self.books, 'currency': 'INR'}

    async def search_books_items(self, books, query, date, page):
        assert books == self.books and not query and date is None
        self.calls.append(page)
        if self.gate:
            await self.gate.wait()
        if page == self.fail_page:
            raise RuntimeError('synthetic provider outage')
        rows = self.pages[page - 1] if page <= len(self.pages) else []
        return {'items': deepcopy(rows), 'page_context': {'page': page, 'has_more_page': page < len(self.pages)}}


class Embeddings:
    model_id = 'synthetic-provider/model@revision:384'

    def __init__(self, *, fail=False):
        self.calls, self.fail = [], fail

    async def embed(self, texts, *, query=False):
        self.calls.append((list(texts), query))
        if self.fail:
            raise RuntimeError('synthetic embedding outage')
        return [[1.0, *([0.0] * 383)] for _ in texts]


@pytest.fixture
def scope(monkeypatch):
    scope = CatalogueScope(str(uuid4()), str(uuid4()), 'books-1')
    verified = SimpleNamespace(id=scope.organisation_id, member_id=scope.member_id,
                               user=SimpleNamespace(id=scope.member_id), admin=False, role='salesperson')
    token = sales_workspace.set(verified)
    set_organisation_id(scope.organisation_id); set_member_id(scope.member_id)
    yield scope
    sales_workspace.reset(token)
    set_organisation_id(None); set_member_id(None)


@pytest.mark.asyncio
async def test_all_pages_are_staged_before_atomic_publication(scope):
    store, client = MemoryStore(), Books([[inventory('Motor', 'one')], [inventory('Fan', 'two')]])
    lease = store.claim(scope)
    result = await sync_catalogue(client, scope, lease, store=store)
    assert client.calls == [1, 2] and result['item_count'] == 2
    assert store.state(scope)['snapshot_id'] == lease.snapshot_id
    assert store.snapshots[lease.snapshot_id]['state'] == 'complete'


@pytest.mark.asyncio
async def test_first_read_starts_background_sync_without_waiting_and_concurrent_reads_claim_once(scope):
    store, client = MemoryStore(), Books()
    client.gate = asyncio.Event()
    try:
        responses = await asyncio.gather(*(retrieve_catalogue(client, {'customer_requirements': [{'product': 'motor'}]}, store=store) for _ in range(3)))
        assert all(not response['items'] for response in responses)
        assert store.claims == 1
        assert store.state(scope).get('snapshot_id') is None
    finally:
        client.gate.set()
        await asyncio.gather(*list(index._SYNC_TASKS.values()))
        await index.close_catalogue_tasks()
    assert store.state(scope)['item_count'] == 1


@pytest.mark.asyncio
async def test_recording_turns_search_existing_snapshot_without_refetching_inventory(scope):
    store, client = MemoryStore(), Books()
    await sync_catalogue(client, scope, store.claim(scope), store=store)
    calls = list(client.calls)
    for _ in range(3):
        result = await retrieve_catalogue(client, {'customer_requirements': [{'product': 'motor'}]}, store=store)
        assert result['status'] == 'ready' and result['items'][0]['item_id'] == 'motor-1'
    assert client.calls == calls and len(store.searches) == 3


@pytest.mark.asyncio
async def test_failed_refresh_keeps_previous_complete_snapshot_and_marks_prices_stale(scope, monkeypatch):
    store, client = MemoryStore(), Books()
    await sync_catalogue(client, scope, store.claim(scope), store=store)
    previous = store.state(scope)['snapshot_id']
    store.clock += timedelta(seconds=901)
    client.pages = [[inventory('Replacement motor', 'new')], [inventory('Another motor', 'other')]]
    client.fail_page = 2
    await sync_catalogue(client, scope, store.claim(scope), store=store)
    assert store.state(scope)['snapshot_id'] == previous
    monkeypatch.setattr(index, '_utcnow', lambda: store.clock)
    result = await retrieve_catalogue(client, {'customer_requirements': [{'product': 'motor'}]}, store=store)
    assert result['status'] == 'stale'
    assert result['items'][0]['item_id'] == 'motor-1'
    assert result['items'][0]['rate'] == 1500 and result['items'][0]['price_status'] == 'confirmation_required'
    assert result['items'][0]['availability_status'] == 'needs_review'


def test_expired_worker_is_fenced_from_staging_publishing_and_new_lease_errors(scope):
    store = MemoryStore()
    first = store.claim(scope)
    store.clock += timedelta(seconds=121)
    second = store.claim(scope)
    assert second.fence == first.fence + 1
    with pytest.raises(CatalogueLeaseLost): store.stage(scope, first, [normalize_item(inventory())], {}, '')
    with pytest.raises(CatalogueLeaseLost): store.publish(scope, first, item_count=0, page_count=1, semantic_status='unavailable', model=None)
    store.fail(scope, first, 'old-worker-error')
    assert store.headers[scope.key]['lease_token'] == second.token
    assert store.headers[scope.key].get('last_error_code') is None


@pytest.mark.asyncio
async def test_embeddings_are_batched_and_only_changed_text_is_reembedded(scope):
    store, embedding = MemoryStore(), Embeddings()
    client = Books([[inventory('Motor', 'one'), inventory('Fan', 'two')]])
    await sync_catalogue(client, scope, store.claim(scope), store=store, embedding_client=embedding)
    assert sum(len(texts) for texts, query in embedding.calls if not query) == 2
    client.pages[0][0]['rate'] = 1600  # Price changes do not change semantic text.
    client.pages[0][1]['description'] = 'New specification'
    await sync_catalogue(client, scope, store.claim(scope, force=True), store=store, embedding_client=embedding)
    assert sum(len(texts) for texts, query in embedding.calls if not query) == 3
    assert store.state(scope)['semantic_status'] == 'ready'
    assert all(len(texts) <= 32 for texts, _ in embedding.calls)


@pytest.mark.asyncio
async def test_embedding_outage_publishes_complete_lexical_snapshot_and_degrades_honestly(scope):
    store, client, embedding = MemoryStore(), Books(), Embeddings(fail=True)
    await sync_catalogue(client, scope, store.claim(scope), store=store, embedding_client=embedding)
    result = await retrieve_catalogue(client, {'customer_requirements': [{'product': 'motor'}]}, embedding, store=store)
    assert result['status'] == 'ready' and result['retrieval_mode'] == 'lexical'
    assert result['snapshot']['semantic'] == 'unavailable' and result['items']
    assert len(embedding.calls) == 1  # No per-turn reindex attempt after outage.


@pytest.mark.asyncio
async def test_semantic_query_uses_persisted_index_and_rank_fusion(scope):
    store, client, embedding = MemoryStore(), Books([[inventory('Motor', 'one'), inventory('Ceiling fan', 'two')]]), Embeddings()
    await sync_catalogue(client, scope, store.claim(scope), store=store, embedding_client=embedding)
    result = await retrieve_catalogue(client, {'customer_requirements': [{'product': 'motor'}]}, embedding, store=store)
    assert result['retrieval_mode'] == 'hybrid' and len(store.semantic_queries) == 1
    assert {item['item_id'] for item in result['items']} == {'one', 'two'}
    assert embedding.calls[-1][1] is True
    assert client.calls == [1]


@pytest.mark.asyncio
async def test_embeddings_cannot_be_reused_across_member_or_books_org(scope):
    store, embedding = MemoryStore(), Embeddings()
    await sync_catalogue(Books(), scope, store.claim(scope), store=store, embedding_client=embedding)
    other_member = CatalogueScope(scope.organisation_id, str(uuid4()), scope.books_org_id)
    other_books = CatalogueScope(scope.organisation_id, scope.member_id, 'different-books')
    hashed = index._text_hash(normalize_item(inventory()))
    assert store.reusable_embeddings(other_member, [hashed], embedding.model_id) == {}
    assert store.reusable_embeddings(other_books, [hashed], embedding.model_id) == {}


@pytest.mark.asyncio
async def test_missing_pagination_metadata_requires_terminal_empty_page(scope):
    class NoMetadata(Books):
        async def search_books_items(self, books, query, date, page):
            self.calls.append(page)
            return {'items': [inventory()] if page == 1 else []}
    store, client = MemoryStore(), NoMetadata()
    result = await sync_catalogue(client, scope, store.claim(scope), store=store)
    assert client.calls == [1, 2] and result['item_count'] == 1


@pytest.mark.asyncio
async def test_repeated_provider_page_cannot_publish_partial_snapshot(scope):
    store, client = MemoryStore(), Books([[inventory()], [inventory()]])
    result = await sync_catalogue(client, scope, store.claim(scope), store=store)
    assert result.get('error') and not store.state(scope).get('snapshot_id')


@pytest.mark.asyncio
async def test_no_pgvector_skips_document_embedding_calls_and_keeps_lexical_index(scope):
    store, embedding, client = MemoryStore(vectors=False), Embeddings(), Books()
    await sync_catalogue(client, scope, store.claim(scope), store=store, embedding_client=embedding)
    assert not embedding.calls
    assert store.state(scope)['semantic_status'] == 'unavailable'


@pytest.mark.asyncio
async def test_model_change_rebuilds_semantics_in_background_without_refetching_inventory_or_resetting_age(scope, monkeypatch):
    store, client, first = MemoryStore(), Books(), Embeddings()
    await sync_catalogue(client, scope, store.claim(scope), store=store, embedding_client=first)
    observed = store.state(scope)['inventory_observed_at']
    provider_calls = list(client.calls)
    second = Embeddings(); second.model_id = 'synthetic-provider/different-model:384'
    store.clock += timedelta(seconds=20)
    monkeypatch.setattr(index, '_utcnow', lambda: store.clock)
    result = await retrieve_catalogue(client, {'customer_requirements': [{'product': 'motor'}]}, second, store=store)
    assert result['snapshot']['semantic'] == 'pending' and result['retrieval_mode'] == 'lexical'
    await asyncio.gather(*list(index._SYNC_TASKS.values()))
    await index.close_catalogue_tasks()
    assert client.calls == provider_calls
    assert store.state(scope)['embedding_model'] == second.model_id
    assert store.state(scope)['semantic_status'] == 'ready'
    assert store.state(scope)['inventory_observed_at'] == observed


@pytest.mark.asyncio
async def test_semantic_outage_recovery_is_background_only_and_backs_off(scope, monkeypatch):
    store, client, embedding = MemoryStore(), Books(), Embeddings(fail=True)
    await sync_catalogue(client, scope, store.claim(scope), store=store, embedding_client=embedding)
    observed = store.state(scope)['inventory_observed_at']
    provider_calls = list(client.calls)
    embedding.fail = False
    monkeypatch.setattr(index, '_utcnow', lambda: store.clock)
    await retrieve_catalogue(client, {'customer_requirements': [{'product': 'motor'}]}, embedding, store=store)
    assert not index._SYNC_TASKS and len(embedding.calls) == 1
    store.clock += timedelta(seconds=61)
    await retrieve_catalogue(client, {'customer_requirements': [{'product': 'motor'}]}, embedding, store=store)
    await asyncio.gather(*list(index._SYNC_TASKS.values()))
    await index.close_catalogue_tasks()
    assert store.state(scope)['semantic_status'] == 'ready'
    assert store.state(scope)['inventory_observed_at'] == observed and client.calls == provider_calls


def test_query_budget_reserves_primary_slots_before_aliases_and_reports_deferred_requirements():
    requirements = [{'id': f'req-{position}', 'product': f'Widget {position}',
                     'search_terms': [f'Alias {position}-{term}' for term in range(6)]} for position in range(70)]
    queries, deferred, searched = index._query_plan({'customer_requirements': requirements})
    assert queries == [f'Widget {position}' for position in range(64)]
    assert searched == [f'req-{position}' for position in range(64)]
    assert [row['requirement_id'] for row in deferred] == [f'req-{position}' for position in range(64, 70)]
    assert all(row['status'] == 'lookup_deferred' for row in deferred)


def test_primary_queries_and_search_documents_share_typed_spec_and_unit_normalization():
    context = {'customer_requirements': [{'product': 'MCB', 'attributes': {'current': '10A', 'poles': 'DP', 'curve': 'C'}}]}
    primary = index._queries(context)[0]
    document = index._item_text(normalize_item(inventory('10A DP 6KA TYPE C MCB')))
    assert 'current a 10' in primary and 'poles 2' in primary and 'breaker curve C' in primary
    assert 'current a 10' in document and 'poles 2' in document and '10 A' in document
    assert len(index._item_text(normalize_item(inventory(description='文' * 10000))).encode()) <= 6000


@pytest.mark.asyncio
async def test_query_embeddings_respect_provider_batch_limit(scope):
    store, client, embedding = MemoryStore(), Books(), Embeddings()
    await sync_catalogue(client, scope, store.claim(scope), store=store, embedding_client=embedding)
    requirements = [{'id': f'req-{position}', 'product': f'Widget {position}'} for position in range(64)]
    await retrieve_catalogue(client, {'customer_requirements': requirements}, embedding, store=store)
    query_batches = [texts for texts, query in embedding.calls if query]
    assert [len(batch) for batch in query_batches] == [32, 32]


@pytest.mark.asyncio
async def test_contradictory_duplicate_rows_keep_review_evidence_across_pages(scope):
    store = MemoryStore()
    client = Books([[inventory('Motor', 'one'), inventory('Fan', 'two', stock_on_hand=3)],
                    [inventory('Fan', 'two', stock_on_hand=9, rate=2000), inventory('Switch', 'three')]])
    await sync_catalogue(client, scope, store.claim(scope), store=store)
    snapshot = store.snapshots[store.state(scope)['snapshot_id']]
    duplicate = snapshot['items']['two']
    assert duplicate['duplicate_source_evidence']['stock_on_hand'] == [3, 9]
    assert duplicate['price_status'] == 'confirmation_required'
    assert duplicate['availability_status'] == 'needs_review'
    assert duplicate['stock_on_hand'] == 9  # Preserved final value, never 3+9.
    assert snapshot['item_count'] == 3


@pytest.mark.asyncio
async def test_local_demo_uses_full_sample_adapter_without_database_or_embeddings():
    class Demo(Books):
        def status(self): return {**super().status(), 'catalogue_source': 'local-demo'}
    embedding = Embeddings()
    result = await retrieve_catalogue(Demo([[inventory('Fan', 'one'), inventory('Motor', 'two')]]), {}, embedding)
    assert len(result['items']) == 2 and result['snapshot']['id'] == 'local-demo'
    assert result['retrieval_mode'] == 'sample' and not embedding.calls


def test_rrf_deduplicates_items_without_using_an_uncalibrated_probability():
    a, b, c = inventory('A', 'a'), inventory('B', 'b'), inventory('C', 'c')
    result = reciprocal_rank_fusion([[a, a, b], [b, c]])
    assert result[0]['item_id'] == 'b'
    assert len(result) == 3
    assert all(0 < item['retrieval_score'] < 1 for item in result)
