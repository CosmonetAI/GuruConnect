"""Run transactional checks on an explicitly designated hosted DEVELOPMENT database.

TEST_DATABASE_URL must be a private direct/pooler PostgreSQL URL with TLS. Never
print it. The caller must confirm GURU_TEST_DATABASE_CONFIRM=development; no
application production DATABASE_URL fallback exists. Migrations are applied by
the supported deployment flow beforehand. Test fixture writes are rolled back.
"""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def main() -> int:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url or os.environ.get("GURU_TEST_DATABASE_CONFIRM") != "development":
        print("SKIPPED: set TEST_DATABASE_URL and GURU_TEST_DATABASE_CONFIRM=development for designated hosted development checks.")
        return 0
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname in {None, "localhost", "127.0.0.1", "::1"}:
        print("Refusing non-remote database URL.", file=sys.stderr)
        return 2
    # libpq uses PG* variables, so credentials never appear in process arguments.
    env = os.environ.copy()
    env["PGDATABASE"] = url
    env["PGSSLMODE"] = "verify-full"
    env["PGCONNECT_TIMEOUT"] = "15"
    result = subprocess.run(["psql", "-v", "ON_ERROR_STOP=1", "-f", str(Path(__file__).with_name("security_invariants.sql"))], env=env, text=True, capture_output=True)
    # Do not echo libpq error strings that may include private URLs/hostnames.
    if result.returncode:
        print("Hosted development database checks failed; inspect the SQL statement in the private test logs.", file=sys.stderr)
        Path(os.environ.get("GURU_DB_TEST_LOG", "/private/tmp/guru-hosted-db-test.log")).write_text(result.stdout + result.stderr)
        return result.returncode
    print(result.stdout)
    print("Hosted PostgreSQL RLS/invariant checks passed. Real Storage HTTP/Auth/Realtime checks remain a separate integration suite.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
