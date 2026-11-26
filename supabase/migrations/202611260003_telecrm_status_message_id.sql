begin;

-- Every status delivery must identify the WhatsApp message it belongs to.
-- Keep this NOT VALID so any older development rows remain readable while all
-- new status rows enforce the complete identity.
alter table public.telecrm_events
  add constraint telecrm_status_message_id_required_check
  check (
    event_type <> 'whatsapp_message_status'
    or (nullif(customer_phone, '') is not null and nullif(message_id, '') is not null and nullif(status, '') is not null)
  ) not valid;

commit;
