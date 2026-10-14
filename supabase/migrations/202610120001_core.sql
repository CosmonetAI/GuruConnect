-- Apply only to an explicitly designated development project before production rollout.
-- Audio and raw inference payloads stay in private Storage; Postgres contains metadata.
begin;
create extension if not exists pgcrypto;
create extension if not exists pgmq;
create schema if not exists recorder_private;
revoke all on schema recorder_private from public, anon, authenticated;

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null default '' check (length(display_name) <= 160),
  preferences jsonb not null default '{}' check (jsonb_typeof(preferences) = 'object'),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table public.organisations (
  id uuid primary key default gen_random_uuid(), name text not null check (length(name) between 1 and 160),
  created_by uuid not null references auth.users(id),
  max_conversation_seconds integer not null default 7200 check (max_conversation_seconds between 60 and 86400),
  max_concurrent_jobs integer not null default 4 check (max_concurrent_jobs between 1 and 1000),
  retention_days integer not null default 365 check (retention_days between 1 and 3650),
  created_at timestamptz not null default now()
);
create table public.members (
  organisation_id uuid not null references public.organisations(id),
  user_id uuid not null references auth.users(id), role text not null check (role in ('owner','admin','salesperson')),
  status text not null default 'active' check (status in ('active','revoked')),
  revoked_at timestamptz, created_at timestamptz not null default now(),
  primary key (organisation_id,user_id), check ((status='revoked') = (revoked_at is not null))
);
create table public.invites (
  id uuid primary key default gen_random_uuid(), organisation_id uuid not null references public.organisations(id),
  email text not null check (length(email) between 3 and 320), role text not null check (role in ('admin','salesperson')),
  token_hash text not null unique check (token_hash ~ '^[a-f0-9]{64}$'),
  invited_by uuid not null references auth.users(id), accepted_by uuid references auth.users(id),
  expires_at timestamptz not null, accepted_at timestamptz, revoked_at timestamptz, created_at timestamptz not null default now()
);
do $$
begin
  if to_regclass('public.customers') is null then
    create table public.customers (
      id uuid primary key default gen_random_uuid(), organisation_id uuid not null, owner_id uuid not null,
      name text not null check (length(name) between 1 and 200), is_unnamed boolean not null default false,
      notes text not null default '' check (length(notes) <= 2000), created_at timestamptz not null default now(),
      updated_at timestamptz not null default now(), deleted_at timestamptz,
      foreign key (organisation_id,owner_id) references public.members(organisation_id,user_id),
      unique (id,organisation_id,owner_id)
    );
  else
    -- GG Project may have created public.customers first. Preserve that table
    -- and add the workspace fields required by the GuruConnect API.
    alter table public.customers add column if not exists organisation_id uuid;
    alter table public.customers add column if not exists owner_id uuid;
    alter table public.customers add column if not exists is_unnamed boolean not null default false;
    alter table public.customers add column if not exists notes text not null default '';
    alter table public.customers add column if not exists deleted_at timestamptz;
    if not exists (
      select 1 from pg_constraint where conrelid = 'public.customers'::regclass
        and conname = 'customers_organisation_owner_fk'
    ) then
      alter table public.customers add constraint customers_organisation_owner_fk
        foreign key (organisation_id,owner_id) references public.members(organisation_id,user_id);
    end if;
    if not exists (
      select 1 from pg_constraint where conrelid = 'public.customers'::regclass
        and conname = 'customers_workspace_identity'
    ) then
      alter table public.customers add constraint customers_workspace_identity
        unique (id,organisation_id,owner_id);
    end if;
  end if;
end $$;
create table public.engine_connections (
  id uuid primary key default gen_random_uuid(), organisation_id uuid not null references public.organisations(id),
  engine_id text not null check (engine_id in ('elevenlabs','faster-whisper','azure-translator')),
  state text not null default 'disconnected' check (state in ('configured','checking','connected','degraded','disabled','disconnected','invalid_credentials')),
  generation integer not null default 1 check (generation > 0),
  config jsonb not null default '{}' check (jsonb_typeof(config)='object'),
  safe_metadata jsonb not null default '{}' check (jsonb_typeof(safe_metadata)='object'),
  credential_scope text, max_concurrency integer not null default 2 check (max_concurrency between 1 and 1000),
  tested_at timestamptz, last_error_code text, updated_at timestamptz not null default now(),
  created_at timestamptz not null default now(), unique (organisation_id,engine_id), unique (id,organisation_id)
);
create table public.provider_secrets (
  connection_id uuid primary key references public.engine_connections(id), encrypted_value text not null,
  encryption_key_version text not null default '1', updated_at timestamptz not null default now()
);
create table public.workspace_engine_settings (
  organisation_id uuid primary key references public.organisations(id),
  default_engine_id text default 'elevenlabs' check (default_engine_id in ('elevenlabs','faster-whisper')),
  allowed_engine_ids text[] not null default array['elevenlabs']::text[] check (allowed_engine_ids <@ array['elevenlabs','faster-whisper']::text[]),
  salesperson_engine_selection boolean not null default false, comparison_allowed boolean not null default false,
  translator_connection_id uuid, budget_policy jsonb not null default '{}' check (jsonb_typeof(budget_policy)='object'),
  updated_at timestamptz not null default now(),
  foreign key (translator_connection_id,organisation_id) references public.engine_connections(id,organisation_id)
);
create table public.conversations (
  id uuid primary key default gen_random_uuid(), organisation_id uuid not null, owner_id uuid not null, customer_id uuid not null,
  customer_name_snapshot text not null check (length(customer_name_snapshot) between 1 and 200),
  salesperson_name_snapshot text not null check (length(salesperson_name_snapshot)<=160),
  title text not null default '' check (length(title)<=200), location text not null default '' check (length(location)<=200),
  language_hint text, consent_confirmed boolean not null check (consent_confirmed), consent_confirmed_at timestamptz not null default now(),
  capture_state text not null default 'ready' check (capture_state in ('ready','recording','paused','stopped','interrupted','deleted')),
  upload_state text not null default 'pending' check (upload_state in ('pending','uploading','saved','missing','conflict','failed')),
  transcription_state text not null default 'pending' check (transcription_state in ('pending','queued','running','available','blocked','failed','complete')),
  english_state text not null default 'pending' check (english_state in ('pending','available','blocked','failed','complete','unavailable')),
  speaker_state text not null default 'pending' check (speaker_state in ('pending','provisional','complete','failed','unavailable','review')),
  final_state text not null default 'pending' check (final_state in ('pending','queued','running','blocked','failed','complete')),
  total_samples bigint not null default 0 check (total_samples>=0), sample_rate integer not null default 16000 check (sample_rate=16000),
  duration_seconds double precision not null default 0 check (duration_seconds>=0),
  manifest jsonb, manifest_hash text check (manifest_hash ~ '^[a-f0-9]{64}$'), active_version_id uuid,
  playback_object_path text, started_at timestamptz, stopped_at timestamptz, deleted_at timestamptz,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  foreign key (organisation_id,owner_id) references public.members(organisation_id,user_id),
  foreign key (customer_id,organisation_id,owner_id) references public.customers(id,organisation_id,owner_id),
  unique (id,organisation_id), unique (id,organisation_id,owner_id)
);
create table public.recording_leases (
  user_id uuid primary key references auth.users(id), organisation_id uuid not null, conversation_id uuid not null unique,
  tab_id text not null check (length(tab_id) between 1 and 128), token uuid not null default gen_random_uuid(),
  fence bigint not null default 1 check (fence>0), expires_at timestamptz not null,
  foreign key (conversation_id,organisation_id,user_id) references public.conversations(id,organisation_id,owner_id)
);
create table public.recording_parts (
  id uuid primary key default gen_random_uuid(), conversation_id uuid not null references public.conversations(id),
  ordinal integer not null check (ordinal>=0), start_sample bigint not null default 0 check (start_sample>=0),
  end_sample bigint check (end_sample>=start_sample), events jsonb not null default '[]' check (jsonb_typeof(events)='array'),
  created_at timestamptz not null default now(), unique (conversation_id,ordinal), unique (id,conversation_id)
);
create table public.audio_chunks (
  id uuid primary key default gen_random_uuid(), conversation_id uuid not null references public.conversations(id),
  part_id uuid not null, sequence integer not null check (sequence>=0), sample_start bigint not null check (sample_start>=0),
  sample_count integer not null check (sample_count between 1 and 160000), sample_rate integer not null default 16000 check (sample_rate=16000),
  bytes integer not null check (bytes between 46 and 320044), mime text not null default 'audio/wav' check (mime='audio/wav'),
  sha256 text not null check (sha256 ~ '^[a-f0-9]{64}$'), object_path text not null unique,
  state text not null default 'reserved' check (state in ('reserved','uploaded','validated','conflict','invalid')),
  upload_expires_at timestamptz not null default now()+interval '15 minutes', confirmed_at timestamptz, validated_at timestamptz,
  created_at timestamptz not null default now(), foreign key (part_id,conversation_id) references public.recording_parts(id,conversation_id),
  unique (conversation_id,part_id,sequence), unique (id,conversation_id), check (bytes=44+sample_count*2)
);
create table public.processing_runs (
  id uuid primary key default gen_random_uuid(), conversation_id uuid not null, organisation_id uuid not null,
  connection_id uuid, connection_generation integer check (connection_generation>0),
  engine_id text not null check (engine_id in ('elevenlabs','faster-whisper')), model_version text not null,
  config_snapshot jsonb not null default '{}', translation_strategy text not null check (translation_strategy in ('azure','native-english','none')),
  translator_connection_id uuid, translator_generation integer, finalisation_policy jsonb not null default '{"enabled":true}',
  kind text not null default 'production' check (kind in ('production','comparison','replay')),
  state text not null default 'pending' check (state in ('pending','queued','running','blocked','failed','complete','cancelled')),
  audio_manifest_hash text, coverage_samples bigint not null default 0 check (coverage_samples>=0),
  idempotency_key text not null, cancelled_at timestamptz, created_at timestamptz not null default now(),
  foreign key (conversation_id,organisation_id) references public.conversations(id,organisation_id),
  foreign key (connection_id,organisation_id) references public.engine_connections(id,organisation_id),
  foreign key (translator_connection_id,organisation_id) references public.engine_connections(id,organisation_id),
  unique (conversation_id,idempotency_key), unique (id,conversation_id), unique (id,organisation_id)
);
create table public.transcription_windows (
  id uuid primary key default gen_random_uuid(), run_id uuid not null, conversation_id uuid not null,
  sequence integer not null check (sequence>=0), sample_start bigint not null check (sample_start>=0),
  sample_end bigint not null check (sample_end>sample_start), context_start bigint not null check (context_start>=0 and context_start<=sample_start),
  state text not null default 'pending' check (state in ('pending','queued','running','complete','blocked','failed')),
  input_hash text check (input_hash ~ '^[a-f0-9]{64}$'), final_window boolean not null default false,
  created_at timestamptz not null default now(), foreign key (run_id,conversation_id) references public.processing_runs(id,conversation_id),
  unique (run_id,sequence), unique (id,run_id)
);
create table public.inference_workers (
  id uuid primary key default gen_random_uuid(), organisation_id uuid not null, connection_id uuid not null,
  token_hash text not null unique check (token_hash ~ '^[a-f0-9]{64}$'), name text not null check (length(name)<=160),
  state text not null default 'offline' check (state in ('offline','ready','busy','degraded','revoked')),
  connection_generation integer not null check (connection_generation>0), model_version text,
  metrics jsonb not null default '{}', last_seen_at timestamptz, revoked_at timestamptz, created_at timestamptz not null default now(),
  foreign key (connection_id,organisation_id) references public.engine_connections(id,organisation_id), unique (id,organisation_id)
);
create table public.processing_jobs (
  id uuid primary key default gen_random_uuid(), conversation_id uuid not null, organisation_id uuid not null,
  run_id uuid, window_id uuid, connection_id uuid, connection_generation integer,
  kind text not null check (kind in ('validate','assemble','transcribe','translate','diarize','finalise','export','delete')),
  worker_type text not null default 'main' check (worker_type in ('main','inference')),
  state text not null default 'queued' check (state in ('queued','running','retry','blocked','uncertain','succeeded','failed','cancelled','dead')),
  dedupe_key text not null check (length(dedupe_key) between 1 and 300), priority integer not null default 10 check (priority between 0 and 100),
  payload jsonb not null default '{}' check (jsonb_typeof(payload)='object'), result jsonb,
  attempt integer not null default 0 check (attempt>=0), max_attempts integer not null default 5 check (max_attempts between 1 and 20),
  worker_id text, inference_worker_id uuid, lease_token uuid, fence bigint not null default 0 check (fence>=0), lease_until timestamptz,
  available_at timestamptz not null default now(), request_reference text, submission_state text not null default 'not_submitted'
    check (submission_state in ('not_submitted','submitting','accepted','uncertain','received')),
  error_code text, finish_in_flight boolean not null default false, cancelled_at timestamptz,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), completed_at timestamptz,
  foreign key (conversation_id,organisation_id) references public.conversations(id,organisation_id),
  foreign key (run_id,conversation_id) references public.processing_runs(id,conversation_id),
  foreign key (window_id,run_id) references public.transcription_windows(id,run_id),
  foreign key (connection_id,organisation_id) references public.engine_connections(id,organisation_id),
  foreign key (inference_worker_id,organisation_id) references public.inference_workers(id,organisation_id),
  unique (conversation_id,dedupe_key), unique (id,conversation_id), unique (id,run_id),
  check (window_id is null or run_id is not null)
);
create table public.inference_artifacts (
  id uuid primary key default gen_random_uuid(), conversation_id uuid not null, run_id uuid not null, job_id uuid not null,
  kind text not null, object_path text not null unique, sha256 text not null check (sha256 ~ '^[a-f0-9]{64}$'),
  bytes bigint not null check (bytes>0), provenance jsonb not null default '{}', created_at timestamptz not null default now(),
  foreign key (run_id,conversation_id) references public.processing_runs(id,conversation_id),
  foreign key (job_id,run_id) references public.processing_jobs(id,run_id), unique (id,conversation_id)
);
create table public.transcript_versions (
  id uuid primary key default gen_random_uuid(), conversation_id uuid not null, run_id uuid not null,
  kind text not null check (kind in ('progressive','final')), state text not null default 'available' check (state in ('available','complete','review')),
  coverage_samples bigint not null check (coverage_samples>=0), source_available boolean not null default false,
  english_state text not null default 'pending' check (english_state in ('pending','available','complete','blocked','failed','unavailable')),
  speaker_state text not null default 'provisional' check (speaker_state in ('provisional','complete','review','failed','unavailable')),
  provenance jsonb not null default '{}', job_id uuid not null unique, created_at timestamptz not null default now(),
  foreign key (run_id,conversation_id) references public.processing_runs(id,conversation_id),
  foreign key (job_id,run_id) references public.processing_jobs(id,run_id), unique (id,conversation_id), unique (id,run_id)
);
alter table public.conversations add constraint conversations_active_version_fk foreign key (active_version_id,id)
  references public.transcript_versions(id,conversation_id) deferrable initially deferred;
create table public.transcript_turns (
  id uuid primary key default gen_random_uuid(), version_id uuid not null, run_id uuid not null, conversation_id uuid not null,
  window_id uuid, ordinal integer not null check (ordinal>=0), start_seconds double precision not null check (start_seconds>=0),
  end_seconds double precision not null check (end_seconds>=start_seconds), source_text text, english_text text,
  source_language text, speaker_ref text, speaker_confidence text not null default 'uncertain' check (speaker_confidence in ('provisional','supported','uncertain','overlap')),
  provenance jsonb not null default '{}', created_at timestamptz not null default now(),
  foreign key (version_id,conversation_id) references public.transcript_versions(id,conversation_id),
  foreign key (version_id,run_id) references public.transcript_versions(id,run_id),
  foreign key (window_id,run_id) references public.transcription_windows(id,run_id),
  unique (version_id,ordinal), unique (id,version_id), unique (id,conversation_id)
);
create table public.transcript_words (
  id uuid primary key default gen_random_uuid(), turn_id uuid not null, version_id uuid not null,
  word text not null, start_seconds double precision not null check (start_seconds>=0),
  end_seconds double precision not null check (end_seconds>=start_seconds), language_kind text not null default 'source' check (language_kind='source'),
  foreign key (turn_id,version_id) references public.transcript_turns(id,version_id)
);
create table public.translations (
  id uuid primary key default gen_random_uuid(), conversation_id uuid not null, run_id uuid not null, source_turn_id uuid not null,
  cache_key text not null check (cache_key ~ '^[a-f0-9]{64}$'), source_language text, translator_config jsonb not null,
  english_text text, state text not null check (state in ('pending','running','complete','blocked','failed')),
  error_code text, character_count integer not null default 0 check (character_count>=0), created_at timestamptz not null default now(),
  foreign key (run_id,conversation_id) references public.processing_runs(id,conversation_id),
  foreign key (source_turn_id,conversation_id) references public.transcript_turns(id,conversation_id), unique (run_id,cache_key)
);
create table public.diarization_intervals (
  id uuid primary key default gen_random_uuid(), version_id uuid not null, conversation_id uuid not null, speaker_ref text not null,
  start_seconds double precision not null check (start_seconds>=0), end_seconds double precision not null check (end_seconds>start_seconds),
  overlap boolean not null default false, provenance jsonb not null default '{}',
  foreign key (version_id,conversation_id) references public.transcript_versions(id,conversation_id)
);
create table public.speaker_assignments (
  id uuid primary key default gen_random_uuid(), conversation_id uuid not null, version_id uuid not null,
  speaker_ref text not null, role text not null check (role in ('salesperson','customer','other','unknown')),
  display_name text not null check (length(display_name)<=160), assigned_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  foreign key (version_id,conversation_id) references public.transcript_versions(id,conversation_id), unique (version_id,speaker_ref)
);
create table public.transcript_revisions (
  id uuid primary key default gen_random_uuid(), conversation_id uuid not null, version_id uuid not null, turn_id uuid,
  actor_id uuid not null references auth.users(id), kind text not null check (kind in ('text','speaker','merge','split','metadata')),
  change jsonb not null, created_at timestamptz not null default now(),
  foreign key (version_id,conversation_id) references public.transcript_versions(id,conversation_id),
  foreign key (turn_id,version_id) references public.transcript_turns(id,version_id)
);
create table public.audit_events (
  id uuid primary key default gen_random_uuid(), organisation_id uuid not null references public.organisations(id),
  conversation_id uuid, actor_id uuid references auth.users(id), action text not null, metadata jsonb not null default '{}',
  created_at timestamptz not null default now(), foreign key (conversation_id,organisation_id) references public.conversations(id,organisation_id)
);
create table public.usage_records (
  id uuid primary key default gen_random_uuid(), conversation_id uuid not null, run_id uuid not null, job_id uuid not null,
  audio_seconds double precision not null default 0 check (audio_seconds>=0), translation_characters bigint not null default 0 check (translation_characters>=0),
  attempt integer not null check (attempt>=1), estimated_cost numeric(18,6) check (estimated_cost>=0), reported_cost numeric(18,6) check (reported_cost>=0),
  currency text, pricing_config_version text, processing_kind text not null, created_at timestamptz not null default now(),
  foreign key (run_id,conversation_id) references public.processing_runs(id,conversation_id),
  foreign key (job_id,run_id) references public.processing_jobs(id,run_id), unique (job_id,attempt,processing_kind)
);
create table public.comparisons (
  id uuid primary key default gen_random_uuid(), conversation_id uuid not null references public.conversations(id),
  left_run_id uuid not null, right_run_id uuid not null, audio_manifest_hash text not null check (audio_manifest_hash ~ '^[a-f0-9]{64}$'),
  requested_by uuid not null references auth.users(id), review_notes text not null default '' check (length(review_notes)<=10000),
  idempotency_key text not null, created_at timestamptz not null default now(),
  foreign key (left_run_id,conversation_id) references public.processing_runs(id,conversation_id),
  foreign key (right_run_id,conversation_id) references public.processing_runs(id,conversation_id),
  check (left_run_id<>right_run_id), unique (conversation_id,idempotency_key)
);
create table public.worker_heartbeats (worker_id text primary key, worker_type text not null, last_seen_at timestamptz not null default now(), metrics jsonb not null default '{}');
create table recorder_private.provider_limits (scope text primary key, max_concurrency integer not null check (max_concurrency>0));
create table recorder_private.queue_receipts (job_id uuid primary key references public.processing_jobs(id), msg_id bigint not null unique);
create table recorder_private.rejected_results (id bigint generated always as identity primary key, job_id uuid not null, fence bigint not null, reason text not null, created_at timestamptz not null default now());

create index conversations_dashboard_idx on public.conversations(organisation_id,owner_id,created_at desc,id) where deleted_at is null;
create index conversations_customer_history_idx on public.conversations(customer_id,created_at desc) where deleted_at is null;
create index customers_list_idx on public.customers(organisation_id,owner_id,created_at desc,id) where deleted_at is null;
create index jobs_claim_idx on public.processing_jobs(worker_type,available_at,priority,created_at) where state in ('queued','retry');
create index jobs_leases_idx on public.processing_jobs(lease_until) where state='running';
create index jobs_conversation_idx on public.processing_jobs(conversation_id,created_at);
create index jobs_connection_idx on public.processing_jobs(connection_id,state);
create index chunks_timeline_idx on public.audio_chunks(conversation_id,sample_start);
create index turns_timeline_idx on public.transcript_turns(version_id,ordinal);
create index members_user_idx on public.members(user_id,organisation_id) where status='active';
create index runs_conversation_idx on public.processing_runs(conversation_id,created_at desc);
create index windows_run_coverage_idx on public.transcription_windows(run_id,sample_start,sample_end);
create index usage_org_lookup_idx on public.usage_records(conversation_id,created_at);
create index audit_org_idx on public.audit_events(organisation_id,created_at desc);

-- Deterministic object path is checked against the server-created reservation.
create function recorder_private.check_chunk_path() returns trigger language plpgsql set search_path='' as $$
declare c public.conversations;
begin
  select * into strict c from public.conversations where id=new.conversation_id;
  if new.object_path <> c.organisation_id::text || '/' || c.owner_id::text || '/' || c.id::text || '/' || new.part_id::text || '/' || new.sequence::text || '.wav' then
    raise exception 'invalid_chunk_path' using errcode='23514';
  end if;
  if tg_op='UPDATE' and (new.conversation_id,new.part_id,new.sequence,new.sample_start,new.sample_count,new.sample_rate,new.bytes,new.mime,new.sha256,new.object_path)
    is distinct from (old.conversation_id,old.part_id,old.sequence,old.sample_start,old.sample_count,old.sample_rate,old.bytes,old.mime,old.sha256,old.object_path) then
    raise exception 'immutable_chunk' using errcode='23514';
  end if;
  return new;
end $$;
create trigger chunks_immutable before insert or update on public.audio_chunks for each row execute function recorder_private.check_chunk_path();

create function recorder_private.immutable_identity() returns trigger language plpgsql set search_path='' as $$
begin
  if (new.id,new.organisation_id,new.owner_id,new.customer_id) is distinct from (old.id,old.organisation_id,old.owner_id,old.customer_id) then
    raise exception 'immutable_conversation_identity' using errcode='23514';
  end if;
  return new;
end $$;
create trigger conversations_identity before update on public.conversations for each row execute function recorder_private.immutable_identity();

create function recorder_private.on_auth_user() returns trigger language plpgsql security definer set search_path='' as $$
begin
  insert into public.profiles(id,display_name) values(new.id,left(coalesce(new.raw_user_meta_data->>'display_name',''),160));
  return new;
end $$;
create trigger recorder_profile_created after insert on auth.users for each row execute function recorder_private.on_auth_user();

commit;
