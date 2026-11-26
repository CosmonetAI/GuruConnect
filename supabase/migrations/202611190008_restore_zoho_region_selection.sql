-- Restore the persisted Zoho region selector after a reset. The operation is
-- intentionally idempotent so it also repairs environments that missed the
-- earlier region migration.
alter table public.zoho_connections
  add column if not exists zoho_region text not null default 'us';

update public.zoho_connections
set zoho_region='us'
where zoho_region is null or zoho_region not in ('us','in');

alter table public.zoho_connections
  drop constraint if exists zoho_connections_region_check;
alter table public.zoho_connections
  add constraint zoho_connections_region_check
  check (zoho_region in ('us','in'));
