-- Rollback-only assertions for the disposable native test cluster. This suite
-- refuses real Supabase credentials and never submits provider requests.
\set ON_ERROR_STOP on
begin;
set local statement_timeout='30s';
do $$begin
  if to_regclass('auth.guru_native_emulation_marker') is null then
    raise exception 'native_test_fixture_required';end if;
end $$;
create function pg_temp.assert_true(v boolean,label text) returns void language plpgsql as $$begin
  if v is distinct from true then raise exception 'ASSERTION FAILED: %',label;end if;
  raise notice 'PASS: %',label;
end $$;
create function pg_temp.assert_rejected(command text,codes text[],label text) returns void language plpgsql as $$begin
  begin execute command;
  exception when others then
    if sqlstate=any(codes) then raise notice 'PASS: % [%]',label,sqlstate;return;end if;
    raise exception 'ASSERTION FAILED: % wrong error %',label,sqlstate;
  end;
  raise exception 'ASSERTION FAILED: % unexpectedly allowed',label;
end $$;
do $$begin execute format('grant usage on schema %I to authenticated,anon,service_role',(select nspname from pg_namespace where oid=pg_my_temp_schema()));end $$;
insert into auth.users(id,email,email_confirmed_at) values
  ('66000000-0000-0000-0000-000000000001','openai-owner@example.invalid',now()),
  ('66000000-0000-0000-0000-000000000002','openai-outsider@example.invalid',now());
create temp table openai_test_ids(key text primary key,id uuid);
insert into openai_test_ids values
  ('workspace',public.create_workspace('66000000-0000-0000-0000-000000000001','OpenAI native test')),
  ('other_workspace',public.create_workspace('66000000-0000-0000-0000-000000000002','Other native test'));
insert into openai_test_ids select engine_id,id from engine_connections where organisation_id=(select id from openai_test_ids where key='workspace');
insert into openai_test_ids select 'other_openai',id from engine_connections where organisation_id=(select id from openai_test_ids where key='other_workspace') and engine_id='openai-translator';
select pg_temp.assert_true((select count(*)=4 from engine_connections where organisation_id=(select id from openai_test_ids where key='workspace')),'new workspace seeds four independent provider connections');
select pg_temp.assert_true((select s.translator_connection_id=c.id from workspace_engine_settings s join engine_connections c on c.organisation_id=s.organisation_id and c.engine_id='openai-translator' where s.organisation_id=(select id from openai_test_ids where key='workspace')),'new workspace selects OpenAI for text translation');
select pg_temp.assert_true((select bool_and(state='disconnected') from engine_connections where organisation_id=(select id from openai_test_ids where key='workspace')),'new provider connections remain disconnected');
select pg_temp.assert_true((select count(*)=0 from provider_secrets where connection_id in (select id from engine_connections where organisation_id=(select id from openai_test_ids where key='workspace'))),'seeding does not invent or copy credentials');
select pg_temp.assert_rejected($q$update engine_connections set config='{"model":"other-model"}' where id=(select id from openai_test_ids where key='openai-translator')$q$,array['23514'],'OpenAI model cannot silently change');
select pg_temp.assert_rejected($q$update workspace_engine_settings set translator_connection_id=(select id from openai_test_ids where key='elevenlabs') where organisation_id=(select id from openai_test_ids where key='workspace')$q$,array['23514'],'speech engine cannot be selected as translator');
select pg_temp.assert_rejected($q$update workspace_engine_settings set translator_connection_id=(select id from openai_test_ids where key='other_openai') where organisation_id=(select id from openai_test_ids where key='workspace')$q$,array['23514','23503'],'OpenAI translator cannot cross workspace');
update engine_connections set state='connected',tested_at=now() where organisation_id=(select id from openai_test_ids where key='workspace');
insert into customers(id,organisation_id,owner_id,name)
  values('6c000000-0000-0000-0000-000000000001',(select id from openai_test_ids where key='workspace'),'66000000-0000-0000-0000-000000000001','Consent fixture');
insert into conversations(id,organisation_id,owner_id,customer_id,customer_name_snapshot,salesperson_name_snapshot,consent_confirmed)
  values('67000000-0000-0000-0000-000000000001',(select id from openai_test_ids where key='workspace'),'66000000-0000-0000-0000-000000000001','6c000000-0000-0000-0000-000000000001','Consent fixture','Owner',true);
insert into processing_runs(id,conversation_id,organisation_id,connection_id,connection_generation,engine_id,model_version,translation_strategy,translator_connection_id,translator_generation,kind,idempotency_key)
  values('68000000-0000-0000-0000-000000000001','67000000-0000-0000-0000-000000000001',(select id from openai_test_ids where key='workspace'),(select id from openai_test_ids where key='elevenlabs'),1,'elevenlabs','scribe_v2','azure',(select id from openai_test_ids where key='azure-translator'),1,'production','old-azure-run');
insert into processing_runs(id,conversation_id,organisation_id,connection_id,connection_generation,engine_id,model_version,translation_strategy,translator_connection_id,translator_generation,config_snapshot,kind,idempotency_key)
  values('68000000-0000-0000-0000-000000000002','67000000-0000-0000-0000-000000000001',(select id from openai_test_ids where key='workspace'),(select id from openai_test_ids where key='elevenlabs'),1,'elevenlabs','scribe_v2','openai',(select id from openai_test_ids where key='openai-translator'),1,'{"translation":{"provider":"openai-translator","config":{"model":"gpt-5.6-luna"}}}','comparison','new-openai-run');
select pg_temp.assert_true(exists(select 1 from processing_runs where id='68000000-0000-0000-0000-000000000002' and translation_strategy='openai'),'OpenAI run snapshots are accepted');
select pg_temp.assert_rejected($q$insert into processing_runs(conversation_id,organisation_id,connection_id,connection_generation,engine_id,model_version,translation_strategy,translator_connection_id,translator_generation,kind,idempotency_key) values('67000000-0000-0000-0000-000000000001',(select id from openai_test_ids where key='workspace'),(select id from openai_test_ids where key='elevenlabs'),1,'elevenlabs','scribe_v2','azure',(select id from openai_test_ids where key='openai-translator'),1,'comparison','provider-mismatch')$q$,array['23514'],'run strategy must match translator provider');
select pg_temp.assert_rejected($q$update processing_runs set translation_strategy='openai',translator_connection_id=(select id from openai_test_ids where key='openai-translator') where id='68000000-0000-0000-0000-000000000001'$q$,array['23514'],'historical Azure run snapshots stay immutable');
insert into processing_jobs(id,conversation_id,organisation_id,run_id,connection_id,connection_generation,kind,state,dedupe_key,payload)
  values('69000000-0000-0000-0000-000000000001','67000000-0000-0000-0000-000000000001',(select id from openai_test_ids where key='workspace'),'68000000-0000-0000-0000-000000000001',(select id from openai_test_ids where key='azure-translator'),1,'translate','blocked','blocked-azure','{"source_job_id":"6a000000-0000-0000-0000-000000000001"}');
select pg_temp.assert_rejected($q$select resume_translation_job('69000000-0000-0000-0000-000000000001','66000000-0000-0000-0000-000000000002')$q$,array['42501'],'outsider cannot rebind another workspace translation');
select pg_temp.assert_true(resume_translation_job('69000000-0000-0000-0000-000000000001','66000000-0000-0000-0000-000000000001'),'explicit retry binds selected OpenAI dependency');
select pg_temp.assert_true((select connection_id=(select id from openai_test_ids where key='openai-translator') and state='queued' and payload->>'translation_strategy'='openai' and payload->>'translator_model'='gpt-5.6-luna' and payload->>'source_job_id'='6a000000-0000-0000-0000-000000000001' from processing_jobs where id='69000000-0000-0000-0000-000000000001'),'retry preserves source reference and snapshots new translation provenance');
select pg_temp.assert_true((select translation_strategy='azure' and translator_connection_id=(select id from openai_test_ids where key='azure-translator') from processing_runs where id='68000000-0000-0000-0000-000000000001'),'dependency rebinding does not rewrite original run');
select pg_temp.assert_true(exists(select 1 from audit_events where conversation_id='67000000-0000-0000-0000-000000000001' and action='translation.dependency_bound' and metadata->>'old_provider'='azure-translator' and metadata->>'provider'='openai-translator'),'provider switch is audited with old and new dependencies');
select pg_temp.assert_true(not resume_translation_job('69000000-0000-0000-0000-000000000001','66000000-0000-0000-0000-000000000001'),'duplicate explicit retry cannot enqueue twice');
update processing_jobs set state='uncertain',submission_state='uncertain' where id='69000000-0000-0000-0000-000000000001';
select pg_temp.assert_rejected($q$select resume_translation_job('69000000-0000-0000-0000-000000000001','66000000-0000-0000-0000-000000000001')$q$,array['23514'],'uncertain paid submission requires reconciliation');
update processing_jobs set state='running',submission_state='not_submitted',worker_id='fixture-worker',lease_token='6b000000-0000-0000-0000-000000000001',lease_until=now()+interval '2 minutes',attempt=1 where id='69000000-0000-0000-0000-000000000001';
select pg_temp.assert_true(retry_job('69000000-0000-0000-0000-000000000001','fixture-worker','6b000000-0000-0000-0000-000000000001',1,'provider_model_access_denied',false),'model access rejection is recorded');
select pg_temp.assert_true((select state='blocked' from processing_jobs where id='69000000-0000-0000-0000-000000000001'),'model access failures block instead of repeating requests');
update processing_jobs set state='running',submission_state='submitting',worker_id='fixture-worker',lease_token='6b000000-0000-0000-0000-000000000001',lease_until=now()+interval '2 minutes' where id='69000000-0000-0000-0000-000000000001';
select pg_temp.assert_true(retry_job('69000000-0000-0000-0000-000000000001','fixture-worker','6b000000-0000-0000-0000-000000000001',1,'provider_model_access_denied',false),'submission boundary failure is recorded');
select pg_temp.assert_true((select state='uncertain' from processing_jobs where id='69000000-0000-0000-0000-000000000001'),'submission uncertainty wins over rejection classification');
update processing_jobs set state='running',submission_state='not_submitted',worker_id='fixture-worker',lease_token='6b000000-0000-0000-0000-000000000001',lease_until=now()+interval '2 minutes' where id='69000000-0000-0000-0000-000000000001';
select pg_temp.assert_true(retry_job('69000000-0000-0000-0000-000000000001','fixture-worker','6b000000-0000-0000-0000-000000000001',1,'translator_input_too_large',false),'oversized translation input rejection is recorded');
select pg_temp.assert_true((select state='failed' and submission_state='not_submitted' from processing_jobs where id='69000000-0000-0000-0000-000000000001'),'invalid source input fails before another paid attempt');
insert into usage_records(conversation_id,run_id,job_id,attempt,processing_kind,input_tokens,output_tokens,provider_model)
  values('67000000-0000-0000-0000-000000000001','68000000-0000-0000-0000-000000000001','69000000-0000-0000-0000-000000000001',1,'translation',100,50,'gpt-5.6-luna');
select pg_temp.assert_true((select input_tokens=100 and output_tokens=50 and estimated_cost is null from usage_records where job_id='69000000-0000-0000-0000-000000000001'),'reported token counts are preserved without inventing a price');
select pg_temp.assert_rejected($q$update usage_records set input_tokens=-1 where job_id='69000000-0000-0000-0000-000000000001'$q$,array['23514'],'negative token accounting rejected');
set local role authenticated;
select pg_temp.assert_rejected($q$select public.resume_translation_job('69000000-0000-0000-0000-000000000001','66000000-0000-0000-0000-000000000001')$q$,array['42501'],'browser cannot call trusted translation rebinding RPC');
reset role;
select pg_temp.assert_true((select prosecdef and proconfig @> array['search_path=""'] from pg_proc where oid='public.resume_translation_job(uuid,uuid)'::regprocedure),'translation retry definer function has hardened search path');
rollback;
