-- Canonical member status used by assignment and UI automation.
-- This is intentionally a new migration; previously applied migrations stay immutable.
alter table public.profiles
  add column if not exists final_status text;

alter table public.profiles
  add column if not exists final_status_until date;

update public.profiles
set final_status = case
      when availability_status = 'on_leave' then 'on_leave'
      when availability_status = 'be_right_back' then 'be_right_back'
      when availability_status = 'inactive' then 'inactive'
      else 'active'
    end,
    final_status_until = case
      when availability_status = 'on_leave' then leave_end_date
      else null
    end
where final_status is null;

alter table public.profiles
  alter column final_status set default 'active';

alter table public.profiles
  alter column final_status set not null;

alter table public.profiles
  drop constraint if exists profiles_final_status_check;

alter table public.profiles
  add constraint profiles_final_status_check
  check (final_status in ('active', 'inactive', 'on_leave', 'be_right_back'));

create index if not exists profiles_final_status_idx
  on public.profiles(final_status, final_status_until);
