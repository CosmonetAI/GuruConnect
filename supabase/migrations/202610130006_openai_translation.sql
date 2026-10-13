begin;

-- Forward-only migration: existing credentials, runs and translator selections
-- retain their historical identities. OpenAI is a separate text-only dependency.
alter table public.engine_connections drop constraint engine_connections_engine_id_check;
alter table public.engine_connections add constraint engine_connections_engine_id_check
  check (engine_id in ('elevenlabs','faster-whisper','azure-translator','openai-translator'));
alter table public.engine_connections add constraint openai_fixed_translation_model
  check (engine_id <> 'openai-translator' or (config->>'model') is not distinct from 'gpt-5.6-luna');
alter table public.processing_runs drop constraint processing_runs_translation_strategy_check;
alter table public.processing_runs add constraint processing_runs_translation_strategy_check
  check (translation_strategy in ('azure','openai','native-english','none'));

alter table public.usage_records
  add column input_tokens bigint not null default 0 check (input_tokens>=0),
  add column output_tokens bigint not null default 0 check (output_tokens>=0),
  add column provider_model text check (provider_model is null or length(provider_model) between 1 and 200);

insert into public.engine_connections(organisation_id,engine_id,state,config)
  select id,'openai-translator','disconnected','{"model":"gpt-5.6-luna"}'::jsonb
  from public.organisations
  on conflict (organisation_id,engine_id) do nothing;

create or replace function recorder_private.check_settings_scope() returns trigger
language plpgsql set search_path='' as $$
begin
  if new.translator_connection_id is not null and not exists (
    select 1 from public.engine_connections where id=new.translator_connection_id
      and organisation_id=new.organisation_id and engine_id in ('azure-translator','openai-translator')
  ) then raise exception 'translator_connection_mismatch' using errcode='23514';end if;
  if new.default_engine_id is not null and not (new.default_engine_id=any(new.allowed_engine_ids)) then
    raise exception 'default_engine_not_allowed' using errcode='23514';end if;
  if jsonb_typeof(new.budget_policy)<>'object' or exists(select 1 from jsonb_each(new.budget_policy) kv
    where kv.key in ('max_audio_seconds_per_day','max_translation_characters_per_day')
      and (jsonb_typeof(kv.value)<>'number' or (kv.value::text)::numeric<0)) then
    raise exception 'invalid_budget_policy' using errcode='23514';end if;
  return new;
end $$;

create or replace function recorder_private.check_run_scope() returns trigger
language plpgsql set search_path='' as $$
declare ec public.engine_connections;tc public.engine_connections;
begin
  if new.connection_id is not null then
    select * into strict ec from public.engine_connections where id=new.connection_id;
    if ec.organisation_id is distinct from new.organisation_id or ec.engine_id is distinct from new.engine_id
      or ec.generation is distinct from new.connection_generation then
      raise exception 'run_connection_mismatch' using errcode='23514';end if;
  elsif new.connection_generation is not null then raise exception 'run_connection_mismatch' using errcode='23514';end if;
  if new.translator_connection_id is not null then
    select * into strict tc from public.engine_connections where id=new.translator_connection_id;
    if tc.organisation_id is distinct from new.organisation_id
      or tc.generation is distinct from new.translator_generation
      or not ((tc.engine_id='azure-translator' and new.translation_strategy='azure')
        or (tc.engine_id='openai-translator' and new.translation_strategy='openai')) then
      raise exception 'run_translator_mismatch' using errcode='23514';end if;
  elsif new.translator_generation is not null then raise exception 'run_translator_mismatch' using errcode='23514';end if;
  if new.engine_id='faster-whisper' and new.translation_strategy<>'native-english' then
    raise exception 'native_translation_required' using errcode='23514';end if;
  return new;
end $$;

create or replace function public.create_workspace(p_user_id uuid,p_name text) returns uuid
language plpgsql security definer set search_path='' as $$
declare v_id uuid;v_translator uuid;
begin
  if not exists(select 1 from auth.users where id=p_user_id and email_confirmed_at is not null) then
    raise exception 'verified_email_required' using errcode='42501';end if;
  insert into public.organisations(name,created_by) values(p_name,p_user_id) returning id into v_id;
  insert into public.members(organisation_id,user_id,role) values(v_id,p_user_id,'owner');
  insert into public.engine_connections(organisation_id,engine_id)
    values(v_id,'elevenlabs'),(v_id,'faster-whisper'),(v_id,'azure-translator');
  insert into public.engine_connections(organisation_id,engine_id,config)
    values(v_id,'openai-translator','{"model":"gpt-5.6-luna"}'::jsonb) returning id into v_translator;
  insert into public.workspace_engine_settings(organisation_id,translator_connection_id) values(v_id,v_translator);
  insert into public.audit_events(organisation_id,actor_id,action) values(v_id,p_user_id,'workspace.created');
  return v_id;
end $$;

-- Rejections known to have occurred before paid execution block until explicitly
-- corrected. Submission uncertainty always wins; no automatic duplicate charge.
create or replace function public.retry_job(p_job_id uuid,p_worker_id text,p_lease_token uuid,p_fence bigint,p_error_code text,p_uncertain boolean default false) returns boolean
language plpgsql security definer set search_path='' as $$
declare j public.processing_jobs;v_delay integer;v_msg bigint;v_state text;
begin
  select * into j from public.processing_jobs where id=p_job_id and state='running'
    and worker_id=p_worker_id and lease_token=p_lease_token and fence=p_fence and lease_until>now() for update;
  if not found then return false;end if;
  if p_error_code !~ '^[a-z0-9_]{1,80}$' then raise exception 'unsafe_error_code';end if;
  v_state:=case when p_uncertain or j.submission_state in ('submitting','accepted','uncertain') then 'uncertain'
    when p_error_code in ('invalid_credentials','connection_unavailable','translator_unavailable','translator_unconfigured',
      'metadata_permission_denied','provider_permission_denied','provider_access_denied',
      'provider_quota_exceeded','provider_payment_required','provider_plan_access_denied',
      'provider_model_access_denied','openai_model_unavailable','openai_model_not_allowed') then 'blocked'
    when p_error_code in ('invalid_audio','invalid_result','input_hash_mismatch','size_limit','permission_denied',
      'translator_input_too_large','translator_invalid_input') then 'failed'
    when j.attempt>=j.max_attempts then 'dead' else 'retry' end;
  v_delay:=least(3600,(power(2,least(j.attempt,10))*3)::integer)+floor(random()*5)::integer;
  update public.processing_jobs set state=v_state,error_code=p_error_code,lease_until=null,worker_id=null,lease_token=null,
    available_at=now()+make_interval(secs=>v_delay),updated_at=now(),
    submission_state=case when v_state='uncertain' then 'uncertain' else submission_state end where id=j.id;
  select msg_id into v_msg from recorder_private.queue_receipts where job_id=j.id;
  if v_state='retry' then perform pgmq.set_vt('recorder_jobs',v_msg,v_delay);
  else
    if v_msg is not null then perform pgmq.archive('recorder_jobs',v_msg);delete from recorder_private.queue_receipts where job_id=j.id;end if;
    if v_state='dead' then perform pgmq.send('recorder_dead_letters',jsonb_build_object('job_id',j.id,'error_code',p_error_code));end if;
  end if;
  return true;
end $$;

-- Used only by an authenticated explicit Retry English action. Bind a safe
-- terminal text job to the selected dependency while retaining its ASR run and
-- source_job_id. Completed translations and uncertain submissions are untouched.
create function public.resume_translation_job(p_job_id uuid,p_actor_id uuid) returns boolean
language plpgsql security definer set search_path='' as $$
declare j public.processing_jobs;ec public.engine_connections;old_ec public.engine_connections;v_resumed boolean;
begin
  select * into j from public.processing_jobs where id=p_job_id;
  if not found or j.kind<>'translate' then return false;end if;
  perform 1 from public.conversations where id=j.conversation_id for update;
  -- Match configure/disconnect lock order: connection before its jobs. The
  -- selection is snapshotted here; its generation stays locked until commit.
  select c.* into ec from public.workspace_engine_settings s join public.engine_connections c
    on c.id=s.translator_connection_id and c.organisation_id=s.organisation_id
    where s.organisation_id=j.organisation_id and c.engine_id in ('azure-translator','openai-translator')
      and c.state='connected' and c.tested_at is not null for share of c;
  if not found then return false;end if;
  select * into j from public.processing_jobs where id=p_job_id for update;
  if j.state not in ('blocked','failed','dead','uncertain') then return false;end if;
  if not exists(select 1 from public.members where organisation_id=j.organisation_id and user_id=p_actor_id
    and status='active' and role in ('owner','admin')) and not exists (
    select 1 from public.conversations c join public.members m on m.organisation_id=c.organisation_id and m.user_id=c.owner_id
    where c.id=j.conversation_id and c.owner_id=p_actor_id and m.status='active'
  ) then raise exception 'resume_forbidden' using errcode='42501';end if;
  if j.cancelled_at is not null or exists(select 1 from public.conversations where id=j.conversation_id and deleted_at is not null)
    or exists(select 1 from public.processing_runs where id=j.run_id and cancelled_at is not null) then return false;end if;
  if j.state='uncertain' or j.submission_state<>'not_submitted' then
    raise exception 'submission_reconciliation_required' using errcode='23514';end if;
  select * into old_ec from public.engine_connections where id=j.connection_id;
  update public.processing_jobs set connection_id=ec.id,connection_generation=ec.generation,
    payload=payload||jsonb_build_object('translation_strategy',case when ec.engine_id='openai-translator' then 'openai' else 'azure' end,
      'translation_provider',ec.engine_id,'translator_model',ec.config->>'model'),updated_at=now() where id=j.id;
  select public.resume_job(j.id,p_actor_id,false) into v_resumed;
  if v_resumed then
    insert into public.audit_events(organisation_id,conversation_id,actor_id,action,metadata)
      values(j.organisation_id,j.conversation_id,p_actor_id,'translation.dependency_bound',jsonb_build_object(
        'job_id',j.id,'old_connection_id',j.connection_id,'old_generation',j.connection_generation,'old_provider',old_ec.engine_id,
        'connection_id',ec.id,'generation',ec.generation,'provider',ec.engine_id,'model',ec.config->>'model'));
  end if;
  return v_resumed;
end $$;

revoke all on function public.resume_translation_job(uuid,uuid) from public,anon,authenticated;
grant execute on function public.resume_translation_job(uuid,uuid) to service_role;
revoke all on function public.create_workspace(uuid,text) from public,anon,authenticated;
grant execute on function public.create_workspace(uuid,text) to service_role;
revoke all on function public.retry_job(uuid,text,uuid,bigint,text,boolean) from public,anon,authenticated;
grant execute on function public.retry_job(uuid,text,uuid,bigint,text,boolean) to service_role;
revoke all on all functions in schema recorder_private from public,anon,authenticated;

commit;
