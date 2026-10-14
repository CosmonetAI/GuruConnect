-- Tenant-editable prompts for intent detection. The backend caches these briefly
-- and falls back to its built-in prompt if the table is unavailable.
create table if not exists public.sales_agent_prompts (
  id uuid primary key default gen_random_uuid(),
  tenant_key text not null default 'guru-group',
  prompt_key text not null,
  prompt text not null,
  enabled boolean not null default true,
  updated_at timestamptz not null default now(),
  unique (tenant_key, prompt_key)
);
alter table public.sales_agent_prompts enable row level security;
insert into public.sales_agent_prompts(tenant_key,prompt_key,prompt)
values ('guru-group','intent_detection','Classify the customer message using the allowed intents in the application prompt. Preserve the active topic when the message is a short continuation. Treat confirmations as continuation only; never invent missing entities.')
on conflict (tenant_key,prompt_key) do nothing;
