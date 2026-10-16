from .db import one

async def next_daily_ticket(connection, organisation_id, ticket_day):
    await connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (f"daily-ticket:{organisation_id}:{ticket_day}",))
    row = await one(connection, "select coalesce(max(daily_sequence),0)+1 as sequence from guruconnect_enquiries where organisation_id=%s and ticket_day=%s", (organisation_id, ticket_day))
    return row["sequence"]

async def least_loaded_salesperson(connection, organisation_id):
    """Pick an active, available salesperson with the fewest assigned enquiries."""
    # Serialise selection with walk-in/enquiry creation so concurrent registrations
    # cannot all observe the same queue length and overload one salesperson.
    await connection.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"salesperson-assignment:{organisation_id}",),
    )
    return await one(connection, """
        select m.user_id, coalesce(p.display_name, '') as display_name,
               coalesce(u.email, '') as email
        from members m
        join auth.users u on u.id=m.user_id
        left join profiles p on p.id=m.user_id
        left join guruconnect_walkin_agents wa
          on wa.organisation_id=m.organisation_id and wa.user_id=m.user_id
        left join guruconnect_enquiries e on e.organisation_id=m.organisation_id and e.assigned_to=m.user_id
        where m.organisation_id=%s and m.role in ('owner','salesperson') and m.status='active'
          and coalesce(p.availability_status,'active')='active'
          and coalesce(wa.availability,'Available')='Available'
        group by m.user_id,p.display_name,u.email
        order by count(e.id) asc, random()
        limit 1
    """, (organisation_id,))
