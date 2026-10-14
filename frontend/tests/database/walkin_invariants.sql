-- Rollback-only metadata/privilege assertions for a disposable test database.
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
insert into auth.users(id,email,email_confirmed_at,raw_user_meta_data) values
 ('a1000000-0000-0000-0000-000000000001','walkin-owner-one@example.invalid',now(),'{}'),
 ('a1000000-0000-0000-0000-000000000002','walkin-owner-two@example.invalid',now(),'{}');
insert into public.organisations(id,name,created_by) values
 ('b1000000-0000-0000-0000-000000000001','Walkin isolation fixture','a1000000-0000-0000-0000-000000000001'),
 ('b1000000-0000-0000-0000-000000000002','Other walkin fixture','a1000000-0000-0000-0000-000000000002');
insert into public.members(organisation_id,user_id,role) values
 ('b1000000-0000-0000-0000-000000000001','a1000000-0000-0000-0000-000000000001','owner'),
 ('b1000000-0000-0000-0000-000000000002','a1000000-0000-0000-0000-000000000002','owner');
insert into public.guruconnect_walkin_visits(id,organisation_id,registered_by,idempotency_key,payload_hash,payload,customer_name,mobile_number,purpose,status) values
 ('c1000000-0000-0000-0000-000000000001','b1000000-0000-0000-0000-000000000001','a1000000-0000-0000-0000-000000000001','fixture-key-001',repeat('a',64),'{}','Fixture visitor','9876543210','Other','Waiting for Assignment');
select pg_temp.assert_true((select bool_and(relrowsecurity) from pg_class where relname in ('guruconnect_walkin_visits','guruconnect_walkin_history','guruconnect_walkin_agents','guruconnect_walkin_recordings')),'all hosted walkin tables enforce RLS');
select pg_temp.assert_rejected($q$update public.guruconnect_walkin_visits set assigned_agent_id='a1000000-0000-0000-0000-000000000002',assigned_at=now(),status='Assigned'$q$,array['23503'],'assignment cannot reference a foreign workspace member');
select pg_temp.assert_rejected($q$update public.guruconnect_walkin_visits set status='Converted to Enquiry'$q$,array['23514'],'converted status requires an actual enquiry link');
select pg_temp.assert_rejected($q$update public.guruconnect_walkin_visits set status='In Discussion'$q$,array['23514'],'discussion requires an assigned workspace agent');
select pg_temp.assert_rejected($q$insert into public.guruconnect_walkin_history(organisation_id,visit_id,actor_id,event_type) values('b1000000-0000-0000-0000-000000000002','c1000000-0000-0000-0000-000000000001','a1000000-0000-0000-0000-000000000002','Foreign write')$q$,array['23503'],'history cannot refer to a foreign workspace visit');
insert into public.customers(id,organisation_id,owner_id,name) values('d1000000-0000-0000-0000-000000000001','b1000000-0000-0000-0000-000000000001','a1000000-0000-0000-0000-000000000001','Fixture recording customer');
insert into public.conversations(id,organisation_id,owner_id,customer_id,customer_name_snapshot,salesperson_name_snapshot,consent_confirmed) values('e1000000-0000-0000-0000-000000000001','b1000000-0000-0000-0000-000000000001','a1000000-0000-0000-0000-000000000001','d1000000-0000-0000-0000-000000000001','Fixture recording customer','Fixture owner',true);
select pg_temp.assert_rejected($q$insert into public.guruconnect_walkin_recordings(organisation_id,visit_id,conversation_id,owner_id) values('b1000000-0000-0000-0000-000000000001','c1000000-0000-0000-0000-000000000001','e1000000-0000-0000-0000-000000000001','a1000000-0000-0000-0000-000000000002')$q$,array['23503'],'recording link cannot forge a different conversation owner');
insert into public.guruconnect_walkin_recordings(organisation_id,visit_id,conversation_id,owner_id) values('b1000000-0000-0000-0000-000000000001','c1000000-0000-0000-0000-000000000001','e1000000-0000-0000-0000-000000000001','a1000000-0000-0000-0000-000000000001');
set local role authenticated;
select pg_temp.assert_rejected('select * from public.guruconnect_walkin_visits',array['42501'],'authenticated browser denied visitor PII');
select pg_temp.assert_rejected('select * from public.guruconnect_walkin_history',array['42501'],'authenticated browser denied visit history');
select pg_temp.assert_rejected('select * from public.guruconnect_walkin_agents',array['42501'],'authenticated browser denied agent metadata');
select pg_temp.assert_rejected('select * from public.guruconnect_walkin_recordings',array['42501'],'authenticated browser denied recording associations');
reset role;
set local role anon;
select pg_temp.assert_rejected('select * from public.guruconnect_walkin_visits',array['42501'],'anonymous browser denied visitor PII');
select pg_temp.assert_rejected('select * from public.guruconnect_walkin_history',array['42501'],'anonymous browser denied history');
select pg_temp.assert_rejected('select * from public.guruconnect_walkin_agents',array['42501'],'anonymous browser denied agent metadata');
select pg_temp.assert_rejected('select * from public.guruconnect_walkin_recordings',array['42501'],'anonymous browser denied recording associations');
reset role;
set local role service_role;
select pg_temp.assert_rejected('delete from public.guruconnect_walkin_history',array['42501'],'runtime role cannot delete walkin audit history');
select pg_temp.assert_rejected($q$update public.guruconnect_walkin_history set notes='Forged'$q$,array['42501'],'runtime role cannot rewrite walkin audit history');
select pg_temp.assert_rejected('delete from public.guruconnect_walkin_recordings',array['42501'],'runtime role cannot remove durable recording links');
reset role;
rollback;
