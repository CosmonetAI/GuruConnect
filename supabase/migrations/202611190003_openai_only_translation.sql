-- The recording lifecycle uses ElevenLabs for speech-to-text and OpenAI for
-- non-English translation. English source turns are completed by the
-- transcription worker and never enqueue a translation job.
--
-- Keep explicit retries on the same provider contract as new recordings.
create or replace function public.resume_translation_job(p_job_id uuid,p_actor_id uuid) returns boolean
language plpgsql security definer set search_path='' as $$
declare j public.processing_jobs;ec public.engine_connections;old_ec public.engine_connections;v_resumed boolean;
begin
  select * into j from public.processing_jobs where id=p_job_id;
  if not found or j.kind<>'translate' then return false;end if;
  perform 1 from public.conversations where id=j.conversation_id for update;
  select c.* into ec from public.workspace_engine_settings s join public.engine_connections c
    on c.id=s.translator_connection_id and c.organisation_id=s.organisation_id
    where s.organisation_id=j.organisation_id and c.engine_id='openai-translator'
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
    payload=payload||jsonb_build_object('translation_strategy','openai',
      'translation_provider','openai-translator','translator_model',ec.config->>'model'),updated_at=now() where id=j.id;
  select public.resume_job(j.id,p_actor_id,false) into v_resumed;
  if v_resumed then
    insert into public.audit_events(organisation_id,conversation_id,actor_id,action,metadata)
      values(j.organisation_id,j.conversation_id,p_actor_id,'translation.dependency_bound',jsonb_build_object(
        'job_id',j.id,'old_connection_id',j.connection_id,'old_generation',j.connection_generation,'old_provider',old_ec.engine_id,
        'connection_id',ec.id,'generation',ec.generation,'provider','openai-translator','model',ec.config->>'model'));
  end if;
  return v_resumed;
end $$;
