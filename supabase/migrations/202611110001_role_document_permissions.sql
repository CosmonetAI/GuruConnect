begin;

create table if not exists public.role_document_permissions (
  organisation_id uuid not null references public.organisations(id) on delete cascade,
  role text not null check (role in ('owner', 'salesperson')),
  document_type text not null check (document_type in ('quote', 'invoice', 'sales-order', 'purchase-order')),
  can_create boolean not null default false,
  can_edit boolean not null default false,
  can_delete boolean not null default false,
  can_accept boolean not null default false,
  can_mark_paid boolean not null default false,
  updated_at timestamptz not null default now(),
  primary key (organisation_id, role, document_type)
);

alter table public.role_document_permissions add column if not exists can_accept boolean not null default false;
alter table public.role_document_permissions add column if not exists can_mark_paid boolean not null default false;

create index if not exists role_document_permissions_org_idx
  on public.role_document_permissions(organisation_id, role);

commit;
