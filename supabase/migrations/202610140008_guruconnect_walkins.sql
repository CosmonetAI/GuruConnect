-- Workspace-bound walk-in metadata. The authenticated API is the only writer;
-- Supabase browser REST and Realtime access are denied for customer details.
begin;

create table public.guruconnect_walkin_agents (
  organisation_id uuid not null,
  user_id uuid not null,
  availability text not null default 'Available' check (availability in ('Available','Busy','Unavailable')),
  updated_at timestamptz not null default now(),
  primary key (organisation_id,user_id),
  foreign key (organisation_id,user_id) references public.members(organisation_id,user_id)
);

create table public.guruconnect_walkin_visits (
  id uuid primary key default gen_random_uuid(),
  visit_number bigint generated always as identity unique,
  organisation_id uuid not null,
  registered_by uuid not null,
  assigned_agent_id uuid,
  idempotency_key text not null check (idempotency_key ~ '^[A-Za-z0-9_.:-]{8,128}$'),
  payload_hash text not null check (payload_hash ~ '^[a-f0-9]{64}$'),
  payload jsonb not null check (jsonb_typeof(payload)='object' and octet_length(payload::text)<=40000),
  customer_name text not null check (length(customer_name) between 2 and 150),
  mobile_number text not null check (length(mobile_number) between 7 and 20),
  purpose text not null check (purpose in ('Product enquiry','New business enquiry','Product demonstration','Pricing or quotation request','Existing order enquiry','Service or support request','Meeting with a specific employee','Follow-up meeting','Other')),
  status text not null check (status in ('Registered','Waiting for Assignment','Assigned','Waiting for Sales Agent','In Discussion','Requirements Captured','Follow-up Required','Converted to Enquiry','Completed','Cancelled')),
  assignment_note text not null default '' check (length(assignment_note)<=2000),
  requirements jsonb not null default '{}' check (jsonb_typeof(requirements)='object' and octet_length(requirements::text)<=50000),
  requirements_updated_at timestamptz,
  converted_enquiry_id uuid,
  check_in_at timestamptz not null default now(),
  assigned_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id,organisation_id),
  unique (organisation_id,registered_by,idempotency_key),
  foreign key (organisation_id,registered_by) references public.members(organisation_id,user_id),
  foreign key (organisation_id,assigned_agent_id) references public.members(organisation_id,user_id),
  check ((assigned_agent_id is null) = (assigned_at is null)),
  check (status not in ('Assigned','Waiting for Sales Agent','In Discussion','Requirements Captured','Follow-up Required','Converted to Enquiry') or assigned_agent_id is not null),
  check (status <> 'Converted to Enquiry' or converted_enquiry_id is not null)
);

alter table public.guruconnect_enquiries add constraint guruconnect_enquiries_workspace_identity unique (id,organisation_id);
alter table public.guruconnect_walkin_visits add constraint guruconnect_walkin_conversion_workspace
  foreign key (converted_enquiry_id,organisation_id) references public.guruconnect_enquiries(id,organisation_id);
-- Different retry keys and agents cannot create two enquiries for one visit.
create unique index guruconnect_enquiry_walkin_source_unique
  on public.guruconnect_enquiries(organisation_id,(payload->>'sourceEventId'))
  where payload->>'sourceEventId' like 'walkin:%';
create index guruconnect_walkins_workspace_recent on public.guruconnect_walkin_visits(organisation_id,check_in_at desc,id desc);
create index guruconnect_walkins_agent_recent on public.guruconnect_walkin_visits(organisation_id,assigned_agent_id,check_in_at desc,id desc);

create table public.guruconnect_walkin_history (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null,
  visit_id uuid not null,
  actor_id uuid not null,
  event_type text not null check (length(event_type) between 1 and 80),
  old_value text check (length(old_value)<=200),
  new_value text check (length(new_value)<=200),
  notes text not null default '' check (length(notes)<=2000),
  created_at timestamptz not null default now(),
  foreign key (visit_id,organisation_id) references public.guruconnect_walkin_visits(id,organisation_id),
  foreign key (organisation_id,actor_id) references public.members(organisation_id,user_id)
);
create index guruconnect_walkin_history_recent on public.guruconnect_walkin_history(organisation_id,created_at desc,id desc);
create index guruconnect_walkin_visit_history_recent on public.guruconnect_walkin_history(visit_id,created_at desc,id desc);

-- Association is durable metadata rather than a free-form conversation title.
create table public.guruconnect_walkin_recordings (
  organisation_id uuid not null,
  visit_id uuid not null,
  conversation_id uuid primary key,
  owner_id uuid not null,
  created_at timestamptz not null default now(),
  foreign key (visit_id,organisation_id) references public.guruconnect_walkin_visits(id,organisation_id),
  foreign key (conversation_id,organisation_id,owner_id) references public.conversations(id,organisation_id,owner_id)
);
create index guruconnect_walkin_recordings_visit on public.guruconnect_walkin_recordings(organisation_id,visit_id,created_at desc);

alter table public.guruconnect_walkin_agents enable row level security;
alter table public.guruconnect_walkin_visits enable row level security;
alter table public.guruconnect_walkin_history enable row level security;
alter table public.guruconnect_walkin_recordings enable row level security;
revoke all on table public.guruconnect_walkin_agents,public.guruconnect_walkin_visits,public.guruconnect_walkin_history from public,anon,authenticated;
revoke all on table public.guruconnect_walkin_recordings from public,anon,authenticated;
revoke all on sequence public.guruconnect_walkin_visits_visit_number_seq from public,anon,authenticated;
grant all on table public.guruconnect_walkin_agents,public.guruconnect_walkin_visits to service_role;
-- Audit history is append-only even for the runtime role.
grant select,insert on table public.guruconnect_walkin_history to service_role;
grant select,insert on table public.guruconnect_walkin_recordings to service_role;
grant usage,select on sequence public.guruconnect_walkin_visits_visit_number_seq to service_role;

commit;
