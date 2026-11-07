from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .db import one


def current_ticket_day(timezone_name: str, now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(timezone_name)).date()


def format_ticket_number(ticket_day: date, daily_sequence: int) -> str:
    return f"WI-{ticket_day:%d%m%Y}-{daily_sequence:06d}"


def format_enquiry_number(enquiry_day: date, daily_sequence: int) -> str:
    return f"ENQ-{enquiry_day:%d%m%Y}-{daily_sequence:06d}"


async def next_daily_ticket(connection, organisation_id, ticket_day):
    await connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (f"daily-ticket:{organisation_id}:{ticket_day}",))
    row = await one(connection, "select coalesce(max(daily_sequence),0)+1 as sequence from guruconnect_enquiries where organisation_id=%s and ticket_day=%s", (organisation_id, ticket_day))
    return row["sequence"]

async def least_loaded_salesperson(connection, organisation_id, ticket_day):
    """Pick one eligible salesperson with the fewest enquiries for that day."""
    # Serialise selection with walk-in/enquiry creation so concurrent registrations
    # cannot all observe the same queue length and overload one salesperson.
    await connection.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"salesperson-assignment:{organisation_id}:{ticket_day}",),
    )
    await connection.execute("""
        update profiles p
        set availability_status='active', leave_end_date=null, final_status='active', final_status_until=null, updated_at=clock_timestamp()
        where coalesce(p.final_status, p.availability_status)='on_leave'
          and coalesce(p.final_status_until, p.leave_end_date) < current_date
          and exists (
              select 1 from members m
              where m.organisation_id=%s and m.user_id=p.id and m.status='active'
          )
    """, (organisation_id,))
    return await one(connection, """
        select m.user_id, coalesce(p.display_name, '') as display_name,
               coalesce(u.email, '') as email
        from members m
        join auth.users u on u.id=m.user_id
        left join profiles p on p.id=m.user_id
        left join guruconnect_walkin_agents wa
          on wa.organisation_id=m.organisation_id and wa.user_id=m.user_id
        left join guruconnect_enquiries e
          on e.organisation_id=m.organisation_id
         and e.assigned_to=m.user_id
         and e.ticket_day=%s
         and e.stage in ('new','in_process')
        where m.organisation_id=%s and m.role in ('owner','salesperson') and m.status='active'
          and (
              m.role='salesperson'
              or not exists (
                  select 1
                  from members active_sales
                  join profiles active_profile on active_profile.id=active_sales.user_id
                  where active_sales.organisation_id=m.organisation_id
                    and active_sales.role='salesperson'
                    and active_sales.status='active'
                    and coalesce(active_profile.final_status, active_profile.availability_status,'active')='active'
                    and (active_profile.last_seen_at >= now()-interval '90 seconds'
                         or exists (select 1 from profile_presence_sessions active_presence where active_presence.user_id=active_sales.user_id and active_presence.last_seen_at >= now()-interval '90 seconds'))
                    and coalesce((select active_availability.availability from guruconnect_walkin_agents active_availability where active_availability.organisation_id=m.organisation_id and active_availability.user_id=active_sales.user_id), 'Available')='Available'
              )
          )
          and (
              coalesce(p.final_status, p.availability_status,'active')='active'
              or (coalesce(p.final_status, p.availability_status)='on_leave' and coalesce(p.final_status_until, p.leave_end_date) < current_date)
          )
          and (p.last_seen_at >= now()-interval '90 seconds'
               or exists (select 1 from profile_presence_sessions ps where ps.user_id=p.id and ps.last_seen_at >= now()-interval '90 seconds'))
          and coalesce(wa.availability,'Available')='Available'
        group by m.user_id,p.display_name,u.email
        order by count(e.id) asc, random()
        limit 1
    """, (ticket_day, organisation_id))
