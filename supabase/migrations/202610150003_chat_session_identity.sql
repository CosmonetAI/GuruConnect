alter table public.chat_sessions add column if not exists identity_phone text;
alter table public.chat_sessions add column if not exists identity_email text;
create index if not exists chat_sessions_identity_phone_idx on public.chat_sessions(identity_phone) where identity_phone is not null;
create index if not exists chat_sessions_identity_email_idx on public.chat_sessions(identity_email) where identity_email is not null;
