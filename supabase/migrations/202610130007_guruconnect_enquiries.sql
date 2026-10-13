-- GuruConnect sales metadata is independent from provider jobs. A finalized
-- recording can be attached once, and idempotent saves never schedule inference.
begin;

-- The private request ledger preserves opaque create keys without returning
-- them in normal conversation/history responses. Legacy callers may omit keys.
create table recorder_private.conversation_requests (
  organisation_id uuid not null,
  owner_id uuid not null,
  idempotency_key text not null check (idempotency_key ~ '^[A-Za-z0-9_.:-]{8,128}$'),
  request_hash text not null check (request_hash ~ '^[a-f0-9]{64}$'),
  conversation_id uuid not null unique,
  created_at timestamptz not null default now(),
  primary key (organisation_id,owner_id,idempotency_key),
  foreign key (conversation_id,organisation_id,owner_id)
    references public.conversations(id,organisation_id,owner_id)
);
revoke all on table recorder_private.conversation_requests from public,anon,authenticated;
grant usage on schema recorder_private to service_role;
grant all on table recorder_private.conversation_requests to service_role;

create table public.guruconnect_enquiries (
  id uuid primary key default gen_random_uuid(),
  enquiry_number bigint generated always as identity unique,
  organisation_id uuid not null,
  owner_id uuid not null,
  conversation_id uuid unique,
  idempotency_key text not null check (idempotency_key ~ '^[A-Za-z0-9_.:-]{8,128}$'),
  payload_hash text not null check (payload_hash ~ '^[a-f0-9]{64}$'),
  payload jsonb not null check (jsonb_typeof(payload)='object' and octet_length(payload::text)<=100000),
  customer_name text not null check (length(customer_name) between 2 and 200),
  phone text not null check (length(phone) between 7 and 20),
  channel text not null check (length(channel) between 1 and 80),
  branch text not null check (length(branch) between 1 and 160),
  created_at timestamptz not null default now(),
  foreign key (organisation_id,owner_id) references public.members(organisation_id,user_id),
  foreign key (conversation_id,organisation_id,owner_id)
    references public.conversations(id,organisation_id,owner_id),
  unique (organisation_id,owner_id,idempotency_key)
);

create index guruconnect_enquiries_workspace_recent
  on public.guruconnect_enquiries(organisation_id,created_at desc,id desc);
create index guruconnect_enquiries_owner_recent
  on public.guruconnect_enquiries(organisation_id,owner_id,created_at desc,id desc);

-- Only the authenticated backend owns validation, ownership and idempotency.
-- Browser access via the Supabase REST table endpoints remains denied.
alter table public.guruconnect_enquiries enable row level security;
revoke all on table public.guruconnect_enquiries from public,anon,authenticated;
revoke all on sequence public.guruconnect_enquiries_enquiry_number_seq from public,anon,authenticated;
grant all on table public.guruconnect_enquiries to service_role;
grant usage,select on sequence public.guruconnect_enquiries_enquiry_number_seq to service_role;

commit;
