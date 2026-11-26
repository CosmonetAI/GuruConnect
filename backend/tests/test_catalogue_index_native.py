"""Run against the disposable PostgreSQL created by check-catalogue-index.sh."""
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.catalogue_index import CatalogueLeaseLost, CatalogueScope, PostgresCatalogueStore, sync_catalogue
from app.catalogue_matching import normalize_item
from test_catalogue_index import Books, inventory


pytestmark = pytest.mark.skipif(not os.environ.get('CATALOGUE_INDEX_TEST_DATABASE_URL'), reason='Disposable native PostgreSQL required')


@pytest.fixture
def native_store():
    engine = create_engine(os.environ['CATALOGUE_INDEX_TEST_DATABASE_URL'])
    organisation, member = str(uuid4()), str(uuid4())
    with engine.begin() as db:
        db.execute(text('insert into members(organisation_id,user_id) values(cast(:org as uuid),cast(:member as uuid))'),
                   {'org': organisation, 'member': member})
    store = PostgresCatalogueStore(sessionmaker(bind=engine))
    yield store, CatalogueScope(organisation, member, 'books-1'), engine
    engine.dispose()


@pytest.mark.asyncio
async def test_native_pagination_publication_and_lexical_search(native_store):
    store, scope, _ = native_store
    client = Books([[inventory('Industrial motor 1HP', 'MOTOR-1')], [inventory('Ceiling fan 1200mm', 'FAN-1')]])
    lease = store.claim(scope)
    result = await sync_catalogue(client, scope, lease, store=store)
    assert not result.get('error'), result
    assert store.state(scope)['item_count'] == 2
    groups = store.lexical_search(scope, lease.snapshot_id, ['motor', 'FAN-1', 'ceileing'])
    assert groups[0][0]['item_id'] == 'MOTOR-1'
    assert groups[1][0]['item_id'] == 'FAN-1'
    assert groups[2][0]['item_id'] == 'FAN-1'


def test_native_concurrent_claims_and_expired_worker_fencing(native_store):
    store, scope, engine = native_store
    first = store.claim(scope)
    assert store.claim(scope) is None
    with engine.begin() as db:
        db.execute(text("update catalogue_index_scopes set lease_expires_at=now()-interval '1 second' where organisation_id=cast(:org as uuid)"),
                   {'org': scope.organisation_id})
    second = store.claim(scope)
    assert second.fence == first.fence + 1
    with pytest.raises(CatalogueLeaseLost): store.stage(scope, first, [normalize_item(inventory())], {}, None)
    with pytest.raises(CatalogueLeaseLost): store.publish(scope, first, item_count=0, page_count=1, semantic_status='unavailable', model=None)
    store.fail(scope, first, 'late-old-worker')
    store.stage(scope, second, [normalize_item(inventory())], {}, None)
    store.publish(scope, second, item_count=1, page_count=1, semantic_status='unavailable', model=None)
    assert str(store.state(scope)['snapshot_id']) == second.snapshot_id
    assert store.state(scope)['last_error_code'] is None


@pytest.mark.asyncio
async def test_native_partial_refresh_never_becomes_active(native_store):
    store, scope, _ = native_store
    await sync_catalogue(Books(), scope, store.claim(scope), store=store)
    previous = store.state(scope)['snapshot_id']
    lease = store.claim(scope, force=True)
    store.stage(scope, lease, [normalize_item(inventory('Partial replacement', 'new'))], {}, None)
    assert store.state(scope)['snapshot_id'] == previous
    store.fail(scope, lease, 'provider-page-failed')
    assert store.state(scope)['snapshot_id'] == previous
    assert store.state(scope)['last_error_code'] == 'provider-page-failed'


def test_native_scope_isolation_and_embedding_reuse(native_store):
    from app.catalogue_index import _text_hash
    store, scope, engine = native_store
    second_member = str(uuid4())
    with engine.begin() as db:
        db.execute(text('insert into members(organisation_id,user_id) values(cast(:org as uuid),cast(:member as uuid))'),
                   {'org': scope.organisation_id, 'member': second_member})
    second_scope = CatalogueScope(scope.organisation_id, second_member, scope.books_org_id)
    third_scope = CatalogueScope(scope.organisation_id, scope.member_id, 'books-2')
    row = normalize_item(inventory())
    vector = [1.0, *([0.0] * 383)]
    hashed = _text_hash(row)
    lease = store.claim(scope)
    store.stage(scope, lease, [row], {hashed: vector}, 'model-one')
    store.publish(scope, lease, item_count=1, page_count=1, semantic_status='unavailable', model='model-one')
    assert store.reusable_embeddings(scope, [hashed], 'model-one') == {hashed: vector}
    assert store.reusable_embeddings(scope, [hashed], 'model-two') == {}
    assert store.reusable_embeddings(second_scope, [hashed], 'model-one') == {}
    assert store.reusable_embeddings(third_scope, [hashed], 'model-one') == {}
    assert store.lexical_search(second_scope, lease.snapshot_id, ['motor']) == [[]]
    assert store.lexical_search(third_scope, lease.snapshot_id, ['motor']) == [[]]


def test_native_guard_binds_exact_building_snapshot(native_store):
    from app.catalogue_index import CatalogueLease
    store, scope, _ = native_store
    first = store.claim(scope)
    store.stage(scope, first, [normalize_item(inventory())], {}, None)
    store.publish(scope, first, item_count=1, page_count=1, semantic_status='unavailable', model=None)
    second = store.claim(scope, force=True)
    forged = CatalogueLease(first.snapshot_id, second.token, second.fence)
    with pytest.raises(CatalogueLeaseLost): store.stage(scope, forged, [normalize_item(inventory('Tampered', 'new'))], {}, None)
    assert str(store.state(scope)['snapshot_id']) == first.snapshot_id


@pytest.mark.asyncio
async def test_native_spec_focused_retrieval_finds_last_page_variant_after_two_hundred_wrong_variants(native_store):
    from app.catalogue_index import _queries
    from app.catalogue_matching import match_catalogue
    store, scope, _ = native_store
    wrong = [inventory('16A SP 6KA TYPE C MCB', f'wrong-{position:04d}') for position in range(200)]
    right = inventory('10A DP 6KA TYPE C MCB', 'right-last')
    client = Books([wrong, [right]])
    await sync_catalogue(client, scope, store.claim(scope), store=store)
    context = {'customer_requirements': [{'product': 'MCB', 'attributes': {'current': '10A', 'poles': 'DP', 'curve': 'C'},
                                         'accessory_terms': ['contact', 'operator']}]}
    queries = _queries(context)
    groups = store.lexical_search(scope, store.state(scope)['snapshot_id'], queries)
    assert groups[0][0]['item_id'] == 'right-last'
    matched = match_catalogue([row for group in groups for row in group], context)
    assert matched['items'][0]['item_id'] == 'right-last'


def test_native_snapshot_retention_has_reader_grace_and_cleans_old_generations(native_store):
    store, scope, engine = native_store
    identities = []
    for _ in range(5):
        lease = store.claim(scope, force=True)
        store.stage(scope, lease, [normalize_item(inventory())], {}, None)
        store.publish(scope, lease, item_count=1, page_count=1, semantic_status='unavailable', model=None)
        identities.append(lease.snapshot_id)
    # The ten-minute read grace protects recently retired snapshots.
    with engine.connect() as db:
        assert db.execute(text('select count(*) from catalogue_index_snapshots where organisation_id=cast(:org as uuid)'),
                          {'org': scope.organisation_id}).scalar_one() == 5
    with engine.begin() as db:
        db.execute(text("update catalogue_index_snapshots set completed_at=completed_at-interval '20 minutes' where organisation_id=cast(:org as uuid)"),
                   {'org': scope.organisation_id})
    newest = store.claim(scope, force=True)
    store.stage(scope, newest, [normalize_item(inventory())], {}, None)
    store.publish(scope, newest, item_count=1, page_count=1, semantic_status='unavailable', model=None)
    with engine.connect() as db:
        retained = db.execute(text('select id from catalogue_index_snapshots where organisation_id=cast(:org as uuid)'),
                              {'org': scope.organisation_id}).scalars().all()
    assert len(retained) == 3 and newest.snapshot_id in {str(value) for value in retained}


@pytest.mark.asyncio
async def test_native_semantic_only_copy_preserves_inventory_age_and_does_not_call_books(native_store):
    from test_catalogue_index import Embeddings
    store, scope, _ = native_store
    client = Books()
    await sync_catalogue(client, scope, store.claim(scope), store=store)
    previous = store.state(scope)
    calls = list(client.calls)
    embedding = Embeddings()
    lease = store.claim(scope, semantic_only=True, model=embedding.model_id)
    assert lease is not None
    result = await sync_catalogue(client, scope, lease, store=store, embedding_client=embedding, source_snapshot=previous)
    assert not result.get('error'), result
    assert client.calls == calls
    assert store.state(scope)['inventory_observed_at'] == previous['inventory_observed_at']


def test_native_gc_removes_abandoned_failed_staging_but_never_active_snapshot(native_store):
    store, scope, engine = native_store
    abandoned = store.claim(scope)
    store.stage(scope, abandoned, [normalize_item(inventory())], {}, None)
    with engine.begin() as db:
        db.execute(text("update catalogue_index_scopes set lease_expires_at=now()-interval '1 second' where organisation_id=cast(:org as uuid)"),
                   {'org': scope.organisation_id})
        db.execute(text("update catalogue_index_snapshots set created_at=created_at-interval '2 hours' where id=cast(:id as uuid)"),
                   {'id': abandoned.snapshot_id})
    current = store.claim(scope)
    store.stage(scope, current, [normalize_item(inventory())], {}, None)
    store.publish(scope, current, item_count=1, page_count=1, semantic_status='unavailable', model=None)
    with engine.connect() as db:
        retained = db.execute(text('select id from catalogue_index_snapshots where organisation_id=cast(:org as uuid)'),
                              {'org': scope.organisation_id}).scalars().all()
    assert {str(value) for value in retained} == {current.snapshot_id}


def test_native_membership_revocation_fences_further_background_writes(native_store):
    store, scope, engine = native_store
    lease = store.claim(scope)
    with engine.begin() as db:
        db.execute(text("update members set status='revoked' where organisation_id=cast(:org as uuid) and user_id=cast(:member as uuid)"),
                   {'org': scope.organisation_id, 'member': scope.member_id})
    with pytest.raises(CatalogueLeaseLost):
        store.stage(scope, lease, [normalize_item(inventory())], {}, None)


def test_native_index_tables_are_private_to_server_roles(native_store):
    _, _, engine = native_store
    with engine.connect() as db:
        for table in ('catalogue_index_scopes','catalogue_index_snapshots','catalogue_index_items'):
            assert db.execute(text('select has_table_privilege(\'authenticated\',:table,\'select\')'), {'table': table}).scalar_one() is False
            assert db.execute(text('select has_table_privilege(\'service_role\',:table,\'select\')'), {'table': table}).scalar_one() is True
