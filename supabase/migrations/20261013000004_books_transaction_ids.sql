-- Transaction references created by the AI sales workflow.
-- Kept in a new migration so the original schema remains unchanged.
alter table public.enquiries add column if not exists books_salesorder_id text;
alter table public.enquiries add column if not exists books_salesorder_number text;
alter table public.enquiries add column if not exists books_invoice_id text;
alter table public.enquiries add column if not exists books_invoice_number text;
alter table public.enquiries add column if not exists quote_url text;
alter table public.enquiries add column if not exists transaction_status text;
