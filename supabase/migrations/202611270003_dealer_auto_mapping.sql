begin;

-- Create a trigger function to automatically map a dealer to the owner account
-- when a dealer member is created
create or replace function public.auto_map_dealer_to_owner()
returns trigger as $$
declare
  owner_member_id uuid;
begin
  -- Only process if this is a dealer role
  if new.role = 'dealer' then
    -- Get the owner member for this organisation
    select user_id into owner_member_id
    from public.members
    where organisation_id = new.organisation_id
      and role = 'owner'
      and status = 'active'
    limit 1;
    
    -- If owner exists, create the mapping
    if owner_member_id is not null then
      -- Insert the dealer mapping with owner account
      insert into public.dealer_account_mappings
        (organisation_id, dealer_id, account_member_id, books_contact_id, discount_percent, gst_group_id, updated_at)
      values
        (new.organisation_id, new.user_id, owner_member_id, '', 0, null, now())
      on conflict (organisation_id, dealer_id) do nothing;
    end if;
  end if;
  
  return new;
end;
$$ language plpgsql;

-- Drop existing trigger if it exists
drop trigger if exists trigger_auto_map_dealer_on_create on public.members;

-- Create the trigger to fire after a member is inserted
create trigger trigger_auto_map_dealer_on_create
after insert on public.members
for each row
execute function public.auto_map_dealer_to_owner();

commit;

