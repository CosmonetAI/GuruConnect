-- CRM provisioning metadata for visitor/enquiry hand-off.
alter table public.guruconnect_enquiries
  add column if not exists bigin_pipeline_id text,
  add column if not exists bigin_deal_id text,
  add column if not exists follow_up_required boolean not null default false,
  add column if not exists crm_sync_error text;

create index if not exists guruconnect_enquiries_followup_idx
  on public.guruconnect_enquiries (organisation_id, assigned_to, follow_up_required, created_at desc);
