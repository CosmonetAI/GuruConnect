-- Send one automatic follow-up for visitors captured by the existing 21:00
-- IST missed-visitor sweep. The Monday run covers Friday; pg_cron is UTC.
begin;

create table if not exists public.guruconnect_followup_email_deliveries (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null,
  visit_id uuid not null,
  scheduled_for date not null,
  recipient text not null,
  member_id uuid not null,
  status text not null default 'sending'
    check (status in ('sending', 'sent', 'failed')),
  provider_response jsonb not null default '{}'::jsonb
    check (jsonb_typeof(provider_response) = 'object'),
  error_code text,
  sent_at timestamptz,
  created_at timestamptz not null default now(),
  unique (organisation_id, visit_id),
  foreign key (organisation_id, visit_id)
    references public.guruconnect_missed_visitors(organisation_id, visit_id)
    on delete cascade,
  foreign key (organisation_id, member_id)
    references public.members(organisation_id, user_id)
);

create index if not exists guruconnect_followup_email_dispatch_idx
  on public.guruconnect_followup_email_deliveries(status, scheduled_for);

alter table public.guruconnect_followup_email_deliveries enable row level security;
revoke all on table public.guruconnect_followup_email_deliveries from public, anon, authenticated;
grant select, insert, update on table public.guruconnect_followup_email_deliveries to service_role;

create extension if not exists pg_cron with schema extensions;
create extension if not exists pg_net with schema extensions;

create or replace function public.invoke_missed_visitor_followup_email()
returns bigint
language plpgsql
security definer
set search_path = public, extensions, vault
as $$
declare
  backend_url text;
  cron_secret text;
begin
  select decrypted_secret into backend_url
    from vault.decrypted_secrets where name = 'zoho_refresh_url' limit 1;
  select decrypted_secret into cron_secret
    from vault.decrypted_secrets where name = 'zoho_refresh_cron_secret' limit 1;
  if backend_url is null or cron_secret is null then
    raise warning 'Follow-up email cron is not configured: reuse the Zoho refresh URL and secret Vault entries';
    return null;
  end if;
  return net.http_post(
    url := rtrim(backend_url, '/') || '/api/internal/followup-emails/dispatch',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'X-Zoho-Refresh-Secret', cron_secret
    ),
    body := '{}'::jsonb
  );
end;
$$;

revoke all on function public.invoke_missed_visitor_followup_email() from public, anon, authenticated;

do $$
declare existing_job_id bigint;
begin
  select jobid into existing_job_id
    from cron.job where jobname = 'guruconnect-missed-visitor-followup-email';
  if existing_job_id is not null then perform cron.unschedule(existing_job_id); end if;
end;
$$;

-- 05:30 UTC = 11:00 Asia/Kolkata, Monday through Friday.
select cron.schedule(
  'guruconnect-missed-visitor-followup-email',
  '30 5 * * 1-5',
  $$select public.invoke_missed_visitor_followup_email();$$
);

commit;
