-- Forward-only cleanup for deployments where the Azure translator migration
-- was already applied. Historical Azure rows and completed runs remain for
-- auditability, but new runtime configuration and jobs are OpenAI-only.
update public.engine_connections
set state='disabled', tested_at=null, last_error_code='azure_translator_disabled',
    generation=generation+1, updated_at=now()
where engine_id='azure-translator';

update public.processing_jobs
set state=case when submission_state in ('submitting','accepted','uncertain') then 'uncertain' else 'blocked' end,
    error_code='azure_translator_disabled', lease_token=null, lease_until=null,
    worker_id=null, updated_at=now()
where connection_id in (select id from public.engine_connections where engine_id='azure-translator')
  and state in ('queued','retry','running');

update public.workspace_engine_settings s
set translator_connection_id=(
  select c.id from public.engine_connections c
  where c.organisation_id=s.organisation_id
    and c.engine_id='openai-translator'
    and c.state in ('connected','degraded')
  order by c.id
  limit 1
)
where exists (
  select 1 from public.engine_connections old
  where old.id=s.translator_connection_id and old.engine_id='azure-translator'
);

-- Add the stricter checks after legacy Azure rows have been disabled. NOT
-- VALID preserves those historical rows while rejecting new Azure writes.
alter table public.engine_connections
  drop constraint if exists engine_connections_engine_id_check;
alter table public.engine_connections
  add constraint engine_connections_engine_id_check
  check (engine_id in ('elevenlabs','faster-whisper','openai-translator')) not valid;

alter table public.processing_runs
  drop constraint if exists processing_runs_translation_strategy_check;
alter table public.processing_runs
  add constraint processing_runs_translation_strategy_check
  check (translation_strategy in ('openai','native-english','none')) not valid;

create or replace function recorder_private.check_settings_scope() returns trigger
language plpgsql set search_path='' as $$
begin
  if new.translator_connection_id is not null and not exists (
    select 1 from public.engine_connections
    where id=new.translator_connection_id
      and organisation_id=new.organisation_id
      and engine_id='openai-translator'
  ) then
    raise exception 'translator_connection_mismatch' using errcode='23514';
  end if;
  if new.default_engine_id is not null and not (new.default_engine_id=any(new.allowed_engine_ids)) then
    raise exception 'default_engine_not_allowed' using errcode='23514';
  end if;
  if jsonb_typeof(new.budget_policy)<>'object' or exists(
    select 1 from jsonb_each(new.budget_policy) kv
    where kv.key in ('max_audio_seconds_per_day','max_translation_characters_per_day')
      and (jsonb_typeof(kv.value)<>'number' or (kv.value::text)::numeric<0)
  ) then
    raise exception 'invalid_budget_policy' using errcode='23514';
  end if;
  return new;
end $$;

create or replace function recorder_private.check_run_scope() returns trigger
language plpgsql set search_path='' as $$
declare ec public.engine_connections; tc public.engine_connections;
begin
  if new.connection_id is not null then
    select * into strict ec from public.engine_connections where id=new.connection_id;
    if ec.organisation_id is distinct from new.organisation_id
      or ec.engine_id is distinct from new.engine_id
      or ec.generation is distinct from new.connection_generation then
      raise exception 'run_connection_mismatch' using errcode='23514';
    end if;
  elsif new.connection_generation is not null then
    raise exception 'run_connection_mismatch' using errcode='23514';
  end if;
  if new.translator_connection_id is not null then
    select * into strict tc from public.engine_connections where id=new.translator_connection_id;
    if tc.organisation_id is distinct from new.organisation_id
      or tc.generation is distinct from new.translator_generation
      or tc.engine_id is distinct from 'openai-translator'
      or new.translation_strategy is distinct from 'openai' then
      raise exception 'run_translator_mismatch' using errcode='23514';
    end if;
  elsif new.translator_generation is not null then
    raise exception 'run_translator_mismatch' using errcode='23514';
  end if;
  if new.engine_id='faster-whisper' and new.translation_strategy<>'native-english' then
    raise exception 'native_translation_required' using errcode='23514';
  end if;
  return new;
end $$;

create or replace function public.create_workspace(p_user_id uuid,p_name text) returns uuid
language plpgsql security definer set search_path='' as $$
declare v_id uuid; v_translator uuid;
begin
  if not exists(select 1 from auth.users where id=p_user_id and email_confirmed_at is not null) then
    raise exception 'verified_email_required' using errcode='42501';
  end if;
  insert into public.organisations(name,created_by) values(p_name,p_user_id) returning id into v_id;
  insert into public.members(organisation_id,user_id,role) values(v_id,p_user_id,'owner');
  insert into public.engine_connections(organisation_id,engine_id)
    values(v_id,'elevenlabs'),(v_id,'faster-whisper');
  insert into public.engine_connections(organisation_id,engine_id,config)
    values(v_id,'openai-translator','{"model":"gpt-5.4-mini-2026-03-17"}'::jsonb)
    returning id into v_translator;
  insert into public.workspace_engine_settings(organisation_id,translator_connection_id)
    values(v_id,v_translator);
  insert into public.audit_events(organisation_id,actor_id,action)
    values(v_id,p_user_id,'workspace.created');
  return v_id;
end $$;
