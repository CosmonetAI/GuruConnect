from datetime import datetime, timezone
from contextvars import ContextVar
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
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
# Preserve certificate verification selected by DATABASE_URL. Never replace
# verify-full with require (which encrypts without verifying the server).
if _connect_args and 'sslmode' not in make_url(_database_url).query:
    _connect_args['sslmode'] = 'verify-full'

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


sales_workspace = ContextVar('sales_workspace', default=None)


def current_workspace():
    context = sales_workspace.get()
    if context is None:
        raise HTTPException(401, 'Authentication required')
    return context


def scope_parameters():
    context = current_workspace()
    return {'org': str(context.id), 'member': str(context.member_id),
            'actor': str(context.user.id), 'admin': context.admin}


DOCUMENT_TYPES = ('quote', 'invoice', 'sales-order', 'purchase-order')


def document_permission(document_type: str, action: str) -> bool:
    """Return the configured role permission, with safe role defaults."""
    context = current_workspace()
    if document_type not in DOCUMENT_TYPES or action not in {'create', 'edit', 'delete', 'accept', 'mark-paid'}:
        return False
    if context.role == 'receptionist':
        default = False
    else:
        default = True
    with require_db() as db:
        row = db.execute(text('''
            select can_create,can_edit,can_delete
            from role_document_permissions
            where organisation_id=cast(:org as uuid) and role=:role and document_type=:document_type
        '''), {'org': str(context.id), 'role': context.role, 'document_type': document_type}).mappings().first()
    return bool(row[action_map(action)]) if row else default


def require_document_permission(document_type: str, action: str) -> None:
    if not document_permission(document_type, action):
        raise HTTPException(403, {
            'code': 'document_permission_denied',
            'message': f'{action.title()} access is disabled for this role on {document_type.replace("-", " ")}.',
        })


def action_map(action: str) -> str:
    return {'create': 'can_create', 'edit': 'can_edit', 'delete': 'can_delete', 'accept': 'can_accept', 'mark-paid': 'can_mark_paid'}[action]
