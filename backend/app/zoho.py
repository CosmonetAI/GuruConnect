from datetime import timedelta
import asyncio, httpx
from contextvars import ContextVar
from sqlalchemy import text
from .db import require_db, row_dict, now
from .settings import settings

_organisation_id: ContextVar[str | None] = ContextVar('zoho_organisation_id', default=None)
_member_id: ContextVar[str | None] = ContextVar('zoho_member_id', default=None)

def set_organisation_id(value: str | None):
    _organisation_id.set(value)

def set_member_id(value: str | None):
    _member_id.set(value)

def get_member_id():
    return _member_id.get()

def get_organisation_id():
    return _organisation_id.get()

class ZohoClient:
    def _tenant_key(self):
        org = _organisation_id.get()
        member = _member_id.get()
        return f"guru-group:{org}:{member}" if org and member else 'guru-group'
    def _member(self):
        org = _organisation_id.get(); member = _member_id.get()
        if not org or not member:
            raise RuntimeError('A signed-in workspace member is required for Zoho access')
        with require_db() as db:
            row = db.execute(text("""select user_id, role, status from members
              where organisation_id=cast(:org as uuid) and user_id=cast(:member as uuid)
              limit 1"""), {'org': org, 'member': member}).first()
        if not row or row.status != 'active' or row.role not in ('owner', 'salesperson'):
            raise PermissionError('Receptionists cannot connect to or access Zoho accounts')
        return row_dict(row)
    def _connection(self):
        self._member()
        with require_db() as db:
            row = db.execute(text("""select * from zoho_connections
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)
              limit 1"""), {'org': _organisation_id.get(), 'member': _member_id.get()}).first()
            return row_dict(row) if row else None
    def status(self):
        c=self._connection()
        if not c: return {'status':'not_connected','connected':False}
        return {'status':c['status'],'connected':bool(c.get('refresh_token')),'expires_at':c.get('expires_at'),'organization_id':c.get('organization_id'),'last_error':c.get('last_error')}
    def configuration(self):
        c=self._connection() or {}
        return {'configured':bool(c.get('client_id') and c.get('client_secret')),'client_id':c.get('client_id'),'client_secret_configured':bool(c.get('client_secret'))}
    async def save_configuration(self, client_id, client_secret):
        self._member()
        with require_db() as db:
            existing=db.execute(text("select client_secret from zoho_connections where tenant_key=:tenant limit 1"), {'tenant': self._tenant_key()}).first()
            if not client_secret:
                client_secret=existing[0] if existing and existing[0] else None
            if not client_secret:
                raise ValueError('Client Secret is required for the first configuration.')
            db.execute(text("""insert into zoho_connections(tenant_key,organisation_id,member_id,client_id,client_secret,status)
              values(:tenant,cast(:org as uuid),cast(:member as uuid),:client_id,:client_secret,'not_connected')
              on conflict (organisation_id,member_id) where organisation_id is not null and member_id is not null
              do update set tenant_key=excluded.tenant_key,
                client_id=excluded.client_id,client_secret=excluded.client_secret,updated_at=now()"""),
              {'tenant':self._tenant_key(),'org':_organisation_id.get(),'member':_member_id.get(),'client_id':client_id,'client_secret':client_secret}); db.commit()
        return self.configuration()
    async def exchange_code(self, code):
        c=self._connection() or {}
        if not c.get('client_id') or not c.get('client_secret'): raise RuntimeError('Zoho client configuration is missing. Complete the frontend setup first.')
        data={'grant_type':'authorization_code','client_id':c['client_id'],'client_secret':c['client_secret'],'code':code}
        async with httpx.AsyncClient(timeout=25) as client: response=await client.post(f'{settings.zoho_accounts_url}/oauth/v2/token',data=data)
        response.raise_for_status(); payload=response.json(); await self._save_tokens(payload); return self.status()
    async def refresh(self, force=False):
        c=self._connection()
        if not c or not c.get('refresh_token'): return False
        if not force and c.get('expires_at') and c['expires_at'] > now()+timedelta(minutes=10): return True
        if not c.get('client_id') or not c.get('client_secret'): raise RuntimeError('Zoho client configuration is missing. Complete the frontend setup first.')
        data={'grant_type':'refresh_token','client_id':c['client_id'],'client_secret':c['client_secret'],'refresh_token':c['refresh_token']}
        async with httpx.AsyncClient(timeout=25) as client: response=await client.post(f'{settings.zoho_accounts_url}/oauth/v2/token',data=data)
        response.raise_for_status(); await self._save_tokens({**response.json(),'refresh_token':c['refresh_token']}); return True
    async def _save_tokens(self,payload):
        self._member()
        with require_db() as db:
            db.execute(text("""update zoho_connections set api_domain=:domain,access_token=:access,
              refresh_token=:refresh,expires_at=:expires,scope=:scope,status='connected',last_error=null,updated_at=now()
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)"""),
              {'org':_organisation_id.get(),'member':_member_id.get(),'domain':payload.get('api_domain',settings.zoho_api_domain),'access':payload['access_token'],'refresh':payload.get('refresh_token'),'expires':now()+timedelta(seconds=int(payload.get('expires_in',3600))),'scope':payload.get('scope','')}); db.commit()
    async def request(self,method,path,*,params=None,json=None,form=None):
        await self.refresh(); c=self._connection()
        if not c or not c.get('access_token'): raise RuntimeError('Zoho is not connected')
        headers={'Authorization':f"Zoho-oauthtoken {c['access_token']}"}
        async with httpx.AsyncClient(timeout=30) as client:
            response=await client.request(method,f"{c.get('api_domain') or settings.zoho_api_domain}{path}",headers=headers,params=params,json=json,data=form)
            if response.status_code==401:
                await self.refresh(force=True); c=self._connection(); headers['Authorization']=f"Zoho-oauthtoken {c['access_token']}"
                response=await client.request(method,f"{c.get('api_domain') or settings.zoho_api_domain}{path}",headers=headers,params=params,json=json,data=form)
        response.raise_for_status(); return response.json() if response.content else {'ok': True}
    async def smoke_test(self):
        result=await self.request('GET','/books/v3/organizations'); orgs=result.get('organizations',result.get('data',[]));
        current=self._connection() or {}; selected=current.get('organization_id')
        ids={str(org.get('organization_id')) for org in orgs if org.get('organization_id')}
        if len(orgs)==1:
            selected=orgs[0].get('organization_id'); self.select_organization(selected)
        elif selected not in ids:
            selected=None
        return {'organizations':orgs,'organization_id':selected}
    def select_organization(self, organization_id):
        self._member()
        with require_db() as db:
            db.execute(text("""update zoho_connections set organization_id=:selected,updated_at=now()
              where organisation_id=cast(:org as uuid) and member_id=cast(:member as uuid)"""),
              {'selected':organization_id,'org':_organisation_id.get(),'member':_member_id.get()}); db.commit()
        return self.status()
    async def list_bigin_contacts(self, page=1):
        fields = 'First_Name,Last_Name,Full_Name,Email,Phone,Mobile,Home_Phone,Title,Account_Name,Owner,Description,Mailing_Street,Mailing_City,Mailing_State,Mailing_Country,Mailing_Zip,Created_Time,Modified_Time,Last_Activity_Time,Tag,Email_Opt_Out'
        return await self.request('GET','/bigin/v2/Contacts',params={'fields': fields, 'per_page':50, 'page':page})
    async def search_bigin_contacts(self, query, search_by, page=1):
        fields = 'First_Name,Last_Name,Full_Name,Email,Phone,Mobile,Home_Phone,Title,Account_Name,Owner,Description,Mailing_Street,Mailing_City,Mailing_State,Mailing_Country,Mailing_Zip,Created_Time,Modified_Time,Last_Activity_Time,Tag,Email_Opt_Out'
        return await self.request('GET','/bigin/v2/Contacts/search',params={search_by: query, 'fields': fields, 'per_page': 50, 'page': page})
    async def search_books_contacts(self, org, query, date=None, page=1, per_page=10):
        params = {'organization_id': org, 'per_page': per_page, 'page': page}
        if query: params['search_text'] = query
        if date: params['date'] = date
        return await self.request('GET','/books/v3/contacts',params=params)
    async def create_books_contact(self, org, data): return await self.request('POST','/books/v3/contacts',params={'organization_id':org},json=data)
    async def ensure_books_contact(self, org, *, name, email=None, phone=None):
        """Find a matching Books customer before creating one, so walk-ins are idempotent."""
        email = (email or '').strip().lower()
        phone = ''.join(ch for ch in (phone or '') if ch.isdigit())
        for query in (email, phone):
            if not query:
                continue
            payload = await self.search_books_customers(org, query)
            rows = payload.get('contacts', payload.get('data', []))
            for row in rows:
                row_email = (row.get('email') or '').strip().lower()
                row_phone = ''.join(ch for ch in str(row.get('phone') or row.get('mobile') or '') if ch.isdigit())
                if (email and row_email == email) or (phone and row_phone and row_phone[-10:] == phone[-10:]):
                    return row
        display_name = (name or email or phone or 'Walk-in customer').strip()
        parts = display_name.split(maxsplit=1)
        payload = {
            'contact_name': display_name,
            'contact_type': 'customer',
            'contact_persons': [{
                'first_name': parts[0], 'last_name': parts[1] if len(parts) > 1 else '',
                'email': email, 'mobile': phone or None, 'is_primary_contact': True,
            }],
        }
        result = await self.create_books_contact(org, payload)
        return result.get('contact') or (result.get('data') or [None])[0] or result
    async def update_books_contact(self, org, contact_id, data): return await self.request('PUT',f'/books/v3/contacts/{contact_id}',params={'organization_id':org},json=data)
    async def update_books_contact_person(self, org, contact_person_id, data): return await self.request('PUT',f'/books/v3/contacts/contactpersons/{contact_person_id}',params={'organization_id':org},json=data)
    async def delete_books_contact(self, org, contact_id): return await self.request('DELETE',f'/books/v3/contacts/{contact_id}',params={'organization_id':org})
    async def update_bigin_contact(self, contact_id, data): return await self.request('PUT',f'/bigin/v2/Contacts/{contact_id}',json={'data':[{'id': contact_id, **data}]})
    async def delete_bigin_contact(self, contact_id): return await self.request('DELETE',f'/bigin/v2/Contacts/{contact_id}')
    async def list_pipelines(self): return await self.request('GET','/bigin/v2/Pipelines',params={'per_page':200,'page':1})
    async def create_pipeline(self,data): return await self.request('POST','/bigin/v2/Pipelines',json={'data':[data]})
    async def update_pipeline(self, record_id, data): return await self.request('PUT',f'/bigin/v2/Pipelines/{record_id}',json={'data':[{'id': record_id, **data}]})
    async def delete_pipeline(self, record_id): return await self.request('DELETE',f'/bigin/v2/Pipelines/{record_id}')
    async def pipeline_layout(self): return await self.request('GET','/bigin/v2/settings/layouts',params={'module':'Pipelines'})
    async def list_pipeline_records(self, pipeline_id, page=1):
        fields = 'id,Deal_Name,Stage,Amount,Closing_Date,Contact_Name,Account_Name,Owner,Sub_Pipeline,Pipeline,Description,Created_Time,Modified_Time,Created_By,Modified_By,Last_Activity_Time,Tag,Associated_Products,Secondary_Contacts'
        return await self.request('GET','/bigin/v2/Pipelines',params={'pipeline_id': pipeline_id, 'fields': fields, 'per_page': 200, 'page': page, 'sort_by': 'Modified_Time', 'sort_order': 'desc'})
    async def search_pipeline_records(self, query): return await self.request('GET','/bigin/v2/Pipelines/search',params={'word': query})
    async def pipeline_record(self, record_id): return await self.request('GET',f'/bigin/v2/Pipelines/{record_id}')
    async def list_accounts(self): return await self.request('GET','/bigin/v2/Accounts',params={'per_page':200,'page':1})
    async def list_bigin_products(self): return await self.request('GET','/bigin/v2/Products',params={'per_page':200,'page':1})
    async def list_tasks(self): return await self.request('GET','/bigin/v2/Tasks',params={'per_page':200,'page':1})
    async def create_note(self,data): return await self.request('POST','/bigin/v2/Notes',json={'data':[data]})
    async def list_notes(self): return await self.request('GET','/bigin/v2/Notes',params={'per_page':200,'page':1})
    async def update_note(self,note_id,data): return await self.request('PUT',f'/bigin/v2/Notes/{note_id}',json={'data':[data]})
    async def create_task(self,data): return await self.request('POST','/bigin/v2/Tasks',json={'data':[data]})
    async def update_task(self,task_id,data): return await self.request('PUT',f'/bigin/v2/Tasks/{task_id}',json={'data':[data]})
    async def list_events(self): return await self.request('GET','/bigin/v2/Events',params={'per_page':200,'page':1})
    async def list_calls(self): return await self.request('GET','/bigin/v2/Calls',params={'per_page':200,'page':1})
    async def books_items(self,org,filters=None):
        params={'organization_id':org,'status':'active','per_page':200,'page':1}
        for key in ('name_contains','description_contains','search_text','rate_less_equals'):
            value=(filters or {}).get(key)
            if value not in (None,''): params[key]=value
        return await self.request('GET','/books/v3/items',params=params)
    async def search_books_items(self, org, query='', date=None, page=1):
        params = {'organization_id': org, 'per_page': 40, 'page': page}
        if query: params['search_text'] = query
        if date: params['date'] = date
        return await self.request('GET','/books/v3/items',params=params)
    async def books_item(self,org,item_id): return await self.request('GET',f'/books/v3/items/{item_id}',params={'organization_id':org})
    async def update_books_item(self,org,item_id,data): return await self.request('PUT',f'/books/v3/items/{item_id}',params={'organization_id':org},json=data)
    async def create_books_item(self,org,data): return await self.request('POST','/books/v3/items',params={'organization_id':org},json=data)
    async def delete_books_item(self,org,item_id): return await self.request('DELETE',f'/books/v3/items/{item_id}',params={'organization_id':org})
    async def books_item_masters(self,org,filters=None):
        params={'organization_id':org,'status':'active','per_page':200,'page':1}; params.update({k:v for k,v in (filters or {}).items() if k in {'name_contains','search_text'}}); return await self.request('GET','/books/v3/itemmasters',params=params)
    async def books_item_variants(self,org,filters=None):
        params={'organization_id':org,'status':'active','per_page':200,'page':1}; params.update({k:v for k,v in (filters or {}).items() if k in {'search_text'}}); return await self.request('GET','/books/v3/itemvariants',params=params)
    async def books_customers(self,org): return await self.request('GET','/books/v3/contacts',params={'organization_id':org,'per_page':200,'page':1})
    async def search_books_customers(self,org,query,date=None, page=1):
        params={'organization_id':org,'per_page':10,'page':page,'customer_type':'customer'}
        if query: params['search_text'] = query
        if date: params['date'] = date
        return await self.request('GET','/books/v3/contacts',params=params)
    async def search_books_vendors(self, org, query, page=1):
        params={'organization_id':org,'per_page':10,'page':page,'customer_type':'vendor','search_text':query}
        return await self.request('GET','/books/v3/contacts',params=params)
    async def books_estimates(self,org): return await self.request('GET','/books/v3/estimates',params={'organization_id':org,'per_page':200,'page':1})
    async def books_estimate(self,org,estimate_id): return await self.request('GET',f'/books/v3/estimates/{estimate_id}',params={'organization_id':org})
    async def create_estimate(self,org,data): return await self.request('POST','/books/v3/estimates',params={'organization_id':org},json=data)
    async def update_estimate(self,org,estimate_id,data): return await self.request('PUT',f'/books/v3/estimates/{estimate_id}',params={'organization_id':org},json=data)
    async def delete_estimate(self,org,estimate_id): return await self.request('DELETE',f'/books/v3/estimates/{estimate_id}',params={'organization_id':org})
    async def accept_estimate(self,org,estimate_id): return await self.request('POST',f'/books/v3/estimates/{estimate_id}/status/accepted',params={'organization_id':org},json={})
    async def email_estimate(self,org,estimate_id): return await self.request('POST',f'/books/v3/estimates/{estimate_id}/email',params={'organization_id':org},json={})
    async def list_sales_orders(self,org): return await self.request('GET','/books/v3/salesorders',params={'organization_id':org,'per_page':200,'page':1})
    async def search_sales_orders(self,org,query='',date=None, page=1):
        params={'organization_id':org,'per_page':10,'page':page}
        if query: params['search_text'] = query
        if date: params['date'] = date
        return await self.request('GET','/books/v3/salesorders',params=params)
    async def search_purchase_orders(self,org,query='',date=None, page=1):
        params={'organization_id':org,'per_page':10,'page':page}
        if query: params['search_text'] = query
        if date: params['date'] = date
        return await self.request('GET','/books/v3/purchaseorders',params=params)
    async def search_invoices(self,org,query='',date=None, page=1):
        params={'organization_id':org,'per_page':10,'page':page}
        if query: params['search_text'] = query
        if date: params['date'] = date
        return await self.request('GET','/books/v3/invoices',params=params)
    async def create_sales_order(self,org,data): return await self.request('POST','/books/v3/salesorders',params={'organization_id':org},json=data)
    async def update_sales_order(self,org,order_id,data): return await self.request('PUT',f'/books/v3/salesorders/{order_id}',params={'organization_id':org},json=data)
    async def delete_sales_order(self,org,order_id): return await self.request('DELETE',f'/books/v3/salesorders/{order_id}',params={'organization_id':org})
    async def email_sales_order(self,org,order_id): return await self.request('POST',f'/books/v3/salesorders/{order_id}/email',params={'organization_id':org},json={})
    async def create_purchase_order(self,org,data): return await self.request('POST','/books/v3/purchaseorders',params={'organization_id':org},json=data)
    async def update_purchase_order(self,org,order_id,data): return await self.request('PUT',f'/books/v3/purchaseorders/{order_id}',params={'organization_id':org},json=data)
    async def delete_purchase_order(self,org,order_id): return await self.request('DELETE',f'/books/v3/purchaseorders/{order_id}',params={'organization_id':org})
    async def email_purchase_order(self,org,order_id): return await self.request('POST',f'/books/v3/purchaseorders/{order_id}/email',params={'organization_id':org},json={})
    async def create_invoice(self,org,data): return await self.request('POST','/books/v3/invoices',params={'organization_id':org},json=data)
    async def update_invoice(self,org,invoice_id,data): return await self.request('PUT',f'/books/v3/invoices/{invoice_id}',params={'organization_id':org},json=data)
    async def delete_invoice(self,org,invoice_id): return await self.request('DELETE',f'/books/v3/invoices/{invoice_id}',params={'organization_id':org})
    async def email_invoice(self,org,invoice_id): return await self.request('POST',f'/books/v3/invoices/{invoice_id}/email',params={'organization_id':org},json={})
    async def list_invoices(self,org): return await self.request('GET','/books/v3/invoices',params={'organization_id':org,'per_page':200,'page':1})

zoho=ZohoClient()
async def refresh_all():
    with require_db() as db:
        rows = [row_dict(row) for row in db.execute(text("select organisation_id,member_id from zoho_connections where refresh_token is not null")).all()]
    for row in rows:
        set_organisation_id(str(row['organisation_id']))
        set_member_id(str(row['member_id']))
        try: await zoho.refresh(force=False)
        except Exception: pass

async def refresh_loop():
    while True:
        try: await refresh_all()
        except Exception: pass
        await asyncio.sleep(50*60)
