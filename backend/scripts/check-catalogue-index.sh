#!/usr/bin/env bash
# Disposable PostgreSQL tests. Uses a private Unix socket and makes no remote calls.
set -euo pipefail
catalogue_project_dir="$(cd "$(dirname "$0")/../.." && pwd)"
catalogue_test_dir="$(mktemp -d /private/tmp/guru-catalogue-index.XXXXXX)"
catalogue_test_port=55491
initdb -D "$catalogue_test_dir/data" -A trust --no-locale -E UTF8 > "$catalogue_test_dir/initdb.log"
pg_ctl -D "$catalogue_test_dir/data" -l "$catalogue_test_dir/postgres.log" -o "-p $catalogue_test_port -h '' -k $catalogue_test_dir" -w start
trap 'pg_ctl -D "$catalogue_test_dir/data" -m fast -w stop' EXIT
psql -h "$catalogue_test_dir" -p "$catalogue_test_port" -d postgres -v ON_ERROR_STOP=1 <<'SQL'
create role anon;
create role authenticated;
create role service_role;
create table public.members(organisation_id uuid not null,user_id uuid not null,status text not null default 'active',
  role text not null default 'salesperson',primary key(organisation_id,user_id));
SQL
psql -h "$catalogue_test_dir" -p "$catalogue_test_port" -d postgres -v ON_ERROR_STOP=1 -f "$catalogue_project_dir/supabase/migrations/202610290002_catalogue_index.sql"
cd "$catalogue_project_dir"
CATALOGUE_INDEX_TEST_DATABASE_URL="postgresql+psycopg:///postgres?host=$catalogue_test_dir&port=$catalogue_test_port" \
  PYTHONPATH=backend PYDANTIC_DISABLE_PLUGINS=__all__ PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  backend/.venv/bin/python -c 'import pytest,pytest_asyncio.plugin;raise SystemExit(pytest.main(["-q","backend/tests/test_catalogue_index.py","backend/tests/test_catalogue_index_native.py"],plugins=[pytest_asyncio.plugin]))'
printf 'Catalogue native evidence: %s\n' "$catalogue_test_dir"
