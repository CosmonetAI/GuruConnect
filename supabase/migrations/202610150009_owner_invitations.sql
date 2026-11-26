-- Owners can grant full workspace access to another teammate.
alter table public.invites drop constraint if exists invites_role_check;
alter table public.invites add constraint invites_role_check check (role in ('owner','salesperson','receptionist'));
