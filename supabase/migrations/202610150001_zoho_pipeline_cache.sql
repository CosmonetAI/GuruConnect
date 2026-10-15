create table if not exists public.zoho_pipeline_records (
  record_id text primary key,
  pipeline_id text not null,
  data jsonb not null default '{}'::jsonb,
  modified_time timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists zoho_pipeline_records_modified_idx on public.zoho_pipeline_records(modified_time desc nulls last);
create table if not exists public.zoho_pipeline_sync (
  tenant_key text primary key default 'guru-group',
  synced_at timestamptz not null default now()
);
alter table public.zoho_pipeline_records enable row level security;
alter table public.zoho_pipeline_sync enable row level security;
