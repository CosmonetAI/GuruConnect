-- Event delivery for the live sales workspace. The webhook payload contains
-- identifiers only; the backend reads private transcript content server-side.
drop trigger if exists transcript_turn_extraction_webhook on public.transcript_turns;
create trigger transcript_turn_extraction_webhook
after insert or update of source_text, english_text on public.transcript_turns
for each row execute function public.invoke_voice_extraction_webhook();

drop trigger if exists translation_extraction_webhook on public.translations;
create trigger translation_extraction_webhook
after insert or update of state, english_text on public.translations
for each row when (new.state = 'complete') execute function public.invoke_voice_extraction_webhook();

do $$ begin
  if exists(select 1 from pg_publication where pubname='supabase_realtime') then
    alter publication supabase_realtime add table public.conversation_extractions;
    alter publication supabase_realtime add table public.transcript_turns;
    alter publication supabase_realtime add table public.translations;
  end if;
exception when duplicate_object then null;
end $$;
