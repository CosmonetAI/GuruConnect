-- Do not deliver an AI event for a non-English transcript until its
-- translation exists. Native-English transcripts carry english_text directly
-- and remain eligible without a translations row.
create or replace function public.invoke_voice_extraction_webhook()
returns trigger language plpgsql security definer
set search_path = public, extensions, vault
as $$
declare
  callback_url text;
  callback_secret text;
  conversation_org uuid;
  record_id uuid;
  translated_ready boolean;
begin
  if tg_table_name = 'translations' then
    if new.state <> 'complete' or nullif(new.english_text, '') is null then
      return new;
    end if;
  elsif tg_table_name = 'transcript_turns' then
    translated_ready := nullif(new.english_text, '') is not null;
    if not translated_ready then
      select exists(
        select 1
        from public.translations t
        where t.source_turn_id = new.id
          and t.state = 'complete'
          and nullif(t.english_text, '') is not null
      ) into translated_ready;
    end if;
    if not translated_ready then
      return new;
    end if;
  end if;

  select organisation_id into conversation_org
    from public.conversations where id = new.conversation_id;
  select decrypted_secret into callback_url
    from vault.decrypted_secrets where name = 'voice_extraction_url' limit 1;
  select decrypted_secret into callback_secret
    from vault.decrypted_secrets where name = 'voice_extraction_webhook_secret' limit 1;
  if callback_url is null or callback_secret is null then
    raise warning 'Voice extraction webhook is not configured: add voice_extraction_url and voice_extraction_webhook_secret to Supabase Vault';
    return new;
  end if;

  record_id := new.id;
  perform net.http_post(
    url := rtrim(callback_url, '/') || '/api/internal/voice/extraction-webhook',
    headers := jsonb_build_object(
      'Content-Type','application/json',
      'X-Voice-Extraction-Secret',callback_secret
    ),
    body := jsonb_build_object(
      'event',tg_op,
      'table',tg_table_name,
      'conversation_id',new.conversation_id,
      'organisation_id',conversation_org,
      'record_id',record_id
    )
  );
  return new;
end;
$$;

revoke all on function public.invoke_voice_extraction_webhook() from public;
