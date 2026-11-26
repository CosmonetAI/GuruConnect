alter table public.zoho_connections
  add column if not exists zoho_taxes jsonb not null default '[]'::jsonb;

alter table public.zoho_connections
  add column if not exists zoho_taxes_updated_at timestamptz;
