#!/usr/bin/env bash
# Isolated native PostgreSQL emulation; does not verify hosted Supabase APIs.
set -euo pipefail
test_dir="$(cd "$(dirname "$0")" && pwd)"
project_dir="$(cd "$test_dir/../.." && pwd)"
test_data="${GURU_NATIVE_TEST_DIR:-}"
test_port="${GURU_NATIVE_TEST_PORT:-55439}"
if [[ -n "$test_data" && -e "$test_data" ]]; then
  printf '%s\n' "Refusing to reuse existing directory: $test_data. Choose GURU_NATIVE_TEST_DIR for a fresh disposable cluster."
  exit 2
fi
if [[ -z "$test_data" ]]; then test_data="$(mktemp -d "${TMPDIR:-/tmp}/guru-db-test.XXXXXX")";fi
mkdir -p "$test_data"
initdb -D "$test_data/data" -A trust --no-locale -E UTF8 > "$test_data/initdb.log"
pg_ctl -D "$test_data/data" -l "$test_data/postgres.log" -o "-p $test_port -h 127.0.0.1 -k $test_data -c wal_level=logical" -w start
trap 'pg_ctl -D "$test_data/data" -m fast -w stop' EXIT
db_args=(-h 127.0.0.1 -p "$test_port" -d postgres -v ON_ERROR_STOP=1)
psql "${db_args[@]}" -f "$test_dir/native_fixture.sql" > "$test_data/fixture.log"
for migration in "$project_dir"/supabase/migrations/*.sql; do
  # pgmq extension is unavailable in stock Postgres; fixture implements signatures.
  sed '/^create extension if not exists pgmq;$/d' "$migration" > "$test_data/current_migration.sql"
  psql "${db_args[@]}" -f "$test_data/current_migration.sql" >> "$test_data/migrations.log"
done
if ! psql "${db_args[@]}" -qAt -f "$test_dir/security_invariants.sql" > "$test_data/invariants.log" 2>&1; then
  tail -n 30 "$test_data/invariants.log"
  exit 1
fi
printf 'SQL assertions passed: %s\n' "$(rg -c 'NOTICE:  PASS:' "$test_data/invariants.log")"
if ! psql "${db_args[@]}" -qAt -f "$test_dir/openai_translation_invariants.sql" > "$test_data/openai-invariants.log" 2>&1; then
  tail -n 30 "$test_data/openai-invariants.log"
  exit 1
fi
printf 'OpenAI SQL assertions passed: %s\n' "$(rg -c 'NOTICE:  PASS:' "$test_data/openai-invariants.log")"
if ! psql "${db_args[@]}" -qAt -f "$test_dir/walkin_invariants.sql" > "$test_data/walkin-invariants.log" 2>&1; then
  tail -n 30 "$test_data/walkin-invariants.log"
  exit 1
fi
printf 'Walk-in SQL assertions passed: %s\n' "$(rg -c 'NOTICE:  PASS:' "$test_data/walkin-invariants.log")"
python3 "$test_dir/concurrent_native.py" --port "$test_port" | tee "$test_data/concurrent.log"
printf '%s\n' "Native Postgres emulation passed. Hosted Supabase/storage/pgmq verification is still required."
printf 'Evidence directory: %s\n' "$test_data"
