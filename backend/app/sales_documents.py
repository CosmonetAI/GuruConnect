"""Member-scoped, idempotent Books documents and SMTP2GO PDF delivery."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from typing import Literal
from uuid import UUID, NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from .sales_pdf import render_demo_sales_pdf
from .document_email import EmailDeliveryError, delivery_fingerprint, email_delivery_configuration, send_document_email


class SalesLine(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    item_id: str = Field(min_length=1, max_length=100)
    quantity: float = Field(gt=0, le=100000, strict=True)
    rate: float = Field(gt=0, le=1000000000, strict=True)
    discount: float = Field(default=0, ge=0, le=1000000000, strict=True)
    discount_type: Literal['percentage', 'fixed'] = 'percentage'
    description: str = Field(default='', max_length=2000)


class DocumentEmailInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    recipient: str = Field(default='', max_length=320)
    subject: str = Field(default='', max_length=240)
    message: str = Field(default='', max_length=10000)


class SalesDocumentInput(DocumentEmailInput):
    type: Literal['invoice', 'quote', 'sales-order']
    delivery: Literal['email', 'download'] = 'download'
    line_items: list[SalesLine] = Field(min_length=1, max_length=50)


def signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def _timestamp():
    return datetime.now(ZoneInfo('UTC')).isoformat()


def _catalogue_number(value):
    """Read provider numeric facts without coercing unknown values to zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _catalogue_boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {'true', 'false'}:
        return value.strip().lower() == 'true'
    return None


def _stock_tracked(item):
    # Books distinguishes inventory from sales-only goods and services. A
    # service's zero-valued stock placeholder does not make it unavailable.
    # Contradictory metadata stays subject to the stricter inventory check.
    tracking = _catalogue_boolean(item.get('track_inventory'))
    item_type = str(item.get('item_type') or '').strip().lower()
    product_type = str(item.get('product_type') or '').strip().lower()
    if tracking is True or item_type == 'inventory':
        return True
    if tracking is False or product_type in {'service', 'digital_service', 'capital_service'} or item_type in {'sales', 'sales_and_purchases'}:
        return False
    return None


def _available_quantity(item):
    # Prefer available-for-sale facts over physical on-hand totals. A negative
    # or malformed available value cannot be replaced by a positive on-hand
    # value; that would conceal reservations or inconsistent inventory.
    for field in ('available_stock', 'available_for_sale_stock', 'actual_available_stock', 'stock_on_hand'):
        value = item.get(field)
        if value is not None and (not isinstance(value, str) or value.strip()):
            return _catalogue_number(value)
    return None


def _catalogue_review(code, message):
    return HTTPException(409, {'code': code, 'document_started': False, 'message': message})


class DocumentRepository:
    """One durable state record per request, using either SQLite or PostgreSQL."""
    def __init__(self, scope, *, local_store=None, database_factory=None):
        self.scope, self.local_store, self.database_factory = scope, local_store, database_factory

    def _matches(self, row):
        return row and all(str(row.get(key)) == str(self.scope[key]) for key in ('organisation_id', 'member_id', 'session_id'))

    def get(self, identity):
        if self.local_store:
            row = self.local_store.get('sales_documents', str(identity))
        else:
            with self.database_factory() as db:
                result = db.execute(text('''select data from voice_sales_documents where id=cast(:id as uuid)
                  and organisation_id=cast(:organisation_id as uuid) and member_id=cast(:member_id as uuid)
                  and session_id=cast(:session_id as uuid)'''), {**self.scope, 'id': str(identity)}).first()
                row = result[0] if result else None
        return row if self._matches(row) else None

    def claim(self, row):
        if self.local_store:
            from recorder.local_store import encoded
            with self.local_store.transaction() as db:
                result = db.execute("insert into records values('sales_documents',?,?) on conflict(table_name,id) do nothing", (row['id'], encoded(row)))
                fresh = result.rowcount == 1
        else:
            with self.database_factory() as db:
                fresh = db.execute(text('''insert into voice_sales_documents(id,organisation_id,member_id,session_id,request_id,data)
                  values(cast(:id as uuid),cast(:organisation_id as uuid),cast(:member_id as uuid),cast(:session_id as uuid),cast(:request_id as uuid),cast(:data as jsonb))
                  on conflict(id) do nothing returning id'''), {**row, 'data': json.dumps(row)}).first() is not None
                db.commit()
        return self.get(row['id']), fresh

    def patch(self, identity, updates, expected=None):
        if self.local_store:
            from recorder.local_store import encoded
            with self.local_store.transaction() as db:
                found = db.execute("select data from records where table_name='sales_documents' and id=?", (str(identity),)).fetchone()
                row = json.loads(found['data']) if found else None
                if not self._matches(row):
                    raise HTTPException(404, 'Sales document not found')
                if any(row.get(key) != value for key, value in (expected or {}).items()):
                    raise HTTPException(409, 'Document processing is already in progress. Retry the same action to check its result.')
                row.update(updates, updated_at=_timestamp())
                db.execute("update records set data=? where table_name='sales_documents' and id=?", (encoded(row), str(identity)))
        else:
            with self.database_factory() as db:
                found = db.execute(text('''select data from voice_sales_documents where id=cast(:id as uuid)
                  and organisation_id=cast(:organisation_id as uuid) and member_id=cast(:member_id as uuid)
                  and session_id=cast(:session_id as uuid) for update'''), {**self.scope, 'id': str(identity)}).first()
                row = found[0] if found else None
                if not self._matches(row):
                    raise HTTPException(404, 'Sales document not found')
                if any(row.get(key) != value for key, value in (expected or {}).items()):
                    raise HTTPException(409, 'Document processing is already in progress. Retry the same action to check its result.')
                row.update(updates, updated_at=_timestamp())
                db.execute(text('update voice_sales_documents set data=cast(:data as jsonb),updated_at=now() where id=cast(:id as uuid)'), {'id': str(identity), 'data': json.dumps(row)})
                db.commit()
        return row


class SalesDocumentService:
    TYPES = {'invoice': ('create_invoice', 'invoice', 'invoice_id', 'invoice_number'),
             'quote': ('create_estimate', 'estimate', 'estimate_id', 'estimate_number'),
             'sales-order': ('create_sales_order', 'salesorder', 'salesorder_id', 'salesorder_number')}

    def __init__(self, repository, customer, *, zoho=None, settings=None, demo=False, sender=None):
        self.repository, self.customer, self.zoho = repository, customer, zoho
        self.settings, self.demo, self.sender = settings, demo, sender or send_document_email

    async def options(self):
        if not self.demo:
            status = await asyncio.to_thread(self.zoho.status)
            if not status.get('connected') or not status.get('organization_id'):
                raise HTTPException(409, 'Connect the salesperson’s Zoho Books account and select an organization first.')
        return {'mode': 'local-demo' if self.demo else 'zoho', 'customer': self.customer,
                'customer_type': 'customer', 'document_types': list(self.TYPES),
                'email': {'provider': 'smtp2go', 'configured': False if self.demo else email_delivery_configuration(self.settings)['configured']}}

    async def _lines(self, lines, org):
        if self.demo:
            from recorder.demo_catalogue import ITEMS
            catalogue = {item['item_id']: item for item in ITEMS}
        else:
            gate = asyncio.Semaphore(3)
            async def fetch(item_id):
                async with gate:
                    result = await self.zoho.request('GET', f'/books/v3/items/{item_id}', params={'organization_id': org})
                return item_id, result.get('item') or {}
            if any(not line.item_id.isdigit() for line in lines):
                raise HTTPException(422, 'Select products from the connected Zoho Books catalogue.')
            catalogue = dict(await asyncio.gather(*(fetch(item_id) for item_id in dict.fromkeys(line.item_id for line in lines))))
        quantities = {}
        for line in lines:
            quantities[line.item_id] = quantities.get(line.item_id, Decimal(0)) + Decimal(str(line.quantity))
        clean = []
        for line in lines:
            item = catalogue.get(line.item_id)
            status = str((item or {}).get('status') or '').strip().lower()
            if (not item or str(item.get('item_id')) != line.item_id or status not in {'', 'active', 'enabled'}
                    or _catalogue_boolean(item.get('can_be_sold')) is False or str(item.get('item_type') or '').strip().lower() == 'purchases'):
                raise _catalogue_review('catalogue_item_unavailable', 'An item is unavailable. Refresh the catalogue and review the cart.')
            current_price = _catalogue_number(item.get('rate'))
            price = float(current_price) if current_price is not None else 0
            if current_price is None or not math.isfinite(price) or price <= 0 or item.get('price_status') not in (None, '', 'known'):
                raise _catalogue_review('catalogue_price_unconfirmed', 'An item has no usable catalogue price. Confirm its positive selling price in Zoho Books before creating a document.')
            if abs(current_price - Decimal(str(line.rate))) > Decimal('0.005'):
                raise _catalogue_review('catalogue_price_changed', 'A catalogue price changed. Refresh the cart before creating the document.')
            if _stock_tracked(item) is not False:
                available = _available_quantity(item)
                if (available is not None and available <= 0) or item.get('availability_status') == 'out_of_stock':
                    raise _catalogue_review('catalogue_out_of_stock', 'An item is out of stock. Refresh the catalogue and review the cart.')
                if available is None or item.get('availability_status') == 'needs_review':
                    raise _catalogue_review('catalogue_stock_unconfirmed', 'Current stock could not be confirmed for an inventory item. Review its stock in Zoho Books before creating a document.')
                if quantities[line.item_id] > available:
                    raise _catalogue_review('catalogue_stock_insufficient', 'The requested quantity exceeds current available stock. Refresh the catalogue and review the cart.')
            amount = price * line.quantity
            if (line.discount_type == 'percentage' and line.discount > 100) or (line.discount_type == 'fixed' and line.discount > amount):
                raise HTTPException(422, 'A line discount exceeds its price.')
            # Use Books' documented percentage syntax for both cart modes.
            # A line-level fixed amount has an equivalent percentage, avoiding
            # ambiguity between a unit discount and a whole-line discount.
            percentage = Decimal(str(line.discount)) if line.discount_type == 'percentage' else (
                Decimal(str(line.discount)) * 100 / (Decimal(str(price)) * Decimal(str(line.quantity))) if amount else Decimal(0))
            discount = f'{percentage.quantize(Decimal("0.000000000001")).normalize():f}%'
            clean.append({'item_id': line.item_id, 'name': item.get('name') or item.get('item_name'),
                          'sku': item.get('sku') or '', 'rate': price, 'quantity': line.quantity,
                          'discount': discount, 'discount_amount': line.discount if line.discount_type == 'fixed' else amount * line.discount / 100,
                          'description': line.description or item.get('description') or '',
                          **({'tax_id': item['tax_id']} if item.get('tax_id') and not self.demo else {})})
        return clean

    async def create(self, body: SalesDocumentInput):
        scope = self.repository.scope
        identity = str(uuid5(NAMESPACE_URL, f"guruconnect:sales-document:{scope['organisation_id']}:{scope['member_id']}:{scope['session_id']}:{body.request_id}"))
        hashed = signature(body.model_dump(mode='json'))
        existing = await asyncio.to_thread(self.repository.get, identity)
        if existing:
            if existing['request_hash'] != hashed:
                raise HTTPException(409, 'This action key belongs to a different document. Review the cart and start a new action.')
            if body.delivery == 'email' and existing.get('state') == 'created' and existing['delivery']['status'] == 'not_requested':
                return await self.email(identity, DocumentEmailInput(**{key: getattr(body, key) for key in ('request_id', 'recipient', 'subject', 'message')}))
            return await self._response(existing)
        if body.delivery == 'email':
            self._email_available(body.recipient)
            self._mail_content(body.subject, body.message)
        status = {} if self.demo else await asyncio.to_thread(self.zoho.status)
        org = status.get('organization_id')
        if not self.demo and (not status.get('connected') or not org):
            raise HTTPException(409, 'Connect Zoho Books and select an organization first.')
        lines = await self._lines(body.line_items, org)
        row = {**scope, 'id': identity, 'request_id': str(body.request_id), 'request_hash': hashed,
               'type': body.type, 'state': 'creating', 'demo': self.demo, 'books_org': org,
               'customer': self.customer, 'lines': lines, 'currency': status.get('currency') or 'INR',
               'delivery': {'status': 'not_requested', 'provider': 'smtp2go'},
               'created_at': _timestamp(), 'updated_at': _timestamp()}
        row, fresh = await asyncio.to_thread(self.repository.claim, row)
        if not fresh:
            if row['request_hash'] != hashed:
                raise HTTPException(409, 'This action key belongs to a different document.')
            return await self._response(row)
        try:
            if self.demo:
                document = {'external_id': None, 'number': f'SAMPLE-{body.type.upper()}-{identity[:8]}'}
            else:
                contact = await self.zoho.ensure_books_contact(org, name=self.customer['name'], email=self.customer.get('email') or None,
                                                             phone=self.customer.get('phone'), contact_type='customer')
                customer_id = contact.get('contact_id') or contact.get('id')
                if not customer_id:
                    raise RuntimeError('books_contact_missing')
                method, key, id_key, number_key = self.TYPES[body.type]
                zone = ZoneInfo(getattr(self.settings, 'app_timezone', None) or 'Asia/Kolkata')
                result = await getattr(self.zoho, method)(org, {'customer_id': customer_id,
                    'date': datetime.now(zone).date().isoformat(), 'reference_number': f'GC-{identity}',
                    'discount_type': 'item_level', 'is_discount_before_tax': True,
                    'line_items': [{k: v for k, v in item.items() if k not in {'name', 'sku', 'discount_amount'}} for item in lines],
                    'notes': 'Created from GuruConnect customer session'})
                created = result.get(key) or {}
                if not created.get(id_key):
                    raise RuntimeError('books_document_identity_missing')
                expected_total = sum(Decimal(str(line['rate'])) * Decimal(str(line['quantity'])) - Decimal(str(line['discount_amount'])) for line in lines)
                # Confirm Books preserved the reviewed line totals before any
                # email is submitted. Tax remains authoritative in Books.
                returned_lines = created.get('line_items') or []
                pricing_review = False
                if returned_lines and all(item.get('item_total') is not None for item in returned_lines):
                    actual_total = sum(Decimal(str(item['item_total'])) for item in returned_lines)
                    pricing_review = abs(actual_total - expected_total) > Decimal('0.01') * len(lines)
                document = {'external_id': str(created[id_key]), 'number': created.get(number_key) or str(created[id_key]),
                            'pricing_review': pricing_review,
                            'total': created.get('total'), 'tax_total': created.get('tax_total'), 'currency_code': created.get('currency_code')}
            row = await asyncio.to_thread(self.repository.patch, identity, {**document, 'state': 'created'}, {'state': 'creating'})
        except Exception:
            # A network failure may happen after Books accepted creation. Never
            # repeat it under this key; the reference lets an admin reconcile.
            await asyncio.to_thread(self.repository.patch, identity, {'state': 'creation_uncertain'})
            raise HTTPException(502, {'code': 'document_creation_uncertain', 'document_started': True, 'document_id': identity,
                'message': f'Document creation needs review in Zoho Books using reference GC-{identity}. Retrying will not create a duplicate.'}) from None
        if body.delivery == 'email':
            return await self.email(identity, DocumentEmailInput(**{key: getattr(body, key) for key in ('request_id', 'recipient', 'subject', 'message')}))
        return await self._response(row)

    def _email_available(self, recipient):
        import re
        if self.demo:
            raise HTTPException(409, 'Demo documents can be downloaded as samples. Email is available for real Zoho Books documents.')
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', recipient):
            raise HTTPException(422, 'Enter a valid recipient email or choose Create & download.')
        if not email_delivery_configuration(self.settings)['configured']:
            raise HTTPException(503, {'code': 'email_not_configured', 'document_started': False,
                'message': 'SMTP2GO is not configured yet. Choose Create & download, or add the backend mail credentials.'})

    def _mail_content(self, subject, message):
        if not subject.strip() or any(value in subject for value in '\r\n\0') or not message.strip() or '\0' in message:
            raise HTTPException(422, {'code': 'email_invalid_message', 'document_started': False,
                'message': 'Enter an email subject and message before creating and sending.'})

    async def _pdf(self, row):
        if row.get('demo'):
            # The renderer consumes line-level flat discounts, not Books' per-
            # unit wire representation used above.
            lines = [{**item, 'discount': item['discount_amount'], 'discount_type': 'fixed'} for item in row['lines']]
            return render_demo_sales_pdf(row['type'], row['customer'], lines, row.get('currency') or 'INR')
        status = await asyncio.to_thread(self.zoho.status)
        if str(status.get('organization_id')) != str(row['books_org']):
            raise HTTPException(409, {'code': 'document_pdf_unavailable', 'document_started': True, 'document_id': row['id'],
                'message': 'Select the Zoho Books organization used to create this document.'})
        return await self.zoho.download_sales_document(row['books_org'], row['type'], row['external_id'])

    async def _response(self, row, pdf=None):
        if not row:
            raise HTTPException(404, 'Sales document not found')
        if row['state'] != 'created':
            raise HTTPException(409, {'code': 'document_processing_pending', 'document_started': True, 'document_id': row['id'],
                'message': f'Document creation is in progress or needs review. Check Zoho reference GC-{row["id"]} before starting a different action.'})
        if row.get('pricing_review'):
            raise HTTPException(409, {'code': 'document_pricing_review', 'document_started': True, 'document_id': row['id'],
                'message': f'Zoho calculated different line totals. Review document {row.get("number")} in Books before sending. No email was submitted.'})
        filename = f"{row['type']}-{row['id'][:8]}.pdf"
        try:
            pdf = pdf if pdf is not None else await self._pdf(row)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(502, {'code': 'document_pdf_unavailable', 'document_started': True, 'document_id': row['id'],
                'message': 'The document was created, but its PDF is temporarily unavailable. Retry the same action; it will reuse the existing document.'}) from None
        return {'document': {key: row.get(key) for key in ('id', 'external_id', 'number', 'type', 'demo', 'total', 'tax_total', 'currency_code')},
                'delivery': row['delivery'], 'pdf': {'filename': filename, 'mime_type': 'application/pdf', 'base64': base64.b64encode(pdf).decode()}}

    async def email(self, identity, body: DocumentEmailInput):
        self._email_available(body.recipient)
        self._mail_content(body.subject, body.message)
        row = await asyncio.to_thread(self.repository.get, str(identity))
        if not row or row['state'] != 'created':
            raise HTTPException(404, 'Created sales document not found')
        if row.get('pricing_review'):
            return await self._response(row)
        prior = row['delivery']
        action_hash = signature(body.model_dump(mode='json'))
        if prior.get('request_id') == str(body.request_id):
            if prior.get('request_hash') != action_hash:
                raise HTTPException(409, 'This delivery action belongs to different email details.')
            return await self._response(row)
        if prior['status'] in {'accepted', 'sending', 'uncertain'}:
            raise HTTPException(409, 'The previous email was submitted or needs delivery review. It will not be sent twice.')
        pdf = await self._pdf(row)
        delivery = {'provider': 'smtp2go', 'status': 'sending', 'request_id': str(body.request_id), 'request_hash': action_hash,
                    'fingerprint': delivery_fingerprint(recipient=body.recipient, subject=body.subject, message=body.message,
                        pdf_bytes=pdf, filename=f'{row["type"]}-{row["id"][:8]}.pdf')}
        row = await asyncio.to_thread(self.repository.patch, str(identity), {'delivery': delivery}, {'delivery': prior})
        try:
            receipt = await self.sender(recipient=body.recipient, subject=body.subject, message=body.message,
                pdf_bytes=pdf, filename=f'{row["type"]}-{row["id"][:8]}.pdf',
                delivery_key=f'{identity}:{body.request_id}', settings_override=self.settings)
            delivery.update(receipt, status='accepted', message='Submitted to SMTP2GO for delivery.')
        except EmailDeliveryError as error:
            delivery.update(status='uncertain' if error.uncertain else 'failed',
                message='Email delivery needs review before resending.' if error.uncertain else 'The document was created, but SMTP2GO did not accept the email. Download the PDF or retry email delivery.')
        except Exception:
            delivery.update(status='uncertain', message='Email submission could not be confirmed. Check SMTP2GO before resending.')
        row = await asyncio.to_thread(self.repository.patch, str(identity), {'delivery': delivery})
        return await self._response(row, pdf)


def install_sales_document_routes(app, service_factory):
    async def service(session_id):
        return await asyncio.to_thread(service_factory, str(session_id))

    @app.get('/api/voice-sessions/{session_id}/sales-documents/options')
    async def options(session_id: UUID):
        return await (await service(session_id)).options()

    @app.post('/api/voice-sessions/{session_id}/sales-documents')
    async def create(session_id: UUID, body: SalesDocumentInput, idempotency_key: str | None = Header(default=None)):
        if idempotency_key and idempotency_key != str(body.request_id):
            raise HTTPException(409, 'The action key does not match this request.')
        return await (await service(session_id)).create(body)

    @app.post('/api/voice-sessions/{session_id}/sales-documents/{document_id}/email')
    async def email(session_id: UUID, document_id: UUID, body: DocumentEmailInput):
        return await (await service(session_id)).email(str(document_id), body)

    @app.get('/api/voice-sessions/{session_id}/sales-documents/{document_id}')
    async def saved(session_id: UUID, document_id: UUID):
        workflow = await service(session_id)
        row = await asyncio.to_thread(workflow.repository.get, str(document_id))
        return await workflow._response(row)
