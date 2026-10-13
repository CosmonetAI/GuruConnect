begin;
create function recorder_private.retire_queue_receipts(p_limit integer) returns integer language plpgsql set search_path='' as $$
declare r record;v_count integer:=0;
begin
  for r in select qr.job_id,qr.msg_id from recorder_private.queue_receipts qr join public.processing_jobs j on j.id=qr.job_id
    where j.state in ('succeeded','cancelled','failed','dead','blocked','uncertain')
    order by j.updated_at limit greatest(1,least(p_limit,1000)) for update of j,qr skip locked loop
    perform pgmq.archive('recorder_jobs',r.msg_id);
    delete from recorder_private.queue_receipts where job_id=r.job_id;
    v_count:=v_count+1;
  end loop;
  return v_count;
end $$;
alter table public.transcript_turns add constraint turns_run_identity unique(id,run_id);
alter table public.translations add constraint translations_source_run_fk foreign key(source_turn_id,run_id) references public.transcript_turns(id,run_id);
alter table public.inference_workers add constraint worker_revocation_consistency check ((state='revoked')=(revoked_at is not null));

create function recorder_private.immutable_machine_data() returns trigger language plpgsql set search_path='' as $$
begin raise exception 'immutable_machine_result' using errcode='23514';end $$;
do $$declare t text;begin
  foreach t in array array['inference_artifacts','transcript_versions','transcript_turns','transcript_words','diarization_intervals'] loop
    execute format('create trigger machine_data_immutable before update on public.%I for each row execute function recorder_private.immutable_machine_data()',t);
  end loop;
end $$;

create function recorder_private.artifact_scope() returns trigger language plpgsql set search_path='' as $$
declare c public.conversations;prefix text;
begin
  select * into strict c from public.conversations where id=new.conversation_id;
  prefix:=c.organisation_id::text||'/'||c.owner_id::text||'/'||c.id::text||'/';
  if new.object_path not like prefix||'%' or new.object_path like '%..%' or new.object_path like '%://%' then
    raise exception 'artifact_path_scope_invalid' using errcode='23514';end if;
  if c.deleted_at is not null then raise exception 'conversation_deleted' using errcode='23514';end if;
  return new;
end $$;
create trigger artifact_scope before insert on public.inference_artifacts for each row execute function recorder_private.artifact_scope();

create function recorder_private.version_job_guard() returns trigger language plpgsql set search_path='' as $$
begin
  if not exists(select 1 from public.processing_jobs j join public.conversations c on c.id=j.conversation_id
    where j.id=new.job_id and j.run_id=new.run_id and j.conversation_id=new.conversation_id and j.state='succeeded' and c.deleted_at is null) then
    raise exception 'successful_job_required' using errcode='23514';end if;
  return new;
end $$;
create trigger transcript_successful_job before insert on public.transcript_versions for each row execute function recorder_private.version_job_guard();

-- Retention removes machine records rather than rewriting them. Tombstones remain
-- and fence delayed confirmations/callbacks; new results cannot recreate content.
create function recorder_private.reject_deleted_content() returns trigger language plpgsql set search_path='' as $$
declare conversation uuid;
begin
  if tg_table_name='transcript_words' then
    select conversation_id into conversation from public.transcript_versions where id=new.version_id;
  else conversation:=new.conversation_id;end if;
  if exists(select 1 from public.conversations where id=conversation and deleted_at is not null) then
    raise exception 'conversation_deleted' using errcode='23514';end if;
  return new;
end $$;
do $$declare t text;begin
  foreach t in array array['recording_parts','audio_chunks','processing_runs','transcription_windows','transcript_turns','transcript_words','translations','diarization_intervals','speaker_assignments','transcript_revisions'] loop
    execute format('create trigger no_deleted_content before insert on public.%I for each row execute function recorder_private.reject_deleted_content()',t);
  end loop;
end $$;
revoke all on all functions in schema recorder_private from public,anon,authenticated;
commit;
