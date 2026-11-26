-- Keep migration history append-only. The earlier availability migration
-- omitted the explicit inactive state that the application already supports.
alter table public.profiles drop constraint if exists profiles_availability_status_check;
alter table public.profiles add constraint profiles_availability_status_check
  check (availability_status in ('active', 'inactive', 'on_leave', 'be_right_back'));
