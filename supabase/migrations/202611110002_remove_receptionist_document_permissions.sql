begin;

delete from public.role_document_permissions where role = 'receptionist';

alter table public.role_document_permissions
  drop constraint if exists role_document_permissions_role_check;

alter table public.role_document_permissions
  add constraint role_document_permissions_role_check
  check (role in ('owner', 'salesperson'));

commit;
