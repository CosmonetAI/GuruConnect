"""Durable, fenced catalogue snapshots and scoped hybrid candidate retrieval.

Inventory synchronization runs outside speech turns. Only complete snapshots
become active; provider failures retain the previous snapshot with visible stale
metadata. Dense vectors are optional and never substitute for item facts.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import re
from uuid import uuid4

from sqlalchemy import text

from .catalogue_matching import normalize_item, _requirement_specs
from .db import require_db, row_dict, scope_parameters
from .settings import settings
from .zoho import get_member_id, get_organisation_id


log = logging.getLogger(__name__)
_SYNC_TASKS: dict[tuple[str, str, str], asyncio.Task] = {}
_DEFAULT_STORE = None
_PAGE_SIZE = 200
_MAX_PAGES = 500
_EMBEDDING_BATCH = 32
_DIMENSIONS = 384


class CatalogueLeaseLost(RuntimeError):
    pass


class CatalogueEmbeddingUnavailable(RuntimeError):
    def __init__(self, vectors):
        super().__init__('Catalogue embeddings are temporarily unavailable')
        self.vectors = vectors


@dataclass(frozen=True)
class CatalogueScope:
    organisation_id: str
    member_id: str
    books_org_id: str

    @property
    def key(self):
        return self.organisation_id, self.member_id, self.books_org_id

    def parameters(self):
        return {'org': self.organisation_id, 'member': self.member_id, 'books': self.books_org_id}


@dataclass(frozen=True)
class CatalogueLease:
    snapshot_id: str
    token: str
    fence: int

    def parameters(self):
        return {'snapshot': self.snapshot_id, 'token': self.token, 'fence': self.fence}


def _utcnow():
    return datetime.now(timezone.utc)


def _refresh_seconds():
    return max(60, min(86400, int(getattr(settings, 'catalogue_refresh_seconds', 900))))


def _lease_seconds():
    return max(60, min(600, int(getattr(settings, 'catalogue_sync_lease_seconds', 120))))


def _scope(status):
    verified = scope_parameters()
    organisation = get_organisation_id()
    member = get_member_id()
    if organisation and organisation != verified['org'] or member and member != verified['member']:
        raise PermissionError('Catalogue workspace identity mismatch')
    books = str(status.get('organization_id') or '').strip()
    if not books or len(books) > 128:
        raise ValueError('Select a Books organization before retrieving inventory')
    return CatalogueScope(verified['org'], verified['member'], books)


def _item_text(item):
    values = [item.get(key) for key in ('name', 'sku', 'brand', 'manufacturer', 'category', 'unit')]
    values.append(_spec_words(item.get('specs') or {}))
    values.append(json.dumps(item.get('explicit_attributes') or {}, ensure_ascii=False, sort_keys=True))
    values.append(item.get('description'))
    source = _unit_spacing(' '.join(str(value) for value in values if value))
    return source.encode('utf-8')[:6000].decode('utf-8', errors='ignore')


def _text_hash(item):
    return hashlib.sha256(_item_text(item).encode()).hexdigest()


def _valid_embedding(value):
    if not isinstance(value, (list, tuple)) or len(value) != _DIMENSIONS:
        return False
    if any(isinstance(number, bool) for number in value):
        return False
    try:
        values = [float(number) for number in value]
    except (ValueError, TypeError):
        return False
    return all(math.isfinite(number) for number in values) and any(number != 0 for number in values)


def _unit_spacing(value):
    value = re.sub(r'sq\.?\s*mm', 'sqmm', str(value), flags=re.I)
    value = re.sub(r'(\d)(mm|sqmm|hp|kw|rpm|hz|ph|p|v|a|uf|kvar)\b', r'\1 \2', value, flags=re.I)
    return re.sub(r'\s+', ' ', value).strip()


def _spec_words(specifications):
    parts = []
    for key in sorted(specifications):
        values = specifications[key] if isinstance(specifications[key], list) else [specifications[key]]
        rendered = []
        for value in values:
            if isinstance(value, dict):
                rendered.extend(str(value[key]) for key in ('min', 'max') if key in value)
            else:
                rendered.append(f'{value:g}' if isinstance(value, (int, float)) else str(value))
        parts.append(str(key).replace('_', ' ') + ' ' + ' '.join(rendered))
    return ' '.join(parts)


def _query_plan(context):
    active = [row for row in context.get('customer_requirements') or context.get('requirements') or []
              if isinstance(row, dict) and not row.get('excluded') and row.get('status') != 'removed']
    if not active and context.get('focal_product'):
        active = [{'product': context['focal_product']}]
    queries, deferred, searched, aliases = [], [], [], []
    for position, requirement in enumerate(active, 1):
        identity = str(requirement.get('id') or requirement.get('requirement_id') or f'requirement-{position}')
        product = str(requirement.get('product') or requirement.get('name') or requirement.get('category') or requirement.get('sku') or '')
        specs, features = _requirement_specs(requirement)
        if len(active) == 1:
            features += [str(value) for value in context.get('preferred_features') or []]
            features += [str(value) for value in context.get('brand_preferences') or []]
        focused = _unit_spacing(' '.join([product, _spec_words(specs), *features]))
        primary = str(requirement.get('sku') or focused).strip()
        primary = primary.encode('utf-8')[:6000].decode('utf-8', errors='ignore')
        known = {query.casefold() for query in queries}
        if primary and primary.casefold() not in known and len(queries) >= 64:
            deferred.append({'requirement_id': identity, 'product': product, 'sku': requirement.get('sku'),
                             'status': 'lookup_deferred', 'reason': 'query_budget'})
            continue
        if primary and primary.casefold() not in known:
            queries.append(primary)
        searched.append(identity)
        values = [focused, product, requirement.get('category')]
        for key in ('search_terms', 'category_terms'):
            terms = requirement.get(key) or []
            values.extend([terms] if isinstance(terms, str) else terms if isinstance(terms, list) else [])
        aliases.append(values)
    # Every requirement gets its focused query before earlier requirements can
    # consume spare slots with synonyms.
    for depth in range(max((len(values) for values in aliases), default=0)):
        for values in aliases:
            if depth >= len(values):
                continue
            clean = _unit_spacing(values[depth] or '').encode('utf-8')[:6000].decode('utf-8', errors='ignore')
            if clean and clean.casefold() not in {query.casefold() for query in queries} and len(queries) < 64:
                queries.append(clean)
    return queries, deferred, searched


def _queries(context):
    return _query_plan(context)[0]


class PostgresCatalogueStore:
    """Every method owns its transaction and every query carries all scope keys."""

    def __init__(self, database_factory=None):
        self.database_factory = database_factory or require_db
        self._extensions = None
        self._extension_versions = {}

    def _capabilities(self, db):
        if self._extensions is None:
            rows = db.execute(text("""select e.extname,e.extversion,n.nspname from pg_extension e
              join pg_namespace n on n.oid=e.extnamespace where e.extname in ('pg_trgm','vector')""")).all()
            self._extensions = {row.extname: row.nspname for row in rows
                                if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', row.nspname)}
            self._extension_versions = {row.extname: row.extversion for row in rows}
        return self._extensions

    def vector_available(self):
        with self.database_factory() as db:
            schema = self._capabilities(db).get('vector')
            if not schema:
                return False
            row = db.execute(text("""select 1 from information_schema.columns
              where table_schema='public' and table_name='catalogue_index_items' and column_name='embedding_vector'""")).first()
            return bool(row)

    def state(self, scope):
        with self.database_factory() as db:
            row = db.execute(text("""select h.*,s.id as snapshot_id,s.item_count,s.page_count,s.completed_at,
              s.semantic_status,s.embedding_model,s.inventory_observed_at from catalogue_index_scopes h
              left join catalogue_index_snapshots s on s.id=h.active_snapshot_id and s.organisation_id=h.organisation_id
                and s.member_id=h.member_id and s.books_org_id=h.books_org_id and s.state='complete'
              where h.organisation_id=cast(:org as uuid) and h.member_id=cast(:member as uuid) and h.books_org_id=:books"""), scope.parameters()).first()
            return row_dict(row) if row else {}

    def claim(self, scope, *, force=False, semantic_only=False, model=None):
        token, identity = str(uuid4()), str(uuid4())
        params = {**scope.parameters(), 'token': token, 'snapshot': identity,
                  'seconds': _lease_seconds(), 'force': force, 'semantic_only': semantic_only, 'model': model}
        with self.database_factory() as db:
            db.execute(text("""insert into catalogue_index_scopes(organisation_id,member_id,books_org_id)
              values(cast(:org as uuid),cast(:member as uuid),:books) on conflict do nothing"""), params)
            row = db.execute(text("""update catalogue_index_scopes set lease_token=cast(:token as uuid),fence=fence+1,
              requested_embedding_model=:model,
              lease_expires_at=now()+make_interval(secs=>:seconds),updated_at=now()
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid) and books_org_id=:books
                and (lease_expires_at is null or lease_expires_at<=now()) and (:force
                  or (not :semantic_only and next_refresh_at<=now())
                  or (:semantic_only and (next_semantic_retry_at<=now()
                    or coalesce(requested_embedding_model,'')<>coalesce(:model,''))))
                and exists(select 1 from members m where m.organisation_id=catalogue_index_scopes.organisation_id
                  and m.user_id=catalogue_index_scopes.member_id and m.status='active' and m.role in ('owner','salesperson'))
              returning fence"""), params).first()
            if not row:
                db.commit()
                return None
            db.execute(text("""insert into catalogue_index_snapshots(id,organisation_id,member_id,books_org_id,fence)
              values(cast(:snapshot as uuid),cast(:org as uuid),cast(:member as uuid),:books,:fence)"""),
              {**params, 'fence': row.fence})
            db.execute(text("""update catalogue_index_snapshots set state='failed',error_code='fenced_out'
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid) and books_org_id=:books
                and fence<:fence and state='building'"""), {**params, 'fence': row.fence})
            db.commit()
            return CatalogueLease(identity, token, row.fence)

    def _guard(self, db, scope, lease):
        row = db.execute(text("""select h.fence from catalogue_index_scopes h
          join catalogue_index_snapshots s on s.id=cast(:snapshot as uuid) and s.organisation_id=h.organisation_id
            and s.member_id=h.member_id and s.books_org_id=h.books_org_id and s.fence=h.fence and s.state='building'
          where h.organisation_id=cast(:org as uuid) and h.member_id=cast(:member as uuid) and h.books_org_id=:books
            and h.lease_token=cast(:token as uuid) and h.fence=:fence and h.lease_expires_at>now()
            and exists(select 1 from members m where m.organisation_id=h.organisation_id and m.user_id=h.member_id
              and m.status='active' and m.role in ('owner','salesperson')) for update of h"""),
          {**scope.parameters(), **lease.parameters()}).first()
        if not row:
            raise CatalogueLeaseLost('Catalogue synchronization lease expired or was superseded')

    def renew(self, scope, lease):
        with self.database_factory() as db:
            self._guard(db, scope, lease)
            db.execute(text("""update catalogue_index_scopes set lease_expires_at=now()+make_interval(secs=>:seconds),updated_at=now()
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid) and books_org_id=:books
                and fence=:fence and lease_token=cast(:token as uuid)"""),
              {**scope.parameters(), **lease.parameters(), 'seconds': _lease_seconds()})
            db.commit()

    def reusable_embeddings(self, scope, hashes, model):
        if not hashes or not model:
            return {}
        with self.database_factory() as db:
            rows = db.execute(text("""select distinct i.text_hash,i.embedding_data from catalogue_index_items i
              join catalogue_index_scopes h on h.organisation_id=i.organisation_id and h.member_id=i.member_id
                and h.books_org_id=i.books_org_id and h.active_snapshot_id=i.snapshot_id
              where i.organisation_id=cast(:org as uuid) and i.member_id=cast(:member as uuid) and i.books_org_id=:books
                and i.embedding_model=:model and i.embedding_data is not null
                and i.text_hash in(select jsonb_array_elements_text(cast(:hashes as jsonb)))"""),
              {**scope.parameters(), 'model': model, 'hashes': json.dumps(hashes)}).all()
            return {row.text_hash: row.embedding_data for row in rows if _valid_embedding(row.embedding_data)}

    def _gc(self, db, scope):
        # Cache data is reproducible. Keep the active snapshot and three recent
        # complete generations, with a ten-minute grace for in-flight readers.
        # Abandoned staging/failed generations expire after an hour.
        db.execute(text("""delete from catalogue_index_snapshots s
          where s.organisation_id=cast(:org as uuid) and s.member_id=cast(:member as uuid) and s.books_org_id=:books
            and s.id is distinct from(select active_snapshot_id from catalogue_index_scopes
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid) and books_org_id=:books)
            and ((s.state='complete' and s.completed_at<now()-interval '10 minutes'
                  and s.id not in(select id from catalogue_index_snapshots where organisation_id=cast(:org as uuid)
                    and member_id=cast(:member as uuid) and books_org_id=:books and state='complete'
                    order by completed_at desc limit 3))
              or (s.state='failed' and s.created_at<now()-interval '1 hour')
              or (s.state='building' and s.created_at<now()-interval '1 hour' and not exists(
                  select 1 from catalogue_index_scopes h where h.organisation_id=s.organisation_id and h.member_id=s.member_id
                    and h.books_org_id=s.books_org_id and h.fence=s.fence and h.lease_expires_at>now())))"""), scope.parameters())

    def stage(self, scope, lease, items, embeddings, model):
        with self.database_factory() as db:
            self._guard(db, scope, lease)
            rows = [{**scope.parameters(), **lease.parameters(), 'item': item['item_id'], 'name': item['name'], 'sku': item['sku'],
                     'data': json.dumps(item, ensure_ascii=False, default=str), 'search': _item_text(item),
                     'hash': _text_hash(item), 'embedding': json.dumps(embeddings.get(_text_hash(item))) if embeddings.get(_text_hash(item)) else None,
                     'model': model if embeddings.get(_text_hash(item)) else None} for item in items]
            if rows:
                db.execute(text("""insert into catalogue_index_items(snapshot_id,organisation_id,member_id,books_org_id,item_id,
                  name,sku,data,search_text,text_hash,embedding_data,embedding_model)
                  values(cast(:snapshot as uuid),cast(:org as uuid),cast(:member as uuid),:books,:item,:name,:sku,
                    cast(:data as jsonb),:search,:hash,cast(:embedding as jsonb),:model)
                  on conflict(snapshot_id,item_id) do update set name=excluded.name,sku=excluded.sku,data=excluded.data,
                    search_text=excluded.search_text,text_hash=excluded.text_hash,
                    embedding_data=excluded.embedding_data,embedding_model=excluded.embedding_model"""), rows)
                schema = self._capabilities(db).get('vector')
                dense = [row for row in rows if row['embedding']]
                if schema and dense:
                    db.execute(text(f'''update catalogue_index_items set embedding_vector=cast(:embedding as "{schema}".vector)
                      where snapshot_id=cast(:snapshot as uuid) and organisation_id=cast(:org as uuid)
                        and member_id=cast(:member as uuid) and books_org_id=:books and item_id=:item'''), dense)
            db.commit()

    def publish(self, scope, lease, *, item_count, page_count, semantic_status, model, observed_at=None):
        params = {**scope.parameters(), **lease.parameters(), 'items': item_count, 'pages': page_count,
                  'semantic': semantic_status, 'model': model, 'refresh': _refresh_seconds(), 'observed': observed_at}
        with self.database_factory() as db:
            self._guard(db, scope, lease)
            actual = db.execute(text("""select count(*) as count from catalogue_index_items
              where snapshot_id=cast(:snapshot as uuid) and organisation_id=cast(:org as uuid)
                and member_id=cast(:member as uuid) and books_org_id=:books"""), params).first()
            if actual.count != item_count or page_count < 1:
                raise ValueError('Incomplete catalogue snapshot')
            db.execute(text("""update catalogue_index_snapshots set state='complete',completed_at=now(),
              inventory_observed_at=coalesce(cast(:observed as timestamptz),created_at),item_count=:items,
              page_count=:pages,semantic_status=:semantic,embedding_model=:model
              where id=cast(:snapshot as uuid) and organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)
                and books_org_id=:books and fence=:fence and state='building'"""), params)
            db.execute(text("""update catalogue_index_scopes set active_snapshot_id=cast(:snapshot as uuid),lease_token=null,
              lease_expires_at=null,next_refresh_at=(select inventory_observed_at from catalogue_index_snapshots
                where id=cast(:snapshot as uuid))+make_interval(secs=>:refresh),
              next_semantic_retry_at=now()+make_interval(secs=>case when :semantic='ready' then :refresh else 60 end),
              requested_embedding_model=:model,last_error_code=null,updated_at=now()
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid) and books_org_id=:books
                and lease_token=cast(:token as uuid) and fence=:fence"""), params)
            self._gc(db, scope)
            db.commit()

    def fail(self, scope, lease, code):
        params = {**scope.parameters(), **lease.parameters(), 'code': str(code)[:80]}
        with self.database_factory() as db:
            db.execute(text("""update catalogue_index_snapshots set state='failed',error_code=:code
              where id=cast(:snapshot as uuid) and organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)
                and books_org_id=:books and fence=:fence and state='building'"""), params)
            db.execute(text("""update catalogue_index_scopes set lease_token=null,lease_expires_at=null,
              next_refresh_at=greatest(next_refresh_at,now()+interval '60 seconds'),
              next_semantic_retry_at=now()+interval '60 seconds',last_error_code=:code,updated_at=now()
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid) and books_org_id=:books
                and lease_token=cast(:token as uuid) and fence=:fence"""), params)
            self._gc(db, scope)
            db.commit()

    def read_snapshot_page(self, scope, snapshot_id, cursor='', *, limit=200):
        with self.database_factory() as db:
            rows = db.execute(text("""select i.data from catalogue_index_items i join catalogue_index_snapshots s
              on s.id=i.snapshot_id and s.organisation_id=i.organisation_id and s.member_id=i.member_id
                and s.books_org_id=i.books_org_id and s.state='complete'
              where i.organisation_id=cast(:org as uuid) and i.member_id=cast(:member as uuid) and i.books_org_id=:books
                and i.snapshot_id=cast(:snapshot as uuid) and i.item_id>:cursor order by i.item_id limit :limit"""),
              {**scope.parameters(), 'snapshot': snapshot_id, 'cursor': cursor, 'limit': limit}).all()
            return [dict(row.data) for row in rows]

    def lexical_search(self, scope, snapshot_id, queries, *, limit=48):
        groups = []
        with self.database_factory() as db:
            schema = self._capabilities(db).get('pg_trgm')
            for query in queries:
                params = {**scope.parameters(), 'snapshot': snapshot_id, 'query': query, 'limit': limit,
                          'contains': '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'}
                rows = db.execute(text("""select data,case when lower(sku)=lower(:query) then 10 else
                  ts_rank(search_vector,websearch_to_tsquery('simple',:query)) end as rank
                  from catalogue_index_items where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)
                    and books_org_id=:books and snapshot_id=cast(:snapshot as uuid)
                    and (lower(sku)=lower(:query) or search_vector@@websearch_to_tsquery('simple',:query)
                      or search_text ilike :contains escape '\\') order by rank desc,item_id limit :limit"""), params).all()
                candidates = [dict(row.data) for row in rows]
                if schema and len(candidates) < limit:
                    fuzzy = db.execute(text(f'''select data,"{schema}".word_similarity(lower(:query),lower(search_text)) as rank
                      from catalogue_index_items where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)
                        and books_org_id=:books and snapshot_id=cast(:snapshot as uuid)
                        and "{schema}".word_similarity(lower(:query),lower(search_text))>=0.35
                      order by rank desc,item_id limit :limit'''), params).all()
                    known = {item['item_id'] for item in candidates}
                    candidates.extend(dict(row.data) for row in fuzzy if row.data['item_id'] not in known)
                groups.append(candidates[:limit])
        return groups

    def semantic_search(self, scope, snapshot_id, vectors, model, *, limit=48):
        with self.database_factory() as db:
            schema = self._capabilities(db).get('vector')
            if not schema:
                return [[] for _ in vectors]
            version = re.match(r'(\d+)\.(\d+)', self._extension_versions.get('vector', '0.0'))
            iterative = version and tuple(map(int, version.groups())) >= (0, 8)
            if iterative:
                db.execute(text("set local hnsw.iterative_scan='strict_order'"))
                db.execute(text('set local hnsw.ef_search=200'))
            groups = []
            for vector in vectors:
                rows = db.execute(text(f'''select data from catalogue_index_items
                  where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)
                    and books_org_id=:books and snapshot_id=cast(:snapshot as uuid)
                    and embedding_model=:model and embedding_vector is not null
                  order by (embedding_vector OPERATOR("{schema}".<=>) cast(:vector as "{schema}".vector)){'' if iterative else ' + 0'}
                  limit :limit'''), {**scope.parameters(), 'snapshot': snapshot_id, 'vector': json.dumps(vector),
                                      'model': model, 'limit': limit}).all()
                groups.append([dict(row.data) for row in rows])
            return groups


def _store():
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = PostgresCatalogueStore()
    return _DEFAULT_STORE


def reciprocal_rank_fusion(groups, *, limit=240):
    scores, items = {}, {}
    for group in groups:
        seen = set()
        for rank, item in enumerate(group, 1):
            identity = item['item_id']
            if identity in seen:
                continue
            seen.add(identity)
            scores[identity] = scores.get(identity, 0.0) + 1.0 / (60 + rank)
            items.setdefault(identity, item)
    ordered = sorted(items, key=lambda identity: scores[identity], reverse=True)
    return [{**items[identity], 'retrieval_score': round(scores[identity], 8)} for identity in ordered[:limit]]


async def _page_embeddings(items, scope, lease, store, client, model):
    hashes = {_text_hash(item): item for item in items}
    vectors = await asyncio.to_thread(store.reusable_embeddings, scope, list(hashes), model)
    missing = [identity for identity in hashes if identity not in vectors]
    for offset in range(0, len(missing), _EMBEDDING_BATCH):
        await asyncio.to_thread(store.renew, scope, lease)
        batch = missing[offset:offset + _EMBEDDING_BATCH]
        try:
            if str(getattr(client, 'model_id', '')) != model:
                raise ValueError('Embedding model changed during synchronization')
            generated = await asyncio.wait_for(client.embed([_item_text(hashes[identity]) for identity in batch], query=False), timeout=45)
            if str(getattr(client, 'model_id', '')) != model:
                raise ValueError('Embedding model changed during synchronization')
            if len(generated) != len(batch) or not all(_valid_embedding(vector) for vector in generated):
                raise ValueError('Invalid catalogue embeddings')
        except Exception as error:
            raise CatalogueEmbeddingUnavailable(vectors) from error
        vectors.update(zip(batch, generated))
    return vectors


async def sync_catalogue(client, scope, lease, *, store=None, embedding_client=None, source_snapshot=None):
    store = store or _store()
    model = str(getattr(embedding_client, 'model_id', '') or '')
    seen_items, seen_pages, page_count = set(), set(), 0
    seen_facts, duplicate_evidence = {}, {}
    cursor = ''
    try:
        vector_supported = bool(embedding_client and model and getattr(embedding_client, 'dimensions', _DIMENSIONS) == _DIMENSIONS
                                and await asyncio.to_thread(store.vector_available))
        semantic = vector_supported
        semantic_status = 'ready' if semantic else 'unavailable'
        for page in range(1, _MAX_PAGES + 1):
            await asyncio.to_thread(store.renew, scope, lease)
            if source_snapshot:
                rows = await asyncio.to_thread(store.read_snapshot_page, scope, str(source_snapshot['snapshot_id']), cursor)
                cursor = rows[-1]['item_id'] if rows else cursor
                payload = {'items': rows, 'page_context': {'page': page,
                           'has_more_page': len(seen_items) + len(rows) < source_snapshot['item_count']}}
            else:
                payload = await asyncio.wait_for(client.search_books_items(scope.books_org_id, '', None, page), timeout=45)
            rows = payload.get('items', payload.get('products', payload.get('data'))) if isinstance(payload, dict) else None
            if not isinstance(rows, list) or payload.get('code') not in (None, 0, '0'):
                raise ValueError('Invalid catalogue page')
            page_context = payload.get('page_context') or {}
            if page_context.get('page') is not None and int(page_context['page']) != page:
                raise ValueError('Catalogue page identity mismatch')
            items = []
            for raw in rows:
                item = normalize_item(raw)
                if item is None:
                    raise ValueError('Catalogue page contains an invalid item')
                item['currency_code'] = item.get('currency_code') or payload.get('currency_code')
                item['catalogue_source'] = 'zoho'
                facts = {key: item.get(key) for key in ('name','sku','rate','stock_on_hand','available_stock','status','can_be_sold')}
                facts['inventory_text_hash'] = _text_hash(item)
                prior = seen_facts.setdefault(item['item_id'], facts)
                changed = duplicate_evidence.setdefault(item['item_id'], {})
                for key, actual in facts.items():
                    if prior.get(key) != actual:
                        values = changed.setdefault(key, [prior.get(key)])
                        if actual not in values and len(values) < 6:
                            values.append(actual)
                if changed:
                    item['catalogue_conflicts'] = [f'Inventory rows disagree on {"catalogue description or attributes" if key == "inventory_text_hash" else key.replace("_", " ")}.' for key in changed]
                    item['duplicate_source_evidence'] = {key: list(values) for key, values in changed.items()}
                    item.update(price_status='confirmation_required', availability_status='needs_review')
                items.append(item)
            signature = hashlib.sha256(json.dumps([item['item_id'] for item in items], separators=(',', ':')).encode()).hexdigest()
            if items and signature in seen_pages:
                raise ValueError('Repeated catalogue page')
            seen_pages.add(signature)
            vectors = {}
            if semantic:
                try:
                    vectors = await _page_embeddings(items, scope, lease, store, embedding_client, model)
                except CatalogueLeaseLost:
                    raise
                except CatalogueEmbeddingUnavailable as error:
                    vectors = error.vectors
                    semantic = False
                    semantic_status = 'unavailable'
                    log.warning('catalogue_embeddings_unavailable')
            elif vector_supported:
                vectors = await asyncio.to_thread(store.reusable_embeddings, scope, [_text_hash(item) for item in items], model)
            await asyncio.to_thread(store.stage, scope, lease, items, vectors, model)
            seen_items.update(item['item_id'] for item in items)
            page_count = page
            has_more = page_context.get('has_more_page', payload.get('has_more'))
            if has_more is None:
                # Missing provider pagination metadata cannot prove that a
                # short page is complete. Continue until an explicit empty page.
                has_more = bool(rows)
            if not isinstance(has_more, bool):
                raise ValueError('Invalid catalogue pagination state')
            if not rows and has_more:
                raise ValueError('Empty catalogue page claims more data')
            if not has_more:
                if source_snapshot and len(seen_items) != source_snapshot['item_count']:
                    raise ValueError('Incomplete semantic source snapshot')
                current = await asyncio.to_thread(client.status)
                if not current.get('connected') or str(current.get('organization_id')) != scope.books_org_id:
                    raise PermissionError('Selected Books organization changed during synchronization')
                await asyncio.to_thread(store.publish, scope, lease, item_count=len(seen_items), page_count=page_count,
                                        semantic_status=semantic_status, model=model or None,
                                        observed_at=(source_snapshot.get('inventory_observed_at') or source_snapshot.get('completed_at')) if source_snapshot else None)
                return {'snapshot_id': lease.snapshot_id, 'item_count': len(seen_items), 'page_count': page_count,
                        'semantic': semantic_status}
        raise ValueError('Catalogue pagination limit exceeded')
    except BaseException as error:
        code = 'cancelled' if isinstance(error, asyncio.CancelledError) else type(error).__name__
        try:
            await asyncio.to_thread(store.fail, scope, lease, code)
        except Exception:
            log.warning('catalogue_sync_failure_marker_unavailable')
        if isinstance(error, asyncio.CancelledError):
            raise
        log.warning('catalogue_sync_failed exception_type=%s', type(error).__name__)
        return {'snapshot_id': lease.snapshot_id, 'error': code, 'semantic': 'unavailable'}


async def close_catalogue_tasks():
    tasks = list(_SYNC_TASKS.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _SYNC_TASKS.clear()


async def retrieve_catalogue(client, context, embedding_client=None, *, store=None):
    """Return scoped indexed candidates and start at most one due background sync."""
    status = await asyncio.to_thread(client.status)
    queries, deferred, searched = _query_plan(context)
    coverage = {'deferred_requirements': deferred, 'searched_requirements': searched, 'retrieval_exhaustive': False}
    if not status.get('connected') or not status.get('organization_id'):
        return {**coverage, 'items': [], 'status': 'unavailable', 'catalogue_status': 'unavailable', 'snapshot': None,
                'notices': [{'code': 'catalogue_not_connected', 'message': 'Connect Zoho Books and select an organization for product suggestions.'}]}
    if status.get('catalogue_source') == 'local-demo':
        payload = await client.search_books_items(status['organization_id'], '', None, 1)
        items = [item for raw in payload.get('items') or [] if (item := normalize_item(raw)) is not None]
        return {**coverage, 'items': items, 'status': 'ready', 'catalogue_status': 'ready', 'deferred_requirements': [], 'retrieval_exhaustive': True,
                'snapshot': {'id': 'local-demo', 'item_count': len(items), 'age_seconds': 0, 'semantic': 'unavailable', 'sync_in_progress': False},
                'retrieval_mode': 'sample', 'notices': []}
    scope = _scope(status)
    store = store or _store()
    try:
        state = await asyncio.to_thread(store.state, scope)
        task = _SYNC_TASKS.get(scope.key)
        if task and task.done():
            _SYNC_TASKS.pop(scope.key, None)
            task = None
        now = _utcnow()
        next_refresh = state.get('next_refresh_at')
        inventory_due = not state or next_refresh is None or next_refresh <= now
        observed = state.get('inventory_observed_at') or state.get('completed_at')
        age = max(0, (now - observed).total_seconds()) if observed else None
        inventory_fresh = age is not None and age < _refresh_seconds() and not state.get('last_error_code')
        model = str(getattr(embedding_client, 'model_id', '') or '')
        semantic_needed = bool(embedding_client and model and state.get('snapshot_id') and (
            state.get('semantic_status') != 'ready' or state.get('embedding_model') != model))
        next_semantic = state.get('next_semantic_retry_at')
        semantic_due = semantic_needed and inventory_fresh and (
            state.get('requested_embedding_model') != model or next_semantic is None or next_semantic <= now)
        if semantic_due:
            semantic_due = await asyncio.to_thread(store.vector_available)
        if (inventory_due or semantic_due) and task is None and len(_SYNC_TASKS) < 2:
            lease = await asyncio.to_thread(store.claim, scope, semantic_only=not inventory_due, model=model or None)
            if lease:
                task = asyncio.create_task(sync_catalogue(client, scope, lease, store=store, embedding_client=embedding_client,
                                                         source_snapshot=state if not inventory_due else None))
                _SYNC_TASKS[scope.key] = task
                task.add_done_callback(lambda completed, key=scope.key: _SYNC_TASKS.pop(key, None) if _SYNC_TASKS.get(key) is completed else None)
            else:
                state = await asyncio.to_thread(store.state, scope)
                observed = state.get('inventory_observed_at') or state.get('completed_at')
                age = max(0, (now - observed).total_seconds()) if observed else None
        stale = bool(state.get('last_error_code')) or age is not None and age >= _refresh_seconds()
        syncing = bool(task) or bool(state.get('lease_expires_at') and state['lease_expires_at'] > now)
        snapshot_id = state.get('snapshot_id')
        if not snapshot_id:
            label = 'syncing' if syncing else 'unavailable'
            return {**coverage, 'items': [], 'status': label, 'catalogue_status': label,
                    'snapshot': {'id': None, 'item_count': 0, 'age_seconds': None, 'semantic': 'pending', 'sync_in_progress': syncing},
                    'notices': [{'code': 'catalogue_syncing' if syncing else 'catalogue_index_unavailable',
                                 'message': 'Inventory is synchronizing in the background.' if syncing else 'Inventory suggestions are temporarily unavailable.'}]}
        lexical = await asyncio.to_thread(store.lexical_search, scope, str(snapshot_id), queries)
        dense, semantic_status, mode = [[] for _ in queries], 'unavailable', 'lexical'
        if embedding_client and state.get('embedding_model') != model:
            semantic_status = 'pending'
        if embedding_client and model and state.get('semantic_status') == 'ready' and state.get('embedding_model') == model and queries:
            try:
                vectors = []
                for offset in range(0, len(queries), _EMBEDDING_BATCH):
                    if str(getattr(embedding_client, 'model_id', '')) != model:
                        raise ValueError('Embedding model changed during retrieval')
                    vectors.extend(await asyncio.wait_for(embedding_client.embed(queries[offset:offset + _EMBEDDING_BATCH], query=True), timeout=10))
                if len(vectors) != len(queries) or not all(_valid_embedding(vector) for vector in vectors):
                    raise ValueError('Invalid query embeddings')
                if str(getattr(embedding_client, 'model_id', '')) != model:
                    raise ValueError('Embedding model changed during retrieval')
                dense = await asyncio.to_thread(store.semantic_search, scope, str(snapshot_id), vectors, model)
                mode, semantic_status = 'hybrid', 'ready'
            except Exception:
                semantic_status = 'unavailable'
                log.warning('catalogue_semantic_query_unavailable')
        per_query = [reciprocal_rank_fusion([left, right], limit=96) for left, right in zip(lexical, dense)]
        candidates, known = [], set()
        for depth in range(96):
            for group in per_query:
                if depth < len(group) and group[depth]['item_id'] not in known:
                    item = {**group[depth], '_catalogue_stale': stale, 'catalogue_snapshot_id': str(snapshot_id),
                            'currency_code': group[depth].get('currency_code') or status.get('currency')}
                    candidates.append(normalize_item(item))
                    known.add(item['item_id'])
        notices = []
        if stale:
            notices.append({'code': 'catalogue_stale', 'message': 'Inventory is refreshing. Confirm current prices and availability before completing the cart.'})
        if embedding_client and semantic_status != 'ready':
            notices.append({'code': 'catalogue_semantic_unavailable', 'message': 'Semantic search is temporarily unavailable; catalogue text search remains available.'})
        if deferred:
            notices.append({'code': 'catalogue_query_limit', 'message': 'Additional product lookups are pending. Their requirements are preserved and have not been marked unavailable.'})
        label = 'stale' if stale else 'ready'
        return {**coverage, 'items': candidates[:240], 'status': label, 'catalogue_status': label, 'retrieval_mode': mode,
                'snapshot': {'id': str(snapshot_id), 'item_count': state.get('item_count', 0), 'age_seconds': age,
                             'semantic': semantic_status, 'sync_in_progress': syncing}, 'notices': notices}
    except Exception as error:
        log.warning('catalogue_index_read_failed exception_type=%s', type(error).__name__)
        return {**coverage, 'items': [], 'status': 'unavailable', 'catalogue_status': 'unavailable', 'snapshot': None,
                'notices': [{'code': 'catalogue_index_unavailable', 'message': 'Inventory indexing is unavailable. Transcript updates continue.'}]}
