"""Zoho-native sales agent.

The pipeline is deliberately split into nodes:
understand -> route -> execute verified read/write tools -> compose.
Only the route/execute node can call Zoho. The final model sees verified
results, not imagined catalogue or CRM facts.
"""
from __future__ import annotations

import json
import re
import asyncio
import time
from typing import Any, TypedDict

from sqlalchemy import text

from .chat import normalize_email, normalize_phone
from .db import require_db, row_dict
from .settings import settings
from .zoho import zoho


class AgentState(TypedDict, total=False):
    message: str
    customer: dict[str, Any]
    history: list[dict[str, Any]]
    language: str
    intent: str
    entities: dict[str, Any]
    plan: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    safety_flags: list[str]
    answer: str
    metadata: dict[str, Any]
    topic: str
    focal_entity: dict[str, Any]
    pending_action: str | None


INTENTS = {
    'greeting', 'general_enquiry', 'product_search', 'product_details',
    'product_comparison', 'recommendation', 'availability', 'quotation',
    'sales_order', 'invoice', 'customer_lookup', 'order_status', 'follow_up', 'human_handoff', 'unknown',
}

_PROMPT_CACHE: dict[str, tuple[float, str]] = {}
_PROMPT_CACHE_TTL = 300


def _annotated_history(history: list[dict[str, Any]], prior: dict[str, Any]) -> list[dict[str, str]]:
    """Annotate the last completed user turn with its known intent/topic."""
    result = []
    previous_intent = prior.get('last_intent')
    for index, message in enumerate(history[-8:]):
        role = message.get('role', 'user')
        content = str(message.get('content', ''))
        if role == 'user' and index == len(history[-8:]) - 2 and previous_intent:
            content += f" [Detected intent: {previous_intent}]"
        result.append({'role': role, 'content': content})
    return result


async def _intent_prompt() -> str:
    """Load a tenant prompt from DB with a short in-process cache."""
    key = 'guru-group:intent_detection'
    cached = _PROMPT_CACHE.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    default = 'Return only JSON with keys intent, confidence, language, entities, missing_fields. Do not invent values.'
    try:
        with require_db() as db:
            row = db.execute(text("select prompt from sales_agent_prompts where tenant_key='guru-group' and prompt_key='intent_detection' and enabled=true limit 1")).first()
            prompt = str(row[0]) if row and row[0] else default
    except Exception:
        prompt = default
    _PROMPT_CACHE[key] = (time.monotonic() + _PROMPT_CACHE_TTL, prompt)
    return prompt


async def _invoke_with_retry(client, *, model: str, messages: list[dict[str, str]], temperature: float = 0, max_tokens: int = 500, attempts: int = 3):
    """Retry transient provider failures without retrying auth/request errors."""
    for attempt in range(attempts):
        try:
            return await client.chat.completions.create(model=model, temperature=temperature, max_tokens=max_tokens, messages=messages)
        except Exception as exc:
            status = getattr(exc, 'status_code', None)
            transient = status is None or status in {408, 409, 425, 429, 500, 502, 503, 504}
            if not transient or attempt == attempts - 1:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))


def detect_language(message: str) -> str:
    text_value = str(message or '').lower()
    if re.search(r'[\u0c80-\u0cff]', text_value):
        return 'kannada'
    if re.search(r'[\u0900-\u097f]', text_value):
        return 'hindi'
    hinglish = ('kya', 'hai', 'hain', 'chahiye', 'mujhe', 'aap', 'apka', 'kitna', 'kab', 'kaise', 'dikhao', 'milega', 'batao', 'nahi')
    if any(re.search(rf'(?<![a-z]){re.escape(word)}(?![a-z])', text_value) for word in hinglish):
        return 'hinglish'
    kannada_latin = ('beku', 'ideya', 'hege', 'eshtu', 'torisi', 'maadi', 'kodsi', 'illa', 'yavudu')
    if any(re.search(rf'(?<![a-z]){re.escape(word)}(?![a-z])', text_value) for word in kannada_latin):
        return 'kannada'
    return 'english'


def extract_entities(message: str) -> dict[str, Any]:
    text_value = message.strip()
    quantities = re.findall(r'\b(?:qty|quantity|pieces?|units?|nos?|number of)\s*[:=-]?\s*(\d+)\b', text_value.lower())
    budgets = re.findall(r'\b(?:under|below|less than|upto|up to|within)\s*(?:₹|rs\.?|inr)?\s*([\d,]+)', text_value.lower())
    emails = re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]+', text_value)
    phones = re.findall(r'(?<!\d)(?:\+?\d[\d\s()-]{8,}\d)(?!\d)', text_value)
    sku = re.findall(r'\b(?:sku|item|model)\s*[:#-]?\s*([\w/-]+)', text_value, flags=re.I)
    item_ids = re.findall(r'\b(?:item\s*id|itemid)\s*[:#-]?\s*(\d{8,})\b', text_value, flags=re.I)
    return {
        'quantity': int(quantities[0]) if quantities else None,
        'max_rate': float(budgets[0].replace(',', '')) if budgets else None,
        'emails': emails,
        'phones': [normalize_phone(phone) for phone in phones if normalize_phone(phone)],
        'sku': sku[0] if sku else None,
        'item_id': item_ids[0] if item_ids else None,
        'product_query': text_value,
    }


def deterministic_intent(message: str) -> str:
    value = message.lower().strip()
    if value in {'hi', 'hello', 'hey', 'namaste', 'ನಮಸ್ಕಾರ', 'नमस्ते'}:
        return 'greeting'
    if any(term in value for term in ('human', 'agent', 'sales person', 'call me', 'manager', 'मानव', 'एजेंट', 'ಮನುಷ್ಯ')):
        return 'human_handoff'
    if any(term in value for term in ('invoice', 'billing', 'bill me', 'tax invoice')):
        return 'invoice'
    if any(term in value for term in ('sales order', 'place order', 'create order', 'confirm order', 'go ahead with the order', 'proceed with the order')):
        return 'sales_order'
    if any(term in value for term in ('quote', 'quotation', 'estimate', 'formal quote', 'send me a quote')):
        return 'quotation'
    if any(term in value for term in ('price', 'cost', 'rate')):
        return 'product_details'
    if any(term in value for term in ('stock', 'available', 'availability', 'inventory', 'in stock', 'ಮಾರಾಟಕ್ಕೆ', 'लಭ್ಯ')):
        return 'availability'
    if any(term in value for term in ('compare', 'difference', 'which is better', 'तुलना', 'ಹೋಲಿಕೆ')):
        return 'product_comparison'
    if any(term in value for term in ('recommend', 'suggest', 'best for', 'help me choose', 'सुझाव', 'ಶಿಫಾರಸು')):
        return 'recommendation'
    if any(term in value for term in ('order status', 'track', 'where is my order', 'shipment', 'delivery status')):
        return 'order_status'
    if any(term in value for term in ('customer', 'contact', 'phone number', 'email id', 'contact details')):
        return 'customer_lookup'
    if any(term in value for term in ('product', 'item', 'model', 'sku', 'catalogue', 'catalog', 'show me', 'what do you have', 'details')):
        return 'product_search'
    if value in {'thanks', 'thank you', 'ok', 'okay', 'sure', 'fine'}:
        return 'general_enquiry'
    return 'general_enquiry'


def parse_json(text_value: str) -> dict[str, Any]:
    raw = (text_value or '').strip().replace('```json', '').replace('```', '').strip()
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, flags=re.S)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


async def understand_node(state: AgentState) -> AgentState:
    state['language'] = detect_language(state['message'])
    state['entities'] = extract_entities(state['message'])
    state['intent'] = deterministic_intent(state['message'])
    prior = state.get('customer', {}).get('session_context') or {}
    prior_intent = str(prior.get('last_intent') or '')
    # Carry forward only previously known structured facts that the new turn
    # did not replace. This makes short follow-ups such as "quantity 3" or
    # "yes, create it" usable without treating old free text as truth.
    prior_entities = prior.get('entities') if isinstance(prior.get('entities'), dict) else {}
    for key, value in prior_entities.items():
        if state['entities'].get(key) in (None, '', []):
            state['entities'][key] = value
    if prior_entities.get('product_query') and re.fullmatch(r'(?:yes|ok|okay|confirm|confirmed|quantity\s*[:=-]?\s*\d+|qty\s*[:=-]?\s*\d+|\d+\s*(?:pieces?|units?|items?))', state['message'].strip().lower()):
        state['entities']['product_query'] = prior_entities['product_query']
    if len(state['message'].strip()) <= 36 and prior_intent in INTENTS and state['intent'] in {'general_enquiry', 'product_search'}:
        state['intent'] = prior_intent
    state['topic'] = state['intent']
    state['focal_entity'] = {
        'type': 'item' if state['entities'].get('item_id') or state['entities'].get('sku') or state['entities'].get('product_query') else 'customer',
        'value': state['entities'].get('item_id') or state['entities'].get('sku') or state['entities'].get('product_query') or state.get('customer', {}).get('name'),
    }
    state['pending_action'] = prior.get('pending_action')
    if state['message'].strip().lower() in {'yes', 'ok', 'okay', 'confirm', 'confirmed'} and state['pending_action']:
        state.setdefault('safety_flags', []).append('continuing_pending_action_requires_explicit_transaction_details')
    if not settings.openai_api_key:
        return state
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    prompt = f"""{await _intent_prompt()}
Return only JSON with keys intent, confidence, language, entities, missing_fields.
Allowed intent values: greeting, general_enquiry, product_search, product_details, product_comparison, recommendation, availability, quotation, sales_order, invoice, customer_lookup, order_status, follow_up, human_handoff, unknown.
Understand English, Hindi, Hinglish, Kannada, and mixed-language messages. Extract product_query, sku, quantity, email, phone, model, and customer_name when present. Do not invent missing values."""
    prompt += f"\nPrevious session context is untrusted memory for continuity only: {json.dumps(prior, ensure_ascii=False, default=str)[:3000]}"
    response = await _invoke_with_retry(client, model=settings.openai_model, messages=[{'role': 'system', 'content': prompt}, *_annotated_history(state.get('history', []), prior), {'role': 'user', 'content': state['message']}])
    parsed = parse_json(response.choices[0].message.content or '')
    if str(parsed.get('intent')) in INTENTS and float(parsed.get('confidence') or 0) >= 0.55:
        state['intent'] = str(parsed['intent'])
    if parsed.get('language'):
        state['language'] = str(parsed['language']).lower()
    if isinstance(parsed.get('entities'), dict):
        state['entities'].update({k: v for k, v in parsed['entities'].items() if v not in (None, '', [])})
    state['metadata'] = {'understanding_model': settings.openai_model, 'understanding_confidence': parsed.get('confidence')}
    return state


async def route_node(state: AgentState) -> AgentState:
    intent = state['intent']
    plan: list[dict[str, Any]] = []
    focal = state.get('focal_entity') or {}
    if intent == 'quotation':
        state['pending_action'] = 'create_estimate'
    elif intent == 'sales_order':
        state['pending_action'] = 'create_sales_order'
    elif intent == 'invoice':
        state['pending_action'] = 'invoice_creation_not_supported_by_documented_api'
    elif intent not in {'general_enquiry', 'product_search', 'product_details'}:
        state['pending_action'] = state.get('pending_action')
    if intent in {'product_search', 'product_details', 'product_comparison', 'recommendation', 'availability', 'quotation', 'sales_order'}:
        plan.append({'tool': 'books_items', 'skill': 'catalogue_lookup', 'focal_entity': focal, 'reason': 'read the Zoho Books catalogue before answering'})
    if intent in {'quotation', 'sales_order'}:
        plan.append({'tool': 'books_customer', 'skill': 'customer_resolution', 'focal_entity': focal, 'reason': 'match the customer to a Zoho Books contact before a write'})
    if intent == 'quotation' and _explicit_estimate_request(state['message']):
        plan.append({'tool': 'create_estimate', 'reason': 'create only after an explicit estimate request and validated inputs'})
    if intent == 'sales_order' and _explicit_order_confirmation(state['message']):
        plan.append({'tool': 'create_sales_order', 'reason': 'create only after explicit order confirmation'})
    if intent == 'invoice':
        plan.append({'tool': 'books_invoices', 'skill': 'invoice_lookup', 'focal_entity': focal, 'reason': 'read existing invoices; creation is not in the supplied API contract'})
    if intent == 'customer_lookup':
        plan.append({'tool': 'bigin_contacts', 'reason': 'find the existing Bigin contact'})
    if intent in {'quotation', 'sales_order', 'follow_up'}:
        plan.append({'tool': 'books_context', 'reason': 'create/update the customer context in Zoho Books'})
    if intent == 'order_status':
        plan.append({'tool': 'books_sales_orders', 'reason': 'read available order records'})
    if intent in {'general_enquiry', 'human_handoff'}:
        plan.append({'tool': 'books_context', 'reason': 'retain the customer context in Zoho Books'})
    state['plan'] = plan
    return state


def _extract_rows(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    value = payload.get('data')
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def item_filters(entities: dict[str, Any]) -> dict[str, Any]:
    query = str(entities.get('product_query') or '').strip()
    query = re.sub(r'\b(show me|find|search for|search|looking for|do you have|what do you have|give me|please|products?|items?)\b', ' ', query, flags=re.I)
    query = re.sub(r'\b(?:under|below|less than|upto|up to|within)\s*(?:₹|rs\.?|inr)?\s*[\d,]+', ' ', query, flags=re.I)
    query = re.sub(r'\s+', ' ', query).strip(' ,.-')
    if entities.get('sku'):
        query = str(entities['sku'])
    if entities.get('item_id'):
        query = ''
    filters: dict[str, Any] = {'status': 'active'}
    if query:
        lower_query = query.lower()
        if 'description' in lower_query:
            filters['description_contains'] = re.sub(r'\bdescription\b', ' ', query, flags=re.I).strip()
        elif 'name' in lower_query:
            filters['name_contains'] = re.sub(r'\bname\b', ' ', query, flags=re.I).strip()
        else:
            filters['search_text'] = query
    if entities.get('max_rate') is not None:
        filters['rate_less_equals'] = entities['max_rate']
    return filters


def _explicit_estimate_request(message: str) -> bool:
    value = message.lower()
    return any(term in value for term in ('create estimate', 'create quotation', 'generate quotation', 'prepare quotation', 'make a quote', 'generate a quote', 'formal quote'))


def _explicit_order_confirmation(message: str) -> bool:
    value = message.lower()
    return any(term in value for term in ('create sales order', 'place the order', 'place order', 'confirm the order', 'confirm order', 'go ahead with the order', 'proceed with the order'))


async def _persist_transaction(state: AgentState, fields: dict[str, Any]) -> None:
    session_id = state.get('customer', {}).get('session_id')
    if not session_id or not fields:
        return
    assignments = ', '.join(f"{key}=:{key}" for key in fields)
    with require_db() as db:
        db.execute(text(f"update enquiries set {assignments}, updated_at=now() where session_id=:session"), {'session': session_id, **fields})
        db.commit()


async def execute_node(state: AgentState) -> AgentState:
    results: list[dict[str, Any]] = []
    try:
        status = zoho.status()
        if not status.get('connected'):
            state['safety_flags'] = ['zoho_not_connected']
            state['tool_results'] = results
            return state
        org = status.get('organization_id')
        if not org:
            state['safety_flags'] = ['zoho_organization_not_selected']
            state['tool_results'] = results
            return state
        item = None
        books_customer = None
        for action in state.get('plan', []):
            tool = action['tool']
            if tool == 'books_items':
                filters = item_filters(state.get('entities', {}))
                payload = await zoho.books_items(org, filters=filters)
                rows = _extract_rows(payload, 'items', 'products')
                active_rows = [row for row in rows if str(row.get('status', '')).lower() == 'active']
                item_id = state.get('entities', {}).get('item_id')
                if item_id and state.get('intent') == 'product_details':
                    detail = await zoho.books_item(org, item_id)
                    detail_rows = _extract_rows(detail, 'item', 'items', 'data')
                    active_rows = [row for row in detail_rows if str(row.get('status', '')).lower() == 'active']
                if len(active_rows) == 1:
                    item = active_rows[0]
                elif state.get('entities', {}).get('item_id'):
                    item = next((row for row in active_rows if str(row.get('item_id')) == str(state['entities']['item_id'])), None)
                elif len(active_rows) > 1 and state.get('intent') in {'quotation', 'sales_order'}:
                    state.setdefault('safety_flags', []).append('multiple_active_items_require_item_selection')
                results.append({'tool': tool, 'success': True, 'data': {'items': active_rows[:20], 'count': len(active_rows), 'filters': filters, 'inventory_is_not_used_as_a_filter': True}})
            elif tool == 'books_customer':
                customer = state['customer']
                if customer.get('books_contact_id'):
                    books_customer = {'contact_id': customer['books_contact_id']}
                else:
                    payload = await zoho.books_customers(org)
                    rows = _extract_rows(payload, 'contacts', 'customers', 'data')
                    email = normalize_email(customer.get('email')); phone = normalize_phone(customer.get('phone')); name = str(customer.get('name') or '').strip().lower()
                    match = next((row for row in rows if (email and normalize_email(row.get('email')) == email) or (phone and normalize_phone(row.get('phone')) == phone) or (name and str(row.get('contact_name') or row.get('customer_name') or '').strip().lower() == name)), None)
                    if match:
                        books_customer = {'contact_id': match.get('contact_id') or match.get('customer_id'), 'record': match}
                        if books_customer.get('contact_id'):
                            with require_db() as db:
                                db.execute(text('update customers set books_contact_id=:id,updated_at=now() where id=:customer'), {'id': books_customer['contact_id'], 'customer': customer['id']}); db.commit()
                results.append({'tool': tool, 'success': bool(books_customer and books_customer.get('contact_id')), 'data': books_customer or {'missing': 'books_contact_id'}})
            elif tool == 'books_invoices':
                payload = await zoho.list_invoices(org)
                results.append({'tool': tool, 'success': True, 'data': {'invoices': _extract_rows(payload, 'invoices', 'data')[:20], 'creation_supported_by_documented_api': False}})
            elif tool == 'create_estimate':
                entities = state.get('entities', {})
                if not _explicit_estimate_request(state['message']) or not item or not item.get('item_id') or int(entities.get('quantity') or 0) < 1 or not books_customer or not books_customer.get('contact_id'):
                    state.setdefault('safety_flags', []).append('estimate_requires_explicit_confirmation_and_customer_item_quantity')
                    continue
                payload = await zoho.create_estimate(org, {'customer_id': books_customer['contact_id'], 'line_items': [{'item_id': item['item_id'], 'quantity': int(entities['quantity']), **({'rate': item.get('rate')} if item.get('rate') is not None else {})}], 'notes': 'Created from an explicitly confirmed Guru Group AI sales enquiry.'})
                estimate = payload.get('estimate') or payload.get('data') or payload
                if isinstance(estimate, dict):
                    await _persist_transaction(state, {'books_estimate_id': estimate.get('estimate_id'), 'books_estimate_number': estimate.get('estimate_number'), 'quote_status': estimate.get('status', 'draft')})
                results.append({'tool': tool, 'success': True, 'data': estimate})
            elif tool == 'create_sales_order':
                entities = state.get('entities', {})
                if not _explicit_order_confirmation(state['message']) or not item or not item.get('item_id') or int(entities.get('quantity') or 0) < 1 or not books_customer or not books_customer.get('contact_id'):
                    state.setdefault('safety_flags', []).append('sales_order_requires_explicit_confirmation_and_customer_item_quantity')
                    continue
                payload = await zoho.create_sales_order(org, {'customer_id': books_customer['contact_id'], 'line_items': [{'item_id': item['item_id'], 'quantity': int(entities['quantity']), **({'rate': item.get('rate')} if item.get('rate') is not None else {})}], 'notes': 'Created only after explicit customer order confirmation.'})
                order = payload.get('salesorder') or payload.get('data') or payload
                if isinstance(order, dict):
                    await _persist_transaction(state, {'books_salesorder_id': order.get('salesorder_id'), 'books_salesorder_number': order.get('salesorder_number'), 'transaction_status': 'sales_order_created'})
                results.append({'tool': tool, 'success': True, 'data': order})
            elif tool == 'bigin_contacts':
                payload = await zoho.list_bigin_contacts()
                rows = _extract_rows(payload, 'data')
                customer = state['customer']; needle = normalize_email(customer.get('email')) or normalize_phone(customer.get('phone'))
                matches = [row for row in rows if needle and needle in {normalize_email(row.get('Email')), normalize_phone(row.get('Mobile')), normalize_phone(row.get('Phone'))}]
                results.append({'tool': tool, 'success': True, 'data': {'matches': matches[:5], 'count': len(matches)}})
            elif tool == 'books_sales_orders':
                payload = await zoho.list_sales_orders(org) if org else {}
                results.append({'tool': tool, 'success': True, 'data': {'orders': _extract_rows(payload, 'salesorders', 'data')[:20]}})
            elif tool == 'books_context':
                customer = state['customer']
                contact_id = customer.get('books_contact_id')
                if not contact_id:
                    name_parts = str(customer.get('name') or '').split()
                    first_name = name_parts[0] if name_parts else customer['name']
                    last_name = ' '.join(name_parts[1:])
                    contact_payload = await zoho.create_books_contact(org, {'contact_name': customer.get('name'), 'contact_type': 'customer', 'customer_sub_type': 'business', 'contact_persons': [{'first_name': first_name, 'last_name': last_name, 'email': customer.get('email'), 'mobile': customer.get('phone'), 'is_primary_contact': True}], 'notes': 'Captured through Guru Group AI Sales'})
                    contact = contact_payload.get('contact') or {}
                    contact_id = contact.get('contact_id')
                    if contact_id:
                        with require_db() as db:
                            db.execute(text('update customers set books_contact_id=:id,updated_at=now() where id=:customer'), {'id': contact_id, 'customer': customer['id']}); db.commit()
                note_id = None
                results.append({'tool': tool, 'success': True, 'data': {'books_contact_id': contact_id, 'crm_context_created': bool(contact_id)}})
    except Exception as exc:
        results.append({'tool': 'zoho', 'success': False, 'error': str(exc)[:300]})
    state['tool_results'] = results
    if any(result.get('tool') in {'create_estimate', 'create_sales_order'} and result.get('success') for result in results):
        state['pending_action'] = None
    return state


def verified_context(state: AgentState) -> str:
    return json.dumps(state.get('tool_results', []), ensure_ascii=False, default=str)[:10000]


async def compose_node(state: AgentState) -> AgentState:
    language = state.get('language', 'english')
    results = state.get('tool_results', [])
    if settings.openai_api_key:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        system = f"""You are Guru Group's multilingual sales assistant. Reply in {language}; for Hinglish use natural Roman Hindi; for Kannada use Kannada when the customer uses Kannada. Understand mixed language naturally. Preserve the active customer topic when the latest message is a short continuation, but recognize a clear topic change. Use only the verified Zoho results below. Never invent a price, stock level, customer record, order, estimate, or API success. If Zoho is disconnected or a tool failed, explain that a salesperson needs to check it. Ask one focused missing-detail question. For quotations, do not promise a quote until product, quantity, and commercial details are verified. Be warm, concise, and fluent.
Previous conversation context: {json.dumps(state.get('customer', {}).get('session_context') or {}, ensure_ascii=False, default=str)[:3000]}
Safety flags: {state.get('safety_flags', [])}
Verified Zoho results: {verified_context(state)}"""
        response = await _invoke_with_retry(client, model=settings.openai_model, temperature=.25, max_tokens=700, messages=[{'role': 'system', 'content': system}, *_annotated_history(state.get('history', []), state.get('customer', {}).get('session_context') or {}), {'role': 'user', 'content': state['message']}])
        state['answer'] = response.choices[0].message.content or ''
        state.setdefault('metadata', {})['response_model'] = settings.openai_model
        return state
    state['answer'] = fallback_response(state)
    return state


def fallback_response(state: AgentState) -> str:
    intent = state.get('intent', 'general_enquiry'); language = state.get('language', 'english'); connected = not any(flag == 'zoho_not_connected' for flag in state.get('safety_flags', [])); has_items = any(r.get('tool') == 'books_items' and r.get('success') for r in state.get('tool_results', []))
    if 'zoho_organization_not_selected' in state.get('safety_flags', []):
        return 'Please select the Zoho Books organisation first; I need its organisation ID to search the live catalogue.'
    if language == 'hinglish':
        return 'Main help kar sakta hoon. Product/model aur quantity share kijiye; main Zoho catalogue check karke bataunga.' if connected else 'Zoho abhi connected nahi hai. Sales team ko check karke aapko confirm karna hoga.'
    if language == 'hindi':
        return 'मैं मदद कर सकता हूँ। कृपया उत्पाद/मॉडल और मात्रा बताइए; मैं Zoho कैटलॉग जाँचकर बताऊँगा।' if connected else 'Zoho अभी कनेक्ट नहीं है। सेल्स टीम को जाँचकर पुष्टि करनी होगी।'
    if language == 'kannada':
        return 'ನಾನು ಸಹಾಯ ಮಾಡಬಹುದು. ಉತ್ಪನ್ನ/ಮಾದರಿ ಮತ್ತು ಪ್ರಮಾಣವನ್ನು ತಿಳಿಸಿ; Zoho ಕ್ಯಾಟಲಾಗ್ ಪರಿಶೀಲಿಸಿ ತಿಳಿಸುತ್ತೇನೆ.' if connected else 'Zoho ಇನ್ನೂ ಸಂಪರ್ಕಗೊಂಡಿಲ್ಲ. ಮಾರಾಟ ತಂಡ ಪರಿಶೀಲಿಸಿ ದೃಢೀಕರಿಸಬೇಕು.'
    if intent == 'greeting': return 'Hello. I can help with products, availability, customer records, quotations, and follow-up.'
    if intent == 'quotation': return 'I can prepare the quotation flow. Please share the exact product or SKU and quantity.' if not has_items else 'I found the catalogue context. Please confirm the exact product/SKU and quantity so the quotation can be prepared safely.'
    if intent == 'sales_order': return 'I can create a sales order only after you explicitly confirm the exact product/SKU and quantity.'
    if intent == 'invoice': return 'I can look up existing Zoho Books invoices, but invoice creation is not included in the supplied Books API contract and remains a pending integration item.'
    if intent in {'product_search', 'product_details', 'recommendation', 'availability'}: return 'I can check the Zoho Books catalogue. Please share the exact product, model, or SKU.' if connected else 'Zoho is not connected yet, so I cannot verify catalogue or stock details.'
    return 'I can help with customer records, products, quotations, and follow-ups. What would you like to check?'


async def run_sales_agent(message: str, customer: dict[str, Any], history: list[dict[str, Any]]) -> AgentState:
    state: AgentState = {'message': message, 'customer': customer, 'history': history, 'safety_flags': [], 'metadata': {}}
    try:
        from langgraph.graph import END, StateGraph
        graph = StateGraph(dict)
        graph.add_node('understand', understand_node); graph.add_node('route', route_node); graph.add_node('execute_zoho', execute_node); graph.add_node('compose', compose_node)
        graph.set_entry_point('understand'); graph.add_edge('understand', 'route'); graph.add_edge('route', 'execute_zoho'); graph.add_edge('execute_zoho', 'compose'); graph.add_edge('compose', END)
        return await graph.compile().ainvoke(state)
    except Exception:
        state = await understand_node(state); state = await route_node(state); state = await execute_node(state); return await compose_node(state)
