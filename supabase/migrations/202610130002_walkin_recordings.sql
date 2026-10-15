create table if not exists public.walkin_recordings (
  id uuid primary key default gen_random_uuid(),
  walkin_visit_id uuid not null references public.walkin_visits(id) on delete cascade,
  transcript text not null check (length(trim(transcript)) > 0),
  started_at timestamptz not null,
  ended_at timestamptz not null,
  duration_seconds integer not null check (duration_seconds >= 0),
  recording_status text not null default 'saved' check (recording_status = 'saved'),
  audio_storage_path text,
  consent_at timestamptz not null,
  created_by uuid not null default auth.uid() references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (ended_at >= started_at)
);

create index if not exists walkin_recordings_visit_started_idx
  on public.walkin_recordings(walkin_visit_id, started_at desc);

create or replace function public.walkin_recordings_touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists walkin_recordings_touch_updated_at on public.walkin_recordings;
create trigger walkin_recordings_touch_updated_at
before update on public.walkin_recordings
for each row execute function public.walkin_recordings_touch_updated_at();

alter table public.walkin_recordings enable row level security;

drop policy if exists walkin_recordings_read on public.walkin_recordings;
create policy walkin_recordings_read on public.walkin_recordings
for select to authenticated using (public.walkin_can_access(walkin_visit_id));

drop policy if exists walkin_recordings_insert on public.walkin_recordings;
create policy walkin_recordings_insert on public.walkin_recordings
for insert to authenticated with check (
  public.walkin_is_staff()
  and public.walkin_can_access(walkin_visit_id)
  and created_by = auth.uid()
);

drop policy if exists walkin_recordings_update on public.walkin_recordings;
create policy walkin_recordings_update on public.walkin_recordings
for update to authenticated
using (public.walkin_is_staff() and public.walkin_can_access(walkin_visit_id))
with check (public.walkin_is_staff() and public.walkin_can_access(walkin_visit_id));

grant select, insert, update (
  transcript, started_at, ended_at, duration_seconds, recording_status,
  audio_storage_path, consent_at
) on public.walkin_recordings to authenticated;
