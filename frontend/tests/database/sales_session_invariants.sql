-- Forward sales-session constraints, exercised only in a disposable fixture.
\set ON_ERROR_STOP on
begin;
set constraints all immediate;
create function pg_temp.assert_true(v boolean,label text) returns void language plpgsql as $$begin
  if v is distinct from true then raise exception 'ASSERTION FAILED: %',label;end if;
  raise notice 'PASS: %',label;
end $$;
create function pg_temp.assert_rejected(command text,label text) returns void language plpgsql as $$begin
  begin execute command;
  exception when foreign_key_violation or check_violation then
    raise notice 'PASS: %',label;return;
  end;
  raise exception 'ASSERTION FAILED: % unexpectedly allowed',label;
end $$;
do $$
declare
  actor uuid:=gen_random_uuid(); peer uuid:=gen_random_uuid();
  org uuid:=gen_random_uuid(); foreign_org uuid:=gen_random_uuid();
  customer uuid:=gen_random_uuid(); peer_customer uuid:=gen_random_uuid();
  second_customer uuid:=gen_random_uuid(); foreign_customer uuid:=gen_random_uuid();
  recording uuid:=gen_random_uuid(); other_recording uuid:=gen_random_uuid(); session uuid:=gen_random_uuid();
begin
  insert into auth.users(id,email,email_confirmed_at) values(actor,'sales-scope-owner@example.invalid',now()),(peer,'sales-scope-peer@example.invalid',now());
  insert into public.organisations(id,name,created_by) values(org,'Sales scope fixture',actor),(foreign_org,'Foreign fixture',actor);
  insert into public.members(organisation_id,user_id,role) values(org,actor,'owner'),(org,peer,'salesperson'),(foreign_org,actor,'owner');
  insert into public.customers(id,organisation_id,owner_id,name,normalized_phone) values
    (customer,org,actor,'Fixture','9000000000'),(peer_customer,org,peer,'Fixture','9000000000'),
    (foreign_customer,foreign_org,actor,'Fixture','9000000000'),(second_customer,org,actor,'Second fixture',null);
  perform pg_temp.assert_true((select count(*)=3 from public.customers where normalized_phone='9000000000'),'same phone can belong to distinct workspace/member customers');
  insert into public.conversations(id,organisation_id,owner_id,customer_id,customer_name_snapshot,salesperson_name_snapshot,consent_confirmed) values
    (recording,org,actor,customer,'Fixture','Owner',true),(other_recording,org,actor,second_customer,'Second fixture','Owner',true);
  insert into public.chat_sessions(id,organisation_id,member_id,customer_id,conversation_id)
    values(session,org,actor,customer,recording);
  perform pg_temp.assert_true(exists(select 1 from public.chat_sessions where id=session and conversation_id=recording),'same tenant, owner and customer recording link is accepted');
  perform pg_temp.assert_rejected(format('insert into public.chat_sessions(organisation_id,member_id,customer_id) values(%L,%L,%L)',org,actor,peer_customer),'cross-member session/customer association rejected');
  perform pg_temp.assert_rejected(format('insert into public.chat_sessions(organisation_id,member_id,customer_id) values(%L,%L,%L)',org,actor,foreign_customer),'cross-workspace session/customer association rejected');
  perform pg_temp.assert_rejected(format('insert into public.chat_sessions(organisation_id,member_id,customer_id,conversation_id) values(%L,%L,%L,%L)',org,actor,customer,other_recording),'same-owner recording from another customer rejected');
  perform pg_temp.assert_rejected(format('insert into public.chat_sessions(customer_id) values(%L)',customer),'new unscoped sessions rejected');
  insert into public.voice_session_notes(organisation_id,customer_id,session_id,author_id,content) values(org,customer,session,actor,'Synthetic note');
  perform pg_temp.assert_true(exists(select 1 from public.voice_session_notes where session_id=session),'scoped session note accepted');
  perform pg_temp.assert_rejected(format('insert into public.voice_session_notes(organisation_id,customer_id,session_id,content) values(%L,%L,%L,%L)',org,second_customer,session,'Synthetic note'),'note cannot reference another customer session');
  perform pg_temp.assert_rejected(format('insert into public.voice_session_notes(organisation_id,customer_id,content) values(%L,%L,%L)',foreign_org,customer,'Synthetic note'),'note customer cannot cross workspace');
end $$;
rollback;
