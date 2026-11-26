begin;

-- TeleCRM deliveries are kept separately from browser-created sessions. The
-- normalized columns make the future activity UI cheap to query, while payload
-- preserves every optional provider field for later mapping and replay.
create table if not exists public.telecrm_events (
  id uuid primary key default gen_random_uuid(),
  event_type text not null check (event_type in (
    'call_recording_available', 'whatsapp_message_received',
    'whatsapp_message_status'
  )),
  external_id text,
  dedupe_key text not null unique,
  call_id text,
  message_id text,
  conversation_id text,
  lead_id text,
  customer_phone text not null,
  normalized_customer_phone text,
  customer_name text,
  agent_id text,
  agent_name text,
  agent_email text,
  direction text,
  recording_url text,
  call_started_at text,
  call_ended_at text,
  duration_seconds text,
  call_status text,
  call_summary text,
  call_transcript text,
  call_outcome text,
  customer_requirements text,
  customer_sentiment text,
  next_action text,
  follow_up_required text,
  message_type text,
  message_text text,
  media_url text,
  received_at text,
  sent_at text,
  delivered_at text,
  read_at text,
  failed_at text,
  status text,
  failure_reason text,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  created_at timestamptz not null default now(),
  check (event_type <> 'call_recording_available' or (nullif(recording_url, '') is not null and nullif(customer_phone, '') is not null and nullif(call_started_at, '') is not null and nullif(call_ended_at, '') is not null and nullif(duration_seconds, '') is not null)),
  check (event_type <> 'whatsapp_message_received' or (nullif(customer_phone, '') is not null and nullif(message_text, '') is not null and nullif(received_at, '') is not null)),
  check (event_type <> 'whatsapp_message_status' or (nullif(customer_phone, '') is not null and nullif(status, '') is not null))
);

create index if not exists telecrm_events_created_idx
  on public.telecrm_events (created_at desc);
create index if not exists telecrm_events_phone_created_idx
  on public.telecrm_events (normalized_customer_phone, created_at desc);
create index if not exists telecrm_events_type_created_idx
  on public.telecrm_events (event_type, created_at desc);
create index if not exists telecrm_events_call_id_idx
  on public.telecrm_events (call_id) where call_id is not null;
create index if not exists telecrm_events_message_id_idx
  on public.telecrm_events (message_id) where message_id is not null;

alter table public.telecrm_events enable row level security;
revoke all on table public.telecrm_events from public, anon, authenticated;
grant all on table public.telecrm_events to service_role;

commit;
