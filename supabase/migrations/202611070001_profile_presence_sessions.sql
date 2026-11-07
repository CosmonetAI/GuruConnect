create table if not exists public.profile_presence_sessions (
  user_id uuid not null references auth.users(id) on delete cascade,
  session_id uuid not null,
  last_seen_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  primary key (user_id, session_id)
);

create index if not exists profile_presence_sessions_live_idx
  on public.profile_presence_sessions(user_id, last_seen_at desc);

alter table public.profile_presence_sessions enable row level security;
