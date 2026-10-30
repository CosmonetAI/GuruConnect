from contextlib import asynccontextmanager
from copy import deepcopy
import asyncio, json
import hashlib
import hmac
import secrets
import re
import logging
from uuid import UUID
from fastapi import BackgroundTasks, FastAPI, HTTPException, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text
from .db import require_db, row_dict, now, sales_workspace, scope_parameters
from .zoho import zoho, refresh_loop, refresh_all, set_organisation_id, set_member_id, get_organisation_id, get_member_id
from .chat import get_or_create_customer, get_or_create_customer_sync, run_graph
from .voice_ai import VoiceConversationOrchestrator
from .voice_ai.live import analysis_turns, batch_fingerprint, catalogue_queries, intent_keywords, pending_turns, search_catalogue, turn_fingerprint
from .voice_ai.workspace import (analysis_outcome, catalogue_due, failed_analysis_data, retain_catalogue_recommendations,
                                requirement_signature, retry_failure, row_analysis_error, workspace_fields)

# At most two live analyses hold database/provider resources in this process.
# PostgreSQL advisory locks also serialize a conversation across API instances
# and the optional database webhook, so repeated polls never duplicate paid work.
_voice_live_tasks: dict[tuple[str, str, str], asyncio.Task] = {}

async def stop_voice_live_tasks():
    from .catalogue_index import close_catalogue_tasks
    from .catalogue_embeddings import close_embedding_clients
    tasks = list(_voice_live_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _voice_live_tasks.clear()
    await close_catalogue_tasks()
    await close_embedding_clients()

@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(refresh_loop())
    try:
        yield
    finally:
        task.cancel()
        await stop_voice_live_tasks()
        await asyncio.gather(task, return_exceptions=True)
app=FastAPI(title='Guru Group AI Sales API',version='0.1.0',lifespan=lifespan,
            docs_url=None, redoc_url=None, openapi_url=None)
log = logging.getLogger('sales.api')
from .settings import settings
app.add_middleware(CORSMiddleware,allow_origins=[origin.strip() for origin in settings.cors_origins.split(',') if origin.strip()],allow_credentials=True,allow_methods=['*'],allow_headers=['Authorization','Content-Type','X-User-ID','X-Workspace-ID','Idempotency-Key'])

@app.middleware('http')
async def zoho_workspace_scope(request: Request, call_next):
    # Bind the parent per ASGI request, so separate create_app() instances never
    # overwrite one another's verifier/settings on this shared child app.
    if request.url.path in {'/docs', '/redoc', '/openapi.json'}:
        return JSONResponse(status_code=404, content={'detail': 'Not found'})
    if request.method == 'OPTIONS' or request.url.path in {'/', '/api/health', '/api/internal/zoho/refresh', '/api/internal/voice/extraction-webhook'}:
        return await call_next(request)
    parent = request.scope.get('recorder_app')
    if parent is None:
        return JSONResponse(status_code=503, content={'detail': {'code': 'authentication_not_configured'}})
    token = None
    try:
        from recorder.security import current_user, workspace
        authorization = request.headers.get('authorization', '').split(' ', 1)
        if len(authorization) != 2 or authorization[0].lower() != 'bearer' or not authorization[1]:
            raise HTTPException(401, 'Authentication required')
        parent_request = Request({**request.scope, 'app': parent})
        user = await current_user(parent_request, HTTPAuthorizationCredentials(scheme='Bearer', credentials=authorization[1]))
        try:
            org = UUID(request.headers['x-workspace-id']) if request.headers.get('x-workspace-id') else None
            member = UUID(request.headers['x-workspace-member-id']) if request.headers.get('x-workspace-member-id') else None
        except ValueError:
            raise HTTPException(400, 'Invalid workspace identity') from None
        context = await workspace(parent_request, user, org, member)
        # Voice history/notes do not call Zoho. Every other sales route may
        # invoke the sales agent or integration and requires a sales role.
        if context.role == 'receptionist' and not request.url.path.startswith('/api/voice-sessions'):
            raise HTTPException(403, 'Sales access required')
        token = sales_workspace.set(context)
        set_organisation_id(str(context.id))
        set_member_id(str(context.member_id))
        return await call_next(request)
    except HTTPException as error:
        return JSONResponse(status_code=error.status_code, content={'detail': error.detail}, headers=error.headers)
    except PermissionError:
        return JSONResponse(status_code=403, content={'detail': 'Workspace access denied'})
    except Exception as error:
        log.error('sales_request_failed exception_type=%s', type(error).__name__)
        return JSONResponse(status_code=503, content={'detail': {'code': 'service_unavailable'}})
    finally:
        if token is not None:
            sales_workspace.reset(token)
        set_organisation_id(None)
        set_member_id(None)


def bind_parent(parent_app):
    """Mount this child with the recorder's verified identity on every request."""
    async def bound(scope, receive, send):
        await app({**scope, 'recorder_app': parent_app}, receive, send)
    return bound


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    return JSONResponse({'detail': {'code': 'validation_failed'}}, status_code=422)


@app.exception_handler(Exception)
async def unexpected_error(request, error):
    log.error('sales_error exception_type=%s', type(error).__name__)
    return JSONResponse({'detail': {'code': 'internal_error'}}, status_code=500)

@app.get('/', response_class=HTMLResponse, include_in_schema=False)
def landing_page():
    return '''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Guru Group AI Sales API</title>
  <style>
    :root { color-scheme: light; --navy: #0d2d55; --blue: #1464a5; --gold: #f5aa19; --ink: #12345b; --muted: #6d83a2; --line: #dbe6f1; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: linear-gradient(135deg, #eef5fb 0%, #f9fbfd 52%, #e9f1f9 100%); }
    .shell { width: min(1120px, calc(100% - 40px)); margin: 0 auto; padding: 32px 0 40px; }
    .topbar { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
    .brand { display: flex; align-items: center; gap: 13px; }
    .mark { width: 46px; height: 46px; display: grid; place-items: center; border-radius: 14px; color: #122e50; background: var(--gold); font-weight: 900; letter-spacing: -1px; box-shadow: 0 8px 20px rgba(245, 170, 25, .24); }
    .brand strong { display: block; font-size: 17px; letter-spacing: -.2px; }
    .brand span { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; }
    .status { display: inline-flex; align-items: center; gap: 8px; padding: 10px 14px; border: 1px solid #cbe9dc; border-radius: 999px; color: #13734b; background: #effbf5; font-size: 13px; font-weight: 700; }
    .status i { width: 8px; height: 8px; border-radius: 50%; background: #21ad70; }
    .hero { position: relative; overflow: hidden; margin-top: 58px; padding: 56px; border: 1px solid rgba(214, 227, 240, .95); border-radius: 28px; background: rgba(255,255,255,.88); box-shadow: 0 24px 60px rgba(20, 58, 96, .10); }
    .hero:after { content: ''; position: absolute; width: 260px; height: 260px; right: -90px; top: -100px; border-radius: 50%; background: rgba(245,170,25,.12); }
    .eyebrow { margin: 0 0 14px; color: var(--blue); font-size: 12px; font-weight: 800; letter-spacing: 1.8px; }
    h1 { max-width: 700px; margin: 0; color: var(--navy); font-size: clamp(34px, 5vw, 60px); line-height: 1.05; letter-spacing: -2.5px; }
    .intro { max-width: 680px; margin: 20px 0 0; color: var(--muted); font-size: 18px; line-height: 1.65; }
    .actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 30px; }
    .button { display: inline-flex; align-items: center; justify-content: center; min-height: 46px; padding: 0 19px; border-radius: 11px; text-decoration: none; font-size: 14px; font-weight: 800; transition: transform .18s ease, box-shadow .18s ease; }
    .button:hover { transform: translateY(-2px); }
    .button.primary { color: white; background: var(--blue); box-shadow: 0 9px 20px rgba(20,100,165,.22); }
    .button.secondary { color: var(--blue); border: 1px solid #bfd5e9; background: #f6faff; }
    .cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-top: 18px; }
    .card { padding: 22px; border: 1px solid var(--line); border-radius: 16px; background: #fff; }
    .card h2 { margin: 0 0 8px; color: var(--navy); font-size: 15px; }
    .card p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.6; }
    code { color: #245d91; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }
    footer { padding: 22px 3px 0; color: var(--muted); font-size: 12px; }
    @media (max-width: 720px) { .shell { width: min(100% - 24px, 1120px); padding-top: 20px; } .status { display: none; } .hero { margin-top: 32px; padding: 30px 24px; border-radius: 22px; } .cards { grid-template-columns: 1fr; } h1 { letter-spacing: -1.5px; } }
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand"><div class="mark">GG</div><div><strong>Guru Group</strong><span>AI Sales Intelligence</span></div></div>
      <div class="status"><i></i> API online</div>
    </header>
    <main class="hero">
      <p class="eyebrow">GURU GROUP / DEVELOPER PLATFORM</p>
      <h1>AI-powered sales operations, connected to Zoho.</h1>
      <p class="intro">This service powers customer conversations, product search, contacts, quotes, orders, purchase orders, invoices, and Zoho integrations for the Guru Group sales desk.</p>
      <div class="actions"><a class="button primary" href="/api/health">View health status</a><a class="button secondary" href="/docs">Open API docs</a><a class="button secondary" href="/redoc">Open ReDoc</a></div>
      <section class="cards" aria-label="Service information">
        <article class="card"><h2>Health endpoint</h2><p>Confirm that the backend is responding normally at <code>/api/health</code>.</p></article>
        <article class="card"><h2>Interactive API</h2><p>Explore and test available endpoints with Swagger UI at <code>/docs</code>.</p></article>
        <article class="card"><h2>Built for sales teams</h2><p>Live Zoho Books and Bigin data stays connected to the sales workflow.</p></article>
      </section>
    </main>
    <footer>Guru Group AI Sales API · v0.1.0</footer>
  </div>
</body>
</html>'''
class CustomerIn(BaseModel): name:str=Field(min_length=2); phone:str|None=None; email:str|None=None; phone_extension:str='+91'
class ChatIn(BaseModel): message:str=Field(min_length=1); customer:CustomerIn|None=None; session_id:UUID|None=None
class CodeIn(BaseModel): code:str=Field(min_length=3)
class OrganizationIn(BaseModel): organization_id:str=Field(min_length=1)
class ZohoConfigIn(BaseModel): client_id:str=Field(min_length=5); client_secret:str=''
class ContactIn(BaseModel):
    first_name:str=Field(min_length=1)
    mobile:str=Field(min_length=10, max_length=10)
    last_name:str=''
    title:str=''
    email:str=''
    contact_name:str=''
    company_name:str=''
    contact_type:str='customer'
    customer_sub_type:str='business'
    contact_number:str=''
    ignore_auto_number_generation:bool=False
    salutation:str=''
    contact_salutation:str=''
    website:str=''
    language_code:str='en'
    phone:str=''
    designation:str=''
    department:str=''
    payment_terms:int|None=None
    payment_terms_label:str=''
    credit_limit:float|None=None
    is_portal_enabled:bool=False
    payment_reminder_enabled:bool=True
    billing_address:dict|None=None
    shipping_address:dict|None=None
    contact_persons:list[dict]=Field(default_factory=list)
    custom_fields:list[dict]=Field(default_factory=list)
    tags:list[dict]=Field(default_factory=list)
    pan_no:str=''
    gst_no:str=''
    notes:str='Contact created through Cosmonet AI'

class VoiceSessionIn(BaseModel):
    name: str = Field(min_length=2)
    phone: str = Field(min_length=7)
    email: str = Field(default='', max_length=320)
class VoiceSessionLinkIn(BaseModel):
    conversation_id: UUID | None = None
class VoiceSessionNoteIn(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
class VoiceSessionContextIn(BaseModel):
    product_requirements: list[dict] = Field(default_factory=list, max_length=25)
    cart: list[dict] = Field(default_factory=list, max_length=50)
def _db_http_error(error: Exception):
    if isinstance(error, HTTPException):
        raise error
    if isinstance(error, PermissionError):
        raise HTTPException(403, 'Workspace access denied') from None
    raise HTTPException(503, 'Service temporarily unavailable') from None

RECENT_ZOHO_LIMIT = 40

def _recent_cache_key(dataset: str, row: dict, index: int = 0):
    """Build a stable key while keeping Bigin and Books records separate."""
    row = row if isinstance(row, dict) else {}
    source = str(row.get('__source') or '').strip()
    if dataset == 'products':
        value = row.get('item_id') or row.get('id') or row.get('sku') or row.get('name')
    elif dataset == 'contacts':
        value = row.get('id') or row.get('contact_id') or row.get('Full_Name') or row.get('contact_name')
        source = source or str(row.get('source') or 'zoho')
    elif dataset == 'quotes':
        value = row.get('estimate_id') or row.get('estimate_number') or row.get('reference_number')
    elif dataset == 'orders':
        value = row.get('salesorder_id') or row.get('salesorder_number') or row.get('reference_number')
    elif dataset == 'purchase_orders':
        value = row.get('purchaseorder_id') or row.get('purchaseorder_number') or row.get('reference_number')
    elif dataset == 'invoices':
        value = row.get('invoice_id') or row.get('invoice_number') or row.get('reference_number')
    elif dataset == 'pipelines':
        pipeline = row.get('Pipeline') if isinstance(row.get('Pipeline'), dict) else {}
        layout = row.get('$layout_id') if isinstance(row.get('$layout_id'), dict) else {}
        value = f"{pipeline.get('id') or layout.get('id') or ''}:{row.get('id') or row.get('record_id') or ''}"
    else:
        value = row.get('id') or row.get('record_id')
    if value not in (None, ''):
        return f'{source}:{value}' if source else str(value)
    raw = json.dumps(row, sort_keys=True, default=str)
    return f'fallback:{index}:{hashlib.sha256(raw.encode()).hexdigest()}'

def _recent_cache_rows(dataset: str, limit: int = RECENT_ZOHO_LIMIT):
    org = get_organisation_id(); member = get_member_id()
    if not org or not member:
        return []
    zoho_org = str((zoho.status() or {}).get('organization_id') or '')
    try:
        with require_db() as db:
            rows = db.execute(text("""select data from public.zoho_recent_records
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)
                and zoho_organization_id=:zoho_org and dataset=:dataset
                order by last_seen_at desc limit :limit"""),
                {'org': org, 'member': member, 'zoho_org': zoho_org, 'dataset': dataset, 'limit': limit}).all()
        return [row[0] if isinstance(row[0], dict) else json.loads(row[0]) for row in rows]
    except Exception:
        # The live Zoho path must continue to work if this optional cache has
        # not been migrated yet or Supabase is temporarily unavailable.
        return []

def _cache_recent_rows(dataset: str, records):
    org = get_organisation_id(); member = get_member_id()
    rows = [row for row in (records or []) if isinstance(row, dict)]
    if not org or not member or not rows:
        return
    zoho_org = str((zoho.status() or {}).get('organization_id') or '')
    try:
        with require_db() as db:
            # Reverse insertion order so the first row returned by Zoho gets
            # the newest timestamp and appears first in the next default view.
            for index, row in enumerate(reversed(rows)):
                db.execute(text("""insert into public.zoho_recent_records
                  (organisation_id, member_id, zoho_organization_id, dataset, record_key, data, last_seen_at, updated_at)
                  values(cast(:org as uuid), cast(:member as uuid), :zoho_org, :dataset, :record_key,
                    cast(:data as jsonb), :seen_at, :seen_at)
                  on conflict (organisation_id, member_id, zoho_organization_id, dataset, record_key)
                  do update set data=excluded.data, last_seen_at=excluded.last_seen_at,
                    updated_at=excluded.updated_at"""), {
                    'org': org, 'member': member, 'zoho_org': zoho_org, 'dataset': dataset,
                    'record_key': _recent_cache_key(dataset, row, index),
                    'data': json.dumps(row, default=str), 'seen_at': now(),
                })
            db.execute(text("""delete from public.zoho_recent_records
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)
                and zoho_organization_id=:zoho_org and dataset=:dataset and record_key not in (
                  select record_key from public.zoho_recent_records
                  where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)
                    and zoho_organization_id=:zoho_org and dataset=:dataset
                    order by last_seen_at desc limit :limit)"""),
                {'org': org, 'member': member, 'zoho_org': zoho_org, 'dataset': dataset, 'limit': RECENT_ZOHO_LIMIT})
            db.commit()
    except Exception:
        # Cache writes are deliberately best-effort and never block a Zoho
        # response or a document form.
        return

def _recent_cache_remove(dataset: str, record_key: str):
    org = get_organisation_id(); member = get_member_id()
    if not org or not member or not record_key:
        return
    zoho_org = str((zoho.status() or {}).get('organization_id') or '')
    try:
        with require_db() as db:
            db.execute(text("""delete from public.zoho_recent_records
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)
                and zoho_organization_id=:zoho_org and dataset=:dataset
                and (record_key=:record_key or record_key like :record_suffix)"""),
                {'org': org, 'member': member, 'zoho_org': zoho_org, 'dataset': dataset,
                 'record_key': str(record_key), 'record_suffix': f'%:{record_key}'})
            db.commit()
    except Exception:
        return
@app.get('/api/health')
def health(): return {'ok':True,'service':'guru-group-ai-sales'}
@app.get('/api/zoho/status')
def zoho_status():
    try: return zoho.status()
    except Exception as e: return _db_http_error(e)
@app.get('/api/zoho/configuration')
def zoho_configuration():
    try: return zoho.configuration()
    except Exception as e: return _db_http_error(e)
@app.post('/api/zoho/configuration')
async def zoho_configuration_save(payload:ZohoConfigIn): return await zoho.save_configuration(payload.client_id,payload.client_secret)
@app.post('/api/zoho/connect')
async def zoho_connect(payload:CodeIn):
    try: return await zoho.exchange_code(payload.code)
    except Exception: raise HTTPException(400,'Zoho authorization failed')
@app.post('/api/zoho/organizations')
async def zoho_organizations():
    try: return await zoho.smoke_test()
    except Exception: raise HTTPException(502,'Unable to load Zoho Books organizations')
@app.post('/api/zoho/organization')
async def zoho_organization(payload:OrganizationIn):
    try: return await zoho.select_organization(payload.organization_id)
    except Exception: raise HTTPException(400,'Unable to select Zoho Books organization')
@app.post('/api/zoho/smoke-test')
async def zoho_smoke():
    try: return await zoho.smoke_test()
    except Exception: raise HTTPException(502,'Integration service unavailable')

def _session(db, session_id, *, lock=False):
    params = {**scope_parameters(), 'session': str(session_id)}
    row = db.execute(text("""select s.*,c.name,c.phone,c.email from chat_sessions s
      join customers c on c.id=s.customer_id and c.organisation_id=s.organisation_id and c.owner_id=s.member_id
      where s.id=cast(:session as uuid) and s.organisation_id=cast(:org as uuid)
        and s.member_id=cast(:member as uuid) and c.deleted_at is null""" + (' for update of s' if lock else '')), params).first()
    if not row:
        raise HTTPException(404, 'Session not found')
    return row_dict(row)


def _session_messages(db, session_id, *, history=False):
    columns = 'm.role,m.content' if history else 'm.id,m.role,m.content,m.intent,m.metadata,m.created_at'
    suffix = ' order by m.created_at desc limit 10' if history else ' order by m.created_at'
    return [row_dict(row) for row in db.execute(text(f"""select {columns} from chat_messages m
      join chat_sessions s on s.id=m.session_id
      where m.session_id=cast(:session as uuid) and s.organisation_id=cast(:org as uuid)
        and s.member_id=cast(:member as uuid)""" + suffix), {**scope_parameters(), 'session': str(session_id)}).all()]


def _recording_status_label(row):
    capture = row.get('capture_state')
    if capture in {'recording', 'paused'}:
        return 'Recording'
    if row.get('transcription_state') in {'failed', 'blocked', 'uncertain'}:
        return 'Transcription failed'
    if row.get('english_state') in {'failed', 'blocked', 'uncertain'}:
        return 'Translation failed'
    if row.get('upload_state') not in {None, 'saved'}:
        return 'Uploading audio'
    if row.get('transcription_state') in {'pending', 'queued', 'running', 'processing'}:
        return 'Transcribing'
    if row.get('english_state') in {'pending', 'queued', 'running', 'processing'}:
        return 'Translating'
    if row.get('final_state') in {'pending', 'queued', 'running', 'processing'}:
        return 'Processing'
    if row.get('final_state') == 'complete' and row.get('english_state') == 'complete':
        return 'Processing complete'
    return 'Ready to record'


def _voice_history(db, session):
    params = {**scope_parameters(), 'session': str(session['id']), 'customer': str(session['customer_id'])}
    chunks = [row_dict(row) for row in db.execute(text("""select r.id,r.chunk_index,r.duration_seconds,r.mime_type,r.size_bytes,r.object_path,r.created_at
      from chat_recording_chunks r join chat_sessions s on s.id=r.session_id
      where r.session_id=cast(:session as uuid) and s.organisation_id=cast(:org as uuid)
        and s.member_id=cast(:member as uuid) order by r.chunk_index"""), params).all()]
    recordings = [row_dict(row) for row in db.execute(text("""select id,title,created_at,updated_at,capture_state,upload_state,transcription_state,english_state,speaker_state,final_state,total_samples,duration_seconds
      from conversations where customer_id=cast(:customer as uuid) and organisation_id=cast(:org as uuid)
        and owner_id=cast(:member as uuid) and deleted_at is null and upload_state='saved' and total_samples>0
      order by created_at desc limit 100"""), params).all()]
    insights = []
    transcript_preview = []
    if session.get('conversation_id'):
        insights = [row_dict(row) for row in db.execute(text("""select id,fingerprint,status,data,error_message,created_at,updated_at
          from conversation_extractions where conversation_id=cast(:conversation as uuid)
          order by created_at"""), {**params, 'conversation': str(session['conversation_id'])}).all()]
        transcript_preview = [row_dict(row) for row in db.execute(text("""select t.id,t.ordinal,t.start_seconds,t.end_seconds,t.source_text,
          coalesce(t.english_text,tr.english_text) as english_text,t.speaker_ref,t.created_at
          from transcript_turns t join conversations c on c.id=t.conversation_id and c.active_version_id=t.version_id
          left join lateral (select english_text from translations where source_turn_id=t.id and state='complete'
            order by created_at desc limit 1) tr on true
          where t.conversation_id=cast(:conversation as uuid) and (t.english_text is not null or tr.english_text is not null)
          order by t.start_seconds,t.ordinal"""), {**params, 'conversation': str(session['conversation_id'])}).all()]
    return {'session': session, 'conversation_id': session.get('conversation_id'),
            'messages': _session_messages(db, session['id']), 'recordings': chunks, 'recorder_sessions': recordings,
            'insights': insights, 'transcript_preview': transcript_preview}


def _extract_voice_chunk(db, payload):
    """Process one database event. The event contains identifiers only; transcript data is read server-side."""
    conversation_id = str(payload.get('conversation_id') or '')
    record_id = str(payload.get('record_id') or '')
    if not conversation_id or not record_id:
        raise HTTPException(422, 'Invalid extraction event')
    row = db.execute(text("""select t.id,t.version_id,t.source_text,coalesce(t.english_text,tr.english_text) as english_text,
      t.source_language,t.speaker_ref,t.start_seconds,t.end_seconds,t.created_at
      from transcript_turns t left join translations tr on tr.source_turn_id=t.id and tr.state='complete'
      where t.id=cast(:record as uuid) and t.conversation_id=cast(:conversation as uuid)
        and exists(select 1 from conversations c where c.id=t.conversation_id and c.active_version_id=t.version_id)
      union all
      select t.id,t.version_id,t.source_text,coalesce(t.english_text,tr.english_text),t.source_language,t.speaker_ref,t.start_seconds,t.end_seconds,t.created_at
      from translations tr join transcript_turns t on t.id=tr.source_turn_id
      where tr.id=cast(:record as uuid) and tr.conversation_id=cast(:conversation as uuid) and tr.state='complete'
        and exists(select 1 from conversations c where c.id=t.conversation_id and c.active_version_id=t.version_id)
      limit 1"""), {'record': record_id, 'conversation': conversation_id}).first()
    if not row or not row.english_text:
        return {'accepted': True, 'status': 'waiting_for_translation'}
    chunk = row_dict(row)
    fingerprint = turn_fingerprint(chunk)
    # Serialize deliveries for one conversation/chunk. This prevents two
    # concurrent database webhook retries from both calling OpenAI or merging
    # the same context update.
    db.execute(text("select pg_advisory_xact_lock(hashtext(:key))"), {'key': f'voice-live:{conversation_id}'})
    active = db.execute(text('''select active_version_id,
      (select coverage_samples from transcript_versions v where v.id=conversations.active_version_id) as analysis_coverage_samples
      from conversations where id=cast(:conversation as uuid)'''),
                        {'conversation': conversation_id}).first()
    if not active or str(active[0]) != str(chunk['version_id']):
        return {'accepted': True, 'status': 'superseded'}
    existing = db.execute(text("select id,status,data from conversation_extractions where conversation_id=cast(:conversation as uuid) and fingerprint=:fingerprint"), {'conversation': conversation_id, 'fingerprint': fingerprint}).first()
    if existing and existing.status == 'complete':
        return {'accepted': True, 'status': 'duplicate', 'extraction_id': str(existing.id)}
    covered = db.execute(text("""select id from conversation_extractions
      where conversation_id=cast(:conversation as uuid) and status='complete'
        and data->'_live'->'turn_fingerprints' @> cast(:turns as jsonb) limit 1"""),
      {'conversation': conversation_id, 'turns': json.dumps([fingerprint])}).first()
    if covered:
        return {'accepted': True, 'status': 'duplicate', 'extraction_id': str(covered.id)}
    # Webhooks can arrive out of order. Analyse all unprocessed speech in audio
    # order under the same lock as live polling, rather than applying an older
    # isolated turn after a more recent customer correction.
    dialogue = [row_dict(item) for item in db.execute(text("""select t.id,t.version_id,t.source_text,t.provenance,
      coalesce(t.english_text,tr.english_text) as english_text,t.source_language,t.speaker_ref,
      t.start_seconds,t.end_seconds,t.created_at from transcript_turns t
      left join lateral (select english_text from translations where source_turn_id=t.id and state='complete'
        order by created_at desc limit 1) tr on true
      where t.conversation_id=cast(:conversation as uuid) and t.version_id=cast(:version as uuid)
      order by t.start_seconds,t.ordinal"""), {'conversation': conversation_id, 'version': str(chunk['version_id'])}).all()]
    dialogue = analysis_turns(dialogue, row_dict(active).get('analysis_coverage_samples'))
    completed = [row_dict(item) for item in db.execute(text("""select fingerprint,status,data
      from conversation_extractions where conversation_id=cast(:conversation as uuid) and status='complete'"""),
      {'conversation': conversation_id}).all()]
    pending = pending_turns(dialogue, completed)
    if not pending:
        return {'accepted': True, 'status': 'waiting_for_translation'}
    fingerprint = batch_fingerprint(pending)
    chunk = {**pending[-1], 'english_text': '\n'.join(str(turn['english_text']) for turn in pending),
             'turn_fingerprints': [turn_fingerprint(turn) for turn in pending],
             'recent_dialogue': [{key: turn.get(key) for key in ('start_seconds', 'end_seconds', 'english_text', 'speaker_ref')}
                                 for turn in dialogue if turn.get('analysis_ready') and turn.get('english_text')
                                 and (turn.get('start_seconds') or 0) <= (pending[-1].get('start_seconds') or 0)][-24:]}
    if not settings.openai_api_key:
        raise HTTPException(503, 'OpenAI extraction is not configured')
    db.execute(text("""insert into conversation_extractions(conversation_id,organisation_id,fingerprint,status,data)
      select cast(:conversation as uuid),c.organisation_id,:fingerprint,'running','{}'::jsonb from conversations c where c.id=cast(:conversation as uuid)
      on conflict(conversation_id,fingerprint) do update set status='running',error_message=null,updated_at=now()"""), {'conversation': conversation_id, 'fingerprint': fingerprint})
    context_row = db.execute(text("select context from voice_ai_contexts where conversation_id=cast(:conversation as uuid)"), {'conversation': conversation_id}).first()
    previous_context = deepcopy(context_row[0] if context_row else {})
    chunk['conversation_id'] = conversation_id
    chunk['fingerprint'] = fingerprint
    try:
        result = asyncio.run(asyncio.wait_for(VoiceConversationOrchestrator().run(chunk=chunk, durable_context=context_row[0] if context_row else {}), timeout=45))
    except Exception as error:
        # Commit only this chunk's retry marker. The advisory transaction lock
        # still prevents a concurrent delivery from processing the same
        # fingerprint, and unrelated chunks are never marked failed.
        db.execute(text("""update conversation_extractions
          set status='failed',error_message=:message,updated_at=now()
          where conversation_id=cast(:conversation as uuid) and fingerprint=:fingerprint"""),
          {'conversation': conversation_id, 'fingerprint': fingerprint, 'message': type(error).__name__})
        db.commit()
        raise
    status, error = analysis_outcome(result)
    if status != 'complete':
        data = failed_analysis_data(result, {'version_id': str(chunk['version_id']), 'turn_fingerprints': chunk['turn_fingerprints']})
        db.execute(text("""update conversation_extractions set status='failed',data=cast(:data as jsonb),error_message=:message,updated_at=now()
          where conversation_id=cast(:conversation as uuid) and fingerprint=:fingerprint"""),
          {'conversation': conversation_id, 'fingerprint': fingerprint, 'message': error['code'], 'data': json.dumps(data, ensure_ascii=False)})
        db.commit()
        return {'accepted': True, 'status': status, 'analysis_error': error}
    if (not catalogue_queries(result.get('context') or {})
            or requirement_signature(result.get('context') or {}) != requirement_signature(previous_context)):
        result.setdefault('context', {})['recent_recommendations'] = []
    data = {**result.get('extraction', {}), '_source': {'conversation_id': conversation_id, 'transcript_chunk_id': str(chunk['id']), 'fingerprint': fingerprint},
            '_live': {'version_id': str(chunk['version_id']), 'turn_fingerprints': chunk['turn_fingerprints']},
            '_route': result.get('route', {}), '_agent_result': result.get('agent_result', {})}
    db.execute(text("""update conversation_extractions set status='complete',data=cast(:data as jsonb),updated_at=now()
      where conversation_id=cast(:conversation as uuid) and fingerprint=:fingerprint"""), {'conversation': conversation_id, 'fingerprint': fingerprint, 'data': json.dumps(data, ensure_ascii=False)})
    db.execute(text("""insert into voice_ai_contexts(conversation_id,organisation_id,context,last_intent,last_route,updated_at)
      select cast(:conversation as uuid),c.organisation_id,cast(:context as jsonb),:intent,cast(:route as jsonb),now()
      from conversations c where c.id=cast(:conversation as uuid)
      on conflict(conversation_id) do update set context=excluded.context,last_intent=excluded.last_intent,last_route=excluded.last_route,updated_at=now()"""),
      {'conversation': conversation_id, 'context': json.dumps(result.get('context', {}), ensure_ascii=False), 'intent': result.get('route', {}).get('intent'), 'route': json.dumps(result.get('route', {}))})
    db.commit()
    return {'accepted': True, 'status': 'complete', 'extraction_id': fingerprint}


@app.post('/api/internal/voice/extraction-webhook')
def voice_extraction_webhook(request: Request, payload: dict, x_voice_extraction_secret: str = Header(default='')):
    expected = settings.voice_extraction_webhook_secret
    if not expected or not hmac.compare_digest(x_voice_extraction_secret, expected):
        raise HTTPException(401, 'Invalid webhook signature')
    with require_db() as db:
        try:
            result = _extract_voice_chunk(db, payload)
        except Exception:
            # _extract_voice_chunk persists a failure marker for the exact
            # fingerprint. Do not broaden that update to other chunks in the
            # same conversation when deliveries are concurrent.
            raise
    return result


@app.post('/api/voice-sessions')
def open_voice_session(payload: VoiceSessionIn, request: Request, background_tasks: BackgroundTasks):
    try:
        if payload.email.strip() and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', payload.email.strip()):
            raise HTTPException(422, 'Enter a valid customer email address before starting the session')
        customer = get_or_create_customer_sync(payload.name, payload.phone, payload.email)
        params = {**scope_parameters(), 'customer': str(customer['id']),
                  'phone': customer.get('normalized_phone'), 'email': customer.get('normalized_email'),
                  'title': f"{customer['name']} voice session"}
        with require_db() as db:
            row = db.execute(text("""select * from chat_sessions where organisation_id=cast(:org as uuid)
              and member_id=cast(:member as uuid) and customer_id=cast(:customer as uuid)
              order by updated_at desc limit 1"""), params).first()
            if not row:
                row = db.execute(text("""insert into chat_sessions(customer_id,title,identity_phone,identity_email,organisation_id,member_id)
                  values(cast(:customer as uuid),:title,:phone,:email,cast(:org as uuid),cast(:member as uuid)) returning *"""), params).first()
            session = row_dict(row)
            db.commit()
            background_tasks.add_task(_warm_voice_catalogue, sales_workspace.get())
            return {**_voice_history(db, session), 'customer': customer}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(422, 'Phone number or email is required to start a voice session') from None
    except Exception:
        raise HTTPException(503, 'Unable to open voice session') from None


async def _warm_voice_catalogue(context):
    """Start due inventory work after session creation, before microphone input."""
    if context is None or context.role == 'receptionist':
        return
    from .catalogue_embeddings import get_embedding_client
    from .catalogue_index import retrieve_catalogue
    previous_org, previous_member = get_organisation_id(), get_member_id()
    token = sales_workspace.set(context)
    set_organisation_id(str(context.id))
    set_member_id(str(context.member_id))
    try:
        await asyncio.wait_for(retrieve_catalogue(zoho, {}, get_embedding_client()), timeout=5)
    except Exception as error:
        log.warning('catalogue_warmup_unavailable exception_type=%s', type(error).__name__)
    finally:
        sales_workspace.reset(token)
        set_organisation_id(previous_org)
        set_member_id(previous_member)


@app.get('/api/voice-sessions/{session_id}/history')
def voice_session_history(session_id: UUID, request: Request):
    with require_db() as db:
        return _voice_history(db, _session(db, session_id))


def _voice_live_snapshot(session_id):
    """Read the selected recording only; never concatenate historical versions."""
    with require_db() as db:
        session = _session(db, session_id)
        params = {**scope_parameters(), 'conversation': str(session.get('conversation_id') or ''),
                  'customer': str(session['customer_id'])}
        recording, turns, extractions, context = None, [], [], {}
        if session.get('conversation_id'):
            row = db.execute(text("""select id,active_version_id,created_at,updated_at,capture_state,upload_state,
              transcription_state,english_state,speaker_state,final_state,total_samples,duration_seconds,
              (select coverage_samples from transcript_versions v where v.id=conversations.active_version_id) as analysis_coverage_samples
              from conversations where id=cast(:conversation as uuid) and organisation_id=cast(:org as uuid)
                and owner_id=cast(:member as uuid) and customer_id=cast(:customer as uuid) and deleted_at is null"""), params).first()
            recording = row_dict(row) if row else None
        if recording:
            params['version'] = str(recording.get('active_version_id') or '')
            if recording.get('active_version_id'):
                turns = [row_dict(row) for row in db.execute(text("""select t.id,t.ordinal,t.version_id,t.start_seconds,
                  t.end_seconds,t.source_text,coalesce(t.english_text,tr.english_text) as english_text,
                  t.source_language,t.speaker_ref,sa.role as speaker_role,sa.display_name as speaker_name,t.created_at,t.provenance
                  from transcript_turns t
                  left join lateral (select english_text from translations where source_turn_id=t.id
                    and state='complete' order by created_at desc limit 1) tr on true
                  left join speaker_assignments sa on sa.version_id=t.version_id and sa.speaker_ref=t.speaker_ref
                  where t.conversation_id=cast(:conversation as uuid) and t.version_id=cast(:version as uuid)
                  order by t.start_seconds,t.ordinal"""), params).all()]
            extractions = [row_dict(row) for row in db.execute(text("""select id,fingerprint,status,data,error_message,created_at,updated_at
              from conversation_extractions where conversation_id=cast(:conversation as uuid) order by created_at"""), params).all()]
            stored = db.execute(text("select context from voice_ai_contexts where conversation_id=cast(:conversation as uuid)"), params).first()
            context = stored[0] if stored else {}
        turns = analysis_turns(turns, recording.get('analysis_coverage_samples') if recording else None)
        for turn in turns:
            turn['translation_state'] = 'complete' if str(turn.get('english_text') or '').strip() else 'pending'
        return {'session_id': str(session_id), 'conversation_id': str(recording['id']) if recording else None,
                'revision': str(recording.get('active_version_id') or '') if recording else '',
                'recording': recording, 'transcript_preview': turns, 'insights': extractions,
                'context': context or {}}


def _live_task_key(snapshot):
    scope = scope_parameters()
    return scope['org'], scope['member'], str(snapshot['conversation_id'])


@asynccontextmanager
async def _voice_live_database():
    # The sales database is synchronous. Keep connection setup, SQL and cleanup
    # off the API loop so a slow database never stalls microphone uploads.
    manager = await asyncio.to_thread(require_db)
    database = await asyncio.to_thread(manager.__enter__)
    try:
        yield database
    finally:
        await asyncio.to_thread(manager.__exit__, None, None, None)


async def _voice_live_execute(database, query, parameters):
    return await asyncio.to_thread(database.execute, text(query), parameters)


async def _analyse_voice_live(session_id):
    """A poll starts work, but never awaits AI or catalogue services itself.

    The task inherits the verified request's ContextVars. It owns its database
    session, rechecks the customer scope and uses the same conversation lock as
    the database webhook. A process restart simply leaves the next poll to retry.
    """
    fingerprint = None
    conversation_id = None
    try:
        snapshot = await asyncio.to_thread(_voice_live_snapshot, session_id)
        conversation_id = snapshot.get('conversation_id')
        if not conversation_id:
            return
        async with _voice_live_database() as db:
            claim = await _voice_live_execute(db, 'select pg_try_advisory_xact_lock(hashtext(:key)) as acquired',
                                             {'key': f'voice-live:{conversation_id}'})
            locked = claim.first()
            if not locked or not locked.acquired:
                return
            # Re-read after acquiring the lock: a webhook may have completed
            # between the initial snapshot and this claim.
            snapshot = await asyncio.to_thread(_voice_live_snapshot, session_id)
            if snapshot.get('conversation_id') != conversation_id:
                return
            pending = pending_turns(snapshot['transcript_preview'], snapshot['insights'])
            context = snapshot['context']
            previous_context = deepcopy(context)
            if pending:
                fingerprint = batch_fingerprint(pending)
                scope = scope_parameters()
                await _voice_live_execute(db, """insert into conversation_extractions(conversation_id,organisation_id,fingerprint,status,data)
                  values(cast(:conversation as uuid),cast(:org as uuid),:fingerprint,'running','{}'::jsonb)
                  on conflict(conversation_id,fingerprint) do update set status='running',error_message=null,updated_at=now()""",
                  {**scope, 'conversation': conversation_id, 'fingerprint': fingerprint})
                chunk = {'id': str(pending[-1]['id']), 'conversation_id': conversation_id,
                         'fingerprint': fingerprint, 'turn_fingerprints': [turn_fingerprint(turn) for turn in pending],
                         'english_text': '\n'.join(str(turn['english_text']) for turn in pending),
                         'end_seconds': pending[-1].get('end_seconds'),
                         'recent_dialogue': [{key: turn.get(key) for key in ('start_seconds','end_seconds','english_text','speaker_ref','speaker_role')}
                                             for turn in snapshot['transcript_preview'] if turn.get('analysis_ready', True)
                                             and turn.get('english_text') and (turn.get('start_seconds') or 0) <= (pending[-1].get('start_seconds') or 0)][-24:],
                         'turns': [{key: turn.get(key) for key in ('start_seconds','end_seconds','english_text','speaker_ref','speaker_role')} for turn in pending]}
                result = await asyncio.wait_for(VoiceConversationOrchestrator().run(chunk=chunk, durable_context=context), timeout=45)
                status, error = analysis_outcome(result)
                if status != 'complete':
                    latest = await asyncio.to_thread(_session, db, session_id)
                    if str(latest.get('conversation_id')) != conversation_id:
                        return
                    data = failed_analysis_data(result, {'version_id': snapshot['revision'], 'turn_fingerprints': chunk['turn_fingerprints']})
                    await _voice_live_execute(db, """update conversation_extractions set status='failed',data=cast(:data as jsonb),error_message=:error,updated_at=now()
                      where conversation_id=cast(:conversation as uuid) and fingerprint=:fingerprint""",
                      {'conversation': conversation_id, 'fingerprint': fingerprint, 'error': error['code'],
                       'data': json.dumps(data, ensure_ascii=False, default=str)})
                    await asyncio.to_thread(db.commit)
                    return
                context = result['context']
            else:
                result = {'context': context, 'extraction': {}, 'route': {}, 'agent_result': {}}
            try:
                catalogue = await asyncio.wait_for(search_catalogue(zoho, context), timeout=40)
            except TimeoutError:
                catalogue = {'status': 'unavailable', 'items': [], 'queries': [], 'notices': [{
                    'code': 'catalogue_lookup_failed', 'message': 'Product search timed out. Transcript updates continue.',
                }]}
            except Exception:
                catalogue = {'status': 'unavailable', 'items': [], 'queries': [], 'notices': [{
                    'code': 'catalogue_lookup_failed', 'message': 'Product search is temporarily unavailable. Your requirements are preserved.',
                }]}
            catalogue['requirements_signature'] = requirement_signature(context)
            retain_catalogue_recommendations(context, catalogue, previous_context)
            latest = await asyncio.to_thread(_session, db, session_id)
            if str(latest.get('conversation_id')) != conversation_id:
                return
            params = {**scope_parameters(), 'conversation': conversation_id,
                      'context': json.dumps(context, ensure_ascii=False, default=str),
                      'intent': result['route'].get('intent'), 'route': json.dumps(result['route'])}
            await _voice_live_execute(db, """insert into voice_ai_contexts(conversation_id,organisation_id,context,last_intent,last_route,updated_at)
              values(cast(:conversation as uuid),cast(:org as uuid),cast(:context as jsonb),:intent,cast(:route as jsonb),now())
              on conflict(conversation_id) do update set context=excluded.context,
                last_intent=coalesce(excluded.last_intent,voice_ai_contexts.last_intent),
                last_route=case when excluded.last_route='{}'::jsonb then voice_ai_contexts.last_route else excluded.last_route end,
                updated_at=now()""", params)
            data = {**result.get('extraction', {}), 'analysis_status': 'complete', '_route': result['route'], '_catalogue': catalogue,
                    '_live': {'version_id': snapshot['revision'], 'turn_fingerprints': chunk['turn_fingerprints'] if pending else []}}
            if fingerprint:
                await _voice_live_execute(db, """update conversation_extractions set status='complete',data=cast(:data as jsonb),updated_at=now()
                  where conversation_id=cast(:conversation as uuid) and fingerprint=:fingerprint""",
                  {'conversation': conversation_id, 'fingerprint': fingerprint, 'data': json.dumps(data, ensure_ascii=False, default=str)})
            else:
                # A webhook may have already extracted every turn. Publish the
                # catalogue lookup separately without paying for another model call.
                signature = hashlib.sha256(json.dumps({'queries': catalogue.get('queries') or [],
                    'requirements_signature': catalogue['requirements_signature']}, sort_keys=True).encode()).hexdigest()
                await _voice_live_execute(db, """insert into conversation_extractions(conversation_id,organisation_id,fingerprint,status,data)
                  values(cast(:conversation as uuid),cast(:org as uuid),:fingerprint,'complete',cast(:data as jsonb))
                  on conflict(conversation_id,fingerprint) do update set data=excluded.data,updated_at=now()""",
                  {**scope_parameters(), 'conversation': conversation_id, 'fingerprint': f'catalogue:{signature}',
                   'data': json.dumps(data, ensure_ascii=False, default=str)})
            await asyncio.to_thread(db.commit)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        log.warning('voice_live_analysis_failed exception_type=%s', type(error).__name__)
        if fingerprint and conversation_id:
            # Store a short retry marker after the model transaction rolls back.
            # No raw provider errors or customer speech enter logs/notices.
            try:
                async with _voice_live_database() as db:
                    await _voice_live_execute(db, """insert into conversation_extractions(conversation_id,organisation_id,fingerprint,status,error_message)
                      values(cast(:conversation as uuid),cast(:org as uuid),:fingerprint,'failed',:error)
                      on conflict(conversation_id,fingerprint) do update set status='failed',error_message=excluded.error_message,updated_at=now()""",
                      {**scope_parameters(), 'conversation': conversation_id, 'fingerprint': fingerprint, 'error': type(error).__name__})
                    await asyncio.to_thread(db.commit)
            except Exception:
                log.warning('voice_live_failure_marker_unavailable')


@app.get('/api/voice-sessions/{session_id}/live')
async def voice_session_live(session_id: UUID, request: Request):
    snapshot = await asyncio.to_thread(_voice_live_snapshot, session_id)
    pending = pending_turns(snapshot['transcript_preview'], snapshot['insights'])
    catalogue_rows = [row for row in snapshot['insights'] if (row.get('data') or {}).get('_catalogue')]
    latest_catalogue_row = max(catalogue_rows, key=lambda row: str(row.get('updated_at') or ''), default=None)
    catalogue = (latest_catalogue_row['data']['_catalogue'] if latest_catalogue_row else {})
    context = snapshot['context']
    fields = workspace_fields(context, catalogue)
    requirements = fields['requirements']
    notices = list(catalogue.get('notices') or [])
    status = 'waiting_for_translation' if not any(turn.get('english_text') for turn in snapshot['transcript_preview']) else 'complete'
    key = _live_task_key(snapshot) if snapshot['conversation_id'] else None
    task = _voice_live_tasks.get(key)
    if task and task.done():
        _voice_live_tasks.pop(key, None)
        task = None
    failed = retry_failure(snapshot['insights'], pending=bool(pending), moment=now())
    retry_wait = bool(failed)
    failed_status, analysis_error = row_analysis_error(failed)
    needs_catalogue = catalogue_due(catalogue, latest_catalogue_row.get('updated_at') if latest_catalogue_row else None, requirements, moment=now())
    if requirements and catalogue.get('requirements_signature') and catalogue['requirements_signature'] != requirement_signature(context):
        needs_catalogue = True
    if pending:
        status = failed_status if retry_wait else 'pending'
    if (pending or needs_catalogue) and sales_workspace.get().role != 'receptionist':
        if not settings.openai_api_key and pending:
            status = 'unavailable'
            analysis_error = {'code': 'voice_analysis_not_configured', 'message': 'Live AI suggestions are waiting for the managed AI service. Recording and transcript updates continue.'}
            notices.append(analysis_error)
        elif not task and not retry_wait and len(_voice_live_tasks) < 2:
            task = asyncio.create_task(_analyse_voice_live(session_id))
            _voice_live_tasks[key] = task
            task.add_done_callback(lambda completed, selected=key: _voice_live_tasks.pop(selected, None) if _voice_live_tasks.get(selected) is completed else None)
    if task and pending:
        status = 'running'
    if retry_wait:
        notices.append({'code': 'voice_analysis_failed', 'message': analysis_error['message']})
    return {**snapshot, 'insights': snapshot['insights'][-30:], **fields,
            'keywords': intent_keywords(context), 'recommendations': context.get('recent_recommendations') or [],
            'analysis_status': status, 'analysis_error': analysis_error if status in {'failed', 'unavailable'} else None,
            'catalogue_status': 'searching' if task else catalogue.get('status', 'waiting'),
            'notices': notices}


@app.post('/api/voice-sessions/{session_id}/conversation')
def link_voice_conversation(session_id: UUID, payload: VoiceSessionLinkIn, request: Request):
    with require_db() as db:
        session = _session(db, session_id, lock=True)
        params = {**scope_parameters(), 'session': str(session_id), 'customer': str(session['customer_id']),
                  'conversation': str(payload.conversation_id) if payload.conversation_id else None}
        if payload.conversation_id:
            row = db.execute(text("""select id from conversations where id=cast(:conversation as uuid)
              and organisation_id=cast(:org as uuid) and owner_id=cast(:member as uuid)
              and customer_id=cast(:customer as uuid) and deleted_at is null for share"""), params).first()
            if not row:
                raise HTTPException(404, 'Conversation not found')
        row = db.execute(text("""update chat_sessions set conversation_id=cast(:conversation as uuid),updated_at=now()
          where id=cast(:session as uuid) and organisation_id=cast(:org as uuid)
            and member_id=cast(:member as uuid) and customer_id=cast(:customer as uuid) returning id"""), params).first()
        if not row:
            raise HTTPException(404, 'Session not found')
        db.commit()
    return {'linked': True, 'session_id': str(session_id), 'conversation_id': payload.conversation_id}


@app.get('/api/voice-sessions/{session_id}/notes')
def voice_session_notes(session_id: UUID, request: Request):
    with require_db() as db:
        session = _session(db, session_id)
        notes = db.execute(text("""select n.id,n.content,n.created_at,n.author_id,n.session_id from voice_session_notes n
          join customers c on c.id=n.customer_id and c.organisation_id=n.organisation_id
          where n.organisation_id=cast(:org as uuid) and n.customer_id=cast(:customer as uuid)
            and c.owner_id=cast(:member as uuid) and c.deleted_at is null order by n.created_at"""),
          {**scope_parameters(), 'customer': str(session['customer_id'])}).all()
        return {'notes': [row_dict(note) for note in notes]}


@app.post('/api/voice-sessions/{session_id}/notes')
def add_voice_session_note(session_id: UUID, payload: VoiceSessionNoteIn, request: Request):
    content = payload.content.strip()
    if not content:
        raise HTTPException(422, 'Note cannot be empty')
    with require_db() as db:
        session = _session(db, session_id, lock=True)
        note = db.execute(text("""insert into voice_session_notes(organisation_id,customer_id,session_id,author_id,content)
          select s.organisation_id,s.customer_id,s.id,cast(:actor as uuid),:content from chat_sessions s
          where s.id=cast(:session as uuid) and s.organisation_id=cast(:org as uuid) and s.member_id=cast(:member as uuid)
          returning id,content,created_at,author_id,session_id"""),
          {**scope_parameters(), 'session': str(session['id']), 'content': content}).first()
        if not note:
            raise HTTPException(404, 'Session not found')
        db.commit()
        return {'note': row_dict(note)}


@app.get('/api/voice-sessions/{session_id}/context')
def voice_session_context(session_id: UUID, request: Request):
    with require_db() as db:
        context = _session(db, session_id).get('context') or {}
        return {'product_requirements': context.get('product_requirements', []) if isinstance(context, dict) else [], 'cart': context.get('cart', []) if isinstance(context, dict) else []}


@app.put('/api/voice-sessions/{session_id}/context')
def update_voice_session_context(session_id: UUID, payload: VoiceSessionContextIn, request: Request):
    with require_db() as db:
        session = _session(db, session_id, lock=True)
        context = session.get('context') if isinstance(session.get('context'), dict) else {}
        context['product_requirements'] = payload.product_requirements
        context['cart'] = payload.cart
        row = db.execute(text("""update chat_sessions set context=cast(:context as jsonb),updated_at=now()
          where id=cast(:session as uuid) and organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid) returning id"""),
          {**scope_parameters(), 'context': json.dumps(context), 'session': str(session_id)}).first()
        if not row:
            raise HTTPException(404, 'Session not found')
        db.commit()
        return {'product_requirements': payload.product_requirements, 'cart': payload.cart}


def _pipeline_layouts(payload):
    rows = payload.get('layouts', payload.get('data', [])) if isinstance(payload, dict) else []
    result = []
    for row in rows:
        if not row.get('id'): continue
        stages = []
        for section in row.get('sections', []):
            for field in section.get('fields', []):
                if field.get('api_name') == 'Stage':
                    stages = [item.get('actual_value') or item.get('display_value') for item in field.get('pick_list_values', []) if item.get('actual_value') or item.get('display_value')]
        result.append({'id': str(row.get('id')), 'name': row.get('display_label') or row.get('name') or str(row.get('id')), 'display_label': row.get('display_label') or row.get('name') or str(row.get('id')), 'stages': stages})
    return result

@app.get('/api/pipelines/layouts')
async def pipeline_layouts(refresh: bool = False):
    try:
        return {'layouts': _pipeline_layouts(await zoho.pipeline_layout())}
    except Exception: raise HTTPException(502, 'Unable to load Zoho Bigin pipeline layouts')

@app.get('/api/pipelines')
async def pipeline_records(pipeline_id: str, page: int = 1):
    try:
        cached_records = [row for row in _recent_cache_rows('pipelines')
                          if isinstance(row.get('Pipeline'), dict) and str(row['Pipeline'].get('id')) == str(pipeline_id)]
        if page == 1 and cached_records:
            return {'records': cached_records, 'page': 1, 'has_more': True, 'source': 'supabase', 'last_sync': None}
        with require_db() as db:
            rows = db.execute(text("""select record_id,pipeline_id,data,modified_time from zoho_pipeline_records
              where pipeline_id=:pipeline_id and organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)
              order by modified_time desc limit 40"""), {'pipeline_id': pipeline_id, 'org': get_organisation_id(), 'member': get_member_id()}).all()
            last_sync = db.execute(text("""select synced_at from zoho_pipeline_sync
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid) limit 1"""), {'org': get_organisation_id(), 'member': get_member_id()}).scalar()
        if page == 1 and rows:
            records = [json.loads(row[2]) if isinstance(row[2], str) else row[2] for row in rows]
            _cache_recent_rows('pipelines', records)
            return {'records': records, 'page': 1, 'has_more': len(rows) == 40, 'source': 'supabase', 'last_sync': last_sync.isoformat() if last_sync else None}
        result = await zoho.list_pipeline_records(pipeline_id, page)
        info = result.get('info', {})
        records = result.get('data', [])
        _cache_recent_rows('pipelines', records)
        return {'records': records, 'page': page, 'has_more': bool(info.get('more_records')), 'source': 'zoho', 'last_sync': last_sync.isoformat() if last_sync else None}
    except Exception: raise HTTPException(502, 'Unable to load pipeline records')

@app.get('/api/pipelines/search')
async def pipeline_search(word: str):
    cleaned_word = word.replace('\\', '').strip()
    if not cleaned_word:
        return {'records': [], 'info': {'count': 0, 'more_records': False}}
    try:
        result = await zoho.search_pipeline_records(cleaned_word)
        records = result.get('data', [])
        _cache_recent_rows('pipelines', records)
        return {'records': records, 'info': result.get('info', {})}
    except Exception: raise HTTPException(502, 'Unable to search Zoho Bigin pipelines')

@app.get('/api/pipelines/record/{record_id}')
async def pipeline_record(record_id: str):
    try:
        result = await zoho.pipeline_record(record_id)
        record = (result.get('data') or [None])[0]
        _cache_recent_rows('pipelines', [record] if record else [])
        return {'record': record}
    except Exception: raise HTTPException(502, 'Unable to load pipeline record')

@app.post('/api/pipelines')
async def create_pipeline_record(body: dict):
    try:
        result = await zoho.create_pipeline(body)
        record = (result.get('data') or [None])[0]
        _cache_recent_rows('pipelines', [record] if record else [])
        return {'record': record, 'response': result}
    except Exception: raise HTTPException(502, 'Unable to create Zoho Bigin pipeline record')

@app.put('/api/pipelines/record/{record_id}')
async def update_pipeline_record(record_id: str, body: dict):
    try:
        result = await zoho.update_pipeline(record_id, body)
        record = (result.get('data') or [None])[0]
        _cache_recent_rows('pipelines', [record] if record else [])
        if 'Stage' in body:
            try:
                with require_db() as db:
                    db.execute(text("""update zoho_pipeline_records
                      set data=jsonb_set(data, '{Stage}', to_jsonb(cast(:stage as text)), true), modified_time=now()
                      where record_id=:record_id
                        and organisation_id=cast(:org as uuid)
                        and member_id=cast(:member as uuid)"""), {
                        'stage': body.get('Stage') or '',
                        'record_id': record_id,
                        'org': get_organisation_id(),
                        'member': get_member_id(),
                    })
                    db.commit()
            except Exception:
                pass
        return {'record': record, 'response': result}
    except Exception: raise HTTPException(502, 'Unable to update Zoho Bigin pipeline record')

@app.delete('/api/pipelines/record/{record_id}')
async def delete_pipeline_record(record_id: str):
    try:
        result = await zoho.delete_pipeline(record_id)
        _recent_cache_remove('pipelines', record_id)
        return result
    except Exception: raise HTTPException(502, 'Unable to delete Zoho Bigin pipeline record')

@app.get('/api/bigin/deleted/{module}')
async def deleted_bigin_records(module: str, type: str = 'recycle', page: int = 1):
    allowed_modules = {'Pipelines', 'Tasks'}
    allowed_types = {'all', 'recycle', 'permanent'}
    if module not in allowed_modules:
        raise HTTPException(422, 'Only Pipelines and Tasks are supported in the recycle bin')
    if type not in allowed_types:
        raise HTTPException(422, 'Deleted record type must be all, recycle, or permanent')
    if page < 1:
        raise HTTPException(422, 'Page must be at least 1')
    try:
        return await zoho.list_deleted_bigin_records(module, type, page)
    except Exception: raise HTTPException(502, 'Unable to load deleted Zoho Bigin')

@app.get('/api/pipelines/deleted')
async def deleted_pipeline_records(type: str = 'recycle', page: int = 1):
    return await deleted_bigin_records('Pipelines', type, page)

@app.get('/api/tasks/deleted')
async def deleted_task_records(type: str = 'recycle', page: int = 1):
    return await deleted_bigin_records('Tasks', type, page)

@app.delete('/api/tasks/{task_id}')
async def delete_task(task_id: str):
    try:
        return await zoho.delete_task(task_id)
    except Exception: raise HTTPException(502, 'Unable to delete Zoho Bigin task')

@app.post('/api/pipelines/sync')
async def sync_pipelines():
    try:
        with require_db() as db:
            last = db.execute(text("""select synced_at from zoho_pipeline_sync
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid) limit 1"""), {'org': get_organisation_id(), 'member': get_member_id()}).scalar()
            if last:
                age = (now() - last).total_seconds()
                if age < 3600:
                    return {'synced': False, 'limited': True, 'last_sync': last.isoformat(), 'next_sync_in_seconds': int(3600-age)}
        layouts = _pipeline_layouts(await zoho.pipeline_layout())
        fetched = []
        for layout in layouts:
            result = await zoho.list_pipeline_records(layout['id'], 1)
            fetched.extend(result.get('data', []))
        fetched = sorted(fetched, key=lambda row: row.get('Modified_Time') or '', reverse=True)[:40]
        _cache_recent_rows('pipelines', fetched)
        with require_db() as db:
            for row in fetched:
                pipeline = row.get('Pipeline') if isinstance(row.get('Pipeline'), dict) else {}
                db.execute(text("""insert into zoho_pipeline_records(record_id,pipeline_id,data,modified_time,organisation_id,member_id)
                  values(:record_id,:pipeline_id,cast(:data as jsonb),:modified_time,cast(:org as uuid),cast(:member as uuid))
                  on conflict(organisation_id,member_id,record_id) do update set pipeline_id=:pipeline_id,data=cast(:data as jsonb),modified_time=:modified_time,
                    organisation_id=cast(:org as uuid),member_id=cast(:member as uuid)"""), {'record_id': str(row.get('id')), 'pipeline_id': str(pipeline.get('id') or row.get('$layout_id', {}).get('id') or ''), 'data': json.dumps(row), 'modified_time': row.get('Modified_Time'), 'org': get_organisation_id(), 'member': get_member_id()})
            db.execute(text("""insert into zoho_pipeline_sync(tenant_key,organisation_id,member_id,synced_at)
              values(:tenant,cast(:org as uuid),cast(:member as uuid),now())
              on conflict(tenant_key) do update set organisation_id=excluded.organisation_id,member_id=excluded.member_id,synced_at=now()"""), {'tenant': f'guru-group:{get_organisation_id()}:{get_member_id()}', 'org': get_organisation_id(), 'member': get_member_id()})
            db.execute(text("""delete from zoho_pipeline_records
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)
              and record_id not in (select record_id from zoho_pipeline_records where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid) order by modified_time desc nulls last limit 40)"""), {'org': get_organisation_id(), 'member': get_member_id()})
            db.commit()
        return {'synced': True, 'limited': False, 'last_sync': now().isoformat(), 'count': len(fetched)}
    except Exception: raise HTTPException(502, 'Unable to sync Zoho Bigin pipelines')
@app.get('/api/products')
async def products(search: str = '', date: str = '', page: int = 1):
    """Return the member's recent cache by default, then use live Books search."""
    query = search.strip()
    try:
        status = zoho.status()
        if not status.get('connected'):
            raise HTTPException(409, 'Connect Zoho before searching products')
        if not status.get('organization_id'):
            raise HTTPException(409, 'Select a Zoho Books organization before searching products')
        if not query and not date.strip() and page == 1:
            cached = _recent_cache_rows('products')
            if cached:
                return {'items': cached, 'search': '', 'page': 1, 'has_more': True,
                        'search_by': 'search_text', 'organization_id': status['organization_id'],
                        'source': 'supabase', 'stored_in_supabase': True}
        payload = await zoho.search_books_items(status['organization_id'], query, date.strip() or None, page)
        rows = payload.get('items', payload.get('products', payload.get('data', [])))
        items = [row for row in rows if isinstance(row, dict)]
        _cache_recent_rows('products', items)
        context = payload.get('page_context', {})
        return {'items': items[:40], 'search': query, 'page': page, 'has_more': bool(context.get('has_more_page')), 'search_by': 'search_text', 'organization_id': status['organization_id'], 'source': 'zoho', 'stored_in_supabase': True}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'Unable to search Zoho Books items')
@app.post('/api/products')
async def create_product(payload: dict):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        item_name = str(payload.get('name') or payload.get('item_name') or '').strip()
        if not item_name: raise HTTPException(422, 'Product name is required')
        if payload.get('rate') in (None, ''): raise HTTPException(422, 'Selling rate is required')
        data = {key: value for key, value in payload.items() if value not in (None, '')}
        data['name'] = data['item_name'] = item_name; data['rate'] = float(data['rate'])
        if 'purchase_rate' in data: data['purchase_rate'] = float(data['purchase_rate'])
        result = await zoho.create_books_item(org, data)
        item = result.get('item')
        _cache_recent_rows('products', [item] if item else [])
        return {'item': item, 'response': result}
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to create Zoho Books item')
@app.get('/api/books/customers')
async def books_customers(search: str = '', date: str = ''):
    query = search.strip()
    if not query: return {'customers': []}
    try:
        status = zoho.status()
        if not status.get('connected') or not status.get('organization_id'):
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        payload = await zoho.search_books_customers(status['organization_id'], query, date.strip() or None)
        rows = [{**row, '__source': 'books'} for row in payload.get('contacts', payload.get('data', [])) if isinstance(row, dict)]
        _cache_recent_rows('contacts', rows)
        return {'customers': rows, 'info': payload.get('page_context', {})}
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to search Books customers')
@app.get('/api/books/vendors')
async def books_vendors(search: str = ''):
    query = search.strip()
    if not query: return {'vendors': []}
    try:
        status = zoho.status()
        if not status.get('connected') or not status.get('organization_id'):
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        payload = await zoho.search_books_vendors(status['organization_id'], query)
        rows = [{**row, '__source': 'books'} for row in payload.get('contacts', payload.get('data', [])) if isinstance(row, dict)]
        _cache_recent_rows('contacts', rows)
        return {'vendors': rows, 'info': payload.get('page_context', {})}
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to search Books vendors')

@app.get('/api/document-contacts')
async def document_contacts(search: str = '', role: str = 'customer'):
    query = search.strip()
    if not query:
        return {'contacts': []}
    if role not in {'customer', 'vendor'}:
        raise HTTPException(422, 'Contact role must be customer or vendor')
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org:
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        digits = ''.join(ch for ch in query if ch.isdigit())
        search_by = 'phone' if query.isdigit() or (len(digits) == 10 and digits == query) else ('email' if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', query) else 'word')
        try:
            bigin_payload = await zoho.search_bigin_contacts(query, search_by)
        except ValueError:
            bigin_payload = {}
        bigin = []
        for row in bigin_payload.get('data', []):
            if not isinstance(row, dict):
                continue
            name = row.get('Full_Name') or ' '.join(filter(None, [row.get('First_Name'), row.get('Last_Name')])) or 'Unnamed contact'
            bigin.append({**row, '__source': 'bigin', 'contact_id': f"bigin:{row.get('id')}", 'contact_name': name, 'email': row.get('Email') or '', 'phone': row.get('Mobile') or row.get('Phone') or '', 'contact_role': role})
        books_payload = await (zoho.search_books_vendors(org, query) if role == 'vendor' else zoho.search_books_customers(org, query))
        books = []
        for row in books_payload.get('contacts', books_payload.get('data', [])):
            if isinstance(row, dict):
                books.append({**row, '__source': 'books', 'contact_id': row.get('contact_id'), 'contact_name': row.get('contact_name') or row.get('customer_name') or row.get('vendor_name') or 'Unnamed contact', 'contact_role': role})
        result_contacts = [*bigin, *books][:40]
        _cache_recent_rows('contacts', result_contacts)
        return {'contacts': result_contacts, 'search': query, 'role': role}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'Unable to search Zoho contact sources')

@app.get('/api/purchase-orders/options')
@app.get('/api/documents/options')
async def purchase_order_options():
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org:
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        currencies, locations, accounts, projects, tags, taxes = await asyncio.gather(
            zoho.list_books_currencies(org),
            zoho.list_books_locations(org),
            zoho.list_books_chart_of_accounts(org),
            zoho.list_books_projects(org),
            zoho.list_books_reporting_tags(org),
            zoho.list_books_taxes(org),
        )
        return {
            'currencies': currencies.get('currencies', []),
            'locations': locations.get('locations', []),
            'accounts': accounts.get('chartofaccounts', accounts.get('accounts', accounts.get('data', []))),
            'projects': projects.get('projects', projects.get('data', [])),
            'reporting_tags': tags.get('reporting_tags', tags.get('tags', tags.get('data', []))),
            'taxes': taxes.get('taxes', taxes.get('data', [])),
            'organization_id': org,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'Unable to load purchase order options')

@app.get('/api/purchase-orders/vendors/{vendor_id}')
async def purchase_order_vendor(vendor_id: str):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org:
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        detail, persons = await asyncio.gather(
            zoho.get_books_contact(org, vendor_id),
            zoho.get_books_contact_persons(org, vendor_id),
        )
        contact = detail.get('contact', detail.get('data', detail))
        if isinstance(contact, list):
            contact = contact[0] if contact else {}
        contact = contact if isinstance(contact, dict) else {}
        contact_persons = persons.get('contacts') or persons.get('contact_persons') or persons.get('contactpersons') or persons.get('data') or contact.get('contact_persons') or contact.get('contactpersons') or []
        return {
            'vendor': contact,
            'contact_persons': contact_persons,
            'organization_id': org,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'Unable to load Zoho Books vendor details')

@app.get('/api/documents/contacts/{contact_id}')
async def document_contact(contact_id: str):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org:
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        detail, persons = await asyncio.gather(
            zoho.get_books_contact(org, contact_id),
            zoho.get_books_contact_persons(org, contact_id),
        )
        contact = detail.get('contact', detail.get('data', detail))
        if isinstance(contact, list):
            contact = contact[0] if contact else {}
        contact = contact if isinstance(contact, dict) else {}
        contact_persons = persons.get('contacts') or persons.get('contact_persons') or persons.get('contactpersons') or persons.get('data') or contact.get('contact_persons') or contact.get('contactpersons') or []
        return {'contact': contact, 'contact_persons': contact_persons, 'organization_id': org}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'Unable to load Zoho Books contact details')

@app.get('/api/purchase-orders/options/tag-options')
async def purchase_order_tag_options(tag_id: str):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org:
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        payload = await zoho.list_books_reporting_tag_options(org, tag_id)
        return {'options': payload.get('options', payload.get('tag_options', payload.get('data', []))), 'tag_id': tag_id, 'organization_id': org}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'Unable to load reporting tag options')

async def _resolve_books_document_contact(payload: dict, role: str, org: str):
    id_key = f'{role}_id'; source_key = f'{role}_source'; record_key = f'{role}_record'
    raw_id = str(payload.get(id_key) or '')
    # Older voice-session clients used customer_source/customer_record even for
    # purchase orders. Keep accepting those aliases while the UI sends the
    # role-specific vendor_source/vendor_record fields.
    source = payload.get(source_key)
    record = payload.get(record_key)
    if role == 'vendor':
        source = source or payload.get('customer_source')
        record = record or payload.get('customer_record')
    is_bigin = source == 'bigin' or raw_id.startswith('bigin:')
    if not is_bigin:
        return payload.get(id_key)
    record = record or {}
    if not record and raw_id.startswith('bigin:'):
        result = await zoho.get_bigin_contact(raw_id.removeprefix('bigin:'))
        record = (result.get('data') or [result.get('contact')])[0] if isinstance(result.get('data'), list) else (result.get('data') or result.get('contact') or result)
    name = record.get('contact_name') or record.get('Full_Name') or payload.get(f'{role}_name') or 'Unnamed contact'
    email = record.get('email') or record.get('Email')
    phone = record.get('phone') or record.get('Mobile') or record.get('Phone')
    books_contact = await zoho.ensure_books_contact(org, name=name, email=email, phone=phone, contact_type=role)
    books_id = (books_contact.get('contact_id') or books_contact.get('id')) if isinstance(books_contact, dict) else None
    if not books_id:
        raise HTTPException(502, f'Unable to create the Bigin {role} in Zoho Books')
    return books_id

def _clean_document_contact_metadata(payload: dict):
    return {
        key: value for key, value in payload.items()
        if key not in {'customer_source', 'customer_record', 'vendor_source', 'vendor_record'}
    }
@app.get('/api/quotes')
async def quotes(search: str = '', date: str = '', page: int = 1):
    query = search.strip()
    try:
        status = zoho.status()
        if not status.get('connected') or not status.get('organization_id'):
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        if not query and not date.strip() and page == 1:
            cached = _recent_cache_rows('quotes')
            if cached:
                return {'quotes': cached, 'info': {'has_more_page': True}, 'page': 1,
                        'has_more': True, 'organization_id': status['organization_id'], 'source': 'supabase'}
        params = {'organization_id':status['organization_id'],'per_page':10,'page':page}
        if query: params['search_text'] = query
        if date.strip(): params['date'] = date.strip()
        payload = await zoho.request('GET','/books/v3/estimates',params=params)
        info = payload.get('page_context', {})
        rows = payload.get('estimates', payload.get('data', []))
        _cache_recent_rows('quotes', rows)
        return {'quotes': rows, 'info': info, 'page': page, 'has_more': bool(info.get('has_more_page')), 'organization_id': status['organization_id'], 'source': 'zoho'}
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to search Zoho Books quotes')
@app.post('/api/quotes')
async def create_quote(payload: dict):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        if not payload.get('customer_id'): raise HTTPException(422, 'Customer is required')
        payload = _clean_document_contact_metadata({**payload, 'customer_id': await _resolve_books_document_contact(payload, 'customer', org)})
        lines = payload.get('line_items') or []
        if not lines or not lines[0].get('item_id') or not lines[0].get('quantity'): raise HTTPException(422, 'At least one item and quantity are required')
        result = await zoho.create_estimate(org, payload)
        quote = (result.get('estimate') or result.get('data') or [None])[0] if isinstance(result.get('data'), list) else result.get('estimate')
        _cache_recent_rows('quotes', [quote] if quote else [])
        return {'quote': quote, 'response': result}
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to create Zoho Books quote')


def _connected_zoho_email_payload(payload: dict | None) -> dict:
    """Keep sender identity under the connected Zoho OAuth account's control."""
    payload = payload or {}
    allowed = {'to_mail_ids', 'cc_mail_ids', 'bcc_mail_ids', 'subject', 'body'}
    cleaned = {key: payload[key] for key in allowed if key in payload}
    recipients = cleaned.get('to_mail_ids')
    if not isinstance(recipients, list) or not recipients or not all(isinstance(item, str) and item.strip() for item in recipients):
        raise HTTPException(422, 'At least one recipient email is required')
    if 'subject' in cleaned and not isinstance(cleaned['subject'], str):
        raise HTTPException(422, 'Email subject must be text')
    if 'body' in cleaned and not isinstance(cleaned['body'], str):
        raise HTTPException(422, 'Email message must be text')
    return cleaned


@app.post('/api/quotes/{estimate_id}/email')
async def email_quote(estimate_id: str, payload: dict | None = None):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        return await zoho.email_estimate(org, estimate_id, _connected_zoho_email_payload(payload))
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to email Zoho Books quote')
@app.get('/api/orders')
async def orders(search: str = '', date: str = '', page: int = 1):
    query = search.strip()
    try:
        status = zoho.status()
        if not status.get('connected') or not status.get('organization_id'):
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        if not query and not date.strip() and page == 1:
            cached = _recent_cache_rows('orders')
            if cached:
                return {'orders': cached, 'info': {'has_more_page': True}, 'page': 1,
                        'has_more': True, 'organization_id': status['organization_id'], 'source': 'supabase'}
        payload = await zoho.search_sales_orders(status['organization_id'], query, date.strip() or None, page)
        info = payload.get('page_context', {})
        rows = payload.get('salesorders', payload.get('data', []))
        _cache_recent_rows('orders', rows)
        return {'orders': rows, 'info': info, 'page': page, 'has_more': bool(info.get('has_more_page')), 'organization_id': status['organization_id'], 'source': 'zoho'}
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to search Zoho Books orders')
@app.post('/api/orders')
async def create_order(payload: dict):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        if not payload.get('customer_id'): raise HTTPException(422, 'Customer is required')
        payload = _clean_document_contact_metadata({**payload, 'customer_id': await _resolve_books_document_contact(payload, 'customer', org)})
        lines = payload.get('line_items') or []
        if not lines or not lines[0].get('item_id') or not lines[0].get('quantity'): raise HTTPException(422, 'At least one item and quantity are required')
        result = await zoho.create_sales_order(org, payload)
        order = result.get('salesorder')
        _cache_recent_rows('orders', [order] if order else [])
        return {'order': order, 'response': result}
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to create Zoho Books sales order')
@app.post('/api/orders/{salesorder_id}/email')
async def email_order(salesorder_id: str, payload: dict | None = None):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        return await zoho.email_sales_order(org, salesorder_id, _connected_zoho_email_payload(payload))
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to email Zoho Books sales order')
@app.get('/api/purchase-orders')
async def purchase_orders(search: str = '', date: str = '', page: int = 1):
    query = search.strip()
    try:
        status = zoho.status()
        if not status.get('connected') or not status.get('organization_id'):
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        if not query and not date.strip() and page == 1:
            cached = _recent_cache_rows('purchase_orders')
            if cached:
                return {'purchase_orders': cached, 'info': {'has_more_page': True}, 'page': 1,
                        'has_more': True, 'organization_id': status['organization_id'], 'source': 'supabase'}
        payload = await zoho.search_purchase_orders(status['organization_id'], query, date.strip() or None, page)
        info = payload.get('page_context', {})
        rows = payload.get('purchaseorders', payload.get('data', []))
        _cache_recent_rows('purchase_orders', rows)
        return {'purchase_orders': rows, 'info': info, 'page': page, 'has_more': bool(info.get('has_more_page')), 'organization_id': status['organization_id'], 'source': 'zoho'}
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to search Zoho Books purchase orders')
@app.post('/api/purchase-orders')
async def create_purchase_order(payload: dict):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org:
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        if not payload.get('vendor_id'):
            raise HTTPException(422, 'Vendor is required')
        payload = _clean_document_contact_metadata({**payload, 'vendor_id': await _resolve_books_document_contact(payload, 'vendor', org)})
        lines = payload.get('line_items') or []
        if not lines or not lines[0].get('item_id') or not lines[0].get('quantity'):
            raise HTTPException(422, 'At least one item and quantity are required')
        result = await zoho.create_purchase_order(org, payload)
        purchase_order = result.get('purchaseorder')
        _cache_recent_rows('purchase_orders', [purchase_order] if purchase_order else [])
        return {'purchase_order': purchase_order, 'response': result}
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to create Zoho Books purchase order')
@app.post('/api/purchase-orders/{purchaseorder_id}/email')
async def email_purchase_order(purchaseorder_id: str, payload: dict | None = None):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        return await zoho.email_purchase_order(org, purchaseorder_id, _connected_zoho_email_payload(payload))
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to email Zoho Books purchase order')
@app.get('/api/invoices')
async def invoices(search: str = '', date: str = '', page: int = 1):
    query = search.strip()
    try:
        status = zoho.status()
        if not status.get('connected') or not status.get('organization_id'):
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        if not query and not date.strip() and page == 1:
            cached = _recent_cache_rows('invoices')
            if cached:
                return {'invoices': cached, 'info': {'has_more_page': True}, 'page': 1,
                        'has_more': True, 'organization_id': status['organization_id'], 'source': 'supabase'}
        payload = await zoho.search_invoices(status['organization_id'], query, date.strip() or None, page)
        info = payload.get('page_context', {})
        rows = payload.get('invoices', payload.get('data', []))
        _cache_recent_rows('invoices', rows)
        return {'invoices': rows, 'info': info, 'page': page, 'has_more': bool(info.get('has_more_page')), 'organization_id': status['organization_id'], 'source': 'zoho'}
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to search Zoho Books invoices')
@app.post('/api/invoices')
async def create_invoice(payload: dict):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org:
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        if not payload.get('customer_id'):
            raise HTTPException(422, 'Customer is required')
        payload = _clean_document_contact_metadata({**payload, 'customer_id': await _resolve_books_document_contact(payload, 'customer', org)})
        lines = payload.get('line_items') or []
        if not lines or not lines[0].get('item_id') or not lines[0].get('quantity'):
            raise HTTPException(422, 'At least one item and quantity are required')
        result = await zoho.create_invoice(org, payload)
        invoice = result.get('invoice')
        _cache_recent_rows('invoices', [invoice] if invoice else [])
        return {'invoice': invoice, 'response': result}
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to create Zoho Books invoice')
@app.post('/api/invoices/{invoice_id}/email')
async def email_invoice(invoice_id: str, payload: dict | None = None):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        return await zoho.email_invoice(org, invoice_id, _connected_zoho_email_payload(payload))
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to email Zoho Books invoice')
@app.get('/api/contacts')
async def contacts(search: str = '', date: str = '', page: int = 1):
    query = search.strip()
    try:
        status = zoho.status()
        if not status.get('connected'):
            raise HTTPException(409, 'Connect Zoho before searching contacts')
        if not query and not date.strip() and page == 1:
            cached = _recent_cache_rows('contacts')
            if cached:
                return {'contacts': cached, 'info': {'more_records': True, 'has_more_page': True},
                        'search': '', 'page': 1, 'has_more': True, 'search_by': 'word',
                        'source': 'supabase'}
        digits = ''.join(ch for ch in query if ch.isdigit())
        if query.isdigit() or (len(digits) == 10 and digits == query):
            search_by = 'phone'
        elif re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', query):
            search_by = 'email'
        else:
            search_by = 'word'
        try:
            payload = await zoho.search_bigin_contacts(query, search_by, page) if query else await zoho.list_bigin_contacts(page)
        except ValueError:
            # Bigin can return an empty/non-JSON body when no records match.
            # Treat that as an empty result so the Books fallback can run.
            payload = {}
        rows = payload.get('data', [])
        date_value = date.strip()
        if date_value:
            rows = [row for row in rows if str(row.get('Created_Time') or row.get('created_time') or '').startswith(date_value)]
        source = 'bigin'
        if query:
            if not rows and status.get('organization_id'):
                books_payload = await zoho.search_books_contacts(status['organization_id'], query, date_value or None, page)
                rows = books_payload.get('contacts', books_payload.get('data', []))
                payload = books_payload
                source = 'books'
            rows = [{**row, '__source': source} for row in rows if isinstance(row, dict)]
        else:
            # Default contact-book view is a combined first page: Bigin records
            # remain first, and Books fills any space up to the app's page size.
            bigin_rows = [{**row, '__source': 'bigin'} for row in rows if isinstance(row, dict)]
            books_rows = []
            books_payload = {}
            if len(bigin_rows) < 40 and status.get('organization_id'):
                books_payload = await zoho.search_books_contacts(status['organization_id'], '', date_value or None, 1, per_page=40)
                books_rows = [{**row, '__source': 'books'} for row in books_payload.get('contacts', books_payload.get('data', [])) if isinstance(row, dict)]
            seen = set()
            rows = []
            for row in [*bigin_rows, *books_rows]:
                key = row.get('id') or row.get('contact_id') or row.get('Full_Name') or row.get('contact_name')
                if key in seen:
                    continue
                if key:
                    seen.add(key)
                rows.append(row)
                if len(rows) == 40:
                    break
            if books_rows:
                payload = books_payload
        _cache_recent_rows('contacts', rows)
        info = payload.get('info', payload.get('page_context', {}))
        return {'contacts': rows[:40], 'info': info, 'search': query, 'page': page, 'has_more': bool(info.get('more_records') or info.get('has_more_page')), 'search_by': search_by, 'source': source}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'Unable to search Bigin contacts')
@app.post('/api/contacts')
@app.post('/api/books/contacts')
async def create_contact(payload: ContactIn):
    mobile = ''.join(ch for ch in payload.mobile if ch.isdigit())
    if len(mobile) != 10 or mobile[0] == '0':
        raise HTTPException(422, 'Mobile number must contain exactly 10 digits and cannot start with 0')
    if payload.email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', payload.email.strip()):
        raise HTTPException(422, 'Enter a valid email address')
    try:
        if not zoho.status().get('connected'):
            raise HTTPException(409, 'Connect Zoho before creating contacts')
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        first_name = payload.first_name.strip(); last_name = payload.last_name.strip()
        primary_person = {
            'salutation': payload.salutation.strip(), 'first_name': first_name, 'last_name': last_name,
            'email': payload.email.strip(), 'phone': payload.phone.strip(), 'mobile': mobile,
            'designation': payload.title.strip(), 'is_primary_contact': True,
        }
        data = {
            'contact_name': payload.contact_name.strip() or ' '.join(part for part in (first_name, last_name) if part),
            'company_name': payload.company_name.strip(), 'contact_type': payload.contact_type or 'customer',
            'customer_sub_type': payload.customer_sub_type or 'business', 'contact_number': payload.contact_number.strip(),
            'ignore_auto_number_generation': payload.ignore_auto_number_generation, 'salutation': payload.salutation.strip(), 'contact_salutation': payload.contact_salutation.strip(),
            'first_name': first_name, 'last_name': last_name, 'website': payload.website.strip(),
            'language_code': payload.language_code or 'en', 'email': payload.email.strip(), 'phone': payload.phone.strip(),
            'mobile': mobile, 'designation': payload.designation.strip() or payload.title.strip(), 'department': payload.department.strip(), 'notes': payload.notes or 'Contact created through Cosmonet AI',
            'is_portal_enabled': payload.is_portal_enabled, 'payment_reminder_enabled': payload.payment_reminder_enabled,
            'contact_persons': [primary_person, *payload.contact_persons], 'custom_fields': payload.custom_fields, 'tags': payload.tags,
        }
        for key in ('payment_terms', 'payment_terms_label', 'credit_limit', 'billing_address', 'shipping_address', 'pan_no', 'gst_no'):
            value = getattr(payload, key)
            if value not in ('', None, [], {}): data[key] = value
        result = await zoho.create_books_contact(org, data)
        updated_contact = result.get('contact') or result.get('contact_person') or ((result.get('contact_persons') or [None])[0])
        _cache_recent_rows('contacts', [{**updated_contact, '__source': 'books'}] if isinstance(updated_contact, dict) else [])
        return {'contact': updated_contact, 'response': result}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'Unable to create Zoho Books contact')
async def _update_books_record(update_fn, record_id: str, payload: dict):
    status = zoho.status(); org = status.get('organization_id')
    if not status.get('connected') or not org:
        raise HTTPException(409, 'Connect Zoho and select a Books organization first')
    if not payload:
        raise HTTPException(422, 'At least one editable field is required')
    try:
        return await update_fn(org, record_id, payload)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'Unable to update Zoho Books record')

def _books_contact_view(payload: dict):
    contact = payload.get('contact') or payload.get('data') or payload
    if isinstance(contact, list):
        contact = contact[0] if contact else {}
    if not isinstance(contact, dict):
        return contact
    people = contact.get('contact_persons') or []
    primary = next((person for person in people if person.get('is_primary_contact')), people[0] if people else {})
    return {
        **contact,
        'email': contact.get('email') or primary.get('email') or '',
        'phone': contact.get('phone') or primary.get('phone') or '',
        'mobile': contact.get('mobile') or primary.get('mobile') or '',
    }

@app.put('/api/products/{item_id}')
async def update_product(item_id: str, payload: dict):
    result = await _update_books_record(zoho.update_books_item, item_id, payload)
    item = result.get('item') if isinstance(result, dict) else None
    _cache_recent_rows('products', [item] if item else [])
    return {'item': result}
@app.delete('/api/products/{item_id}')
async def delete_product(item_id: str): return await _delete_books_record(zoho.delete_books_item, item_id, 'products')

@app.put('/api/quotes/{estimate_id}')
async def update_quote(estimate_id: str, payload: dict):
    result = await _update_books_record(zoho.update_estimate, estimate_id, payload)
    quote = result.get('estimate') if isinstance(result, dict) else None
    _cache_recent_rows('quotes', [quote] if quote else [])
    return {'quote': result}
@app.delete('/api/quotes/{estimate_id}')
async def delete_quote(estimate_id: str): return await _delete_books_record(zoho.delete_estimate, estimate_id, 'quotes')

@app.put('/api/orders/{salesorder_id}')
async def update_order(salesorder_id: str, payload: dict):
    result = await _update_books_record(zoho.update_sales_order, salesorder_id, payload)
    order = result.get('salesorder') if isinstance(result, dict) else None
    _cache_recent_rows('orders', [order] if order else [])
    return {'order': result}
@app.delete('/api/orders/{salesorder_id}')
async def delete_order(salesorder_id: str): return await _delete_books_record(zoho.delete_sales_order, salesorder_id, 'orders')

@app.put('/api/purchase-orders/{purchaseorder_id}')
async def update_purchase_order(purchaseorder_id: str, payload: dict):
    result = await _update_books_record(zoho.update_purchase_order, purchaseorder_id, payload)
    purchase_order = result.get('purchaseorder') if isinstance(result, dict) else None
    _cache_recent_rows('purchase_orders', [purchase_order] if purchase_order else [])
    return {'purchase_order': result}
@app.delete('/api/purchase-orders/{purchaseorder_id}')
async def delete_purchase_order(purchaseorder_id: str): return await _delete_books_record(zoho.delete_purchase_order, purchaseorder_id, 'purchase_orders')

@app.put('/api/invoices/{invoice_id}')
async def update_invoice(invoice_id: str, payload: dict):
    result = await _update_books_record(zoho.update_invoice, invoice_id, payload)
    invoice = result.get('invoice') if isinstance(result, dict) else None
    _cache_recent_rows('invoices', [invoice] if invoice else [])
    return {'invoice': result}
@app.delete('/api/invoices/{invoice_id}')
async def delete_invoice(invoice_id: str): return await _delete_books_record(zoho.delete_invoice, invoice_id, 'invoices')

@app.put('/api/contacts/{contact_id}')
async def update_contact(contact_id: str, payload: dict):
    if not payload:
        raise HTTPException(422, 'At least one editable field is required')
    status = zoho.status(); org = status.get('organization_id')
    if not status.get('connected') or not org:
        raise HTTPException(409, 'Connect Zoho and select a Books organization first')
    try:
        result = await zoho.update_books_contact(org, contact_id, payload)
        contact = result.get('contact') if isinstance(result, dict) else None
        _cache_recent_rows('contacts', [{**contact, '__source': 'books'}] if isinstance(contact, dict) else [])
        return {'contact': result, 'source': 'books'}
    except Exception:
        pass
    try:
        return {'contact': await zoho.update_bigin_contact(contact_id, payload), 'source': 'bigin'}
    except Exception:
        raise HTTPException(502, 'Unable to update contact in Zoho Books or Zoho Bigin')

@app.put('/api/bigin/contacts/{contact_id}')
async def update_bigin_contact(contact_id: str, payload: dict):
    if not payload:
        raise HTTPException(422, 'At least one editable field is required')
    try:
        if not zoho.status().get('connected'):
            raise HTTPException(409, 'Connect Zoho before updating contacts')
        await zoho.update_bigin_contact(contact_id, payload)
        refreshed = await zoho.get_bigin_contact(contact_id)
        contact = (refreshed.get('data') or [refreshed.get('contact')])[0] if isinstance(refreshed.get('data'), list) else (refreshed.get('data') or refreshed.get('contact') or refreshed)
        _cache_recent_rows('contacts', [{**contact, '__source': 'bigin'}] if isinstance(contact, dict) else [])
        return {'contact': contact}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'Unable to update Zoho Bigin contact')

@app.put('/api/books/contacts/{contact_id}')
async def update_books_contact(contact_id: str, payload: dict):
    status = zoho.status(); org = status.get('organization_id')
    if not status.get('connected') or not org:
        raise HTTPException(409, 'Connect Zoho and select a Books organization first')
    if not payload:
        raise HTTPException(422, 'At least one editable field is required')
    try:
        editable_keys = {
            'contact_name', 'company_name', 'contact_type', 'customer_sub_type', 'contact_number',
            'ignore_auto_number_generation', 'salutation', 'contact_salutation', 'first_name', 'last_name', 'website',
            'language_code', 'email', 'phone', 'mobile', 'designation', 'department', 'payment_terms', 'payment_terms_label',
            'credit_limit', 'is_portal_enabled', 'payment_reminder_enabled', 'billing_address',
            'shipping_address', 'contact_persons', 'custom_fields', 'tags', 'pan_no', 'gst_no', 'notes',
        }
        books_payload = {key: value for key, value in payload.items() if key in editable_keys}
        if not books_payload:
            raise HTTPException(422, 'At least one editable field is required')
        await zoho.update_books_contact(org, contact_id, books_payload)
        refreshed = await zoho.get_books_contact(org, contact_id)
        contact = _books_contact_view(refreshed)
        _cache_recent_rows('contacts', [{**contact, '__source': 'books'}] if isinstance(contact, dict) else [])
        return {'contact': contact}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'Unable to update Zoho Books contact')
async def _delete_books_record(delete_fn, record_id: str, dataset: str | None = None):
    status = zoho.status(); org = status.get('organization_id')
    if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
    try:
        result = await delete_fn(org, record_id)
        if dataset:
            _recent_cache_remove(dataset, record_id)
        return result
    except HTTPException: raise
    except Exception: raise HTTPException(502, 'Unable to delete Zoho Books record')
@app.delete('/api/contacts/{contact_id}')
async def delete_contact(contact_id: str):
    status = zoho.status(); org = status.get('organization_id')
    if not status.get('connected') or not org:
        raise HTTPException(409, 'Connect Zoho and select a Books organization first')
    try:
        result = await zoho.delete_books_contact(org, contact_id)
        _recent_cache_remove('contacts', contact_id)
        return {'response': result, 'source': 'books'}
    except Exception:
        pass
    try:
        result = await zoho.delete_bigin_contact(contact_id)
        _recent_cache_remove('contacts', contact_id)
        return {'response': result, 'source': 'bigin'}
    except Exception:
        raise HTTPException(502, 'Unable to delete contact in Zoho Books or Zoho Bigin')

@app.delete('/api/bigin/contacts/{contact_id}')
async def delete_bigin_contact(contact_id: str):
    return await delete_contact(contact_id)

@app.delete('/api/books/contacts/{contact_id}')
async def delete_books_contact(contact_id: str):
    return await _delete_books_record(zoho.delete_books_contact, contact_id, 'contacts')

@app.post('/api/internal/zoho/refresh')
async def zoho_refresh_cron(x_zoho_refresh_secret: str|None = Header(default=None)):
    if not settings.zoho_refresh_cron_secret or not x_zoho_refresh_secret or not secrets.compare_digest(x_zoho_refresh_secret, settings.zoho_refresh_cron_secret):
        raise HTTPException(401, 'Unauthorized')
    try:
        refreshed=await refresh_all()
        return {'ok':True,'refreshed':refreshed}
    except Exception:
        raise HTTPException(502, 'Zoho token refresh failed')
@app.get('/api/chats')
def chats(request: Request):
    try:
        with require_db() as db:
            rows=db.execute(text("select s.id,s.title,s.status,s.updated_at,c.name,c.phone,c.email from chat_sessions s join customers c on c.id=s.customer_id and c.organisation_id=s.organisation_id and c.owner_id=s.member_id where s.organisation_id=cast(:org as uuid) and s.member_id=cast(:member as uuid) and c.deleted_at is null order by s.updated_at desc limit 50"), scope_parameters()).all(); return [row_dict(r) for r in rows]
    except Exception as e: return _db_http_error(e)

@app.get('/api/session-contacts')
async def session_contacts(search: str = ''):
    """Federated recent contacts for Sessions: Bigin, Books, enquiries and saved sessions."""
    query = search.strip().lower()
    contacts = {}
    def add(row, source, external_id=None, session_id=None):
        name = (row.get('name') or row.get('contact_name') or row.get('Full_Name') or row.get('full_name') or row.get('customer_name') or 'Unnamed contact').strip()
        email = (row.get('email') or row.get('Email') or '').strip().lower()
        phone = str(row.get('phone') or row.get('Phone') or row.get('mobile') or row.get('Mobile') or '').strip()
        if query and query not in ' '.join((name, email, phone)).lower(): return
        key = f"phone:{''.join(ch for ch in phone if ch.isdigit())[-10:]}" if phone else f"email:{email}"
        if key == 'email:' and not phone: key = f"{source}:{external_id or name}"
        item = contacts.setdefault(key, {'id': key, 'name': name, 'email': email, 'phone': phone, 'sources': [], 'records': {}})
        if source not in item['sources']: item['sources'].append(source)
        if external_id: item['records'][source] = str(external_id)
        if session_id: item['session_id'] = str(session_id)
        if row.get('capture_state') is not None:
            item['recording_status'] = _recording_status_label(row)
    errors = []
    try:
        with require_db() as db:
            rows = db.execute(text("""select s.id as session_id,s.updated_at,c.name,c.phone,c.email,c.bigin_contact_id,c.books_contact_id,
                r.capture_state,r.upload_state,r.transcription_state,r.english_state,r.final_state
                from chat_sessions s
                join customers c on c.id=s.customer_id and c.organisation_id=s.organisation_id and c.owner_id=s.member_id
                left join conversations r on r.id=s.conversation_id and r.organisation_id=s.organisation_id and r.owner_id=s.member_id and r.deleted_at is null
                where s.organisation_id=cast(:org as uuid) and s.member_id=cast(:member as uuid) and c.deleted_at is null
                order by s.updated_at desc limit 50"""), scope_parameters()).all()
            for raw in rows:
                row = row_dict(raw)
                add(row, 'session', session_id=row['session_id'])
            rows = db.execute(text("""select e.id as enquiry_id,e.customer_name as name,e.phone,e.payload->>'email' as email,e.stage as enquiry_stage,e.bigin_contact_id,e.books_contact_id,
                r.capture_state,r.upload_state,r.transcription_state,r.english_state,r.final_state
                from guruconnect_enquiries e
                left join conversations r on r.id=e.conversation_id and r.organisation_id=e.organisation_id and r.owner_id=e.owner_id and r.deleted_at is null
                where e.organisation_id=cast(:org as uuid) and e.assigned_to=cast(:member as uuid)
                order by e.created_at desc limit 50"""), scope_parameters()).all()
            for raw in rows:
                row = row_dict(raw)
                add(row, 'enquiry', external_id=row['books_contact_id'] or row['bigin_contact_id'])
                key = f"phone:{''.join(ch for ch in str(row['phone'] or '') if ch.isdigit())[-10:]}" if row['phone'] else f"email:{str(row['email'] or '').lower()}"
                if key in contacts:
                    contacts[key]['enquiry_id'] = str(row['enquiry_id'])
                    contacts[key]['enquiry_stage'] = row['enquiry_stage'] or 'new'
    except Exception:
        errors.append('Local contacts unavailable')
    try:
        status = zoho.status()
        books_org = status.get('organization_id')
        if status.get('connected') and books_org:
            books = await zoho.books_customers(books_org)
            for row in (books.get('contacts', books.get('data', [])) or [])[:50]: add(row, 'books', row.get('contact_id') or row.get('id'))
        else: errors.append('Books is not connected')
    except Exception: errors.append('Books contacts unavailable')
    try:
        if zoho.status().get('connected'):
            bigin = await zoho.search_bigin_contacts(query, 'word') if query else await zoho.list_bigin_contacts()
            for row in (bigin.get('data', []) or [])[:50]: add(row, 'bigin', row.get('id'))
    except Exception: errors.append('Bigin contacts unavailable')
    return {'contacts': list(contacts.values())[:50], 'errors': errors}
@app.get('/api/chats/{session_id}/messages')
def messages(session_id: UUID):
    with require_db() as db:
        _session(db, session_id)
        return _session_messages(db, session_id)


@app.post('/api/chat')
async def chat(payload: ChatIn):
    with require_db() as db:
        params = scope_parameters()
        if not payload.session_id:
            if not payload.customer:
                raise HTTPException(422, 'Name plus phone or email is required to start a chat')
            local_phone = ''.join(ch for ch in (payload.customer.phone or '') if ch.isdigit())
            if len(local_phone) != 10 or local_phone[0] == '0':
                raise HTTPException(422, 'Phone number must contain exactly 10 digits and cannot start with 0')
            if payload.customer.email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', payload.customer.email.strip()):
                raise HTTPException(422, 'Enter a valid email address')
            customer = await get_or_create_customer(payload.customer.name,(payload.customer.phone_extension or '+91') + local_phone,payload.customer.email)
            row = db.execute(text("""insert into chat_sessions(customer_id,title,organisation_id,member_id)
              values(cast(:customer as uuid),:title,cast(:org as uuid),cast(:member as uuid)) returning *"""),
              {**params, 'customer': str(customer['id']), 'title': f"{customer['name']} enquiry"}).first()
            session = row_dict(row)
        else:
            session = _session(db, payload.session_id, lock=True)
            row = db.execute(text("""select * from customers where id=cast(:customer as uuid)
              and organisation_id=cast(:org as uuid) and owner_id=cast(:member as uuid) and deleted_at is null"""),
              {**params, 'customer': str(session['customer_id'])}).first()
            if not row:
                raise HTTPException(404, 'Customer not found')
            customer = row_dict(row)
        params['session'] = str(session['id'])
        customer['session_context'] = session.get('context') or {}
        customer['session_id'] = str(session['id'])
        history = _session_messages(db, session['id'], history=True)[::-1]
        existing = db.execute(text("""select e.id from enquiries e join chat_sessions s on s.id=e.session_id
          where s.id=cast(:session as uuid) and s.organisation_id=cast(:org as uuid)
            and s.member_id=cast(:member as uuid) limit 1"""), params).first()
        if not existing:
            db.execute(text("""insert into enquiries(session_id,customer_id,source,status)
              select id,customer_id,'chat','new' from chat_sessions where id=cast(:session as uuid)
                and organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)"""), params)
        db.execute(text("""insert into chat_messages(session_id,role,content)
          select id,'user',:content from chat_sessions where id=cast(:session as uuid)
            and organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)"""), {**params, 'content': payload.message})
        db.commit()
    result = await run_graph(payload.message, customer, history)
    with require_db() as db:
        # Recheck scope after the provider call before storing its result.
        session = _session(db, session['id'], lock=True)
        db.execute(text("""insert into chat_messages(session_id,role,content,intent,metadata)
          select id,'assistant',:content,:intent,cast(:metadata as jsonb) from chat_sessions
          where id=cast(:session as uuid) and organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)"""),
          {**params, 'content': result['answer'], 'intent': result['intent'], 'metadata': json.dumps(result['metadata'])})
        next_context = {**(session.get('context') or {}), 'last_intent':result.get('intent'),'language':result.get('language'),'topic':result.get('topic',result.get('intent')),'focal_entity':result.get('focal_entity'),'pending_action':result.get('pending_action'),'entities':result.get('entities',{}),'tool_results':result.get('tool_results',[])[:8]}
        db.execute(text("""update chat_sessions set last_intent=:intent,language=:language,context=cast(:context as jsonb),updated_at=now()
          where id=cast(:session as uuid) and organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)"""),
          {**params, 'intent':result['intent'],'language':result.get('language'),'context':json.dumps(next_context,default=str)})
        db.execute(text("""update enquiries e set status='processed',extracted_data=cast(:data as jsonb),updated_at=now()
          from chat_sessions s where e.session_id=s.id and s.id=cast(:session as uuid)
            and s.organisation_id=cast(:org as uuid) and s.member_id=cast(:member as uuid)"""),
          {**params, 'data':json.dumps({'intent':result.get('intent'),'language':result.get('language'),'entities':result.get('entities',{}),'tool_results':result.get('tool_results',[])},default=str)})
        db.commit()
    return {'session_id':str(session['id']),'customer':customer,'intent':result['intent'],'answer':result['answer']}


def _sales_document_service(session_id):
    from .sales_documents import DocumentRepository, SalesDocumentService
    context = sales_workspace.get()
    if not context or context.role not in {'owner', 'salesperson'}:
        raise HTTPException(403, 'Sales access is required to create or email documents.')
    with require_db() as db:
        session = _session(db, session_id)
    scope = {**{'organisation_id': str(context.id), 'member_id': str(context.member_id)}, 'session_id': str(session_id)}
    customer = {key: session.get(key) or '' for key in ('name', 'phone', 'email')}
    return SalesDocumentService(DocumentRepository(scope, database_factory=require_db), customer, zoho=zoho, settings=settings)


from .sales_documents import install_sales_document_routes
install_sales_document_routes(app, _sales_document_service)
