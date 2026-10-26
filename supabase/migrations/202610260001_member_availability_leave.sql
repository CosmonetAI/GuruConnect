begin;

-- Replace the legacy profile value with the three supported availability states.
update public.profiles
set availability_status='active'
where availability_status not in ('active', 'on_leave', 'be_right_back');

alter table public.profiles add column if not exists leave_end_date date;
update public.profiles
set availability_status='active', leave_end_date=null
where availability_status='on_leave' and leave_end_date is null;

alter table public.profiles drop constraint if exists profiles_availability_status_check;
alter table public.profiles add constraint profiles_availability_status_check
  check (availability_status in ('active', 'on_leave', 'be_right_back'));
alter table public.profiles drop constraint if exists profiles_leave_end_date_check;
alter table public.profiles add constraint profiles_leave_end_date_check
  check ((availability_status = 'on_leave') = (leave_end_date is not null));

create index if not exists profiles_active_leave_idx
  on public.profiles(availability_status, leave_end_date);

create or replace function public.expire_member_leave()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  changed integer;
begin
  update public.profiles
  set availability_status='active', leave_end_date=null, updated_at=now()
  where availability_status='on_leave' and leave_end_date < current_date;
  get diagnostics changed = row_count;
  return changed;
end;
$$;

revoke all on function public.expire_member_leave() from public;

do $$
declare
  existing_job_id bigint;
begin
  select jobid into existing_job_id from cron.job where jobname = 'member-leave-expiry';
  if existing_job_id is not null then
    perform cron.unschedule(existing_job_id);
  end if;
end;
$$;

select cron.schedule(
  'member-leave-expiry',
  '*/15 * * * *',
  $$select public.expire_member_leave();$$
);

commit;
