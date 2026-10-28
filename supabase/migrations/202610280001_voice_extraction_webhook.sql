-- Extraction is event driven: transcript/translation writes notify the API once.
-- Store the result separately so retries are idempotent and never rewrite machine output.
create table if not exists public.conversation_extractions (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  organisation_id uuid not null references public.organisations(id) on delete cascade,
  fingerprint text not null,
  status text not null default 'pending' check (status in ('pending','running','complete','failed')),
  data jsonb not null default '{}'::jsonb,
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (conversation_id, fingerprint)
);
create index if not exists conversation_extractions_recent_idx
  on public.conversation_extractions(conversation_id, updated_at desc);

alter table public.conversation_extractions enable row level security;
create policy conversation_extractions_read on public.conversation_extractions
  for select to authenticated using (public.recorder_conversation_access(conversation_id));
revoke all on public.conversation_extractions from anon, authenticated;
grant select on public.conversation_extractions to authenticated;
grant all on public.conversation_extractions to service_role;

create extension if not exists pg_net with schema extensions;

create or replace function public.invoke_voice_extraction_webhook()
returns trigger language plpgsql security definer
set search_path = public, extensions, vault
as $$
declare
  callback_url text;
  callback_secret text;
  conversation_org uuid;
  record_id uuid;
begin
  if tg_table_name = 'translations' and new.state <> 'complete' then return new; end if;
  select organisation_id into conversation_org from public.conversations where id = new.conversation_id;
  select decrypted_secret into callback_url from vault.decrypted_secrets where name = 'voice_extraction_url' limit 1;
  select decrypted_secret into callback_secret from vault.decrypted_secrets where name = 'voice_extraction_webhook_secret' limit 1;
  if callback_url is null or callback_secret is null then
    raise warning 'Voice extraction webhook is not configured: add voice_extraction_url and voice_extraction_webhook_secret to Supabase Vault';
    return new;
  end if;
  record_id := new.id;
  perform net.http_post(
    url := rtrim(callback_url, '/') || '/api/internal/voice/extraction-webhook',
    headers := jsonb_build_object('Content-Type','application/json','X-Voice-Extraction-Secret',callback_secret),
    body := jsonb_build_object('event',tg_op,'table',tg_table_name,'conversation_id',new.conversation_id,
      'organisation_id',conversation_org,'record_id',record_id)
  );
  return new;
end;
$$;

drop trigger if exists transcript_turn_extraction_webhook on public.transcript_turns;
create trigger transcript_turn_extraction_webhook after insert on public.transcript_turns
  for each row execute function public.invoke_voice_extraction_webhook();
drop trigger if exists translation_extraction_webhook on public.translations;
create trigger translation_extraction_webhook after insert or update of state on public.translations
  for each row when (new.state = 'complete') execute function public.invoke_voice_extraction_webhook();

revoke all on function public.invoke_voice_extraction_webhook() from public;
