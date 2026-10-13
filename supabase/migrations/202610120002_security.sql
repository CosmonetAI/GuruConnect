begin;
-- All helpers bypass member RLS to avoid recursive policy evaluation, and retain
-- the current authenticated uid. Explicit search paths and qualified names are mandatory.
create function public.recorder_member(p_organisation_id uuid) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(select 1 from public.members where organisation_id=p_organisation_id and user_id=(select auth.uid()) and status='active')
$$;
create function public.recorder_admin(p_organisation_id uuid) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(select 1 from public.members where organisation_id=p_organisation_id and user_id=(select auth.uid()) and status='active' and role in ('owner','admin'))
$$;
create function public.recorder_conversation_access(p_conversation_id uuid) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(select 1 from public.conversations c join public.members m on m.organisation_id=c.organisation_id
    where c.id=p_conversation_id and c.deleted_at is null and m.user_id=(select auth.uid()) and m.status='active'
    and (c.owner_id=m.user_id or m.role in ('owner','admin')))
$$;
create function public.recorder_customer_access(p_customer_id uuid) returns boolean
language sql stable security definer set search_path='' as $$
  select exists(select 1 from public.customers c join public.members m on m.organisation_id=c.organisation_id
    where c.id=p_customer_id and c.deleted_at is null and m.user_id=(select auth.uid()) and m.status='active'
    and (c.owner_id=m.user_id or m.role in ('owner','admin')))
$$;
create function public.recorder_object_access(p_bucket text,p_path text) returns boolean
language sql stable security definer set search_path='' as $$
  select (p_bucket='recorder-audio' and exists(select 1 from public.audio_chunks a where a.object_path=p_path and public.recorder_conversation_access(a.conversation_id)))
    or (p_bucket='recorder-playback' and exists(select 1 from public.conversations c where c.playback_object_path=p_path and public.recorder_conversation_access(c.id)))
$$;
create function public.recorder_upload_allowed(p_bucket text,p_path text) returns boolean
language sql stable security definer set search_path='' as $$
  select p_bucket='recorder-audio' and exists(select 1 from public.audio_chunks a join public.conversations c on c.id=a.conversation_id
    join public.members m on m.organisation_id=c.organisation_id and m.user_id=c.owner_id
    where a.object_path=p_path and a.state='reserved' and a.upload_expires_at>now() and c.deleted_at is null
    and c.owner_id=(select auth.uid()) and m.status='active')
$$;

do $$
declare t text;
begin
  foreach t in array array['profiles','organisations','members','invites','customers','engine_connections','provider_secrets','workspace_engine_settings',
    'conversations','recording_leases','recording_parts','audio_chunks','processing_runs','transcription_windows','inference_workers','processing_jobs',
    'inference_artifacts','transcript_versions','transcript_turns','transcript_words','translations','diarization_intervals','speaker_assignments',
    'transcript_revisions','audit_events','usage_records','comparisons','worker_heartbeats'] loop
    execute format('alter table public.%I enable row level security',t);
    execute format('revoke all on table public.%I from public, anon, authenticated',t);
    execute format('grant all on table public.%I to service_role',t);
  end loop;
end $$;
grant select on public.profiles, public.organisations, public.members, public.customers,
  public.workspace_engine_settings, public.conversations, public.recording_parts, public.audio_chunks,
  public.processing_runs, public.transcription_windows, public.transcript_versions, public.transcript_turns,
  public.transcript_words, public.translations, public.diarization_intervals, public.speaker_assignments,
  public.transcript_revisions, public.audit_events, public.usage_records, public.comparisons to authenticated;
grant select (id,organisation_id,engine_id,state,generation,config,max_concurrency,tested_at,last_error_code,updated_at,created_at) on public.engine_connections to authenticated;
grant select (id,organisation_id,connection_id,name,state,connection_generation,model_version,metrics,last_seen_at,revoked_at,created_at) on public.inference_workers to authenticated;
grant select (id,organisation_id,email,role,invited_by,accepted_by,expires_at,accepted_at,revoked_at,created_at) on public.invites to authenticated;
grant select (id,conversation_id,organisation_id,run_id,window_id,connection_id,connection_generation,kind,worker_type,state,priority,attempt,max_attempts,available_at,error_code,created_at,updated_at,completed_at) on public.processing_jobs to authenticated;
grant update (display_name,preferences,updated_at) on public.profiles to authenticated;
create policy profile_read on public.profiles for select to authenticated using(id=(select auth.uid()));
create policy profile_update on public.profiles for update to authenticated using(id=(select auth.uid())) with check(id=(select auth.uid()));
create policy organisation_read on public.organisations for select to authenticated using(public.recorder_member(id));
create policy member_read on public.members for select to authenticated using(public.recorder_member(organisation_id) and (user_id=(select auth.uid()) or public.recorder_admin(organisation_id)));
create policy invite_read on public.invites for select to authenticated using(public.recorder_admin(organisation_id));
create policy customer_read on public.customers for select to authenticated using(public.recorder_customer_access(id));
create policy connection_read on public.engine_connections for select to authenticated using(public.recorder_member(organisation_id));
create policy settings_read on public.workspace_engine_settings for select to authenticated using(public.recorder_member(organisation_id));
create policy conversation_read on public.conversations for select to authenticated using(public.recorder_conversation_access(id));
create policy worker_read on public.inference_workers for select to authenticated using(public.recorder_admin(organisation_id));
create policy jobs_read on public.processing_jobs for select to authenticated using(public.recorder_conversation_access(conversation_id));
create policy words_read on public.transcript_words for select to authenticated using(exists(select 1 from public.transcript_versions v where v.id=version_id and public.recorder_conversation_access(v.conversation_id)));
create policy audit_read on public.audit_events for select to authenticated using(public.recorder_admin(organisation_id) or (conversation_id is not null and public.recorder_conversation_access(conversation_id)));
do $$
declare t text;
begin
  foreach t in array array['recording_parts','audio_chunks','processing_runs','transcription_windows','transcript_versions','transcript_turns','translations',
    'diarization_intervals','speaker_assignments','transcript_revisions','usage_records','comparisons'] loop
    execute format('create policy authorised_read on public.%I for select to authenticated using(public.recorder_conversation_access(conversation_id))',t);
  end loop;
end $$;
-- provider_secrets, artifacts, leases, worker heartbeats and queue metadata have no
-- browser policies/grants. Artifacts are downloaded only after trusted API authorisation.

insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types) values
  ('recorder-audio','recorder-audio',false,320044,array['audio/wav']),
  ('recorder-playback','recorder-playback',false,230400044,array['audio/wav']),
  ('recorder-artifacts','recorder-artifacts',false,230400044,array['audio/wav','application/json','application/octet-stream','text/plain','text/csv','application/zip'])
on conflict(id) do update set public=false,file_size_limit=excluded.file_size_limit,allowed_mime_types=excluded.allowed_mime_types;
create policy recorder_private_read on storage.objects for select to authenticated
  using(public.recorder_object_access(bucket_id,name));
create policy recorder_reserved_upload on storage.objects for insert to authenticated
  with check(public.recorder_upload_allowed(bucket_id,name));
-- No browser UPDATE or DELETE policy: immutable chunks cannot be upserted.

revoke all on all functions in schema recorder_private from public,anon,authenticated;
revoke all on function public.recorder_member(uuid),public.recorder_admin(uuid),public.recorder_conversation_access(uuid),
  public.recorder_customer_access(uuid),public.recorder_object_access(text,text),public.recorder_upload_allowed(text,text) from public,anon;
grant execute on function public.recorder_member(uuid),public.recorder_admin(uuid),public.recorder_conversation_access(uuid),
  public.recorder_customer_access(uuid),public.recorder_object_access(text,text),public.recorder_upload_allowed(text,text) to authenticated,service_role;
revoke all on schema pgmq from public,anon,authenticated;
revoke all on all tables in schema pgmq from public,anon,authenticated;
revoke all on all functions in schema pgmq from public,anon,authenticated;
do $$ begin
  if exists(select 1 from pg_namespace where nspname='pgmq_public') then
    execute 'revoke all on schema pgmq_public from public,anon,authenticated';
    execute 'revoke all on all functions in schema pgmq_public from public,anon,authenticated';
  end if;
end $$;

-- Compact durable progress rows only. Transcript text is fetched via paginated API.
do $$ begin
  if exists(select 1 from pg_publication where pubname='supabase_realtime') then
    alter publication supabase_realtime add table public.conversations;
  end if;
end $$;
commit;
