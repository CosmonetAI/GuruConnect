from contextlib import asynccontextmanager
import asyncio, json
import secrets
import re
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from .db import require_db, row_dict, now
from .zoho import zoho, refresh_loop
from .chat import get_or_create_customer, run_graph

@asynccontextmanager
async def lifespan(app):
    task=asyncio.create_task(refresh_loop()); yield; task.cancel()
app=FastAPI(title='Guru Group AI Sales API',version='0.1.0',lifespan=lifespan)
from .settings import settings
app.add_middleware(CORSMiddleware,allow_origins=[origin.strip() for origin in settings.cors_origins.split(',') if origin.strip()],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])

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
class ChatIn(BaseModel): message:str=Field(min_length=1); customer:CustomerIn|None=None; session_id:str|None=None
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
    billing_address:dict|None=None
    gst_no:str=''
    notes:str='Contact created through Cosmonet AI'
def _db_http_error(error: Exception):
    detail = 'Database unavailable. Configure Render DATABASE_URL with the Supabase IPv4 pooler URI and redeploy.'
    if isinstance(error, OperationalError):
        raise HTTPException(503, detail) from error
    raise error
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
    except Exception as e: raise HTTPException(400,f'Zoho authorization failed: {e}')
@app.post('/api/zoho/organizations')
async def zoho_organizations():
    try: return await zoho.smoke_test()
    except Exception as e: raise HTTPException(502,f'Unable to load Zoho Books organizations: {e}')
@app.post('/api/zoho/organization')
async def zoho_organization(payload:OrganizationIn):
    try: return zoho.select_organization(payload.organization_id)
    except Exception as e: raise HTTPException(400,f'Unable to select Zoho Books organization: {e}')
@app.post('/api/zoho/smoke-test')
async def zoho_smoke():
    try: return await zoho.smoke_test()
    except Exception as e: raise HTTPException(502,str(e))

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
    except Exception as e: raise HTTPException(502, f'Unable to load Zoho Bigin pipeline layouts: {e}')

@app.get('/api/pipelines')
async def pipeline_records(pipeline_id: str, page: int = 1):
    try:
        with require_db() as db:
            rows = db.execute(text("select record_id,pipeline_id,data,modified_time from zoho_pipeline_records where pipeline_id=:pipeline_id order by modified_time desc limit 50"), {'pipeline_id': pipeline_id}).all()
            last_sync = db.execute(text("select synced_at from zoho_pipeline_sync where tenant_key='guru-group' limit 1")).scalar()
        if page == 1 and rows:
            return {'records': [json.loads(row[2]) if isinstance(row[2], str) else row[2] for row in rows], 'page': 1, 'has_more': len(rows) == 50, 'source': 'supabase', 'last_sync': last_sync.isoformat() if last_sync else None}
        result = await zoho.list_pipeline_records(pipeline_id, page)
        info = result.get('info', {})
        return {'records': result.get('data', []), 'page': page, 'has_more': bool(info.get('more_records')), 'source': 'zoho', 'last_sync': last_sync.isoformat() if last_sync else None}
    except Exception as e: raise HTTPException(502, f'Unable to load pipeline records: {e}')

@app.get('/api/pipelines/search')
async def pipeline_search(word: str):
    cleaned_word = word.replace('\\', '').strip()
    if not cleaned_word:
        return {'records': [], 'info': {'count': 0, 'more_records': False}}
    try:
        result = await zoho.search_pipeline_records(cleaned_word)
        return {'records': result.get('data', []), 'info': result.get('info', {})}
    except Exception as e: raise HTTPException(502, f'Unable to search Zoho Bigin pipelines: {e}')

@app.get('/api/pipelines/record/{record_id}')
async def pipeline_record(record_id: str):
    try:
        result = await zoho.pipeline_record(record_id)
        return {'record': (result.get('data') or [None])[0]}
    except Exception as e: raise HTTPException(502, f'Unable to load pipeline record: {e}')

@app.post('/api/pipelines/sync')
async def sync_pipelines():
    try:
        with require_db() as db:
            last = db.execute(text("select synced_at from zoho_pipeline_sync where tenant_key='guru-group' limit 1")).scalar()
            if last:
                age = (now() - last).total_seconds()
                if age < 3600:
                    return {'synced': False, 'limited': True, 'last_sync': last.isoformat(), 'next_sync_in_seconds': int(3600-age)}
        layouts = _pipeline_layouts(await zoho.pipeline_layout())
        fetched = []
        for layout in layouts:
            result = await zoho.list_pipeline_records(layout['id'], 1)
            fetched.extend(result.get('data', []))
        fetched = sorted(fetched, key=lambda row: row.get('Modified_Time') or '', reverse=True)[:50]
        with require_db() as db:
            for row in fetched:
                pipeline = row.get('Pipeline') if isinstance(row.get('Pipeline'), dict) else {}
                db.execute(text("""insert into zoho_pipeline_records(record_id,pipeline_id,data,modified_time) values(:record_id,:pipeline_id,cast(:data as jsonb),:modified_time) on conflict(record_id) do update set pipeline_id=:pipeline_id,data=cast(:data as jsonb),modified_time=:modified_time"""), {'record_id': str(row.get('id')), 'pipeline_id': str(pipeline.get('id') or row.get('$layout_id', {}).get('id') or ''), 'data': json.dumps(row), 'modified_time': row.get('Modified_Time')})
            db.execute(text("insert into zoho_pipeline_sync(tenant_key,synced_at) values('guru-group',now()) on conflict(tenant_key) do update set synced_at=now()"))
            db.execute(text("delete from zoho_pipeline_records where record_id not in (select record_id from zoho_pipeline_records order by modified_time desc nulls last limit 50)"))
            db.commit()
        return {'synced': True, 'limited': False, 'last_sync': now().isoformat(), 'count': len(fetched)}
    except Exception as e: raise HTTPException(502, f'Unable to sync Zoho Bigin pipelines: {e}')
@app.get('/api/products')
async def products(search: str = '', date: str = '', page: int = 1):
    """Live Books catalogue search; product records are never copied to Supabase."""
    query = search.strip()
    try:
        status = zoho.status()
        if not status.get('connected'):
            raise HTTPException(409, 'Connect Zoho before searching products')
        if not status.get('organization_id'):
            raise HTTPException(409, 'Select a Zoho Books organization before searching products')
        payload = await zoho.search_books_items(status['organization_id'], query, date.strip() or None, page)
        rows = payload.get('items', payload.get('products', payload.get('data', [])))
        items = [row for row in rows if isinstance(row, dict) and str(row.get('status', '')).lower() == 'active']
        context = payload.get('page_context', {})
        return {'items': items[:40], 'search': query, 'page': page, 'has_more': bool(context.get('has_more_page')), 'search_by': 'search_text', 'organization_id': status['organization_id'], 'stored_in_supabase': False}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f'Unable to search Zoho Books items: {e}')
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
        return {'item': result.get('item'), 'response': result}
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to create Zoho Books item: {e}')
@app.get('/api/books/customers')
async def books_customers(search: str = '', date: str = ''):
    query = search.strip()
    if not query: return {'customers': []}
    try:
        status = zoho.status()
        if not status.get('connected') or not status.get('organization_id'):
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        payload = await zoho.search_books_customers(status['organization_id'], query, date.strip() or None)
        return {'customers': payload.get('contacts', payload.get('data', [])), 'info': payload.get('page_context', {})}
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to search Books customers: {e}')
@app.get('/api/books/vendors')
async def books_vendors(search: str = ''):
    query = search.strip()
    if not query: return {'vendors': []}
    try:
        status = zoho.status()
        if not status.get('connected') or not status.get('organization_id'):
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        payload = await zoho.search_books_vendors(status['organization_id'], query)
        return {'vendors': payload.get('contacts', payload.get('data', [])), 'info': payload.get('page_context', {})}
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to search Books vendors: {e}')
@app.get('/api/quotes')
async def quotes(search: str = '', date: str = '', page: int = 1):
    query = search.strip()
    try:
        status = zoho.status()
        if not status.get('connected') or not status.get('organization_id'):
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        params = {'organization_id':status['organization_id'],'per_page':10,'page':page}
        if query: params['search_text'] = query
        if date.strip(): params['date'] = date.strip()
        payload = await zoho.request('GET','/books/v3/estimates',params=params)
        info = payload.get('page_context', {})
        return {'quotes': payload.get('estimates', payload.get('data', [])), 'info': info, 'page': page, 'has_more': bool(info.get('has_more_page')), 'organization_id': status['organization_id']}
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to search Zoho Books quotes: {e}')
@app.post('/api/quotes')
async def create_quote(payload: dict):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        if not payload.get('customer_id'): raise HTTPException(422, 'Customer is required')
        lines = payload.get('line_items') or []
        if not lines or not lines[0].get('item_id') or not lines[0].get('quantity'): raise HTTPException(422, 'At least one item and quantity are required')
        result = await zoho.create_estimate(org, payload)
        return {'quote': (result.get('estimate') or result.get('data') or [None])[0] if isinstance(result.get('data'), list) else result.get('estimate'), 'response': result}
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to create Zoho Books quote: {e}')
@app.post('/api/quotes/{estimate_id}/email')
async def email_quote(estimate_id: str):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        return await zoho.email_estimate(org, estimate_id)
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to email Zoho Books quote: {e}')
@app.get('/api/orders')
async def orders(search: str = '', date: str = '', page: int = 1):
    query = search.strip()
    try:
        status = zoho.status()
        if not status.get('connected') or not status.get('organization_id'):
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        payload = await zoho.search_sales_orders(status['organization_id'], query, date.strip() or None, page)
        info = payload.get('page_context', {})
        return {'orders': payload.get('salesorders', payload.get('data', [])), 'info': info, 'page': page, 'has_more': bool(info.get('has_more_page')), 'organization_id': status['organization_id']}
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to search Zoho Books orders: {e}')
@app.post('/api/orders')
async def create_order(payload: dict):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        if not payload.get('customer_id'): raise HTTPException(422, 'Customer is required')
        lines = payload.get('line_items') or []
        if not lines or not lines[0].get('item_id') or not lines[0].get('quantity'): raise HTTPException(422, 'At least one item and quantity are required')
        result = await zoho.create_sales_order(org, payload)
        return {'order': result.get('salesorder'), 'response': result}
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to create Zoho Books sales order: {e}')
@app.post('/api/orders/{salesorder_id}/email')
async def email_order(salesorder_id: str):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        return await zoho.email_sales_order(org, salesorder_id)
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to email Zoho Books sales order: {e}')
@app.get('/api/purchase-orders')
async def purchase_orders(search: str = '', date: str = '', page: int = 1):
    query = search.strip()
    try:
        status = zoho.status()
        if not status.get('connected') or not status.get('organization_id'):
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        payload = await zoho.search_purchase_orders(status['organization_id'], query, date.strip() or None, page)
        info = payload.get('page_context', {})
        return {'purchase_orders': payload.get('purchaseorders', payload.get('data', [])), 'info': info, 'page': page, 'has_more': bool(info.get('has_more_page')), 'organization_id': status['organization_id']}
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to search Zoho Books purchase orders: {e}')
@app.post('/api/purchase-orders')
async def create_purchase_order(payload: dict):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org:
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        if not payload.get('vendor_id'):
            raise HTTPException(422, 'Vendor is required')
        lines = payload.get('line_items') or []
        if not lines or not lines[0].get('item_id') or not lines[0].get('quantity'):
            raise HTTPException(422, 'At least one item and quantity are required')
        result = await zoho.create_purchase_order(org, payload)
        return {'purchase_order': result.get('purchaseorder'), 'response': result}
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to create Zoho Books purchase order: {e}')
@app.post('/api/purchase-orders/{purchaseorder_id}/email')
async def email_purchase_order(purchaseorder_id: str):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        return await zoho.email_purchase_order(org, purchaseorder_id)
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to email Zoho Books purchase order: {e}')
@app.get('/api/invoices')
async def invoices(search: str = '', date: str = '', page: int = 1):
    query = search.strip()
    try:
        status = zoho.status()
        if not status.get('connected') or not status.get('organization_id'):
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        payload = await zoho.search_invoices(status['organization_id'], query, date.strip() or None, page)
        info = payload.get('page_context', {})
        return {'invoices': payload.get('invoices', payload.get('data', [])), 'info': info, 'page': page, 'has_more': bool(info.get('has_more_page')), 'organization_id': status['organization_id']}
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to search Zoho Books invoices: {e}')
@app.post('/api/invoices')
async def create_invoice(payload: dict):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org:
            raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        if not payload.get('customer_id'):
            raise HTTPException(422, 'Customer is required')
        lines = payload.get('line_items') or []
        if not lines or not lines[0].get('item_id') or not lines[0].get('quantity'):
            raise HTTPException(422, 'At least one item and quantity are required')
        result = await zoho.create_invoice(org, payload)
        return {'invoice': result.get('invoice'), 'response': result}
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to create Zoho Books invoice: {e}')
@app.post('/api/invoices/{invoice_id}/email')
async def email_invoice(invoice_id: str):
    try:
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        return await zoho.email_invoice(org, invoice_id)
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to email Zoho Books invoice: {e}')
@app.get('/api/contacts')
async def contacts(search: str = '', date: str = '', page: int = 1):
    query = search.strip()
    try:
        status = zoho.status()
        if not status.get('connected'):
            raise HTTPException(409, 'Connect Zoho before searching contacts')
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
        info = payload.get('info', payload.get('page_context', {}))
        return {'contacts': rows[:40], 'info': info, 'search': query, 'page': page, 'has_more': bool(info.get('more_records') or info.get('has_more_page')), 'search_by': search_by, 'source': source}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f'Unable to search Bigin contacts: {e}')
@app.post('/api/contacts')
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
        data = {'contact_name': payload.contact_name.strip() or ' '.join(part for part in (first_name, last_name) if part), 'contact_type': payload.contact_type or 'customer', 'customer_sub_type': payload.customer_sub_type or 'business', 'contact_persons': [{'first_name': first_name, 'last_name': last_name, 'mobile': mobile, 'is_primary_contact': True}], 'notes': payload.notes or 'Contact created through Cosmonet AI'}
        if payload.company_name.strip(): data['company_name'] = payload.company_name.strip()
        if payload.email.strip(): data['contact_persons'][0]['email'] = payload.email.strip()
        if payload.title.strip(): data['contact_persons'][0]['designation'] = payload.title.strip()
        if payload.billing_address: data['billing_address'] = payload.billing_address
        if payload.gst_no.strip(): data['gst_no'] = payload.gst_no.strip()
        result = await zoho.create_books_contact(org, data)
        updated_contact = result.get('contact') or result.get('contact_person') or ((result.get('contact_persons') or [None])[0])
        return {'contact': updated_contact, 'response': result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f'Unable to create Zoho Books contact: {e}')
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
    except Exception as e:
        raise HTTPException(502, f'Unable to update Zoho Books record: {e}')

@app.put('/api/products/{item_id}')
async def update_product(item_id: str, payload: dict):
    return {'item': await _update_books_record(zoho.update_books_item, item_id, payload)}
@app.delete('/api/products/{item_id}')
async def delete_product(item_id: str): return await _delete_books_record(zoho.delete_books_item, item_id)

@app.put('/api/quotes/{estimate_id}')
async def update_quote(estimate_id: str, payload: dict):
    return {'quote': await _update_books_record(zoho.update_estimate, estimate_id, payload)}
@app.delete('/api/quotes/{estimate_id}')
async def delete_quote(estimate_id: str): return await _delete_books_record(zoho.delete_estimate, estimate_id)

@app.put('/api/orders/{salesorder_id}')
async def update_order(salesorder_id: str, payload: dict):
    return {'order': await _update_books_record(zoho.update_sales_order, salesorder_id, payload)}
@app.delete('/api/orders/{salesorder_id}')
async def delete_order(salesorder_id: str): return await _delete_books_record(zoho.delete_sales_order, salesorder_id)

@app.put('/api/purchase-orders/{purchaseorder_id}')
async def update_purchase_order(purchaseorder_id: str, payload: dict):
    return {'purchase_order': await _update_books_record(zoho.update_purchase_order, purchaseorder_id, payload)}
@app.delete('/api/purchase-orders/{purchaseorder_id}')
async def delete_purchase_order(purchaseorder_id: str): return await _delete_books_record(zoho.delete_purchase_order, purchaseorder_id)

@app.put('/api/invoices/{invoice_id}')
async def update_invoice(invoice_id: str, payload: dict):
    return {'invoice': await _update_books_record(zoho.update_invoice, invoice_id, payload)}
@app.delete('/api/invoices/{invoice_id}')
async def delete_invoice(invoice_id: str): return await _delete_books_record(zoho.delete_invoice, invoice_id)

@app.put('/api/contacts/{contact_id}')
async def update_contact(contact_id: str, payload: dict):
    if not payload:
        raise HTTPException(422, 'At least one editable field is required')
    try:
        if not zoho.status().get('connected'):
            raise HTTPException(409, 'Connect Zoho before updating contacts')
        status = zoho.status(); org = status.get('organization_id')
        if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
        person_id = payload.pop('contact_person_id', None)
        person_fields = {'first_name', 'last_name', 'email', 'phone', 'mobile', 'designation', 'department', 'skype', 'fax'}
        person_payload = {key: value for key, value in payload.items() if key.lower() in person_fields}
        aliases = {'First_Name': 'first_name', 'Last_Name': 'last_name', 'Email': 'email', 'Phone': 'phone', 'Mobile': 'mobile', 'Title': 'designation', 'Description': 'description'}
        for key, value in list(payload.items()):
            if key in aliases:
                person_payload[aliases[key]] = value
        if person_id and person_payload:
            result = await zoho.update_books_contact_person(org, person_id, person_payload)
        else:
            result = await zoho.update_books_contact(org, contact_id, payload)
        return {'contact': result.get('contact'), 'response': result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f'Unable to update Zoho Books contact: {e}')

@app.put('/api/books/contacts/{contact_id}')
async def update_books_contact(contact_id: str, payload: dict):
    return {'contact': await _update_books_record(zoho.update_books_contact, contact_id, payload)}
async def _delete_books_record(delete_fn, record_id: str):
    status = zoho.status(); org = status.get('organization_id')
    if not status.get('connected') or not org: raise HTTPException(409, 'Connect Zoho and select a Books organization first')
    try: return await delete_fn(org, record_id)
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, f'Unable to delete Zoho Books record: {e}')
@app.delete('/api/contacts/{contact_id}')
async def delete_contact(contact_id: str): return await _delete_books_record(zoho.delete_books_contact, contact_id)

@app.post('/api/internal/zoho/refresh')
async def zoho_refresh_cron(x_zoho_refresh_secret: str|None = Header(default=None)):
    if not settings.zoho_refresh_cron_secret or not x_zoho_refresh_secret or not secrets.compare_digest(x_zoho_refresh_secret, settings.zoho_refresh_cron_secret):
        raise HTTPException(401, 'Unauthorized')
    try:
        refreshed=await zoho.refresh(force=False)
        return {'ok':True,'refreshed':refreshed}
    except Exception as e:
        raise HTTPException(502, f'Zoho token refresh failed: {e}')
@app.get('/api/chats')
def chats():
    try:
        with require_db() as db:
            rows=db.execute(text("select s.id,s.title,s.status,s.updated_at,c.name,c.phone,c.email from chat_sessions s join customers c on c.id=s.customer_id order by s.updated_at desc")).all(); return [row_dict(r) for r in rows]
    except Exception as e: return _db_http_error(e)
@app.get('/api/chats/{session_id}/messages')
def messages(session_id:str):
    try:
        with require_db() as db:
            rows=db.execute(text("select id,role,content,intent,metadata,created_at from chat_messages where session_id=:id order by created_at"),{'id':session_id}).all(); return [row_dict(r) for r in rows]
    except Exception as e: return _db_http_error(e)
@app.post('/api/chat')
async def chat(payload:ChatIn):
    try:
        with require_db() as db:
            if not payload.session_id:
                if not payload.customer: raise HTTPException(422,'Name plus phone or email is required to start a chat')
                local_phone=''.join(ch for ch in (payload.customer.phone or '') if ch.isdigit())
                if len(local_phone) != 10 or local_phone[0] == '0':
                    raise HTTPException(422,'Phone number must contain exactly 10 digits and cannot start with 0')
                if payload.customer.email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', payload.customer.email.strip()):
                    raise HTTPException(422,'Enter a valid email address')
                customer=await get_or_create_customer(payload.customer.name,(payload.customer.phone_extension or '+91') + local_phone,payload.customer.email)
                session=db.execute(text("insert into chat_sessions(customer_id,title) values(:customer,:title) returning *"),{'customer':customer['id'],'title':f"{customer['name']} enquiry"}).first(); db.commit(); session=row_dict(session)
            else:
                session=row_dict(db.execute(text("select * from chat_sessions where id=:id"),{'id':payload.session_id}).first())
                if not session: raise HTTPException(404,'Session not found')
                customer=row_dict(db.execute(text("select * from customers where id=:id"),{'id':session['customer_id']}).first())
                customer['session_context']=session.get('context') or {}
            history=[row_dict(r) for r in db.execute(text("select role,content from chat_messages where session_id=:id order by created_at desc limit 10"),{'id':session['id']}).all()][::-1]
            customer['session_id']=str(session['id'])
            existing_enquiry=db.execute(text("select id from enquiries where session_id=:id limit 1"),{'id':session['id']}).first()
            if not existing_enquiry:
                db.execute(text("insert into enquiries(session_id,customer_id,source,status) values(:session,:customer,'chat','new')"),{'session':session['id'],'customer':customer['id']})
            db.execute(text("insert into chat_messages(session_id,role,content) values(:id,'user',:content)"),{'id':session['id'],'content':payload.message}); db.commit()
        result=await run_graph(payload.message,customer,history)
        with require_db() as db:
            db.execute(text("insert into chat_messages(session_id,role,content,intent,metadata) values(:id,'assistant',:content,:intent,cast(:metadata as jsonb))"),{'id':session['id'],'content':result['answer'],'intent':result['intent'],'metadata':json.dumps(result['metadata'])})
            next_context={'last_intent':result.get('intent'),'language':result.get('language'),'topic':result.get('topic', result.get('intent')),'focal_entity':result.get('focal_entity'),'pending_action':result.get('pending_action'),'entities':result.get('entities',{}),'tool_results':result.get('tool_results',[])[:8]}
            db.execute(text("update chat_sessions set last_intent=:intent,language=:language,context=cast(:context as jsonb),updated_at=now() where id=:id"),{'intent':result['intent'],'language':result.get('language'),'context':json.dumps(next_context,default=str),'id':session['id']})
            db.execute(text("update enquiries set status='processed',extracted_data=cast(:data as jsonb),updated_at=now() where session_id=:id"),{'data':json.dumps({'intent':result.get('intent'),'language':result.get('language'),'entities':result.get('entities',{}),'tool_results':result.get('tool_results',[])},default=str),'id':session['id']}); db.commit()
        return {'session_id':str(session['id']),'customer':customer,'intent':result['intent'],'answer':result['answer']}
    except HTTPException: raise
    except Exception as e: raise HTTPException(500,str(e))
