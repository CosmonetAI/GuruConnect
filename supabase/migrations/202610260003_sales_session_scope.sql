begin;

-- Customer identity is local to an authenticated workspace member. The legacy
-- global indexes prevented different workspaces from recording the same phone.
drop index if exists public.customers_phone_unique;
drop index if exists public.customers_email_unique;
create unique index customers_member_phone_unique
  on public.customers(organisation_id,owner_id,normalized_phone)
  where normalized_phone is not null and deleted_at is null;
create unique index customers_member_email_unique
  on public.customers(organisation_id,owner_id,normalized_email)
  where normalized_email is not null and deleted_at is null;

alter table public.customers add constraint customers_id_organisation_unique unique(id,organisation_id);
alter table public.conversations add constraint conversations_sales_link_unique unique(id,organisation_id,owner_id,customer_id);
alter table public.chat_sessions add constraint chat_sessions_note_scope_unique unique(id,organisation_id,customer_id);

-- NOT VALID preserves legacy unscoped rows without making them visible through
-- the API. It still enforces the scope of every new or changed row. Deferred
-- links permit the existing owner transfer transaction to update both sides.
alter table public.chat_sessions add constraint chat_sessions_scope_required
  check (organisation_id is not null and member_id is not null) not valid;
alter table public.chat_sessions add constraint chat_sessions_customer_scope_fk
  foreign key(customer_id,organisation_id,member_id)
  references public.customers(id,organisation_id,owner_id)
  deferrable initially deferred not valid;
alter table public.chat_sessions add constraint chat_sessions_conversation_scope_fk
  foreign key(conversation_id,organisation_id,member_id,customer_id)
  references public.conversations(id,organisation_id,owner_id,customer_id)
  deferrable initially deferred not valid;
alter table public.voice_session_notes add constraint voice_session_notes_customer_scope_fk
  foreign key(customer_id,organisation_id)
  references public.customers(id,organisation_id)
  deferrable initially deferred not valid;
alter table public.voice_session_notes add constraint voice_session_notes_session_scope_fk
  foreign key(session_id,organisation_id,customer_id)
  references public.chat_sessions(id,organisation_id,customer_id)
  deferrable initially deferred not valid;

commit;
