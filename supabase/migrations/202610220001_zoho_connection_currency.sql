-- Cache the selected Zoho Books organization's base currency.
alter table public.zoho_connections
  add column if not exists currency text;
