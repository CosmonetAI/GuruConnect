alter table public.chat_sessions add column if not exists conversation_id uuid;
create index if not exists chat_sessions_conversation_idx on public.chat_sessions(conversation_id) where conversation_id is not null;
