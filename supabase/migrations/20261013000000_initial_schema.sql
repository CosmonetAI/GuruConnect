create extension if not exists pgcrypto;

create table if not exists public.zoho_connections (
  id uuid primary key default gen_random_uuid(), tenant_key text not null unique default 'guru-group',
  api_domain text not null default 'https://www.zohoapis.com', access_token text, refresh_token text,
  client_id text, client_secret text, redirect_uri text, expires_at timestamptz, organization_id text, scope text, status text not null default 'not_connected',
  last_error text, updated_at timestamptz not null default now(), created_at timestamptz not null default now()
);
alter table public.zoho_connections add column if not exists client_id text;
alter table public.zoho_connections add column if not exists client_secret text;
alter table public.zoho_connections add column if not exists redirect_uri text;
create table if not exists public.customers (
  id uuid primary key default gen_random_uuid(), name text not null, email text, phone text,
  normalized_email text, normalized_phone text, bigin_contact_id text, books_contact_id text,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
-- The earlier core migration may have created public.customers with only the
-- workspace fields. Add the legacy integration columns before indexing them.
alter table public.customers add column if not exists email text;
alter table public.customers add column if not exists phone text;
alter table public.customers add column if not exists normalized_email text;
alter table public.customers add column if not exists normalized_phone text;
alter table public.customers add column if not exists bigin_contact_id text;
alter table public.customers add column if not exists books_contact_id text;
alter table public.customers add column if not exists updated_at timestamptz not null default now();
create unique index if not exists customers_phone_unique on public.customers(normalized_phone) where normalized_phone is not null;
create unique index if not exists customers_email_unique on public.customers(normalized_email) where normalized_email is not null;
create table if not exists public.chat_sessions (
  id uuid primary key default gen_random_uuid(), customer_id uuid not null references public.customers(id) on delete cascade,
  title text not null default 'New enquiry', language text, status text not null default 'open', last_intent text,
  context jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
alter table public.chat_sessions add column if not exists context jsonb not null default '{}'::jsonb;
create table if not exists public.chat_messages (
  id uuid primary key default gen_random_uuid(), session_id uuid not null references public.chat_sessions(id) on delete cascade,
  role text not null check (role in ('user','assistant','system')), content text not null, intent text,
  metadata jsonb not null default '{}'::jsonb, created_at timestamptz not null default now()
);
create table if not exists public.enquiries (
  id uuid primary key default gen_random_uuid(), session_id uuid not null references public.chat_sessions(id) on delete cascade,
  customer_id uuid not null references public.customers(id) on delete cascade, source text not null default 'chat',
  status text not null default 'new', bigin_pipeline_id text, books_estimate_id text, books_estimate_number text,
  quote_status text, extracted_data jsonb not null default '{}'::jsonb, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists public.integration_audit_log (
  id uuid primary key default gen_random_uuid(), provider text not null, operation text not null,
  correlation_id text, external_id text, status_code int, retry_count int not null default 0, error_message text,
  created_at timestamptz not null default now()
);
alter table public.zoho_connections enable row level security;
alter table public.customers enable row level security;
alter table public.chat_sessions enable row level security;
alter table public.chat_messages enable row level security;
alter table public.enquiries enable row level security;
alter table public.integration_audit_log enable row level security;
