from sqlalchemy import text
from .db import require_db, row_dict
from .settings import settings

def normalize_phone(v): return ''.join(c for c in (v or '') if c.isdigit())[-15:] or None
def normalize_email(v): return (v or '').strip().lower() or None
def detect_intent(message):
    m=message.lower()
    if any(x in m for x in ('quote','quotation','price','cost','estimate')): return 'quotation'
    if any(x in m for x in ('stock','available','availability','inventory')): return 'availability'
    if any(x in m for x in ('follow','callback','call me')): return 'follow_up'
    return 'general_enquiry'
async def openai_answer(message,history,customer):
    if not settings.openai_api_key: return None
    from openai import AsyncOpenAI
    client=AsyncOpenAI(api_key=settings.openai_api_key)
    system='You are Guru Group sales assistant. Be multilingual. Never invent product, price, stock, quote or delivery facts. Ask for missing product/model, quantity and requirements. Escalate uncertain pricing to a salesperson.'
    r=await client.chat.completions.create(model=settings.openai_model,temperature=.2,messages=[{'role':'system','content':system},*history[-10:],{'role':'user','content':message}])
    return r.choices[0].message.content or None
async def run_graph(message,customer,history):
    from .agent import run_sales_agent
    return await run_sales_agent(message, customer, history)
async def get_or_create_customer(name,phone,email):
    phone,email=normalize_phone(phone),normalize_email(email)
    if not phone and not email: raise ValueError('Phone or email is required')
    with require_db() as db:
        row=db.execute(text("select * from customers where (cast(:phone as text) is not null and right(normalized_phone, 10)=right(cast(:phone as text), 10)) or (cast(:email as text) is not null and normalized_email=cast(:email as text)) limit 1"),{'phone':phone,'email':email}).first()
        if row: return row_dict(row)
        row=db.execute(text("insert into customers(name,phone,email,normalized_phone,normalized_email) values(:name,:phone,:email,:phone,:email) returning *"),{'name':name,'phone':phone,'email':email}).first(); db.commit(); return row_dict(row)
