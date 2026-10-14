-- Ensures the Zoho refresh cron exists on environments where the earlier
-- refresh migration was already applied or the scheduled job was removed.
-- Secrets are intentionally not stored in this migration. Configure these in
-- Supabase Vault: zoho_refresh_url and zoho_refresh_cron_secret.

create extension if not exists pg_cron with schema extensions;
create extension if not exists pg_net with schema extensions;

create or replace function public.invoke_zoho_token_refresh()
returns bigint
language plpgsql
security definer
set search_path = public, extensions, vault
as $$
declare
  refresh_url text;
  refresh_secret text;
begin
  select decrypted_secret into refresh_url
  from vault.decrypted_secrets
  where name = 'zoho_refresh_url'
  limit 1;

  select decrypted_secret into refresh_secret
  from vault.decrypted_secrets
  where name = 'zoho_refresh_cron_secret'
  limit 1;

  if refresh_url is null or refresh_secret is null then
    raise warning 'Configure zoho_refresh_url and zoho_refresh_cron_secret in Supabase Vault';
    return null;
  end if;

  return net.http_post(
    url := rtrim(refresh_url, '/') || '/api/internal/zoho/refresh',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'X-Zoho-Refresh-Secret', refresh_secret
    ),
    body := '{}'::jsonb
  );
end;
$$;

revoke all on function public.invoke_zoho_token_refresh() from public;

do $$
declare
  existing_job_id bigint;
begin
  select jobid into existing_job_id
  from cron.job
  where jobname = 'zoho-token-refresh';

  if existing_job_id is not null then
    perform cron.unschedule(existing_job_id);
  end if;
end;
$$;

select cron.schedule(
  'zoho-token-refresh',
  '*/10 * * * *',
  $$select public.invoke_zoho_token_refresh();$$
);

select vault.create_secret(
  'mUcxeau4hWQJwZvSeltes83Zi6Gj1rcJ4t-LXYcletYqXOotS-va0K5xc5vdgcKk',
  'zoho_refresh_cron_secret',
  'Secret used by the Zoho refresh cron'
);

select vault.create_secret(
  'https://gg-backend-0vh9.onrender.com',
  'zoho_refresh_url',
  'Backend URL used by the Zoho refresh cron'
);
