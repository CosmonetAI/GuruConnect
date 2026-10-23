-- Keep the two user-facing audio buckets separate by workflow.
alter table public.conversations
  add column if not exists audio_bucket text not null default 'session-audio';

update public.conversations c
set audio_bucket = 'walkin-audio'
where exists (
  select 1 from public.guruconnect_walkin_recordings r
  where r.conversation_id = c.id
);

alter table public.conversations
  drop constraint if exists conversations_audio_bucket_check;

alter table public.conversations
  add constraint conversations_audio_bucket_check check (audio_bucket in ('session-audio', 'walkin-audio'));

insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values
  ('session-audio','session-audio',false,6000000,array['audio/wav']),
  ('walkin-audio','walkin-audio',false,320044,array['audio/wav'])
on conflict(id) do update set public=false,file_size_limit=excluded.file_size_limit,allowed_mime_types=excluded.allowed_mime_types;

create or replace function public.recorder_conversation_access(p_conversation_id uuid) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(
    select 1 from public.conversations c
    join public.members m on m.organisation_id=c.organisation_id
    where c.id=p_conversation_id and c.deleted_at is null
      and m.user_id=(select auth.uid()) and m.status='active'
      and ((c.audio_bucket='walkin-audio' and m.role in ('owner','admin','receptionist'))
        or (c.audio_bucket='session-audio' and m.role in ('owner','admin','salesperson')))
  )
$$;

create or replace function public.recorder_object_access(p_bucket text,p_path text) returns boolean
language sql stable security definer set search_path='' as $$
  select (p_bucket in ('session-audio','walkin-audio') and exists(select 1 from public.audio_chunks a where a.object_path=p_path and public.recorder_conversation_access(a.conversation_id)))
    or (p_bucket in ('session-audio','walkin-audio') and exists(select 1 from public.conversations c where c.playback_object_path=p_path and public.recorder_conversation_access(c.id)))
$$;

create or replace function public.recorder_upload_allowed(p_bucket text,p_path text) returns boolean
language sql stable security definer set search_path='' as $$
  select p_bucket in ('session-audio','walkin-audio') and exists(select 1 from public.audio_chunks a join public.conversations c on c.id=a.conversation_id
    join public.members m on m.organisation_id=c.organisation_id and m.user_id=c.owner_id
    where a.object_path=p_path and a.state='reserved' and a.upload_expires_at>now() and c.deleted_at is null
      and c.audio_bucket=p_bucket
      and c.owner_id=(select auth.uid()) and m.status='active')
$$;
