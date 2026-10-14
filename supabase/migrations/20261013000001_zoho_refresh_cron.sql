-- Remove the callback field: Zoho Self Client uses no redirect URL.
alter table public.zoho_connections add column if not exists api_domain text default 'https://www.zohoapis.com';
alter table public.zoho_connections add column if not exists access_token text;
alter table public.zoho_connections add column if not exists refresh_token text;
alter table public.zoho_connections add column if not exists client_id text;
alter table public.zoho_connections add column if not exists client_secret text;
alter table public.zoho_connections add column if not exists expires_at timestamptz;
alter table public.zoho_connections add column if not exists organization_id text;
alter table public.zoho_connections add column if not exists scope text;
alter table public.zoho_connections add column if not exists status text default 'not_connected';
alter table public.zoho_connections add column if not exists last_error text;
alter table public.zoho_connections add column if not exists updated_at timestamptz default now();
alter table public.zoho_connections add column if not exists created_at timestamptz default now();
alter table public.zoho_connections drop column if exists redirect_uri;

-- Supabase provides pg_cron, pg_net, and Vault for scheduled server-side jobs.
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
    raise warning 'Zoho refresh cron is not configured: add zoho_refresh_url and zoho_refresh_cron_secret to Supabase Vault';
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
  select jobid into existing_job_id from cron.job where jobname = 'zoho-token-refresh';
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
