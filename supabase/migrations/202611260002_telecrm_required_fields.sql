begin;

-- Enforce the TeleCRM contract for new deliveries without invalidating older
-- rows that may have been accepted before these fields became mandatory.
alter table public.telecrm_events
  add constraint telecrm_call_required_fields_check
  check (
    event_type <> 'call_recording_available'
    or (
      nullif(recording_url, '') is not null
      and nullif(customer_phone, '') is not null
      and nullif(call_started_at, '') is not null
      and nullif(call_ended_at, '') is not null
      and nullif(duration_seconds, '') is not null
    )
  ) not valid;

alter table public.telecrm_events
  add constraint telecrm_incoming_whatsapp_required_fields_check
  check (
    event_type <> 'whatsapp_message_received'
    or (
      nullif(customer_phone, '') is not null
      and nullif(message_text, '') is not null
      and nullif(received_at, '') is not null
    )
  ) not valid;

alter table public.telecrm_events
  add constraint telecrm_status_required_fields_check
  check (
    event_type <> 'whatsapp_message_status'
    or (nullif(customer_phone, '') is not null and nullif(status, '') is not null)
  ) not valid;

commit;
