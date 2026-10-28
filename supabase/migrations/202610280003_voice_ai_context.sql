create table if not exists public.voice_ai_contexts (
  conversation_id uuid primary key references public.conversations(id) on delete cascade,
  organisation_id uuid not null references public.organisations(id) on delete cascade,
  context jsonb not null default '{}'::jsonb,
  last_intent text,
  last_route jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
create index if not exists voice_ai_contexts_org_idx on public.voice_ai_contexts(organisation_id, updated_at desc);
alter table public.voice_ai_contexts enable row level security;
create policy voice_ai_contexts_read on public.voice_ai_contexts for select to authenticated using (public.recorder_conversation_access(conversation_id));
revoke all on public.voice_ai_contexts from anon, authenticated;
grant select on public.voice_ai_contexts to authenticated;
grant all on public.voice_ai_contexts to service_role;
do $$ begin
  if exists(select 1 from pg_publication where pubname='supabase_realtime') then
    alter publication supabase_realtime add table public.voice_ai_contexts;
  end if;
exception when duplicate_object then null;
end $$;
