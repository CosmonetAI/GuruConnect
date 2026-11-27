begin;

-- Function to automatically create dealer account mapping with owner as default account
create or replace function public.create_dealer_account_mapping()
returns trigger as $$
declare
  owner_member_id uuid;
begin
  -- Only create mapping for dealer members
  if new.role = 'dealer' and new.status = 'active' then
    -- Find the owner in this organisation
    select user_id into owner_member_id
    from public.members
    where organisation_id = new.organisation_id
      and role = 'owner'
      and status = 'active'
    limit 1;
    
    if owner_member_id is not null then
      -- Create mapping with owner as the default account
      -- GST group will be selected through the UI
      insert into public.dealer_account_mappings
        (organisation_id, dealer_id, account_member_id, books_contact_id, discount_percent, gst_group_id)
      values
        (new.organisation_id, new.user_id, owner_member_id, '', 0, null)
      on conflict (organisation_id, dealer_id) do nothing;
    end if;
  end if;
  
  return new;
end;
$$ language plpgsql;

-- Create trigger to automatically set up dealer mapping
drop trigger if exists dealer_account_mapping_trigger on public.members;
create trigger dealer_account_mapping_trigger
  after insert on public.members
  for each row
  execute function public.create_dealer_account_mapping();

commit;
