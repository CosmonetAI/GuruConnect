-- Derived, member-private inventory snapshots. A failed or fenced-out sync
-- never replaces the previous complete snapshot.
create schema if not exists extensions;
create extension if not exists pg_trgm with schema extensions;

create table if not exists public.catalogue_index_scopes (
  organisation_id uuid not null,
  member_id uuid not null,
  books_org_id text not null check(length(books_org_id) between 1 and 128),
  active_snapshot_id uuid,
  lease_token uuid,
  fence bigint not null default 0 check(fence>=0),
  lease_expires_at timestamptz,
  next_refresh_at timestamptz not null default 'epoch',
  next_semantic_retry_at timestamptz not null default 'epoch',
  requested_embedding_model text,
  last_error_code text,
  updated_at timestamptz not null default now(),
  primary key(organisation_id,member_id,books_org_id),
  foreign key(organisation_id,member_id) references public.members(organisation_id,user_id) on delete cascade
);

create table if not exists public.catalogue_index_snapshots (
  id uuid primary key,
  organisation_id uuid not null,
  member_id uuid not null,
  books_org_id text not null,
  fence bigint not null check(fence>0),
  state text not null default 'building' check(state in ('building','complete','failed')),
  item_count integer not null default 0 check(item_count>=0),
  page_count integer not null default 0 check(page_count>=0),
  semantic_status text not null default 'unavailable' check(semantic_status in ('pending','ready','unavailable')),
  embedding_model text,
  error_code text,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  inventory_observed_at timestamptz,
  unique(id,organisation_id,member_id,books_org_id),
  foreign key(organisation_id,member_id,books_org_id)
    references public.catalogue_index_scopes(organisation_id,member_id,books_org_id) on delete cascade,
  check((state='complete') = (completed_at is not null))
);

do $$ begin
  if not exists(select 1 from pg_constraint where conname='catalogue_index_active_snapshot_scope_fk') then
    alter table public.catalogue_index_scopes add constraint catalogue_index_active_snapshot_scope_fk
      foreign key(active_snapshot_id,organisation_id,member_id,books_org_id)
      references public.catalogue_index_snapshots(id,organisation_id,member_id,books_org_id);
  end if;
end $$;

create table if not exists public.catalogue_index_items (
  snapshot_id uuid not null,
  organisation_id uuid not null,
  member_id uuid not null,
  books_org_id text not null,
  item_id text not null,
  name text not null,
  sku text not null default '',
  data jsonb not null,
  search_text text not null,
  search_vector tsvector generated always as (to_tsvector('pg_catalog.simple'::regconfig,search_text)) stored,
  text_hash text not null check(text_hash ~ '^[a-f0-9]{64}$'),
  embedding_data jsonb,
  embedding_model text,
  primary key(snapshot_id,item_id),
  foreign key(snapshot_id,organisation_id,member_id,books_org_id)
    references public.catalogue_index_snapshots(id,organisation_id,member_id,books_org_id) on delete cascade
);
create index if not exists catalogue_index_items_scope_idx
  on public.catalogue_index_items(organisation_id,member_id,books_org_id,snapshot_id);
create index if not exists catalogue_index_items_fts_idx on public.catalogue_index_items using gin(search_vector);
create index if not exists catalogue_index_items_sku_idx on public.catalogue_index_items(snapshot_id,lower(sku));
create index if not exists catalogue_index_items_embedding_reuse_idx
  on public.catalogue_index_items(snapshot_id,text_hash,embedding_model) where embedding_data is not null;

-- Respect the extension's existing schema instead of moving a shared extension.
do $$ declare extension_schema text; begin
  select n.nspname into extension_schema from pg_extension e join pg_namespace n on n.oid=e.extnamespace where e.extname='pg_trgm';
  execute format('create index if not exists catalogue_index_items_trgm_idx on public.catalogue_index_items using gin(search_text %I.gin_trgm_ops)',extension_schema);
end $$;

-- Semantic search is optional. Lexical snapshots remain usable on PostgreSQL
-- installations without pgvector. The configured embedding client uses 384 dimensions.
do $$ declare extension_schema text; begin
  begin
    create extension if not exists vector with schema extensions;
  exception when undefined_file or feature_not_supported or insufficient_privilege then
    raise notice 'pgvector unavailable: catalogue retrieval will use lexical indexes';
  end;
  select n.nspname into extension_schema from pg_extension e join pg_namespace n on n.oid=e.extnamespace where e.extname='vector';
  if extension_schema is not null then
    execute format('alter table public.catalogue_index_items add column if not exists embedding_vector %I.vector(384)',extension_schema);
    begin
      execute format('create index if not exists catalogue_index_items_embedding_hnsw_idx on public.catalogue_index_items using hnsw(embedding_vector %I.vector_cosine_ops)',extension_schema);
    exception when undefined_object or feature_not_supported then
      raise notice 'HNSW unavailable: catalogue retrieval will use exact pgvector distance search';
    end;
  end if;
end $$;

alter table public.catalogue_index_scopes enable row level security;
alter table public.catalogue_index_snapshots enable row level security;
alter table public.catalogue_index_items enable row level security;
revoke all on public.catalogue_index_scopes,public.catalogue_index_snapshots,public.catalogue_index_items from anon,authenticated;
grant all on public.catalogue_index_scopes,public.catalogue_index_snapshots,public.catalogue_index_items to service_role;
