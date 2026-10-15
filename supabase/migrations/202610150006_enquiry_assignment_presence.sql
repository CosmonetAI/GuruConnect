begin;

alter table public.profiles add column if not exists availability_status text not null default 'active';
alter table public.profiles add column if not exists last_seen_at timestamptz;
alter table public.profiles drop constraint if exists profiles_availability_status_check;
alter table public.profiles add constraint profiles_availability_status_check check (availability_status in ('active','not_active','on_leave','be_right_back'));

alter table public.guruconnect_enquiries add column if not exists assigned_to uuid;
alter table public.guruconnect_enquiries add column if not exists stage text not null default 'new';
alter table public.guruconnect_enquiries add column if not exists unread boolean not null default true;
update public.guruconnect_enquiries set assigned_to=owner_id where assigned_to is null;
alter table public.guruconnect_enquiries add constraint guruconnect_enquiries_assignee_fk foreign key (organisation_id,assigned_to) references public.members(organisation_id,user_id);
alter table public.guruconnect_enquiries add constraint guruconnect_enquiries_stage_check check (stage in ('new','in_process','closed'));
create index if not exists guruconnect_enquiries_assigned_idx on public.guruconnect_enquiries(organisation_id,assigned_to,stage,created_at desc);
create index if not exists guruconnect_enquiries_unread_idx on public.guruconnect_enquiries(organisation_id,unread) where unread;

commit;
