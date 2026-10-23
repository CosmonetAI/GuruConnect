-- A deliberate new recording may replace an abandoned lease owned by the same
-- user. Existing migrations remain immutable; this replaces the function in a
-- new migration so stale sessions cannot block the Start recording button.
create or replace function public.acquire_recording_lease(p_conversation_id uuid,p_user_id uuid,p_tab_id text,p_takeover boolean default false)
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
  if found and l.expires_at>now() and l.conversation_id=p_conversation_id and l.tab_id=p_tab_id then return l; end if;
  if found and l.expires_at>now() and not p_takeover then raise exception 'recording_lease_busy' using errcode='55P03'; end if;
  if found and not p_takeover then raise exception 'explicit_takeover_required' using errcode='55P03'; end if;
  if found and l.conversation_id is not null then
    update public.conversations set capture_state='interrupted',updated_at=now() where id=l.conversation_id and capture_state in ('recording','paused');
  end if;
  insert into public.recording_leases(user_id,organisation_id,conversation_id,tab_id,expires_at)
    values(p_user_id,c.organisation_id,c.id,p_tab_id,now()+interval '90 seconds')
    on conflict(user_id) do update set organisation_id=excluded.organisation_id,conversation_id=excluded.conversation_id,
      tab_id=excluded.tab_id,token=gen_random_uuid(),fence=public.recording_leases.fence+1,expires_at=excluded.expires_at
    returning * into l;
  return l;
end $$;
