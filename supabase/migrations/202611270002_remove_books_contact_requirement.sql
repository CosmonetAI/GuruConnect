begin;

-- Remove the books_contact_id NOT NULL constraint by allowing NULL
alter table public.dealer_account_mappings
  alter column books_contact_id set default '',
  drop constraint if exists dealer_account_mappings_books_contact_id_check,
  add constraint dealer_account_mappings_books_contact_id_check check (books_contact_id ~ '^[a-zA-Z0-9]*$');

commit;
