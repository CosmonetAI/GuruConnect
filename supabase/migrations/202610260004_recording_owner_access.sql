begin;

-- The previous role-wide policy exposed every salesperson's audio to peers.
-- Team owners retain authorized administrative visibility; all other members
-- can read only their own recording and the appropriate workflow bucket.
create or replace function public.recorder_conversation_access(p_conversation_id uuid) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(
    select 1 from public.conversations c
    join public.members m on m.organisation_id=c.organisation_id
    where c.id=p_conversation_id and c.deleted_at is null
      and m.user_id=(select auth.uid()) and m.status='active'
      and (m.role in ('owner','admin') or (
        c.owner_id=m.user_id and (
          (c.audio_bucket='session-audio' and m.role='salesperson')
          or (c.audio_bucket='walkin-audio' and m.role='receptionist')
        )
      ))
  )
$$;

create or replace function public.recorder_object_access(p_bucket text,p_path text) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(
    select 1 from public.audio_chunks a
    join public.conversations c on c.id=a.conversation_id
    where a.object_path=p_path and c.audio_bucket=p_bucket
      and p_bucket in ('session-audio','walkin-audio')
      and public.recorder_conversation_access(c.id)
  ) or exists(
    select 1 from public.conversations c
    where c.playback_object_path=p_path and c.audio_bucket=p_bucket
      and p_bucket in ('session-audio','walkin-audio')
      and public.recorder_conversation_access(c.id)
  )
$$;

-- Readiness must reject an older database before serving the changed API.
create function recorder_private.recording_owner_access_ready() returns boolean
language sql stable set search_path='' as $$ select true $$;
revoke all on function recorder_private.recording_owner_access_ready() from public;

commit;
