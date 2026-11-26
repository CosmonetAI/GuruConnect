-- Keep the selected workspace member separate from the authenticated actor.
-- This matters when an owner operates a salesperson workspace.
alter table public.audit_events
  add column if not exists member_id uuid references auth.users(id);

create index if not exists audit_member_idx
  on public.audit_events(organisation_id, member_id, created_at desc);
