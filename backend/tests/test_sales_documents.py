import asyncio
import base64
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.document_email import EmailDeliveryError
from app.sales_documents import DocumentRepository, DocumentEmailInput, SalesDocumentInput, SalesDocumentService
from recorder.config import Settings
from recorder.local_api import create_local_app
from recorder.local_store import LocalStore


PDF = b'%PDF-1.4\nfixture\n%%EOF\n'


class Books:
    def __init__(self):
        self.created, self.pdf_calls, self.fail_create, self.fail_pdf = [], [], False, False
        self.fetched, self.contact_calls = [], 0
        self.item = {'item_id': '123', 'name': 'Fan', 'sku': 'FAN', 'rate': 1000, 'tax_id': '456',
                     'status': 'active', 'can_be_sold': True, 'item_type': 'inventory',
                     'product_type': 'goods', 'track_inventory': True, 'stock_on_hand': 12}

    def status(self):
        return {'connected': True, 'organization_id': '1234', 'currency': 'INR'}

    async def request(self, method, path, **kwargs):
        assert method == 'GET' and path == '/books/v3/items/123'
        self.fetched.append((path, kwargs))
        return {'item': dict(self.item)}

    async def ensure_books_contact(self, org, **kwargs):
        assert org == '1234'
        self.contact_calls += 1
        return {'contact_id': '234'}

    async def create_invoice(self, org, payload):
        self.created.append(payload)
        if self.fail_create:
            raise TimeoutError('unsafe-provider-text')
        return {'invoice': {'invoice_id': '345', 'invoice_number': 'INV/3', 'total': 1800, 'tax_total': 0, 'currency_code': 'INR'}}

    async def create_estimate(self, org, payload):
        self.created.append(payload)
        return {'estimate': {'estimate_id': '346', 'estimate_number': 'EST-3'}}

    async def create_sales_order(self, org, payload):
        self.created.append(payload)
        return {'salesorder': {'salesorder_id': '347', 'salesorder_number': 'SO-3'}}

    async def download_sales_document(self, org, kind, identity):
        self.pdf_calls.append((org, kind, identity))
        if self.fail_pdf:
            raise RuntimeError('private-remote-message')
        return PDF


def setup(tmp_path, *, configured=True, sender=None):
    scope = {'organisation_id': str(uuid4()), 'member_id': str(uuid4()), 'session_id': str(uuid4())}
    repo = DocumentRepository(scope, local_store=LocalStore(tmp_path))
    config = SimpleNamespace(smtp2go_api_key='synthetic' if configured else '', smtp2go_sender_email='sales@example.test', smtp2go_sender_name='GuruConnect', app_timezone='Asia/Kolkata')
    books = Books()
    service = SalesDocumentService(repo, {'name': 'Synthetic customer', 'phone': '9876543210', 'email': ''}, zoho=books, settings=config, sender=sender)
    return service, books


def body(**updates):
    return SalesDocumentInput(**{'request_id': str(uuid4()), 'type': 'invoice', 'delivery': 'download',
        'recipient': '', 'subject': 'Invoice', 'message': 'Your invoice is attached.',
        'line_items': [{'item_id': '123', 'quantity': 2, 'rate': 1000, 'discount': 200, 'discount_type': 'fixed'}], **updates})


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['invoice', 'quote', 'sales-order'])
async def test_no_email_creates_real_books_document_and_downloads_native_pdf_once(tmp_path, kind):
    service, books = setup(tmp_path)
    request = body(type=kind)
    first = await service.create(request)
    second = await service.create(request)
    assert first['document']['id'] == second['document']['id']
    assert len(books.created) == 1
    assert first['delivery']['status'] == 'not_requested'
    assert first['document']['demo'] is False
    assert base64.b64decode(first['pdf']['base64']) == PDF
    assert first['pdf']['filename'].endswith('.pdf')
    # Equivalent percentage preserves the flat whole-line saving for any qty.
    assert books.created[0]['line_items'][0]['discount'] == '10%'
    assert books.created[0]['line_items'][0]['tax_id'] == '456'
    assert books.created[0]['reference_number'].startswith('GC-')
    assert 'discount_amount' not in books.created[0]['line_items'][0]


@pytest.mark.asyncio
async def test_missing_mail_configuration_never_creates_a_document_then_download_still_works(tmp_path):
    service, books = setup(tmp_path, configured=False)
    with pytest.raises(HTTPException) as error:
        await service.create(body(delivery='email', recipient='customer@example.test'))
    assert error.value.status_code == 503
    assert error.value.detail['document_started'] is False
    assert books.created == []
    assert (await service.create(body()))['delivery']['status'] == 'not_requested'


@pytest.mark.asyncio
async def test_email_retry_reuses_document_and_never_duplicates_accepted_mail(tmp_path):
    calls = []

    async def sender(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise EmailDeliveryError('email_provider_rate_limited', 503, retryable=True)
        return {'provider': 'smtp2go', 'provider_email_id': 'receipt-1', 'status': 'accepted'}

    service, books = setup(tmp_path, sender=sender)
    request = body(delivery='email', recipient='customer@example.test')
    initial = await service.create(request)
    assert initial['delivery']['status'] == 'failed'
    assert len(books.created) == len(calls) == 1
    replay = await service.create(request)
    assert replay['delivery']['status'] == 'failed' and len(calls) == 1
    retry = DocumentEmailInput(request_id=uuid4(), recipient='corrected@example.test', subject='Your invoice', message='Please find it attached.')
    sent = await service.email(initial['document']['id'], retry)
    assert sent['delivery']['status'] == 'accepted'
    assert len(calls) == 2 and len(books.created) == 1
    assert calls[-1]['recipient'] == 'corrected@example.test'
    assert calls[-1]['pdf_bytes'] == PDF
    await service.email(initial['document']['id'], retry)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_lost_email_ack_and_creation_ack_require_review_instead_of_automatic_resubmission(tmp_path):
    calls = []

    async def sender(**kwargs):
        calls.append(kwargs)
        raise EmailDeliveryError('email_delivery_uncertain', uncertain=True)

    service, books = setup(tmp_path, sender=sender)
    request = body(delivery='email', recipient='customer@example.test')
    result = await service.create(request)
    assert result['delivery']['status'] == 'uncertain'
    with pytest.raises(HTTPException):
        await service.email(result['document']['id'], DocumentEmailInput(request_id=uuid4(), recipient='customer@example.test', subject='Retry', message='Retry'))
    assert len(calls) == 1
    books.fail_create = True
    other = body()
    with pytest.raises(HTTPException) as error:
        await service.create(other)
    assert error.value.detail['document_started'] is True
    with pytest.raises(HTTPException) as retry_error:
        await service.create(other)
    assert retry_error.value.status_code == 409
    assert len(books.created) == 2
    assert 'unsafe-provider' not in str(error.value.detail)


@pytest.mark.asyncio
async def test_concurrent_same_request_never_creates_twice_or_accepts_changed_cart(tmp_path):
    service, books = setup(tmp_path)
    request = body()
    result = await asyncio.gather(service.create(request), service.create(request), return_exceptions=True)
    assert len(books.created) == 1
    completed = next(row for row in result if isinstance(row, dict))
    assert completed['document']['external_id'] == '345'
    with pytest.raises(HTTPException):
        await service.create(request.model_copy(update={'type': 'quote'}))
    assert len(books.created) == 1


@pytest.mark.asyncio
async def test_prices_and_discounts_validated_before_any_creation_and_pdf_failure_is_recoverable(tmp_path):
    service, books = setup(tmp_path)
    for lines in [[{'item_id': '123', 'quantity': 1, 'rate': 1}],
                  [{'item_id': '123', 'quantity': 1, 'rate': 1000, 'discount': 101, 'discount_type': 'percentage'}]]:
        with pytest.raises(HTTPException):
            await service.create(body(line_items=lines))
    assert not books.created
    books.fail_pdf = True
    request = body()
    with pytest.raises(HTTPException) as error:
        await service.create(request)
    assert error.value.detail['document_started'] is True
    books.fail_pdf = False
    assert (await service.create(request))['document']['external_id'] == '345'
    assert len(books.created) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('rate', [None, 0, -10, True, '', 'unknown', 'NaN', float('nan'), float('inf'), float('-inf'), '1e-999999', '1e999999'])
async def test_unknown_zero_and_nonfinite_current_catalogue_prices_never_create_free_documents(tmp_path, rate):
    service, books = setup(tmp_path)
    books.item['rate'] = rate
    # An explicit marker in a stale recommendation cannot authorize a zero
    # Books price through this workflow either.
    books.item['is_free'] = True
    with pytest.raises(HTTPException) as error:
        await service.create(body())
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'catalogue_price_unconfirmed'
    assert error.value.detail['document_started'] is False
    assert books.created == [] and books.contact_calls == 0
    assert books.fetched == [('/books/v3/items/123', {'params': {'organization_id': '1234'}})]


@pytest.mark.parametrize('rate', [0, -1, float('nan'), float('inf')])
def test_zero_and_nonfinite_submitted_prices_are_invalid_request_values(rate):
    with pytest.raises(ValidationError):
        body(line_items=[{'item_id': '123', 'quantity': 1, 'rate': rate}])


@pytest.mark.asyncio
@pytest.mark.parametrize(('updates', 'code'), [
    ({'available_stock': -2, 'stock_on_hand': 12}, 'catalogue_out_of_stock'),
    ({'available_stock': 0, 'stock_on_hand': 12}, 'catalogue_out_of_stock'),
    ({'stock_on_hand': 0}, 'catalogue_out_of_stock'),
    ({'stock_on_hand': -1}, 'catalogue_out_of_stock'),
    ({'stock_on_hand': None}, 'catalogue_stock_unconfirmed'),
    ({'stock_on_hand': float('nan')}, 'catalogue_stock_unconfirmed'),
    ({'available_stock': 'unknown', 'stock_on_hand': 12}, 'catalogue_stock_unconfirmed'),
    ({'available_stock': float('inf'), 'stock_on_hand': 12}, 'catalogue_stock_unconfirmed'),
    ({'availability_status': 'out_of_stock', 'stock_on_hand': 12}, 'catalogue_out_of_stock'),
    ({'availability_status': 'needs_review', 'stock_on_hand': 12}, 'catalogue_stock_unconfirmed'),
    ({'available_stock': 1, 'stock_on_hand': 12}, 'catalogue_stock_insufficient'),
])
async def test_current_inventory_must_have_confirmed_sufficient_available_stock(tmp_path, updates, code):
    service, books = setup(tmp_path)
    books.item.update(updates)
    with pytest.raises(HTTPException) as error:
        await service.create(body())
    assert error.value.status_code == 409
    assert error.value.detail['code'] == code
    assert error.value.detail['document_started'] is False
    assert books.created == [] and books.contact_calls == 0


@pytest.mark.asyncio
async def test_duplicate_lines_share_one_fresh_item_lookup_and_the_same_stock_limit(tmp_path):
    service, books = setup(tmp_path)
    books.item.update(available_stock=3, stock_on_hand=12)
    request = body(line_items=[{'item_id': '123', 'quantity': 2, 'rate': 1000},
                               {'item_id': '123', 'quantity': 2, 'rate': 1000}])
    with pytest.raises(HTTPException) as error:
        await service.create(request)
    assert error.value.detail['code'] == 'catalogue_stock_insufficient'
    assert len(books.fetched) == 1 and books.created == []
    books.item['available_stock'] = 4
    result = await service.create(request)
    assert result['document']['external_id'] == '345'
    assert len(books.created) == 1 and len(books.created[0]['line_items']) == 2
    assert len(books.fetched) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['invoice', 'quote', 'sales-order'])
@pytest.mark.parametrize('metadata', [
    {'product_type': 'service', 'track_inventory': False},
    {'product_type': 'digital_service'},
    {'product_type': 'capital_service'},
    {'item_type': 'sales'},
    {'item_type': 'sales_and_purchases', 'product_type': 'goods'},
    {'track_inventory': False, 'product_type': 'goods'},
])
async def test_explicit_noninventory_services_and_goods_do_not_require_or_invent_stock(tmp_path, kind, metadata):
    service, books = setup(tmp_path)
    for field in ('item_type', 'product_type', 'track_inventory', 'stock_on_hand'):
        books.item.pop(field, None)
    books.item.update(metadata)
    # Some Books service records include meaningless zero stock counters.
    books.item.update(stock_on_hand=0, available_stock=0)
    result = await service.create(body(type=kind))
    assert result['document']['external_id']
    assert len(books.created) == 1
    assert all('stock' not in key for key in books.created[0]['line_items'][0])


@pytest.mark.asyncio
@pytest.mark.parametrize('metadata', [
    {'product_type': 'goods'},
    {'product_type': 'service', 'track_inventory': True},
    {'item_type': 'inventory', 'track_inventory': False},
    {},
])
async def test_missing_or_contradictory_tracking_metadata_cannot_assume_inventory_available(tmp_path, metadata):
    service, books = setup(tmp_path)
    for field in ('item_type', 'product_type', 'track_inventory', 'stock_on_hand'):
        books.item.pop(field, None)
    books.item.update(metadata)
    with pytest.raises(HTTPException) as error:
        await service.create(body())
    assert error.value.detail['code'] == 'catalogue_stock_unconfirmed'
    assert books.created == []


@pytest.mark.asyncio
@pytest.mark.parametrize(('updates', 'code'), [
    ({'rate': 1100}, 'catalogue_price_changed'),
    ({'price_status': 'confirmation_required'}, 'catalogue_price_unconfirmed'),
    ({'item_id': '999'}, 'catalogue_item_unavailable'),
    ({'status': 'inactive'}, 'catalogue_item_unavailable'),
    ({'status': 'confirmation_pending'}, 'catalogue_item_unavailable'),
    ({'can_be_sold': False}, 'catalogue_item_unavailable'),
    ({'can_be_sold': 'false'}, 'catalogue_item_unavailable'),
    ({'item_type': 'purchases'}, 'catalogue_item_unavailable'),
])
async def test_stale_cart_rechecks_authoritative_item_identity_sellability_and_price_before_creation(tmp_path, updates, code):
    service, books = setup(tmp_path)
    books.item.update(updates)
    with pytest.raises(HTTPException) as error:
        await service.create(body())
    assert error.value.detail['code'] == code
    assert len(books.fetched) == 1
    assert books.created == [] and books.contact_calls == 0


@pytest.mark.asyncio
async def test_native_books_price_difference_stops_email_without_recreating_invoice(tmp_path):
    service, books = setup(tmp_path)
    calls = []

    async def unexpected_mail(**kwargs):
        calls.append(kwargs)
        raise AssertionError('Unreviewed invoice must not be emailed')

    original = books.create_invoice
    async def different_total(org, payload):
        result = await original(org, payload)
        result['invoice']['line_items'] = [{'item_total': 2000}]
        return result

    books.create_invoice = different_total
    service.sender = unexpected_mail
    request = body(delivery='email', recipient='customer@example.test')
    with pytest.raises(HTTPException) as error:
        await service.create(request)
    assert error.value.detail['code'] == 'document_pricing_review'
    assert error.value.detail['document_started'] is True
    with pytest.raises(HTTPException):
        await service.create(request)
    assert len(books.created) == 1 and not calls


def test_receptionist_cannot_create_or_send_financial_documents():
    from app.main import _sales_document_service
    from app.db import sales_workspace
    token = sales_workspace.set(SimpleNamespace(role='receptionist'))
    try:
        with pytest.raises(HTTPException) as error:
            _sales_document_service(str(uuid4()))
        assert error.value.status_code == 403
    finally:
        sales_workspace.reset(token)


def test_local_real_api_opens_without_email_downloads_sample_pdf_and_scopes_saved_document(tmp_path):
    settings = Settings(_env_file=None, local_data_dir=tmp_path, environment='local', database_url=None,
        supabase_url='', supabase_service_role_key=None, embedded_processing_enabled=False)
    app = create_local_app(settings)
    with TestClient(app, base_url='http://127.0.0.1:8010', client=('127.0.0.1', 10000)) as client:
        session = client.post('/api/voice-sessions', json={'name': 'No email customer', 'phone': '9876543210'}).json()['session']['id']
        options = client.get(f'/api/voice-sessions/{session}/sales-documents/options').json()
        assert options['customer']['email'] == '' and options['email']['configured'] is False
        payload = {'request_id': str(uuid4()), 'type': 'quote', 'delivery': 'download',
                   'line_items': [{'item_id': 'DEMO-FAN-BLDC1200', 'quantity': 2, 'rate': 4499}]}
        response = client.post(f'/api/voice-sessions/{session}/sales-documents', json=payload)
        assert response.status_code == 200, response.text
        result = response.json()
        pdf = base64.b64decode(result['pdf']['base64'])
        assert pdf.startswith(b'%PDF-') and pdf.rstrip().endswith(b'%%EOF')
        assert b'SAMPLE' in pdf and result['document']['demo'] is True
        assert client.post(f'/api/voice-sessions/{session}/sales-documents', json=payload).json()['document']['id'] == result['document']['id']
        other = client.post('/api/voice-sessions', json={'name': 'Other customer', 'phone': '9876543211'}).json()['session']['id']
        assert client.get(f'/api/voice-sessions/{other}/sales-documents/{result["document"]["id"]}').status_code == 404
        assert client.post(f'/api/voice-sessions/{session}/sales-documents', json={**payload, 'request_id': str(uuid4()), 'delivery': 'email', 'recipient': 'someone@example.test'}).status_code == 409
