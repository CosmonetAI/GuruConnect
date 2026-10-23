-- Keep the database path guard aligned with the bucketed recorder paths.
-- The recorder stores chunks under:
--   organisation/owner/conversation/bucket/phone/part/sequence.wav
-- The original guard predates audio buckets and rejected every new reservation
-- with SQLSTATE 23514 (surfaced by the API as conflicting_state).
create or replace function recorder_private.check_chunk_path() returns trigger
language plpgsql set search_path='' as $$
declare
  c public.conversations;
  customer_phone text;
  expected_path text;
begin
  select * into strict c
    from public.conversations
   where id=new.conversation_id;

  select right(regexp_replace(coalesce(phone,''),'[^0-9]','','g'),15)
    into customer_phone
    from public.customers
   where id=c.customer_id
     and organisation_id=c.organisation_id;

  customer_phone := coalesce(nullif(customer_phone,''),'no-phone');
  expected_path := c.organisation_id::text || '/' || c.owner_id::text || '/' ||
    c.id::text || '/' || coalesce(c.audio_bucket,'session-audio') || '/' ||
    customer_phone || '/' || new.part_id::text || '/' || new.sequence::text || '.wav';

  if new.object_path <> expected_path then
    raise exception 'invalid_chunk_path' using errcode='23514';
  end if;
  if tg_op='UPDATE' and (new.conversation_id,new.part_id,new.sequence,new.sample_start,new.sample_count,new.sample_rate,new.bytes,new.mime,new.sha256,new.object_path)
    is distinct from (old.conversation_id,old.part_id,old.sequence,old.sample_start,old.sample_count,old.sample_rate,old.bytes,old.mime,old.sha256,old.object_path) then
    raise exception 'immutable_chunk' using errcode='23514';
  end if;
  return new;
end $$;
