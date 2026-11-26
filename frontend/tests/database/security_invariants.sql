-- Rollback-only: authorised designated development DB, never production.
-- SQL RLS tests exercise real Postgres roles; no claim of Storage HTTP/JWT verification.
\set ON_ERROR_STOP on
begin;
set local statement_timeout='30s';
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
select pg_temp.assert_true(not exists(select 1 from public.processing_jobs where state in ('queued','running','retry','uncertain')),'designated development queue is quiescent before fixture tests');
insert into auth.users(id,email,email_confirmed_at,raw_user_meta_data) values
 ('a0000000-0000-0000-0000-000000000001','guru-db-owner@example.invalid',now(),'{"display_name":"Owner"}'),
 ('a0000000-0000-0000-0000-000000000002','guru-db-sales-a@example.invalid',now(),'{"display_name":"Sales A"}'),
 ('a0000000-0000-0000-0000-000000000003','guru-db-sales-b@example.invalid',now(),'{"display_name":"Sales B"}'),
 ('a0000000-0000-0000-0000-000000000004','guru-db-outsider@example.invalid',now(),'{}');
insert into public.organisations(id,name,created_by) values
 ('b0000000-0000-0000-0000-000000000001','DB isolation test','a0000000-0000-0000-0000-000000000001'),
 ('b0000000-0000-0000-0000-000000000002','DB other workspace','a0000000-0000-0000-0000-000000000004');
insert into public.members(organisation_id,user_id,role) values
 ('b0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000001','owner'),
 ('b0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000002','salesperson'),
 ('b0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000003','salesperson'),
 ('b0000000-0000-0000-0000-000000000002','a0000000-0000-0000-0000-000000000004','owner');
insert into public.workspace_engine_settings(organisation_id) values('b0000000-0000-0000-0000-000000000001'),('b0000000-0000-0000-0000-000000000002');
insert into public.customers(id,organisation_id,owner_id,name) values
 ('c0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000002','Sam'),
 ('c0000000-0000-0000-0000-000000000002','b0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000003','Sam'),
 ('c0000000-0000-0000-0000-000000000003','b0000000-0000-0000-0000-000000000002','a0000000-0000-0000-0000-000000000004','Sam');
insert into public.conversations(id,organisation_id,owner_id,customer_id,customer_name_snapshot,salesperson_name_snapshot,consent_confirmed) values
 ('d0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000002','c0000000-0000-0000-0000-000000000001','Sam','Sales A',true),
 ('d0000000-0000-0000-0000-000000000002','b0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000003','c0000000-0000-0000-0000-000000000002','Sam','Sales B',true),
 ('d0000000-0000-0000-0000-000000000003','b0000000-0000-0000-0000-000000000002','a0000000-0000-0000-0000-000000000004','c0000000-0000-0000-0000-000000000003','Sam','Other',true);
insert into public.engine_connections(id,organisation_id,engine_id,state,credential_scope,max_concurrency) values
 ('e0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000001','elevenlabs','connected','test-shared-credential',1),
 ('e0000000-0000-0000-0000-000000000002','b0000000-0000-0000-0000-000000000001','faster-whisper','connected',null,1),
 ('e0000000-0000-0000-0000-000000000003','b0000000-0000-0000-0000-000000000001','azure-translator','connected',null,1),
 ('e0000000-0000-0000-0000-000000000004','b0000000-0000-0000-0000-000000000002','elevenlabs','connected','test-shared-credential',1);
insert into public.provider_secrets(connection_id,encrypted_value) values('e0000000-0000-0000-0000-000000000001','test-encrypted-not-a-real-credential');
insert into public.recording_parts(id,conversation_id,ordinal) values
 ('f0000000-0000-0000-0000-000000000001','d0000000-0000-0000-0000-000000000001',0),
 ('f0000000-0000-0000-0000-000000000002','d0000000-0000-0000-0000-000000000002',0);
insert into public.audio_chunks(conversation_id,part_id,sequence,sample_start,sample_count,bytes,sha256,object_path) values
 ('d0000000-0000-0000-0000-000000000001','f0000000-0000-0000-0000-000000000001',0,0,80000,160044,repeat('a',64),'b0000000-0000-0000-0000-000000000001/a0000000-0000-0000-0000-000000000002/d0000000-0000-0000-0000-000000000001/session-audio/no-phone/f0000000-0000-0000-0000-000000000001/0.wav'),
 ('d0000000-0000-0000-0000-000000000002','f0000000-0000-0000-0000-000000000002',0,0,80000,160044,repeat('b',64),'b0000000-0000-0000-0000-000000000001/a0000000-0000-0000-0000-000000000003/d0000000-0000-0000-0000-000000000002/session-audio/no-phone/f0000000-0000-0000-0000-000000000002/0.wav');
insert into public.processing_runs(id,conversation_id,organisation_id,connection_id,connection_generation,engine_id,model_version,translation_strategy,idempotency_key,kind) values
 ('10000000-0000-0000-0000-000000000001','d0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000001','e0000000-0000-0000-0000-000000000001',1,'elevenlabs','scribe_v2','azure','production','production'),
 ('10000000-0000-0000-0000-000000000002','d0000000-0000-0000-0000-000000000002','b0000000-0000-0000-0000-000000000001','e0000000-0000-0000-0000-000000000001',1,'elevenlabs','scribe_v2','azure','production','production'),
 ('10000000-0000-0000-0000-000000000003','d0000000-0000-0000-0000-000000000003','b0000000-0000-0000-0000-000000000002','e0000000-0000-0000-0000-000000000004',1,'elevenlabs','scribe_v2','azure','production','production'),
 ('10000000-0000-0000-0000-000000000004','d0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000001','e0000000-0000-0000-0000-000000000002',1,'faster-whisper','large-v3','native-english','compare','comparison');

select pg_temp.assert_rejected($q$insert into public.conversations(organisation_id,owner_id,customer_id,customer_name_snapshot,salesperson_name_snapshot,consent_confirmed) values('b0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000002','c0000000-0000-0000-0000-000000000002','Sam','A',true)$q$,array['23503'],'cross-owner customer association rejected');
select pg_temp.assert_rejected($q$insert into public.conversations(organisation_id,owner_id,customer_id,customer_name_snapshot,salesperson_name_snapshot,consent_confirmed) values('b0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000002','c0000000-0000-0000-0000-000000000003','Sam','A',true)$q$,array['23503'],'cross-workspace customer association rejected');
select pg_temp.assert_rejected($q$update public.conversations set owner_id='a0000000-0000-0000-0000-000000000003' where id='d0000000-0000-0000-0000-000000000001'$q$,array['23514'],'historical ownership immutable');
select pg_temp.assert_rejected($q$update public.audio_chunks set sha256=repeat('c',64) where conversation_id='d0000000-0000-0000-0000-000000000001'$q$,array['23514'],'chunk hash immutable');
select pg_temp.assert_rejected($q$insert into public.audio_chunks(conversation_id,part_id,sequence,sample_start,sample_count,bytes,sha256,object_path) values('d0000000-0000-0000-0000-000000000001','f0000000-0000-0000-0000-000000000001',1,1,100,244,repeat('a',64),'b0000000-0000-0000-0000-000000000001/a0000000-0000-0000-0000-000000000002/d0000000-0000-0000-0000-000000000001/session-audio/no-phone/f0000000-0000-0000-0000-000000000001/1.wav')$q$,array['23P01'],'overlapping chunk rejected');
select pg_temp.assert_rejected($q$update public.conversations set total_samples=115200001 where id='d0000000-0000-0000-0000-000000000001'$q$,array['23514'],'maximum-duration overflow rejected');
select pg_temp.assert_rejected($q$update public.processing_runs set engine_id='faster-whisper' where id='10000000-0000-0000-0000-000000000001'$q$,array['23514'],'run snapshot immutable');
select pg_temp.assert_rejected($q$update public.workspace_engine_settings set translator_connection_id='e0000000-0000-0000-0000-000000000004' where organisation_id='b0000000-0000-0000-0000-000000000001'$q$,array['23514','23503'],'cross-workspace translator rejected');

set local role authenticated;
set local request.jwt.claims='{"sub":"a0000000-0000-0000-0000-000000000002","role":"authenticated"}';
select pg_temp.assert_true((select count(*)=1 from public.customers),'Sales A sees own Sam customer only');
select pg_temp.assert_true((select count(*)=1 from public.conversations),'Sales A sees own conversation only');
select pg_temp.assert_true((select count(*)=1 from public.audio_chunks),'Sales A sees own chunks only');
select pg_temp.assert_true((select count(*)=2 from public.processing_runs),'Sales A sees own production and comparison only');
select pg_temp.assert_true((select count(*)=1 from public.members),'salesperson cannot list peers');
select pg_temp.assert_true(not public.recorder_conversation_access('d0000000-0000-0000-0000-000000000002'),'direct other-salesperson helper denied');
select pg_temp.assert_true(not public.recorder_conversation_access('d0000000-0000-0000-0000-000000000003'),'direct other-workspace helper denied');
select pg_temp.assert_rejected('select * from public.provider_secrets',array['42501'],'browser secrets denied');
select pg_temp.assert_rejected('select * from public.inference_artifacts',array['42501'],'browser raw artifacts denied');
select pg_temp.assert_rejected('select token_hash from public.inference_workers',array['42501'],'worker credential hashes denied');
select pg_temp.assert_rejected('select token_hash from public.invites',array['42501'],'invite hashes denied');
select pg_temp.assert_rejected('select payload from public.processing_jobs',array['42501'],'browser job input contents denied');
select pg_temp.assert_rejected('select credential_scope from public.engine_connections',array['42501'],'browser credential fingerprints denied');
select pg_temp.assert_rejected($q$update public.members set role='owner'$q$,array['42501'],'role escalation denied');
select pg_temp.assert_rejected($q$update public.engine_connections set generation=generation+1$q$,array['42501'],'browser generation change denied');
select pg_temp.assert_rejected($q$update public.workspace_engine_settings set default_engine_id='faster-whisper'$q$,array['42501'],'browser default change denied');
select pg_temp.assert_rejected($q$select * from public.claim_jobs('attacker')$q$,array['42501'],'browser queue claim denied');
select pg_temp.assert_rejected($q$select public.create_workspace('a0000000-0000-0000-0000-000000000002','forged')$q$,array['42501'],'browser trusted workspace RPC denied');
select pg_temp.assert_rejected($q$select * from pgmq.q_recorder_jobs$q$,array['42501'],'browser direct queue denied');
insert into storage.objects(bucket_id,name,owner_id) values('session-audio','b0000000-0000-0000-0000-000000000001/a0000000-0000-0000-0000-000000000002/d0000000-0000-0000-0000-000000000001/session-audio/no-phone/f0000000-0000-0000-0000-000000000001/0.wav','a0000000-0000-0000-0000-000000000002');
select pg_temp.assert_true((select count(*)=1 from storage.objects),'owner storage read allowed');
select pg_temp.assert_rejected($q$insert into storage.objects(bucket_id,name) values('session-audio','b0000000-0000-0000-0000-000000000001/a0000000-0000-0000-0000-000000000003/d0000000-0000-0000-0000-000000000002/session-audio/no-phone/f0000000-0000-0000-0000-000000000002/0.wav')$q$,array['42501'],'cross-owner storage upload denied');
select pg_temp.assert_rejected($q$insert into storage.objects(bucket_id,name) values('session-audio','arbitrary.wav')$q$,array['42501'],'unreserved object upload denied');
select pg_temp.assert_rejected($q$insert into storage.objects(bucket_id,name) values('recorder-artifacts','raw.json')$q$,array['42501'],'browser artifact upload denied');
update storage.objects set name='overwritten.wav';
select pg_temp.assert_true((select count(*)=1 from storage.objects where name like '%/0.wav'),'immutable storage update denied');
delete from storage.objects;
select pg_temp.assert_true((select count(*)=1 from storage.objects),'browser deletion denied');
set local request.jwt.claims='{"sub":"a0000000-0000-0000-0000-000000000003","role":"authenticated"}';
select pg_temp.assert_true((select count(*)=1 from public.customers where name='Sam'),'same-name customer remains distinct');
select pg_temp.assert_true((select count(*)=0 from storage.objects),'Sales B cannot read Sales A storage');
set local request.jwt.claims='{"sub":"a0000000-0000-0000-0000-000000000001","role":"authenticated"}';
select pg_temp.assert_true((select count(*)=2 from public.conversations),'authorised owner has team access');
select pg_temp.assert_true((select count(*)=1 from storage.objects),'authorised owner has playback/audio access');
select pg_temp.assert_rejected('select * from public.provider_secrets',array['42501'],'workspace owner browser secrets still denied');
reset role;

select pg_temp.assert_true((public.acquire_recording_lease('d0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000002','tab-a')).fence=1,'first lease acquired');
select pg_temp.assert_true((public.acquire_recording_lease('d0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000002','tab-a')).fence=1,'same-tab lease idempotent');
select pg_temp.assert_rejected($q$select public.acquire_recording_lease('d0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000002','tab-b',false)$q$,array['55P03'],'active other-tab lease requires explicit takeover');
select pg_temp.assert_rejected($q$select public.acquire_recording_lease('d0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000003','tab-b')$q$,array['42501'],'cross-owner recording lease denied');
update public.recording_leases set expires_at=now()-interval '1 second';
select pg_temp.assert_rejected($q$select public.acquire_recording_lease('d0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000002','tab-b')$q$,array['55P03'],'expired lease requires explicit recovery');
select pg_temp.assert_true((public.acquire_recording_lease('d0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000002','tab-b',true)).fence=2,'explicit takeover increments fence');

create temporary table test_jobs as select public.schedule_job('d0000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','transcribe','window-test','{"sample_start":0,"sample_end":80000}') as id;
select pg_temp.assert_true((select id from test_jobs)=public.schedule_job('d0000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','transcribe','window-test','{"sample_start":0,"sample_end":80000}'),'same scheduling retry idempotent');
select pg_temp.assert_rejected($q$select public.schedule_job('d0000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','transcribe','window-test','{"different":true}')$q$,array['23514'],'conflicting scheduling retry rejected');
select public.schedule_job('d0000000-0000-0000-0000-000000000002','10000000-0000-0000-0000-000000000002','transcribe','window-b','{}');
select public.schedule_job('d0000000-0000-0000-0000-000000000003','10000000-0000-0000-0000-000000000003','transcribe','window-other-workspace','{}');
update public.processing_jobs set priority=case conversation_id when 'd0000000-0000-0000-0000-000000000001' then 1 when 'd0000000-0000-0000-0000-000000000002' then 2 else 3 end
 where organisation_id in ('b0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000002');
create temporary table leased_jobs as select * from public.claim_jobs('main-test');
select pg_temp.assert_true((select count(*)=1 from leased_jobs),'eligible job claimed');
select pg_temp.assert_true((select count(*)=0 from public.claim_jobs('replica-test')),'shared credential concurrency applies across workspaces');
select pg_temp.assert_true((select public.finish_job(id,'main-test',gen_random_uuid(),fence,'{}')=false from leased_jobs),'wrong lease token result fenced');
select pg_temp.assert_true((select public.finish_job(id,'main-test',lease_token,fence+1,'{}')=false from leased_jobs),'wrong fencing value result rejected');
select pg_temp.assert_true((select public.renew_job(id,'main-test',lease_token,fence,60) from leased_jobs),'lease heartbeat extends ownership');
select pg_temp.assert_true((select public.begin_job_submission(id,'main-test',lease_token,fence,'request-test') from leased_jobs),'dispatch gate persists uncertainty boundary');
select pg_temp.assert_true((select not public.begin_job_submission(id,'main-test',lease_token,fence,'request-test') from leased_jobs),'duplicate external submission gate rejected');
select pg_temp.assert_true((select public.finish_job(id,'main-test',lease_token,fence,'{"source_segments":[]}') from leased_jobs),'durable fenced result committed');
select pg_temp.assert_true((select public.finish_job(id,'main-test',lease_token,fence,'{"source_segments":[]}') from leased_jobs),'identical completed result retry idempotent');
select pg_temp.assert_true((select not public.finish_job(id,'main-test',lease_token,fence,'{"conflicting":true}') from leased_jobs),'different completed result retry rejected');
select pg_temp.assert_true((select count(*)=0 from recorder_private.queue_receipts where job_id=(select id from leased_jobs)),'successful queue receipt archived atomically');
truncate leased_jobs;
insert into leased_jobs select * from public.claim_jobs('crash-test');
select pg_temp.assert_true((select count(*)=1 from leased_jobs),'next job admitted after released provider slot');
update public.processing_jobs set lease_until=now()-interval '1 second',submission_state='submitting' where id=(select id from leased_jobs);
select pg_temp.assert_true(public.reap_expired_jobs()=1,'crashed expired job reaped');
select pg_temp.assert_true((select state='uncertain' from public.processing_jobs where id=(select id from leased_jobs)),'paid submission uncertainty prevents blind replay');
select pg_temp.assert_rejected($q$select public.resume_job((select id from leased_jobs),'a0000000-0000-0000-0000-000000000001')$q$,array['23514'],'uncertain request requires reconciliation');
select pg_temp.assert_true(public.resume_job((select id from leased_jobs),'a0000000-0000-0000-0000-000000000001',true),'explicit reconciled retry permitted');
truncate leased_jobs;
insert into leased_jobs select * from public.claim_jobs('disconnect-test');
select public.disconnect_connection('e0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000001');
select pg_temp.assert_true(recorder_private.retire_queue_receipts(100)>=1,'blocked queue receipts archived while ledger stays recoverable');
select pg_temp.assert_true((select generation=2 and state='disconnected' from public.engine_connections where id='e0000000-0000-0000-0000-000000000001'),'disconnect increments generation');
select pg_temp.assert_true((select count(*)=0 from public.provider_secrets where connection_id='e0000000-0000-0000-0000-000000000001'),'workspace credential detached');
select pg_temp.assert_true((select not public.finish_job(id,'disconnect-test',lease_token,fence,'{}') from leased_jobs),'disconnect rejects late result');
select pg_temp.assert_true((select count(*)=2 from public.conversations where organisation_id='b0000000-0000-0000-0000-000000000001'),'disconnect preserves customer recordings');
select pg_temp.assert_true((select count(*)=2 from public.audio_chunks where conversation_id in ('d0000000-0000-0000-0000-000000000001','d0000000-0000-0000-0000-000000000002')),'disconnect preserves stored audio metadata');

-- Optional inference: no claim until a durable scoped input artifact exists.
insert into public.inference_workers(id,organisation_id,connection_id,token_hash,name,state,connection_generation,last_seen_at)
 values('20000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000001','e0000000-0000-0000-0000-000000000002',repeat('c',64),'Native test','ready',1,now());
create temporary table native_job as select public.schedule_job('d0000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000004','transcribe','native-window','{"sample_start":0,"sample_end":80000}') as id;
select pg_temp.assert_true((select count(*)=0 from public.claim_jobs('native-test','inference',1,180,'e0000000-0000-0000-0000-000000000002','20000000-0000-0000-0000-000000000001')),'optional jobs await durable prepared input');
select pg_temp.assert_rejected($q$insert into public.inference_artifacts(conversation_id,run_id,job_id,kind,object_path,sha256,bytes) values('d0000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000004',(select id from native_job),'input','other-workspace/input.wav',repeat('a',64),160044)$q$,array['23514'],'cross-workspace artifact path rejected');
insert into public.inference_artifacts(conversation_id,run_id,job_id,kind,object_path,sha256,bytes)
 select 'd0000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000004',id,'input','b0000000-0000-0000-0000-000000000001/a0000000-0000-0000-0000-000000000002/d0000000-0000-0000-0000-000000000001/artifacts/'||id::text||'/input.wav',repeat('a',64),160044 from native_job;
create temporary table native_lease as select * from public.claim_jobs('native-test','inference',1,180,'e0000000-0000-0000-0000-000000000002','20000000-0000-0000-0000-000000000001');
select pg_temp.assert_true((select count(*)=1 from native_lease),'scoped optional worker claims prepared input');
select pg_temp.assert_rejected($q$select * from public.claim_jobs('forged-native','inference',1,180,'e0000000-0000-0000-0000-000000000004','20000000-0000-0000-0000-000000000001')$q$,array['42501'],'cross-connection worker claim rejected');
update public.inference_workers set state='revoked',revoked_at=now() where id='20000000-0000-0000-0000-000000000001';
select pg_temp.assert_true((select not public.renew_job(id,'native-test',lease_token,fence,60) from native_lease),'revoked native worker cannot renew');
select pg_temp.assert_true((select not public.finish_job(id,'native-test',lease_token,fence,'{}') from native_lease),'revoked native worker late result denied');
select pg_temp.assert_rejected($q$update public.inference_artifacts set sha256=repeat('b',64) where job_id=(select id from native_job)$q$,array['23514'],'input artifacts immutable');

-- Clear only this rollback-only fixture's remaining jobs before controlled stages.
update public.processing_jobs set state='cancelled',cancelled_at=now() where organisation_id in ('b0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000002') and state<>'succeeded';
create temporary table accepted_job as select public.schedule_job('d0000000-0000-0000-0000-000000000003','10000000-0000-0000-0000-000000000003','transcribe','accepted-before-disconnect','{"sample_start":0,"sample_end":80000}') as id;
create temporary table accepted_lease as select * from public.claim_jobs('accepted-test');
select pg_temp.assert_true((select count(*)=1 from accepted_lease),'accepted policy test has a live job');
update public.processing_jobs set submission_state='accepted',request_reference='provider-accepted-reference' where id=(select id from accepted_job);
select public.disconnect_connection('e0000000-0000-0000-0000-000000000004','a0000000-0000-0000-0000-000000000004',true);
select pg_temp.assert_true((select public.finish_job(id,'accepted-test',lease_token,fence,'{"finished_after_disconnect":true}') from accepted_lease),'explicit accepted job may finish after disconnect');
insert into public.transcript_versions(id,conversation_id,run_id,kind,coverage_samples,job_id)
 select '30000000-0000-0000-0000-000000000001',conversation_id,run_id,'progressive',80000,id from accepted_lease;
select pg_temp.assert_rejected($q$update public.transcript_versions set coverage_samples=90000 where id='30000000-0000-0000-0000-000000000001'$q$,array['23514'],'published machine version immutable');
select pg_temp.assert_rejected($q$insert into public.transcript_versions(conversation_id,run_id,kind,coverage_samples,job_id) select conversation_id,run_id,'progressive',80000,id from native_lease$q$,array['23514'],'non-successful job cannot publish version');

-- Stopped audio can recover its outbox without allowing capture to resume.
update public.conversations set capture_state='stopped' where id='d0000000-0000-0000-0000-000000000001';
update public.recording_leases set expires_at=now()-interval '1 second';
select pg_temp.assert_true((public.acquire_recording_lease('d0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000002','upload-recovery',true)).fence=3,'stopped uncommitted manifest can recover uploads');
select pg_temp.assert_true(public.heartbeat_recording_lease('d0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000002','upload-recovery',(select token from public.recording_leases where user_id='a0000000-0000-0000-0000-000000000002')),'stopped upload-only lease may renew');
update public.conversations set manifest_hash=repeat('a',64) where id='d0000000-0000-0000-0000-000000000001';
select pg_temp.assert_rejected($q$select public.acquire_recording_lease('d0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000002','upload-recovery',true)$q$,array['23514'],'committed stopped recording cannot reacquire capture');

-- Budget blocks inference only; durable assembly/storage continue.
update public.workspace_engine_settings set budget_policy='{"max_audio_seconds_per_day":0}' where organisation_id='b0000000-0000-0000-0000-000000000001';
update public.engine_connections set state='connected' where id='e0000000-0000-0000-0000-000000000002';
create temporary table assembly_job as select public.schedule_job('d0000000-0000-0000-0000-000000000001',null,'assemble','budget-independent-assembly','{}') as id;
create temporary table assembly_lease as select * from public.claim_jobs('assemble-test');
select pg_temp.assert_true((select count(*)=1 from assembly_lease where id=(select id from assembly_job)),'audio assembly independent of inference budget');
select pg_temp.assert_true((select public.finish_job(id,'assemble-test',lease_token,fence,'{}') from assembly_lease),'assembly persists at exhausted inference budget');

create temporary table deleted_job as select public.schedule_job('d0000000-0000-0000-0000-000000000002',null,'validate','deletion-late-result','{}') as id;
create temporary table deleted_lease as select * from public.claim_jobs('deleted-test');
select pg_temp.assert_true((select count(*)=1 from deleted_lease),'deletion test has a live job');
update public.conversations set deleted_at=now(),capture_state='deleted' where id='d0000000-0000-0000-0000-000000000002';
select pg_temp.assert_true((select not public.finish_job(id,'deleted-test',lease_token,fence,'{}') from deleted_lease),'deletion fences live result publication');
select pg_temp.assert_rejected($q$insert into public.recording_parts(conversation_id,ordinal) values('d0000000-0000-0000-0000-000000000002',1)$q$,array['23514'],'late audio cannot recreate deleted conversation');
create temporary table cleanup_job as select public.schedule_job('d0000000-0000-0000-0000-000000000002',null,'delete','delete-cleanup','{}') as id;
create temporary table cleanup_lease as select * from public.claim_jobs('cleanup-test');
select pg_temp.assert_true((select count(*)=1 from cleanup_lease where id=(select id from cleanup_job)),'tombstone cleanup job remains claimable');
select pg_temp.assert_true((select public.finish_job(id,'cleanup-test',lease_token,fence,'{"deleted":true}') from cleanup_lease),'tombstone cleanup ledger can complete');

update public.members set status='revoked',revoked_at=now() where organisation_id='b0000000-0000-0000-0000-000000000001' and user_id='a0000000-0000-0000-0000-000000000002';
set local role authenticated;
set local request.jwt.claims='{"sub":"a0000000-0000-0000-0000-000000000002","role":"authenticated"}';
select pg_temp.assert_true((select count(*)=0 from public.conversations),'revoked account cannot see historical conversations');
select pg_temp.assert_true((select count(*)=0 from public.customers),'revoked account cannot see customers');
select pg_temp.assert_true((select count(*)=0 from public.engine_connections),'revoked account cannot inspect connections');
select pg_temp.assert_true((select count(*)=0 from storage.objects),'revoked account storage access denied');
select pg_temp.assert_true(not public.recorder_upload_allowed('session-audio','b0000000-0000-0000-0000-000000000001/a0000000-0000-0000-0000-000000000002/d0000000-0000-0000-0000-000000000001/session-audio/no-phone/f0000000-0000-0000-0000-000000000001/0.wav'),'revoked member pending upload denied');
reset role;
select pg_temp.assert_true(not public.heartbeat_recording_lease('d0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000002','upload-recovery',(select token from public.recording_leases where user_id='a0000000-0000-0000-0000-000000000002')),'revoked recording heartbeat denied');
select pg_temp.assert_true(public.allow_api_request('test:user',2,60),'rate first request accepted');
select pg_temp.assert_true(public.allow_api_request('test:user',2,60),'rate second request accepted');
select pg_temp.assert_true(not public.allow_api_request('test:user',2,60),'distributed rate excess rejected');
set local role anon;
select pg_temp.assert_rejected('select * from public.conversations',array['42501'],'anonymous conversation access denied');
select pg_temp.assert_rejected($q$select public.allow_api_request('attack',100)$q$,array['42501'],'anonymous rate bypass RPC denied');
reset role;
select pg_temp.assert_true((select count(*)=0 from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname in ('public','recorder_private') and p.prosecdef and p.proname in ('recorder_member','recorder_admin','recorder_conversation_access','recorder_customer_access','recorder_object_access','recorder_upload_allowed','create_workspace','accept_invitation','acquire_recording_lease','heartbeat_recording_lease','release_recording_lease','schedule_job','reap_expired_jobs','claim_jobs','renew_job','finish_job','retry_job','resume_job','disconnect_connection','allow_api_request','begin_job_submission') and not coalesce(p.proconfig @> array['search_path=""'],false)),'all security definer functions hardened');
rollback;
\echo 'Database security/invariant suite passed; fixtures rolled back.'
