begin;

-- Historical runs keep their model snapshots. New server-managed connections
-- select a documented model in backend configuration, without a browser key.
alter table public.engine_connections drop constraint if exists openai_fixed_translation_model;
alter table public.engine_connections add constraint openai_translation_model_identifier
  check (engine_id <> 'openai-translator' or coalesce((
    jsonb_typeof(config->'model')='string' and (config->>'model') ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$'
  ),false));

commit;
