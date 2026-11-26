begin;

-- Every incoming WhatsApp message must have its provider message id so the
-- delivery can be identified and retried idempotently.
alter table public.telecrm_events
  add constraint telecrm_incoming_message_id_required_check
  check (
    event_type <> 'whatsapp_message_received'
    or (
      nullif(customer_phone, '') is not null
      and nullif(message_id, '') is not null
      and nullif(message_text, '') is not null
      and nullif(received_at, '') is not null
    )
  ) not valid;

commit;
