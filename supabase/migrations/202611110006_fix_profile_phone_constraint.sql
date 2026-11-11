begin;

-- The original profile phone constraint escaped the optional plus twice in the
-- SQL string. That made valid phone numbers fail at the database layer and the
-- API surfaced the resulting check violation as conflicting_state.
alter table public.profiles
  drop constraint if exists profiles_phone_check;

alter table public.profiles
  add constraint profiles_phone_check
  check (phone is null or phone ~ E'^\\+?[0-9][0-9 ()-]{7,19}$');

commit;
