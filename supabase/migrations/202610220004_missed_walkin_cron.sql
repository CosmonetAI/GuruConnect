-- Keep missed visitor follow-up in the database so it continues to run even
-- when the API service is sleeping or restarting. The schedule is 15:30 UTC,
-- which is 21:00 in India (the product's operating timezone).
begin;

create extension if not exists pg_cron with schema extensions;

create or replace function public.guruconnect_rollup_missed_visitors()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  cutoff timestamptz;
  added integer;
begin
  cutoff := (((now() at time zone 'Asia/Kolkata')::date + time '21:00') at time zone 'Asia/Kolkata');
  if now() < cutoff then
    cutoff := cutoff - interval '1 day';
  end if;

  delete from public.guruconnect_missed_visitors
   where expires_at <= now();

  insert into public.guruconnect_missed_visitors
    (organisation_id, member_id, visit_id, registered_by, assigned_agent_id,
     visit_number, customer_name, mobile_number, email, purpose, payload, check_in_at)
  select v.organisation_id,
         coalesce(v.assigned_agent_id, v.registered_by),
         v.id,
         v.registered_by,
         v.assigned_agent_id,
         v.visit_number,
         v.customer_name,
         v.mobile_number,
         coalesce(v.payload->>'email', ''),
         v.purpose,
         v.payload,
         v.check_in_at
    from public.guruconnect_walkin_visits v
   where v.check_in_at < cutoff
     and v.status <> 'Cancelled'
     and not exists (
       select 1
         from public.guruconnect_walkin_recordings r
        where r.organisation_id = v.organisation_id
          and r.visit_id = v.id
     )
     and not exists (
       select 1
         from public.guruconnect_missed_visitors m
        where m.organisation_id = v.organisation_id
          and m.visit_id = v.id
     )
   on conflict (organisation_id, visit_id) do nothing;

  get diagnostics added = row_count;
  return added;
end;
$$;

create or replace function public.guruconnect_clear_missed_visitor()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  delete from public.guruconnect_missed_visitors
   where organisation_id = new.organisation_id
     and visit_id = new.visit_id;
  return new;
end;
$$;

drop trigger if exists guruconnect_clear_missed_visitor on public.guruconnect_walkin_recordings;
create trigger guruconnect_clear_missed_visitor
after insert on public.guruconnect_walkin_recordings
for each row execute function public.guruconnect_clear_missed_visitor();

revoke all on function public.guruconnect_rollup_missed_visitors() from public, anon, authenticated;
revoke all on function public.guruconnect_clear_missed_visitor() from public, anon, authenticated;
grant execute on function public.guruconnect_rollup_missed_visitors() to service_role;

do $$
declare
  existing_job_id bigint;
begin
  select jobid into existing_job_id
    from cron.job
   where jobname = 'guruconnect-missed-visitors';
  if existing_job_id is not null then
    perform cron.unschedule(existing_job_id);
  end if;
end;
$$;

select cron.schedule(
  'guruconnect-missed-visitors',
  '30 15 * * *',
  $$select public.guruconnect_rollup_missed_visitors();$$
);

commit;
