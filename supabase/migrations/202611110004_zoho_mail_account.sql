alter table public.zoho_connections
  add column if not exists mail_account_id text;
