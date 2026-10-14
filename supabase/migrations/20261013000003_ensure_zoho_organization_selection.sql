-- The selected Books organization is optional while a connected account has
-- multiple organizations. It becomes populated only after the user selects one.
alter table public.zoho_connections
  add column if not exists organization_id text;

alter table public.zoho_connections
  alter column organization_id drop default;
