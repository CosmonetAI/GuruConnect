"""Load actual migrations against native no-network extension emulations only."""
from pathlib import Path
import re


def native_sql(source: str) -> str:
    # Retain pgcrypto/btree_gist and every application statement. These three
    # Supabase-only extensions are represented by native_fixture.sql signatures.
    return re.sub(r'^\s*create\s+extension\s+if\s+not\s+exists\s+(?:pgmq|pg_cron|pg_net)\b[^;]*;',
                  '', source, flags=re.IGNORECASE | re.MULTILINE)


def apply_native_migrations(connection, migrations):
    for migration in migrations:
        try:
            connection.execute(native_sql(migration.read_text()), prepare=False)
        except Exception as error:
            # Legacy SQL includes credential literals. Do not print statements,
            # server error context, or values in a failed fixture diagnostic.
            raise RuntimeError(f'Native migration {migration.name} failed (SQLSTATE {getattr(error, "sqlstate", "unknown")})') from None


if __name__ == '__main__':
    import sys
    print(native_sql(Path(sys.argv[1]).read_text()))
