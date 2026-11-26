begin;

alter table public.guruconnect_enquiries add column if not exists ticket_day date;
alter table public.guruconnect_enquiries add column if not exists daily_sequence integer;
alter table public.guruconnect_enquiries add column if not exists public_token_hash text;
create unique index if not exists guruconnect_enquiries_daily_ticket_idx on public.guruconnect_enquiries(organisation_id,ticket_day,daily_sequence) where ticket_day is not null;
create unique index if not exists guruconnect_enquiries_public_token_idx on public.guruconnect_enquiries(public_token_hash) where public_token_hash is not null;

commit;
