create extension if not exists pgcrypto;

create sequence if not exists public.walkin_visit_number_seq;

create or replace function public.walkin_current_role()
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(auth.jwt() -> 'app_metadata' ->> 'role', '');
$$;

create or replace function public.walkin_is_staff()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select public.walkin_current_role() in ('reception', 'sales_agent', 'manager', 'admin');
$$;

create table if not exists public.walkin_sales_agents (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null,
  department text,
  specialization text,
  availability text not null default 'Unavailable'
    check (availability in ('Available', 'Busy', 'Unavailable')),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.walkin_visits (
  id uuid primary key default gen_random_uuid(),
  visit_number text not null unique,
  customer_name text not null check (length(trim(customer_name)) between 2 and 150),
  mobile_number text not null check (length(regexp_replace(mobile_number, '[^0-9]', '', 'g')) between 7 and 15),
  email text check (email is null or email ~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'),
  company_name text,
  customer_type text not null check (customer_type in ('Individual', 'Business', 'Existing Customer')),
  visitor_count integer check (visitor_count is null or visitor_count between 0 and 100),
  visitor_reference text,
  purpose text not null check (purpose in (
    'Product enquiry', 'New business enquiry', 'Product demonstration',
    'Pricing or quotation request', 'Existing order enquiry', 'Service or support request',
    'Meeting with a specific employee', 'Follow-up meeting', 'Other'
  )),
  purpose_description text,
  product_or_service text,
  department text,
  requested_employee text,
  appointment_status text not null default 'Walk-in'
    check (appointment_status in ('Walk-in', 'Scheduled Appointment')),
  appointment_reference text,
  registered_by uuid not null default auth.uid() references auth.users(id),
  assigned_agent_id uuid references public.walkin_sales_agents(user_id),
  assignment_note text,
  status text not null default 'Waiting for Assignment'
    check (status in (
      'Registered', 'Waiting for Assignment', 'Assigned', 'Waiting for Sales Agent',
      'In Discussion', 'Requirements Captured', 'Follow-up Required',
      'Converted to Enquiry', 'Completed', 'Cancelled'
    )),
  check_in_at timestamptz not null default now(),
  assigned_at timestamptz,
  discussion_started_at timestamptz,
  completed_at timestamptz,
  internal_notes text,
  converted_enquiry_reference text unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.walkin_requirements (
  id uuid primary key default gen_random_uuid(),
  walkin_visit_id uuid not null unique references public.walkin_visits(id) on delete cascade,
  detailed_requirement text,
  product_or_service text,
  quantity text,
  budget_range text,
  expected_purchase_timeline text,
  preferred_solution text,
  competitor_or_alternative text,
  special_requests text,
  follow_up_at date,
  next_action text,
  agent_notes text,
  created_by uuid not null default auth.uid() references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.walkin_visit_history (
  id uuid primary key default gen_random_uuid(),
  walkin_visit_id uuid not null references public.walkin_visits(id) on delete cascade,
  event_type text not null,
  old_value text,
  new_value text,
  notes text,
  performed_by uuid references auth.users(id),
  created_at timestamptz not null default now()
);

create index if not exists walkin_visits_check_in_idx on public.walkin_visits(check_in_at desc);
create index if not exists walkin_visits_status_idx on public.walkin_visits(status);
create index if not exists walkin_visits_assigned_agent_idx on public.walkin_visits(assigned_agent_id, check_in_at desc);
create index if not exists walkin_visits_purpose_idx on public.walkin_visits(purpose);
create index if not exists walkin_history_visit_created_idx on public.walkin_visit_history(walkin_visit_id, created_at desc);

create or replace function public.walkin_generate_visit_number()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  new.check_in_at := now();
  new.registered_by := auth.uid();
  new.visit_number := 'WI-' || to_char(now() at time zone 'UTC', 'YYYYMMDD') || '-' ||
    lpad(nextval('public.walkin_visit_number_seq')::text, 6, '0');
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists walkin_visits_before_insert on public.walkin_visits;
create trigger walkin_visits_before_insert
before insert on public.walkin_visits
for each row execute function public.walkin_generate_visit_number();

create or replace function public.walkin_touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists walkin_visits_touch_updated_at on public.walkin_visits;
create trigger walkin_visits_touch_updated_at
before update on public.walkin_visits
for each row execute function public.walkin_touch_updated_at();

drop trigger if exists walkin_requirements_touch_updated_at on public.walkin_requirements;
create trigger walkin_requirements_touch_updated_at
before update on public.walkin_requirements
for each row execute function public.walkin_touch_updated_at();

create or replace function public.walkin_log_visit_changes()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    insert into public.walkin_visit_history (walkin_visit_id, event_type, new_value, notes, performed_by)
    values (new.id, 'Registered', new.status, 'Visitor registered.', auth.uid());
    return new;
  end if;

  if old.assigned_agent_id is distinct from new.assigned_agent_id then
    insert into public.walkin_visit_history (walkin_visit_id, event_type, old_value, new_value, notes, performed_by)
    values (new.id, case when old.assigned_agent_id is null then 'Assigned' else 'Reassigned' end,
      old.assigned_agent_id::text, new.assigned_agent_id::text, new.assignment_note, auth.uid());
  end if;
  if old.status is distinct from new.status then
    insert into public.walkin_visit_history (walkin_visit_id, event_type, old_value, new_value, performed_by)
    values (
      new.id,
      case when new.status = 'Converted to Enquiry' then 'Converted to enquiry' else 'Status changed' end,
      old.status,
      new.status,
      auth.uid()
    );
  end if;
  return new;
end;
$$;

drop trigger if exists walkin_visits_log_changes on public.walkin_visits;
create trigger walkin_visits_log_changes
after insert or update on public.walkin_visits
for each row execute function public.walkin_log_visit_changes();

create or replace function public.walkin_log_requirement_changes()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.walkin_visit_history (walkin_visit_id, event_type, notes, performed_by)
  values (
    new.walkin_visit_id,
    case when tg_op = 'INSERT' then 'Requirements captured' else 'Requirements updated' end,
    'Sales requirement draft saved.',
    auth.uid()
  );
  return new;
end;
$$;

drop trigger if exists walkin_requirements_log_changes on public.walkin_requirements;
create trigger walkin_requirements_log_changes
after insert or update on public.walkin_requirements
for each row execute function public.walkin_log_requirement_changes();

create or replace function public.walkin_can_access(p_visit_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.walkin_visits v
    where v.id = p_visit_id
      and (
        public.walkin_current_role() in ('reception', 'manager', 'admin')
        or v.assigned_agent_id = auth.uid()
      )
  );
$$;

create or replace function public.assign_walkin_visit(p_visit_id uuid, p_agent_id uuid, p_note text default null)
returns public.walkin_visits
language plpgsql
security definer
set search_path = ''
as $$
declare
  result public.walkin_visits;
begin
  if public.walkin_current_role() not in ('reception', 'manager', 'admin') then
    raise exception 'Only reception staff, managers, or administrators can assign visitors.';
  end if;
  if p_agent_id is not null and not exists (
    select 1 from public.walkin_sales_agents a where a.user_id = p_agent_id and a.active
  ) then
    raise exception 'Select an active sales agent.';
  end if;
  update public.walkin_visits
  set assigned_agent_id = p_agent_id,
      assignment_note = p_note,
      assigned_at = case when p_agent_id is null then null else now() end,
      status = case when p_agent_id is null then 'Waiting for Assignment' else 'Assigned' end
  where id = p_visit_id
    and status not in ('Completed', 'Cancelled', 'Converted to Enquiry')
  returning * into result;
  if not found then raise exception 'Visit not found or already closed.'; end if;
  return result;
end;
$$;

create or replace function public.update_walkin_visit_status(p_visit_id uuid, p_status text)
returns public.walkin_visits
language plpgsql
security definer
set search_path = ''
as $$
declare
  result public.walkin_visits;
  current_status text;
  allowed boolean := false;
begin
  select status into current_status from public.walkin_visits where id = p_visit_id for update;
  if not found then raise exception 'Visit not found.'; end if;
  if public.walkin_current_role() not in ('reception', 'manager', 'admin')
    and not exists (select 1 from public.walkin_visits where id = p_visit_id and assigned_agent_id = auth.uid()) then
    raise exception 'You are not authorized to update this visit.';
  end if;
  allowed := case current_status
    when 'Registered' then p_status in ('Waiting for Assignment', 'Assigned', 'Cancelled')
    when 'Waiting for Assignment' then p_status in ('Assigned', 'Cancelled')
    when 'Assigned' then p_status in ('Waiting for Sales Agent', 'In Discussion', 'Cancelled')
    when 'Waiting for Sales Agent' then p_status in ('In Discussion', 'Cancelled')
    when 'In Discussion' then p_status in ('Requirements Captured', 'Follow-up Required', 'Completed', 'Cancelled')
    when 'Requirements Captured' then p_status in ('Follow-up Required', 'Converted to Enquiry', 'Completed')
    when 'Follow-up Required' then p_status in ('In Discussion', 'Requirements Captured', 'Converted to Enquiry', 'Completed', 'Cancelled')
    when 'Converted to Enquiry' then p_status = 'Completed'
    else false
  end;
  if not allowed then raise exception 'Invalid visit status transition.'; end if;
  update public.walkin_visits
  set status = p_status,
      discussion_started_at = case when p_status = 'In Discussion' then coalesce(discussion_started_at, now()) else discussion_started_at end,
      completed_at = case when p_status in ('Completed', 'Cancelled', 'Converted to Enquiry') then now() else completed_at end
  where id = p_visit_id
  returning * into result;
  return result;
end;
$$;

create or replace function public.convert_walkin_visit(p_visit_id uuid, p_enquiry_reference text)
returns public.walkin_visits
language plpgsql
security definer
set search_path = ''
as $$
declare
  result public.walkin_visits;
begin
  if public.walkin_current_role() not in ('manager', 'admin')
    and not exists (select 1 from public.walkin_visits where id = p_visit_id and assigned_agent_id = auth.uid()) then
    raise exception 'Only the assigned agent, a manager, or an administrator can convert a visit.';
  end if;
  update public.walkin_visits
  set status = 'Converted to Enquiry', converted_enquiry_reference = p_enquiry_reference, completed_at = now()
  where id = p_visit_id
    and status in ('Requirements Captured', 'Follow-up Required')
    and converted_enquiry_reference is null
  returning * into result;
  if not found then
    select * into result from public.walkin_visits where id = p_visit_id;
    if result.converted_enquiry_reference is null then raise exception 'Visit must have confirmed requirements before conversion.'; end if;
  end if;
  return result;
end;
$$;

alter table public.walkin_sales_agents enable row level security;
alter table public.walkin_visits enable row level security;
alter table public.walkin_requirements enable row level security;
alter table public.walkin_visit_history enable row level security;

drop policy if exists walkin_agents_read on public.walkin_sales_agents;
create policy walkin_agents_read on public.walkin_sales_agents
for select to authenticated using (public.walkin_is_staff());
drop policy if exists walkin_agents_manage on public.walkin_sales_agents;
create policy walkin_agents_manage on public.walkin_sales_agents
for all to authenticated using (public.walkin_current_role() in ('manager', 'admin'))
with check (public.walkin_current_role() in ('manager', 'admin'));

drop policy if exists walkin_visits_read on public.walkin_visits;
create policy walkin_visits_read on public.walkin_visits
for select to authenticated using (public.walkin_can_access(id));
drop policy if exists walkin_visits_register on public.walkin_visits;
create policy walkin_visits_register on public.walkin_visits
for insert to authenticated with check (
  public.walkin_current_role() in ('reception', 'manager', 'admin')
  and registered_by = auth.uid()
  and assigned_agent_id is null
  and status in ('Registered', 'Waiting for Assignment')
);

drop policy if exists walkin_requirements_read on public.walkin_requirements;
create policy walkin_requirements_read on public.walkin_requirements
for select to authenticated using (public.walkin_can_access(walkin_visit_id));
drop policy if exists walkin_requirements_insert on public.walkin_requirements;
create policy walkin_requirements_insert on public.walkin_requirements
for insert to authenticated with check (
  public.walkin_can_access(walkin_visit_id)
  and created_by = auth.uid()
  and (public.walkin_current_role() in ('sales_agent', 'manager', 'admin') or exists (
    select 1 from public.walkin_visits v
    where v.id = walkin_visit_id and v.assigned_agent_id = auth.uid()
  ))
);
drop policy if exists walkin_requirements_update on public.walkin_requirements;
create policy walkin_requirements_update on public.walkin_requirements
for update to authenticated using (
  public.walkin_current_role() in ('manager', 'admin')
  or exists (select 1 from public.walkin_visits v where v.id = walkin_visit_id and v.assigned_agent_id = auth.uid())
) with check (
  public.walkin_current_role() in ('manager', 'admin')
  or exists (select 1 from public.walkin_visits v where v.id = walkin_visit_id and v.assigned_agent_id = auth.uid())
);

drop policy if exists walkin_history_read on public.walkin_visit_history;
create policy walkin_history_read on public.walkin_visit_history
for select to authenticated using (public.walkin_can_access(walkin_visit_id));

grant select, insert on public.walkin_visits to authenticated;
grant select, insert on public.walkin_requirements to authenticated;
grant update (
  walkin_visit_id, detailed_requirement, product_or_service, quantity, budget_range,
  expected_purchase_timeline, preferred_solution, competitor_or_alternative,
  special_requests, follow_up_at, next_action, agent_notes
) on public.walkin_requirements to authenticated;
grant select on public.walkin_visit_history to authenticated;
grant select on public.walkin_sales_agents to authenticated;
grant update (display_name, department, specialization, availability, active)
  on public.walkin_sales_agents to authenticated;
grant execute on function public.assign_walkin_visit(uuid, uuid, text) to authenticated;
grant execute on function public.update_walkin_visit_status(uuid, text) to authenticated;
grant execute on function public.convert_walkin_visit(uuid, text) to authenticated;
