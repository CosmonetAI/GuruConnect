-- Contact/session federation and the terminal visitor outcome.
alter table public.guruconnect_enquiries
  drop constraint if exists guruconnect_enquiries_stage_check;
alter table public.guruconnect_enquiries
  add constraint guruconnect_enquiries_stage_check
  check (stage in ('new','in_process','closed','customer_did_not_show'));

alter table public.guruconnect_enquiries add column if not exists bigin_contact_id text;
alter table public.guruconnect_enquiries add column if not exists books_contact_id text;

alter table public.customers add column if not exists organisation_id uuid;
alter table public.customers add column if not exists member_id uuid;
alter table public.customers add column if not exists bigin_contact_id text;
alter table public.customers add column if not exists books_contact_id text;
alter table public.chat_sessions add column if not exists organisation_id uuid;
alter table public.chat_sessions add column if not exists member_id uuid;
alter table public.chat_sessions add column if not exists bigin_contact_id text;
alter table public.chat_sessions add column if not exists books_contact_id text;

create index if not exists customers_organisation_identity_idx
  on public.customers (organisation_id, normalized_phone, normalized_email);
create index if not exists chat_sessions_organisation_updated_idx
  on public.chat_sessions (organisation_id, updated_at desc);
create index if not exists guruconnect_enquiries_organisation_customer_idx
  on public.guruconnect_enquiries (organisation_id, phone, created_at desc);
