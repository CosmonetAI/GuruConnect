create table if not exists public.chat_recording_chunks (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.chat_sessions(id) on delete cascade,
  chunk_index integer not null check (chunk_index >= 0),
  duration_seconds numeric(12,3) not null default 0 check (duration_seconds >= 0),
  mime_type text not null default 'audio/webm',
  size_bytes bigint not null check (size_bytes > 0),
  object_path text not null,
  created_at timestamptz not null default now(),
  unique(session_id, chunk_index),
  unique(object_path)
);
create index if not exists chat_recording_chunks_session_idx on public.chat_recording_chunks(session_id, chunk_index);
alter table public.chat_recording_chunks enable row level security;
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values ('chat-recordings','chat-recordings',false,52428800,array['audio/webm','audio/ogg','audio/mp4','audio/wav'])
on conflict (id) do nothing;
