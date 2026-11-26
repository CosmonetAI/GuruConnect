-- Durable creation/delivery claims prevent document duplication after timeouts.
create table if not exists public.voice_sales_documents (
  id uuid primary key,
  organisation_id uuid not null references public.organisations(id) on delete cascade,
  member_id uuid not null references auth.users(id),
  session_id uuid not null references public.chat_sessions(id) on delete cascade,
  request_id uuid not null,
  data jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(organisation_id,member_id,session_id,request_id)
);
alter table public.voice_sales_documents enable row level security;
create policy voice_sales_documents_read on public.voice_sales_documents for select to authenticated
  using(member_id=auth.uid() and exists(select 1 from public.members m
    where m.organisation_id=voice_sales_documents.organisation_id and m.user_id=auth.uid() and m.status='active'));
revoke all on public.voice_sales_documents from anon,authenticated;
grant select on public.voice_sales_documents to authenticated;
grant all on public.voice_sales_documents to service_role;
