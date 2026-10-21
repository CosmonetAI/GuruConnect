create table if not exists public.voice_session_notes (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null,
  customer_id uuid not null references public.customers(id) on delete cascade,
  session_id uuid references public.chat_sessions(id) on delete set null,
  author_id uuid,
  content text not null check (length(trim(content)) between 1 and 5000),
  created_at timestamptz not null default now()
);
create index if not exists voice_session_notes_contact_idx on public.voice_session_notes (organisation_id, customer_id, created_at desc);
alter table public.voice_session_notes enable row level security;
