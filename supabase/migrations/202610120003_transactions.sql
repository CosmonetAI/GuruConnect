begin;
select pgmq.create('recorder_jobs');
select pgmq.create('recorder_dead_letters');
revoke all on all tables in schema pgmq from public,anon,authenticated;
revoke all on all sequences in schema pgmq from public,anon,authenticated;

create function public.create_workspace(p_user_id uuid,p_name text) returns uuid
language plpgsql security definer set search_path='' as $$
declare v_id uuid;
begin
  if not exists(select 1 from auth.users where id=p_user_id and email_confirmed_at is not null) then
    raise exception 'verified_email_required' using errcode='42501';
  end if;
  insert into public.organisations(name,created_by) values(p_name,p_user_id) returning id into v_id;
  insert into public.members(organisation_id,user_id,role) values(v_id,p_user_id,'owner');
  insert into public.workspace_engine_settings(organisation_id) values(v_id);
  insert into public.engine_connections(organisation_id,engine_id) values(v_id,'elevenlabs'),(v_id,'faster-whisper'),(v_id,'azure-translator');
  insert into public.audit_events(organisation_id,actor_id,action) values(v_id,p_user_id,'workspace.created');
  return v_id;
end $$;

create function public.accept_invitation(p_user_id uuid,p_token_hash text) returns uuid
language plpgsql security definer set search_path='' as $$
declare v_invite public.invites; v_email text;
begin
  select email into v_email from auth.users where id=p_user_id and email_confirmed_at is not null;
  if v_email is null then raise exception 'verified_email_required' using errcode='42501'; end if;
  select * into v_invite from public.invites where token_hash=p_token_hash for update;
  if not found or v_invite.revoked_at is not null or v_invite.expires_at<=now() or lower(v_email)<>lower(v_invite.email) then
    raise exception 'invalid_invitation' using errcode='42501';
  end if;
  if v_invite.accepted_by=p_user_id then return v_invite.organisation_id; end if;
  if v_invite.accepted_at is not null then raise exception 'invitation_used' using errcode='42501'; end if;
  if not exists(select 1 from public.members where organisation_id=v_invite.organisation_id and user_id=v_invite.invited_by and role in ('owner','admin') and status='active') then
    raise exception 'inviter_revoked' using errcode='42501';
  end if;
  -- Invites cannot escalate existing members or revive a revoked membership accidentally.
  if exists(select 1 from public.members where organisation_id=v_invite.organisation_id and user_id=p_user_id) then
    raise exception 'membership_exists' using errcode='23505';
  end if;
  insert into public.members(organisation_id,user_id,role) values(v_invite.organisation_id,p_user_id,v_invite.role);
  update public.invites set accepted_by=p_user_id,accepted_at=now() where id=v_invite.id;
  insert into public.audit_events(organisation_id,actor_id,action,metadata) values(v_invite.organisation_id,p_user_id,'invitation.accepted',jsonb_build_object('invite_id',v_invite.id));
  return v_invite.organisation_id;
end $$;

create function public.acquire_recording_lease(p_conversation_id uuid,p_user_id uuid,p_tab_id text,p_takeover boolean default false)
returns public.recording_leases language plpgsql security definer set search_path='' as $$
declare c public.conversations; l public.recording_leases;
begin
  perform pg_advisory_xact_lock(hashtextextended('recording:'||p_user_id::text,0));
  select * into c from public.conversations where id=p_conversation_id and owner_id=p_user_id and deleted_at is null for update;
  if not found or not exists(select 1 from public.members where organisation_id=c.organisation_id and user_id=p_user_id and status='active') then
    raise exception 'recording_forbidden' using errcode='42501';
  end if;
  if c.capture_state='deleted' or (c.capture_state='stopped' and c.manifest_hash is not null) then raise exception 'recording_finished' using errcode='23514'; end if;
  select * into l from public.recording_leases where user_id=p_user_id for update;
  if found and l.expires_at>now() then
    if l.conversation_id=p_conversation_id and l.tab_id=p_tab_id then return l; end if;
    raise exception 'recording_lease_busy' using errcode='55P03';
  end if;
  if found and not p_takeover then raise exception 'explicit_takeover_required' using errcode='55P03'; end if;
  if l.conversation_id is not null then
    update public.conversations set capture_state='interrupted',updated_at=now() where id=l.conversation_id and capture_state in ('recording','paused');
  end if;
  insert into public.recording_leases(user_id,organisation_id,conversation_id,tab_id,expires_at)
    values(p_user_id,c.organisation_id,c.id,p_tab_id,now()+interval '90 seconds')
    on conflict(user_id) do update set organisation_id=excluded.organisation_id,conversation_id=excluded.conversation_id,
      tab_id=excluded.tab_id,token=gen_random_uuid(),fence=public.recording_leases.fence+1,expires_at=excluded.expires_at
    returning * into l;
  return l;
end $$;

create function public.heartbeat_recording_lease(p_conversation_id uuid,p_user_id uuid,p_tab_id text,p_token uuid) returns boolean
language plpgsql security definer set search_path='' as $$
begin
  update public.recording_leases l set expires_at=now()+interval '90 seconds'
    where l.conversation_id=p_conversation_id and l.user_id=p_user_id and l.tab_id=p_tab_id and l.token=p_token and l.expires_at>now()
    and exists(select 1 from public.members m where m.organisation_id=l.organisation_id and m.user_id=l.user_id and m.status='active')
    and exists(select 1 from public.conversations c where c.id=l.conversation_id and c.deleted_at is null
      and c.capture_state<>'deleted' and (c.capture_state<>'stopped' or c.manifest_hash is null));
  return found;
end $$;

create function public.release_recording_lease(p_conversation_id uuid,p_user_id uuid,p_tab_id text,p_token uuid) returns boolean
language plpgsql security definer set search_path='' as $$
begin
  delete from public.recording_leases where conversation_id=p_conversation_id and user_id=p_user_id and tab_id=p_tab_id and token=p_token;
  return found;
end $$;

create function public.schedule_job(p_conversation_id uuid,p_run_id uuid,p_kind text,p_dedupe_key text,p_payload jsonb default '{}',
  p_window_id uuid default null,p_priority integer default 10,p_available_at timestamptz default now()) returns uuid
language plpgsql security definer set search_path='' as $$
declare c public.conversations; r public.processing_runs; v_id uuid; v_conn uuid; v_generation integer; v_type text:='main'; v_msg bigint;
begin
  select * into c from public.conversations where id=p_conversation_id and (deleted_at is null or p_kind='delete') for update;
  if not found then raise exception 'conversation_unavailable' using errcode='23514'; end if;
  if p_run_id is not null then
    select * into r from public.processing_runs where id=p_run_id and conversation_id=c.id and state<>'cancelled';
    if not found then raise exception 'run_unavailable' using errcode='23514'; end if;
    if p_kind='translate' then v_conn:=r.translator_connection_id;v_generation:=r.translator_generation;
    elsif p_kind in ('transcribe','diarize','finalise') then
      v_conn:=r.connection_id;v_generation:=r.connection_generation;
      if r.engine_id='faster-whisper' then v_type:='inference'; end if;
    end if;
  elsif p_kind in ('transcribe','translate','diarize','finalise') then raise exception 'run_required' using errcode='23514'; end if;
  insert into public.processing_jobs(conversation_id,organisation_id,run_id,window_id,connection_id,connection_generation,
    kind,worker_type,dedupe_key,payload,priority,available_at)
    values(c.id,c.organisation_id,p_run_id,p_window_id,v_conn,v_generation,p_kind,v_type,p_dedupe_key,p_payload,p_priority,p_available_at)
    on conflict(conversation_id,dedupe_key) do nothing returning id into v_id;
  if v_id is null then
    select id into v_id from public.processing_jobs where conversation_id=c.id and dedupe_key=p_dedupe_key;
    if not exists(select 1 from public.processing_jobs where id=v_id and run_id is not distinct from p_run_id and kind=p_kind and payload=p_payload and window_id is not distinct from p_window_id) then
      raise exception 'idempotency_conflict' using errcode='23514';
    end if;
    return v_id;
  end if;
  select msg_id into v_msg from (select pgmq.send('recorder_jobs',jsonb_build_object('job_id',v_id),greatest(0,ceil(extract(epoch from p_available_at-now()))::integer)) as msg_id) s;
  insert into recorder_private.queue_receipts(job_id,msg_id) values(v_id,v_msg);
  return v_id;
end $$;

-- Stale attempts with external uncertainty are held for explicit reconciliation.
-- A crash before external submission can be safely retried; accepted/submitting
-- calls are never blindly duplicated by the lease reaper.
create function public.reap_expired_jobs() returns integer language plpgsql security definer set search_path='' as $$
declare j public.processing_jobs; n integer:=0; v_msg bigint;
begin
  perform recorder_private.retire_queue_receipts(100);
  for j in select * from public.processing_jobs where state='running' and lease_until<=now() for update skip locked loop
    update public.processing_jobs set state=case when submission_state in ('submitting','accepted','uncertain') then 'uncertain'
      when attempt>=max_attempts then 'dead' else 'retry' end,
      error_code='lease_expired',worker_id=null,lease_token=null,lease_until=null,available_at=now()+interval '5 seconds',updated_at=now() where id=j.id;
    select msg_id into v_msg from recorder_private.queue_receipts where job_id=j.id;
    if j.submission_state in ('submitting','accepted','uncertain') or j.attempt>=j.max_attempts then
      perform pgmq.archive('recorder_jobs',v_msg);
      delete from recorder_private.queue_receipts where job_id=j.id;
      if j.attempt>=j.max_attempts then perform pgmq.send('recorder_dead_letters',jsonb_build_object('job_id',j.id,'error_code','lease_expired')); end if;
    else perform pgmq.set_vt('recorder_jobs',v_msg,5); end if;
    n:=n+1;
  end loop;
  return n;
end $$;

create function public.claim_jobs(p_worker_id text,p_worker_type text default 'main',p_limit integer default 1,p_lease_seconds integer default 180,
  p_connection_id uuid default null,p_inference_worker_id uuid default null) returns setof public.processing_jobs
language plpgsql security definer set search_path='' as $$
declare j public.processing_jobs; conn public.engine_connections; w public.inference_workers; v_scope text; v_max integer;
  v_active integer; v_orgmax integer; v_msg bigint; n integer:=0;
begin
  if length(p_worker_id) not between 1 and 160 or p_worker_type not in ('main','inference') or p_limit not between 1 and 20 or p_lease_seconds not between 30 and 900 then
    raise exception 'invalid_claim_parameters' using errcode='22023';
  end if;
  if p_worker_type='inference' then
    select * into w from public.inference_workers where id=p_inference_worker_id and connection_id=p_connection_id and revoked_at is null and state in ('ready','busy') and last_seen_at>now()-interval '2 minutes' for share;
    if not found then raise exception 'worker_unavailable' using errcode='42501'; end if;
  elsif p_inference_worker_id is not null or p_connection_id is not null then raise exception 'invalid_main_scope' using errcode='22023'; end if;
  perform public.reap_expired_jobs();
  for j in
    select jobs.* from public.processing_jobs jobs join recorder_private.queue_receipts qr on qr.job_id=jobs.id
      join pgmq.q_recorder_jobs q on q.msg_id=qr.msg_id join public.conversations c on c.id=jobs.conversation_id
      where jobs.state in ('queued','retry') and jobs.available_at<=now() and q.vt<=now() and jobs.worker_type=p_worker_type
        and jobs.attempt<jobs.max_attempts and (c.deleted_at is null or jobs.kind='delete') and jobs.cancelled_at is null
        and (p_worker_type='main' or (jobs.connection_id=p_connection_id and jobs.organisation_id=w.organisation_id and jobs.connection_generation=w.connection_generation
          and exists(select 1 from public.inference_artifacts ia where ia.job_id=jobs.id and ia.kind='input')))
      order by (select count(*) from public.processing_jobs active where active.organisation_id=jobs.organisation_id and active.state='running' and active.lease_until>now()),
        greatest(0,jobs.priority-floor(extract(epoch from now()-jobs.created_at)/300)::integer),jobs.created_at,jobs.id
      limit p_limit*50 for update of jobs,q skip locked
  loop
    -- Workspace then shared-credential admission locks make counting atomic across replicas.
    if not pg_try_advisory_xact_lock(hashtextextended('workspace:'||j.organisation_id::text,0)) then continue;end if;
    select max_concurrent_jobs into v_orgmax from public.organisations where id=j.organisation_id;
    if (select count(*) from public.processing_jobs where organisation_id=j.organisation_id and state='running' and lease_until>now())>=v_orgmax then continue; end if;
    if j.run_id is not null and exists(select 1 from public.processing_runs where id=j.run_id and (state='cancelled' or cancelled_at is not null)) then
      update public.processing_jobs set state='cancelled',updated_at=now() where id=j.id; continue;
    end if;
    if j.connection_id is not null then
      begin
        select * into conn from public.engine_connections where id=j.connection_id for share nowait;
      exception when lock_not_available then continue;
      end;
      if conn.state not in ('connected','degraded') or conn.generation<>j.connection_generation then
        update public.processing_jobs set state='blocked',error_code='connection_unavailable',updated_at=now() where id=j.id; continue;
      end if;
      v_scope:=coalesce(conn.credential_scope,conn.id::text);
      if not pg_try_advisory_xact_lock(hashtextextended('provider:'||v_scope,0)) then continue;end if;
      insert into recorder_private.provider_limits(scope,max_concurrency) values(v_scope,conn.max_concurrency)
        on conflict(scope) do update set max_concurrency=least(recorder_private.provider_limits.max_concurrency,excluded.max_concurrency);
      select max_concurrency into v_max from recorder_private.provider_limits where scope=v_scope;
      select count(*) into v_active from public.processing_jobs a join public.engine_connections ac on ac.id=a.connection_id
        where a.state='running' and a.lease_until>now() and coalesce(ac.credential_scope,ac.id::text)=v_scope;
      if v_active>=v_max then continue; end if;
      -- Reserve capacity for progressive work while comparison/full-session passes queue.
      if (j.kind='finalise' or exists(select 1 from public.processing_runs where id=j.run_id and kind in ('comparison','replay')))
        and v_max>1 and v_active>=greatest(1,v_max/2) and exists(select 1 from public.processing_jobs pj join public.processing_runs pr on pr.id=pj.run_id
          join public.engine_connections pc on pc.id=pj.connection_id where pj.state in ('queued','retry') and pj.available_at<=now()
          and pj.kind='transcribe' and pr.kind='production' and coalesce(pc.credential_scope,pc.id::text)=v_scope) then continue; end if;
    elsif j.kind in ('transcribe','translate','diarize','finalise') then
      update public.processing_jobs set state='blocked',error_code='connection_unconfigured',updated_at=now() where id=j.id; continue;
    end if;
    -- Budget checks use configured limits; absence of a limit means no artificial cap.
    if j.kind in ('transcribe','diarize','finalise') and exists(select 1 from public.workspace_engine_settings s where s.organisation_id=j.organisation_id
      and s.budget_policy ? 'max_audio_seconds_per_day' and
      (select coalesce(sum(u.audio_seconds),0) from public.usage_records u join public.conversations uc on uc.id=u.conversation_id
        where uc.organisation_id=j.organisation_id and u.created_at>=date_trunc('day',now()))
        +(select coalesce(sum(greatest(0,coalesce((a.payload->>'sample_end')::numeric,0)-coalesce((a.payload->>'sample_start')::numeric,0))/16000),0)
          from public.processing_jobs a where a.organisation_id=j.organisation_id and a.state='running' and a.kind in ('transcribe','diarize','finalise') and a.lease_until>now()
          and not exists(select 1 from public.usage_records u where u.job_id=a.id and u.attempt=a.attempt and u.audio_seconds>0))
        +greatest(0,coalesce((j.payload->>'sample_end')::numeric,0)-coalesce((j.payload->>'sample_start')::numeric,0))/16000
        > (s.budget_policy->>'max_audio_seconds_per_day')::numeric) then
      update public.processing_jobs set state='blocked',error_code='workspace_budget_exhausted',updated_at=now() where id=j.id; continue;
    end if;
    if j.kind='translate' and exists(select 1 from public.workspace_engine_settings s where s.organisation_id=j.organisation_id
      and s.budget_policy ? 'max_translation_characters_per_day' and
      (select coalesce(sum(u.translation_characters),0) from public.usage_records u join public.conversations uc on uc.id=u.conversation_id
        where uc.organisation_id=j.organisation_id and u.created_at>=date_trunc('day',now()))
      +(select coalesce(sum(length(t.source_text)),0) from public.processing_jobs a join public.transcript_turns t on t.version_id=(a.payload->>'source_version_id')::uuid
        where a.organisation_id=j.organisation_id and a.state='running' and a.kind='translate' and a.lease_until>now()
        and not exists(select 1 from public.usage_records u where u.job_id=a.id and u.attempt=a.attempt and u.translation_characters>0))
      +(select coalesce(sum(length(t.source_text)),0) from public.transcript_turns t where t.version_id=(j.payload->>'source_version_id')::uuid)
      > (s.budget_policy->>'max_translation_characters_per_day')::numeric) then
      update public.processing_jobs set state='blocked',error_code='workspace_budget_exhausted',updated_at=now() where id=j.id;continue;
    end if;
    update public.processing_jobs set state='running',worker_id=p_worker_id,inference_worker_id=p_inference_worker_id,
      lease_token=gen_random_uuid(),fence=fence+1,lease_until=now()+make_interval(secs=>p_lease_seconds),attempt=attempt+1,updated_at=now()
      where id=j.id returning * into j;
    select msg_id into v_msg from recorder_private.queue_receipts where job_id=j.id;
    perform pgmq.set_vt('recorder_jobs',v_msg,p_lease_seconds);
    return next j;n:=n+1;if n>=p_limit then exit;end if;
  end loop;
end $$;

create function public.renew_job(p_job_id uuid,p_worker_id text,p_lease_token uuid,p_fence bigint,p_lease_seconds integer default 180) returns boolean
language plpgsql security definer set search_path='' as $$
declare v_msg bigint;
begin
  if p_lease_seconds not between 30 and 900 then raise exception 'invalid_lease_duration'; end if;
  update public.processing_jobs j set lease_until=now()+make_interval(secs=>p_lease_seconds),updated_at=now()
    where j.id=p_job_id and j.state='running' and j.worker_id=p_worker_id and j.lease_token=p_lease_token and j.fence=p_fence and j.lease_until>now()
    and j.cancelled_at is null and exists(select 1 from public.conversations c where c.id=j.conversation_id and (c.deleted_at is null or j.kind='delete'))
    and (j.inference_worker_id is null or exists(select 1 from public.inference_workers w where w.id=j.inference_worker_id and w.revoked_at is null))
    and (j.connection_id is null or exists(select 1 from public.engine_connections ec where ec.id=j.connection_id
      and ((ec.generation=j.connection_generation and ec.state in ('connected','degraded')) or j.finish_in_flight)));
  if not found then return false; end if;
  select msg_id into v_msg from recorder_private.queue_receipts where job_id=p_job_id;
  perform pgmq.set_vt('recorder_jobs',v_msg,p_lease_seconds);
  return true;
end $$;

create function public.finish_job(p_job_id uuid,p_worker_id text,p_lease_token uuid,p_fence bigint,p_result jsonb) returns boolean
language plpgsql security definer set search_path='' as $$
declare j public.processing_jobs; ec public.engine_connections; c public.conversations; v_msg bigint; v_reason text;
begin
  select * into j from public.processing_jobs where id=p_job_id;
  if not found then return false; end if;
  select * into c from public.conversations where id=j.conversation_id for share;
  if j.connection_id is not null then select * into ec from public.engine_connections where id=j.connection_id for share; end if;
  select * into j from public.processing_jobs where id=p_job_id for update;
  -- Identical result retry is safe; transcript_versions.job_id enforces one publication.
  if j.state='succeeded' then return j.worker_id=p_worker_id and j.lease_token=p_lease_token and j.fence=p_fence and j.result=p_result and (c.deleted_at is null or j.kind='delete'); end if;
  if j.state<>'running' or j.worker_id is distinct from p_worker_id or j.lease_token is distinct from p_lease_token or j.fence<>p_fence or j.lease_until<=now() then v_reason:='stale_lease';
  elsif (c.deleted_at is not null and j.kind<>'delete') or j.cancelled_at is not null or exists(select 1 from public.processing_runs where id=j.run_id and (state='cancelled' or cancelled_at is not null)) then v_reason:='cancelled';
  elsif j.inference_worker_id is not null and not exists(select 1 from public.inference_workers where id=j.inference_worker_id and revoked_at is null and state<>'revoked') then v_reason:='worker_revoked';
  elsif j.connection_id is not null and not ((ec.generation=j.connection_generation and ec.state in ('connected','degraded')) or
    (j.finish_in_flight and j.submission_state in ('accepted','received'))) then v_reason:='connection_fenced';
  end if;
  if v_reason is not null then
    insert into recorder_private.rejected_results(job_id,fence,reason) values(j.id,p_fence,v_reason);
    return false;
  end if;
  if p_result is null or jsonb_typeof(p_result)<>'object' then raise exception 'invalid_result' using errcode='23514';end if;
  update public.processing_jobs set state='succeeded',result=p_result,submission_state='received',completed_at=now(),updated_at=now() where id=j.id;
  select msg_id into v_msg from recorder_private.queue_receipts where job_id=j.id;
  if v_msg is not null then perform pgmq.archive('recorder_jobs',v_msg);delete from recorder_private.queue_receipts where job_id=j.id;end if;
  return true;
end $$;

create function public.retry_job(p_job_id uuid,p_worker_id text,p_lease_token uuid,p_fence bigint,p_error_code text,p_uncertain boolean default false) returns boolean
language plpgsql security definer set search_path='' as $$
declare j public.processing_jobs; v_delay integer; v_msg bigint; v_state text;
begin
  select * into j from public.processing_jobs where id=p_job_id and state='running' and worker_id=p_worker_id and lease_token=p_lease_token and fence=p_fence and lease_until>now() for update;
  if not found then return false;end if;
  if p_error_code !~ '^[a-z0-9_]{1,80}$' then raise exception 'unsafe_error_code';end if;
  v_state:=case when p_uncertain or j.submission_state in ('submitting','accepted','uncertain') then 'uncertain'
    when p_error_code in ('invalid_credentials','connection_unavailable','translator_unavailable') then 'blocked'
    when p_error_code in ('invalid_audio','invalid_result','input_hash_mismatch','size_limit','permission_denied') then 'failed'
    when j.attempt>=j.max_attempts then 'dead' else 'retry' end;
  v_delay:=least(3600,(power(2,least(j.attempt,10))*3)::integer)+floor(random()*5)::integer;
  update public.processing_jobs set state=v_state,error_code=p_error_code,lease_until=null,worker_id=null,lease_token=null,
    available_at=now()+make_interval(secs=>v_delay),updated_at=now(),submission_state=case when v_state='uncertain' then 'uncertain' else submission_state end where id=j.id;
  select msg_id into v_msg from recorder_private.queue_receipts where job_id=j.id;
  if v_state='retry' then perform pgmq.set_vt('recorder_jobs',v_msg,v_delay);
  else
    if v_msg is not null then perform pgmq.archive('recorder_jobs',v_msg);delete from recorder_private.queue_receipts where job_id=j.id;end if;
    if v_state='dead' then perform pgmq.send('recorder_dead_letters',jsonb_build_object('job_id',j.id,'error_code',p_error_code));end if;
  end if;
  return true;
end $$;

create function public.resume_job(p_job_id uuid,p_actor_id uuid,p_reconciled_not_submitted boolean default false) returns boolean
language plpgsql security definer set search_path='' as $$
declare j public.processing_jobs; ec public.engine_connections; v_msg bigint;
begin
  select * into j from public.processing_jobs where id=p_job_id for update;
  if not found or j.state not in ('blocked','failed','dead','uncertain') then return false;end if;
  if not exists(select 1 from public.members where organisation_id=j.organisation_id and user_id=p_actor_id and status='active' and role in ('owner','admin'))
    and not exists(select 1 from public.conversations c join public.members m on m.organisation_id=c.organisation_id and m.user_id=c.owner_id
      where c.id=j.conversation_id and c.owner_id=p_actor_id and m.status='active') then raise exception 'resume_forbidden' using errcode='42501';end if;
  if exists(select 1 from public.conversations where id=j.conversation_id and deleted_at is not null) then return false;end if;
  if j.state='uncertain' and not p_reconciled_not_submitted then raise exception 'submission_reconciliation_required' using errcode='23514';end if;
  if j.connection_id is not null then
    select * into ec from public.engine_connections where id=j.connection_id for share;
    if ec.state not in ('connected','degraded') then raise exception 'connection_unavailable' using errcode='23514';end if;
    -- Explicit retry records transition to a new credential generation in the audit.
    update public.processing_jobs set connection_generation=ec.generation where id=j.id;
  end if;
  update public.processing_jobs set state='queued',attempt=0,fence=fence+1,lease_until=null,worker_id=null,lease_token=null,
    available_at=now(),submission_state='not_submitted',request_reference=null,error_code=null,finish_in_flight=false,updated_at=now() where id=j.id;
  select msg_id into v_msg from recorder_private.queue_receipts where job_id=j.id;
  if v_msg is null then
    select pgmq.send('recorder_jobs',jsonb_build_object('job_id',j.id)) into v_msg;
    insert into recorder_private.queue_receipts(job_id,msg_id) values(j.id,v_msg);
  else perform pgmq.set_vt('recorder_jobs',v_msg,0);end if;
  insert into public.audit_events(organisation_id,conversation_id,actor_id,action,metadata) values(j.organisation_id,j.conversation_id,p_actor_id,'job.resumed',jsonb_build_object('job_id',j.id,'old_generation',j.connection_generation,'new_generation',ec.generation));
  return true;
end $$;

create function public.disconnect_connection(p_connection_id uuid,p_actor_id uuid,p_finish_accepted boolean default false,p_disable_only boolean default false) returns jsonb
language plpgsql security definer set search_path='' as $$
declare ec public.engine_connections; v_queued integer; v_running integer;
begin
  select * into ec from public.engine_connections where id=p_connection_id for update;
  if not found or not exists(select 1 from public.members where organisation_id=ec.organisation_id and user_id=p_actor_id and status='active' and role in ('owner','admin')) then
    raise exception 'connection_forbidden' using errcode='42501';end if;
  select count(*) filter(where state in ('queued','retry')),count(*) filter(where state='running') into v_queued,v_running
    from public.processing_jobs where connection_id=ec.id and connection_generation=ec.generation;
  update public.processing_jobs set state='blocked',error_code='connection_disconnected',updated_at=now()
    where connection_id=ec.id and state in ('queued','retry');
  update public.processing_jobs set finish_in_flight=(p_finish_accepted and submission_state in ('accepted','received')),
    state=case when p_finish_accepted and submission_state in ('accepted','received') then 'running' else 'blocked' end,
    error_code=case when p_finish_accepted and submission_state in ('accepted','received') then null else 'connection_disconnected' end,updated_at=now()
    where connection_id=ec.id and state='running';
  update public.engine_connections set state=case when p_disable_only then 'disabled' else 'disconnected' end,
    generation=generation+1,updated_at=now() where id=ec.id;
  if not p_disable_only then
    delete from public.provider_secrets where connection_id=ec.id;
    update public.inference_workers set state='revoked',revoked_at=now() where connection_id=ec.id and revoked_at is null;
  end if;
  insert into public.audit_events(organisation_id,actor_id,action,metadata) values(ec.organisation_id,p_actor_id,
    case when p_disable_only then 'connection.disabled' else 'connection.disconnected' end,
    jsonb_build_object('connection_id',ec.id,'old_generation',ec.generation,'finish_accepted',p_finish_accepted,'queued',v_queued,'running',v_running));
  return jsonb_build_object('queued',v_queued,'running',v_running,'generation',ec.generation+1);
end $$;

-- RPCs are trusted-service operations only. API authorisation is still required.
do $$ declare f record;begin
  for f in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public'
    and p.proname in ('create_workspace','accept_invitation','acquire_recording_lease','heartbeat_recording_lease','release_recording_lease',
      'schedule_job','reap_expired_jobs','claim_jobs','renew_job','finish_job','retry_job','resume_job','disconnect_connection') loop
    execute format('revoke all on function %s from public,anon,authenticated',f.signature);
    execute format('grant execute on function %s to service_role',f.signature);
  end loop;
end $$;
commit;
