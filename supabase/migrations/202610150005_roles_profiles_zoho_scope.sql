begin;

-- Workspace roles are intentionally narrow: owners administer the workspace,
-- salespeople use sales screens, and receptionists use walk-in screens.
update public.members set role='salesperson' where role='admin';
update public.invites set role='salesperson' where role='admin';
alter table public.members drop constraint if exists members_role_check;
alter table public.members add constraint members_role_check check (role in ('owner','salesperson','receptionist'));
alter table public.invites drop constraint if exists invites_role_check;
alter table public.invites add constraint invites_role_check check (role in ('salesperson','receptionist'));

alter table public.profiles add column if not exists phone text;
alter table public.profiles add column if not exists avatar_path text;
alter table public.profiles add constraint profiles_phone_check check (phone is null or phone ~ '^\\+?[0-9][0-9 ()-]{7,19}$');

-- Legacy Zoho credentials must be separated by GuruConnect workspace.
alter table public.zoho_connections add column if not exists organisation_id uuid references public.organisations(id);
create unique index if not exists zoho_connections_organisation_idx on public.zoho_connections(organisation_id) where organisation_id is not null;

insert into storage.buckets(id, name, public) values ('profile-avatars', 'profile-avatars', false)
on conflict (id) do nothing;

create policy profile_avatar_insert on storage.objects for insert to authenticated
with check (bucket_id = 'profile-avatars' and (storage.foldername(name))[1] = auth.uid()::text);
create policy profile_avatar_update on storage.objects for update to authenticated
using (bucket_id = 'profile-avatars' and (storage.foldername(name))[1] = auth.uid()::text)
with check (bucket_id = 'profile-avatars' and (storage.foldername(name))[1] = auth.uid()::text);
create policy profile_avatar_read on storage.objects for select to authenticated
using (bucket_id = 'profile-avatars' and (storage.foldername(name))[1] = auth.uid()::text);

commit;
