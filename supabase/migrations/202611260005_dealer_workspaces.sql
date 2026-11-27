begin;
alter table public.members drop constraint if exists members_role_check;
alter table public.members add constraint members_role_check
  check (role in ('owner', 'salesperson', 'receptionist', 'dealer'));
alter table public.invites drop constraint if exists invites_role_check;
alter table public.invites add constraint invites_role_check
  check (role in ('owner', 'salesperson', 'receptionist', 'dealer'));
create table public.dealer_account_mappings (
  organisation_id uuid not null,
  dealer_id uuid not null,
  account_member_id uuid not null,
  books_contact_id text not null,
  discount_percent numeric(5,2) not null default 0 check (discount_percent between 0 and 100),
  gst_group_id text,
  updated_at timestamptz not null default now(),
  primary key (organisation_id, dealer_id),
  foreign key (organisation_id, dealer_id) references public.members(organisation_id, user_id) on delete cascade,
  foreign key (organisation_id, account_member_id) references public.members(organisation_id, user_id)
);
create table public.dealer_invoice_references (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null,
  dealer_id uuid not null,
  account_member_id uuid not null,
  zoho_invoice_id text,
  previous_zoho_invoice_id text,
  action_key uuid not null,
  request_hash text not null check (request_hash ~ '^[a-f0-9]{64}$'),
  state text not null check (state in ('creating', 'active', 'replacing', 'void_pending', 'voided', 'failed', 'uncertain', 'cleanup_pending')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (organisation_id, dealer_id) references public.members(organisation_id, user_id),
  foreign key (organisation_id, account_member_id) references public.members(organisation_id, user_id),
  unique (organisation_id, dealer_id, action_key),
  unique (organisation_id, dealer_id, zoho_invoice_id)
);
create index dealer_invoice_references_listing
  on public.dealer_invoice_references(organisation_id, dealer_id, created_at desc)
  where zoho_invoice_id is not null;
create unique index dealer_invoice_references_one_replacement
  on public.dealer_invoice_references(organisation_id, dealer_id, previous_zoho_invoice_id)
  where state in ('creating', 'replacing', 'void_pending', 'uncertain', 'cleanup_pending')
    and previous_zoho_invoice_id is not null;
alter table public.dealer_account_mappings enable row level security;
alter table public.dealer_invoice_references enable row level security;
revoke all on public.dealer_account_mappings, public.dealer_invoice_references from anon, authenticated;
commit;
