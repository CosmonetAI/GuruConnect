-- Reconcile persisted profile status with actual browser presence.
-- A user is Active only while at least one presence session heartbeat is fresh.
-- Explicit Inactive, On Leave, and Be Right Back choices are preserved.
update public.profiles p
set availability_status = case
      when exists (
        select 1
        from public.profile_presence_sessions s
        where s.user_id = p.id
          and s.last_seen_at >= now() - interval '90 seconds'
      ) then 'active'
      else 'inactive'
    end,
    final_status = case
      when exists (
        select 1
        from public.profile_presence_sessions s
        where s.user_id = p.id
          and s.last_seen_at >= now() - interval '90 seconds'
      ) then 'active'
      else 'inactive'
    end,
    final_status_until = null,
    updated_at = now()
where coalesce(p.final_status, p.availability_status) = 'active';
