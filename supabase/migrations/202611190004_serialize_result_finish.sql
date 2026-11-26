-- Progressive windows and the final pass for one recording can finish close
-- together. Serialize only the short result-claim section per conversation so
-- concurrent finish_job calls cannot take job/run/conversation locks in an
-- order that deadlocks with recording confirmation or scheduling.
create or replace function public.finish_job(p_job_id uuid,p_worker_id text,p_lease_token uuid,p_fence bigint,p_result jsonb) returns boolean
language plpgsql security definer set search_path='' as $$
declare j public.processing_jobs; ec public.engine_connections; c public.conversations; v_msg bigint; v_reason text;
begin
  select * into j from public.processing_jobs where id=p_job_id;
  if not found then return false; end if;
  perform pg_advisory_xact_lock(hashtextextended('finish:'||j.conversation_id::text,0));
  select * into c from public.conversations where id=j.conversation_id;
  if j.connection_id is not null then select * into ec from public.engine_connections where id=j.connection_id; end if;
  select * into j from public.processing_jobs where id=p_job_id for update;
  if j.state='succeeded' then return j.worker_id=p_worker_id and j.lease_token=p_lease_token and j.fence=p_fence and j.result=p_result and (c.deleted_at is null or j.kind='delete'); end if;
  if j.state<>'running' or j.worker_id is distinct from p_worker_id or j.lease_token is distinct from p_lease_token or j.fence<>p_fence or j.lease_until<=now() then v_reason:='stale_lease';
  elsif (c.deleted_at is not null and j.kind<>'delete') or j.cancelled_at is not null or exists(select 1 from public.processing_runs where id=j.run_id and (state='cancelled' or cancelled_at is not null)) then v_reason:='cancelled';
  elsif j.inference_worker_id is not null and not exists(select 1 from public.inference_workers where id=j.inference_worker_id and revoked_at is null and state<>'revoked') then v_reason:='worker_revoked';
  elsif j.connection_id is not null and not ((ec.generation=j.connection_generation and ec.state in ('connected','degraded')) or (j.finish_in_flight and j.submission_state in ('accepted','received'))) then v_reason:='connection_fenced';
  end if;
  if v_reason is not null then
    insert into recorder_private.rejected_results(job_id,fence,reason) values(j.id,p_fence,v_reason);
    return false;
  end if;
  if p_result is null or jsonb_typeof(p_result)<>'object' then raise exception 'invalid_result' using errcode='23514'; end if;
  update public.processing_jobs set state='succeeded',result=p_result,submission_state='received',completed_at=now(),updated_at=now() where id=j.id;
  select msg_id into v_msg from recorder_private.queue_receipts where job_id=j.id;
  if v_msg is not null then perform pgmq.archive('recorder_jobs',v_msg); delete from recorder_private.queue_receipts where job_id=j.id; end if;
  return true;
end $$;
