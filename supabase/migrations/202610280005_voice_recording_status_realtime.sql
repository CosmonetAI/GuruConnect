-- Sessions listens for durable recorder state transitions separately from AI
-- extraction events so recording, translation, and AI indicators stay distinct.
do $$ begin
  if exists(select 1 from pg_publication where pubname='supabase_realtime') then
    alter publication supabase_realtime add table public.conversations;
  end if;
exception when duplicate_object then null;
end $$;
