begin;

-- Zoho OAuth credentials belong to the individual workspace member who
-- authorised them. Receptionists must never be able to create a connection.
alter table public.zoho_connections add column if not exists member_id uuid references auth.users(id);
drop index if exists public.zoho_connections_organisation_idx;
create unique index if not exists zoho_connections_member_idx
  on public.zoho_connections(organisation_id, member_id)
  where organisation_id is not null and member_id is not null;

create or replace function recorder_private.validate_zoho_connection_member()
returns trigger language plpgsql security definer set search_path='' as $$
begin
  if new.organisation_id is null or new.member_id is null then
    raise exception 'zoho_member_scope_required' using errcode='23514';
  end if;
  if not exists (
    select 1 from public.members
    where organisation_id = new.organisation_id
      and user_id = new.member_id
      and status = 'active'
      and role in ('owner','salesperson')
  ) then
    raise exception 'zoho_access_requires_active_owner_or_salesperson' using errcode='42501';
  end if;
  return new;
end $$;

drop trigger if exists validate_zoho_connection_member on public.zoho_connections;
create trigger validate_zoho_connection_member
before insert or update on public.zoho_connections
for each row execute function recorder_private.validate_zoho_connection_member();

-- Pipeline cache timestamps are also isolated by the member connection.
alter table public.zoho_pipeline_sync add column if not exists organisation_id uuid references public.organisations(id);
alter table public.zoho_pipeline_sync add column if not exists member_id uuid references auth.users(id);
create unique index if not exists zoho_pipeline_sync_member_idx
  on public.zoho_pipeline_sync(organisation_id, member_id)
  where organisation_id is not null and member_id is not null;

alter table public.zoho_pipeline_records add column if not exists organisation_id uuid references public.organisations(id);
alter table public.zoho_pipeline_records add column if not exists member_id uuid references auth.users(id);
create index if not exists zoho_pipeline_records_member_idx
  on public.zoho_pipeline_records(organisation_id, member_id, modified_time desc nulls last);

commit;
