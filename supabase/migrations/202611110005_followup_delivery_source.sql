begin;

alter table public.guruconnect_followup_email_deliveries
  add column if not exists source text not null default 'automatic'
  check (source in ('automatic', 'manual'));

commit;
