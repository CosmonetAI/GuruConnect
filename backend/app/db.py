from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .settings import settings

def _sqlalchemy_url(value: str) -> str:
    """Accept Supabase's postgres:// URI as well as SQLAlchemy's psycopg URI."""
    value = value.strip()
    if value.startswith('postgres://'):
        return 'postgresql+psycopg://' + value[len('postgres://'):]
    if value.startswith('postgresql://'):
        return 'postgresql+psycopg://' + value[len('postgresql://'):]
    return value

_database_url = _sqlalchemy_url(settings.database_url) if settings.database_url else ''
_connect_args = {'connect_timeout': 10} if _database_url.startswith('postgres') else {}
if _connect_args:
    _connect_args['sslmode'] = 'require'

# Supabase's direct database hostname may resolve to IPv6. Render services can
# fail to reach that address, so production should use Supabase's IPv4-capable
# session/transaction pooler URI. pool_pre_ping also removes stale connections.
engine = create_engine(
    _database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args=_connect_args,
) if _database_url else None
SessionLocal = sessionmaker(bind=engine) if engine else None
def now(): return datetime.now(timezone.utc)
def require_db():
    if not SessionLocal: raise RuntimeError('DATABASE_URL is not configured. Apply the Supabase migration and configure backend/.env.')
    return SessionLocal()
def row_dict(row): return dict(row._mapping)
