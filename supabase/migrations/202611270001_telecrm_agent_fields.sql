begin;

-- Require TeleCRM agent identity on every new webhook delivery. Keep this
-- NOT VALID so older development rows remain readable while new rows enforce
-- the updated integration contract.
alter table public.telecrm_events
  add constraint telecrm_agent_fields_required_check
  check (
    event_type not in (
      'call_recording_available',
      'whatsapp_message_received',
      'whatsapp_message_status'
    )
    or (
      nullif(agent_id, '') is not null
      and nullif(agent_email, '') is not null
    )
  ) not valid;

commit;
