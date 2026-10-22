-- Keep the most recently observed Zoho records per connected member and
-- organization. The backend uses this as the default page before it calls
-- Zoho again for an explicit search or pagination request.
create table if not exists public.zoho_recent_records (
  organisation_id uuid not null references public.organisations(id),
  member_id uuid not null references auth.users(id),
  zoho_organization_id text not null default '',
  dataset text not null,
  record_key text not null,
  data jsonb not null default '{}'::jsonb,
  last_seen_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (organisation_id, member_id, zoho_organization_id, dataset, record_key)
);

create index if not exists zoho_recent_records_scope_idx
  on public.zoho_recent_records(organisation_id, member_id, zoho_organization_id, dataset, last_seen_at desc);

alter table public.zoho_recent_records enable row level security;

-- The original pipeline cache predates member-scoped connections and used
-- record_id alone as its primary key. Remove any legacy unscoped rows and
-- make the cache key tenant-safe before new syncs write to it.
delete from public.zoho_pipeline_records
 where organisation_id is null or member_id is null;

alter table public.zoho_pipeline_records
  alter column organisation_id set not null,
  alter column member_id set not null;

alter table public.zoho_pipeline_records
  drop constraint if exists zoho_pipeline_records_pkey;

alter table public.zoho_pipeline_records
  add constraint zoho_pipeline_records_pkey primary key (organisation_id, member_id, record_id);
