-- Visitors who left without a started conversation remain visible to sales for
-- fourteen days. The row stores a registration snapshot so later provider
-- changes cannot erase the follow-up context.
begin;

create table if not exists public.guruconnect_missed_visitors (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null,
  member_id uuid not null,
  visit_id uuid not null,
  registered_by uuid not null,
  assigned_agent_id uuid,
  visit_number bigint not null,
  customer_name text not null check (length(customer_name) between 2 and 150),
  mobile_number text not null check (length(mobile_number) between 7 and 20),
  email text not null default '',
  purpose text not null,
  payload jsonb not null check (jsonb_typeof(payload) = 'object' and octet_length(payload::text) <= 40000),
  check_in_at timestamptz not null,
  missed_at timestamptz not null default now(),
  expires_at timestamptz not null default (now() + interval '14 days'),
  created_at timestamptz not null default now(),
  unique (organisation_id, visit_id),
  foreign key (organisation_id, member_id) references public.members(organisation_id, user_id),
  foreign key (organisation_id, registered_by) references public.members(organisation_id, user_id),
  foreign key (organisation_id, assigned_agent_id) references public.members(organisation_id, user_id),
  foreign key (visit_id, organisation_id) references public.guruconnect_walkin_visits(id, organisation_id)
);

create index if not exists guruconnect_missed_visitors_member_recent
  on public.guruconnect_missed_visitors(organisation_id, member_id, missed_at desc);
create index if not exists guruconnect_missed_visitors_expiry
  on public.guruconnect_missed_visitors(expires_at);

alter table public.guruconnect_missed_visitors enable row level security;
revoke all on table public.guruconnect_missed_visitors from public, anon, authenticated;
grant select, insert, delete on table public.guruconnect_missed_visitors to service_role;

commit;
