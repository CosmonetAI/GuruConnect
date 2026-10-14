"""Apply versioned migrations to an explicitly designated remote project.

Set MIGRATION_DATABASE_URL and GURU_MIGRATION_CONFIRM=development|production.
There is no implicit application DATABASE_URL fallback. Credentials never print.
"""
import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse
import psycopg


def main():
    target=os.environ.get('MIGRATION_DATABASE_URL','')
    if not target or os.environ.get('GURU_MIGRATION_CONFIRM') not in {'development','production'}:
        print('SKIPPED: explicitly select a remote project and migration environment.');return
    parsed=urlparse(target)
    if parsed.scheme not in {'postgres','postgresql'} or parsed.hostname in {None,'localhost','127.0.0.1','::1'}:raise ValueError('Remote designated project required')
    root=Path(__file__).resolve().parents[2]
    with psycopg.connect(target,sslmode='verify-full',connect_timeout=15,autocommit=True) as connection:
        connection.execute("select pg_advisory_lock(hashtextextended('guru-schema-migrations',0))")
        connection.execute('create schema if not exists recorder_private')
        connection.execute('revoke all on schema recorder_private from public,anon,authenticated')
        connection.execute('create table if not exists recorder_private.schema_migrations(version text primary key,sha256 text not null,applied_at timestamptz not null default now())')
        for migration in sorted((root/'supabase/migrations').glob('*.sql')):
            sql=migration.read_text();digest=hashlib.sha256(sql.encode()).hexdigest()
            prior=connection.execute('select sha256 from recorder_private.schema_migrations where version=%s',(migration.name,)).fetchone()
            if prior:
                if prior[0]!=digest:raise RuntimeError('Applied migration checksum changed; create a new forward migration')
                continue
            connection.execute(sql,prepare=False)
            connection.execute('insert into recorder_private.schema_migrations(version,sha256) values(%s,%s)',(migration.name,digest))
            print('Applied '+migration.name)
        connection.execute("select pg_advisory_unlock(hashtextextended('guru-schema-migrations',0))")


if __name__=='__main__':main()
