begin;
create extension if not exists btree_gist;
alter table public.audio_chunks add constraint chunks_nonoverlap exclude using gist
  (conversation_id with =,int8range(sample_start,sample_start+sample_count,'[)') with &&);
create unique index single_production_run_idx on public.processing_runs(conversation_id) where kind='production';

create table recorder_private.api_rate_limits (
  scope text not null, bucket_start bigint not null, requests integer not null check (requests>0),
  expires_at timestamptz not null, primary key(scope,bucket_start)
);
create function public.allow_api_request(p_scope text,p_limit integer,p_window_seconds integer default 60) returns boolean
language plpgsql security definer set search_path='' as $$
declare v_bucket bigint; v_requests integer;
begin
  if length(p_scope) not between 1 and 300 or p_limit not between 1 and 100000 or p_window_seconds not between 1 and 86400 then
    raise exception 'invalid_rate_parameters' using errcode='22023';end if;
  v_bucket:=floor(extract(epoch from now())/p_window_seconds)::bigint;
  insert into recorder_private.api_rate_limits(scope,bucket_start,requests,expires_at)
    values(p_scope,v_bucket,1,now()+make_interval(secs=>p_window_seconds*2))
    on conflict(scope,bucket_start) do update set requests=least(recorder_private.api_rate_limits.requests+1,p_limit+1)
    returning requests into v_requests;
  -- Opportunistic bounded cleanup; operational cron can also delete expired rows.
  delete from recorder_private.api_rate_limits where ctid in(select ctid from recorder_private.api_rate_limits where expires_at<now() limit 100);
  return v_requests<=p_limit;
end $$;
revoke all on function public.allow_api_request(text,integer,integer) from public,anon,authenticated;
grant execute on function public.allow_api_request(text,integer,integer) to service_role;

create function recorder_private.check_sample_limit() returns trigger language plpgsql set search_path='' as $$
declare v_max integer;v_org uuid;
begin
  if tg_table_name='audio_chunks' then
    select organisation_id into v_org from public.conversations where id=new.conversation_id;
    select max_conversation_seconds into v_max from public.organisations where id=v_org;
    if new.sample_start+new.sample_count>v_max::bigint*16000 then raise exception 'recording_duration_limit' using errcode='23514';end if;
  else
    select max_conversation_seconds into v_max from public.organisations where id=new.organisation_id;
    if new.total_samples>v_max::bigint*16000 or new.duration_seconds>v_max then raise exception 'recording_duration_limit' using errcode='23514';end if;
  end if;
  return new;
end $$;
create trigger chunks_duration before insert on public.audio_chunks for each row execute function recorder_private.check_sample_limit();
create trigger conversations_duration before insert or update on public.conversations for each row execute function recorder_private.check_sample_limit();

create function recorder_private.check_run_scope() returns trigger language plpgsql set search_path='' as $$
declare ec public.engine_connections;tc public.engine_connections;
begin
  if new.connection_id is not null then
    select * into strict ec from public.engine_connections where id=new.connection_id;
    if ec.engine_id<>new.engine_id or ec.generation<>new.connection_generation then raise exception 'run_connection_mismatch' using errcode='23514';end if;
  elsif new.connection_generation is not null then raise exception 'run_connection_mismatch' using errcode='23514';end if;
  if new.translator_connection_id is not null then
    select * into strict tc from public.engine_connections where id=new.translator_connection_id;
    if tc.engine_id<>'azure-translator' or tc.generation<>new.translator_generation then raise exception 'run_translator_mismatch' using errcode='23514';end if;
  end if;
  if new.engine_id='faster-whisper' and new.translation_strategy<>'native-english' then raise exception 'native_translation_required' using errcode='23514';end if;
  return new;
end $$;
create trigger run_scope before insert on public.processing_runs for each row execute function recorder_private.check_run_scope();

create function recorder_private.immutable_run_snapshot() returns trigger language plpgsql set search_path='' as $$
begin
  if (new.id,new.conversation_id,new.organisation_id,new.connection_id,new.connection_generation,new.engine_id,new.model_version,new.config_snapshot,new.translation_strategy,new.translator_connection_id,new.translator_generation,new.finalisation_policy,new.kind,new.idempotency_key)
    is distinct from (old.id,old.conversation_id,old.organisation_id,old.connection_id,old.connection_generation,old.engine_id,old.model_version,old.config_snapshot,old.translation_strategy,old.translator_connection_id,old.translator_generation,old.finalisation_policy,old.kind,old.idempotency_key) then
    raise exception 'immutable_run_snapshot' using errcode='23514';end if;
  return new;
end $$;
create trigger run_snapshot before update on public.processing_runs for each row execute function recorder_private.immutable_run_snapshot();

create function recorder_private.check_settings_scope() returns trigger language plpgsql set search_path='' as $$
begin
  if new.translator_connection_id is not null and not exists(select 1 from public.engine_connections where id=new.translator_connection_id and organisation_id=new.organisation_id and engine_id='azure-translator') then
    raise exception 'translator_connection_mismatch' using errcode='23514';end if;
  if new.default_engine_id is not null and not (new.default_engine_id=any(new.allowed_engine_ids)) then raise exception 'default_engine_not_allowed' using errcode='23514';end if;
  if jsonb_typeof(new.budget_policy)<>'object' or exists(select 1 from jsonb_each(new.budget_policy) kv
    where kv.key in ('max_audio_seconds_per_day','max_translation_characters_per_day') and (jsonb_typeof(kv.value)<>'number' or (kv.value::text)::numeric<0)) then
    raise exception 'invalid_budget_policy' using errcode='23514';end if;
  return new;
end $$;
create trigger settings_scope before insert or update on public.workspace_engine_settings for each row execute function recorder_private.check_settings_scope();

-- Lock scopes in a stable order, re-check cancellation/generation, and persist the
-- uncertainty boundary before making a paid external request outside the transaction.
create function public.begin_job_submission(p_job_id uuid,p_worker_id text,p_lease_token uuid,p_fence bigint,p_request_reference text) returns boolean
language plpgsql security definer set search_path='' as $$
declare j public.processing_jobs;ec public.engine_connections;c public.conversations;
begin
  select * into j from public.processing_jobs where id=p_job_id;
  if not found then return false;end if;
  select * into c from public.conversations where id=j.conversation_id for share;
  if j.connection_id is not null then select * into ec from public.engine_connections where id=j.connection_id for share;end if;
  select * into j from public.processing_jobs where id=p_job_id for update;
  if j.state<>'running' or j.worker_id is distinct from p_worker_id or j.lease_token is distinct from p_lease_token or j.fence<>p_fence or j.lease_until<=now()
    or c.deleted_at is not null or j.cancelled_at is not null or j.submission_state<>'not_submitted'
    or exists(select 1 from public.processing_runs where id=j.run_id and cancelled_at is not null)
    or (j.connection_id is not null and (ec.generation<>j.connection_generation or ec.state not in ('connected','degraded')))
    or (j.inference_worker_id is not null and not exists(select 1 from public.inference_workers where id=j.inference_worker_id and revoked_at is null)) then return false;end if;
  update public.processing_jobs set submission_state='submitting',request_reference=p_request_reference,updated_at=now() where id=j.id;
  return true;
end $$;
revoke all on function public.begin_job_submission(uuid,text,uuid,bigint,text) from public,anon,authenticated;
grant execute on function public.begin_job_submission(uuid,text,uuid,bigint,text) to service_role;
revoke all on all functions in schema recorder_private from public,anon,authenticated;
commit;
