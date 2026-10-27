-- EMULATION ONLY: minimal Supabase auth/storage and pgmq signatures for native
-- PostgreSQL syntax/RLS testing. Never apply this fixture to a Supabase project.
create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
create schema auth;
create table auth.guru_native_emulation_marker(marker boolean primary key default true);
insert into auth.guru_native_emulation_marker values(true);
create table auth.users(id uuid primary key,email text,email_confirmed_at timestamptz,raw_user_meta_data jsonb default '{}');
create function auth.uid() returns uuid language sql stable as $$ select (nullif(current_setting('request.jwt.claims',true),'')::jsonb->>'sub')::uuid $$;
grant usage on schema auth to authenticated,service_role;
grant execute on function auth.uid() to authenticated,service_role;
create schema storage;
create function storage.foldername(name text) returns text[] language sql immutable
  as $$ select (string_to_array(name,'/'))[1:greatest(array_length(string_to_array(name,'/'),1)-1,0)] $$;
create table storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint,allowed_mime_types text[]);
create table storage.objects(id uuid primary key default gen_random_uuid(),bucket_id text references storage.buckets(id),name text not null,owner_id text,unique(bucket_id,name));
alter table storage.objects enable row level security;
grant usage on schema storage to authenticated,service_role;
grant select,insert,update,delete on storage.objects to authenticated,service_role;
create schema pgmq;
create function pgmq.create(queue_name text) returns void language plpgsql as $$ begin
  execute format('create table if not exists pgmq.q_%I(msg_id bigint generated always as identity primary key,read_ct integer default 0,enqueued_at timestamptz default now(),vt timestamptz default now(),message jsonb)',queue_name);
  execute format('create table if not exists pgmq.a_%I(msg_id bigint,read_ct integer,enqueued_at timestamptz,vt timestamptz,message jsonb)',queue_name);
end $$;
create function pgmq.send(queue_name text,msg jsonb,delay integer default 0) returns bigint language plpgsql as $$ declare result bigint;begin
  execute format('insert into pgmq.q_%I(message,vt) values($1,now()+make_interval(secs=>$2)) returning msg_id',queue_name) into result using msg,delay;
  return result;
end $$;
create function pgmq.set_vt(queue_name text,msg_id bigint,vt integer) returns boolean language plpgsql as $$begin
  execute format('update pgmq.q_%I set vt=now()+make_interval(secs=>$1),read_ct=read_ct+1 where msg_id=$2',queue_name) using vt,msg_id;
  return found;
end $$;
create function pgmq.archive(queue_name text,msg_id bigint) returns boolean language plpgsql as $$begin
  execute format('with removed as(delete from pgmq.q_%I where msg_id=$1 returning *) insert into pgmq.a_%I select * from removed',queue_name,queue_name) using msg_id;
  return found;
end $$;
create publication supabase_realtime;

-- EMULATION ONLY: cron stores schedules without executing commands; pg_net
-- returns a synthetic request id without contacting a URL. Vault deliberately
-- discards supplied secret values, including legacy migration literals.
create schema extensions;
create schema cron;
create table cron.job(jobid bigint generated always as identity primary key,jobname text unique,schedule text,command text);
create function cron.schedule(job_name text,schedule text,command text) returns bigint language plpgsql as $$
declare result bigint;
begin
  insert into cron.job(jobname,schedule,command) values(job_name,schedule,command)
    on conflict(jobname) do update set schedule=excluded.schedule,command=excluded.command returning jobid into result;
  return result;
end $$;
create function cron.unschedule(job_id bigint) returns boolean language plpgsql as $$
begin
  delete from cron.job where jobid=job_id;
  return found;
end $$;
create schema vault;
create table vault.decrypted_secrets(id uuid primary key default gen_random_uuid(),name text,decrypted_secret text);
create function vault.create_secret(secret text,name text default null,description text default '') returns uuid language plpgsql as $$
declare result uuid;
begin
  insert into vault.decrypted_secrets(name,decrypted_secret) values(name,'native-emulation-placeholder') returning id into result;
  return result;
end $$;
create schema net;
create sequence net.native_request_id;
create function net.http_post(url text,body jsonb default '{}'::jsonb,params jsonb default '{}'::jsonb,
  headers jsonb default '{}'::jsonb,timeout_milliseconds integer default 1000) returns bigint language sql
  as $$ select nextval('net.native_request_id') $$;
