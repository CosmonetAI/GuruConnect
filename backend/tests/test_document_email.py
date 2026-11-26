"""SMTP2GO uses mocked transport: no external email or paid API call occurs."""
import base64
import json
from types import SimpleNamespace

import httpx
from pydantic import SecretStr
import pytest

from app.document_email import (EmailDeliveryError, MAX_PDF_BYTES, SMTP2GO_SEND_URL,
    delivery_fingerprint, delivery_replay, email_delivery_configuration, send_document_email)

PDF = b'%PDF-1.4\nsynthetic unit-test document\n%%EOF'
CONFIG = SimpleNamespace(smtp2go_api_key=SecretStr('synthetic-smtp-secret'),
    smtp2go_sender_email='sales@example.test', smtp2go_sender_name='GuruConnect Sales', smtp2go_timeout_seconds=20)
MESSAGE = {'recipient': 'customer@example.test', 'subject': 'Your quote', 'message': 'Hi,\nPlease find your quote attached.',
           'pdf_bytes': PDF, 'filename': 'quote-123.pdf', 'delivery_key': 'document-delivery-123'}
SUCCESS = {'request_id': 'provider-request-123', 'data': {'succeeded': 1, 'failed': 0, 'failures': [], 'email_id': 'provider-email-123'}}


@pytest.mark.asyncio
async def test_pdf_payload_and_receipt_use_documented_smtp2go_api_without_secrets_in_result():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=SUCCESS)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        receipt = await send_document_email(**MESSAGE, settings_override=CONFIG, client=client)
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == SMTP2GO_SEND_URL
    assert request.headers['X-Smtp2go-Api-Key'] == 'synthetic-smtp-secret'
    payload = json.loads(request.content)
    assert 'api_key' not in payload
    assert payload['sender'] == 'GuruConnect Sales <sales@example.test>'
    assert payload['to'] == ['customer@example.test']
    assert payload['text_body'] == MESSAGE['message']
    assert payload['fastaccept'] is True
    assert payload['attachments'] == [{'filename': 'quote-123.pdf', 'mimetype': 'application/pdf',
                                      'fileblob': base64.b64encode(PDF).decode()}]
    assert payload['custom_headers'][0]['header'] == 'X-GuruConnect-Delivery'
    assert len(payload['custom_headers'][0]['value']) == 64
    assert receipt['status'] == 'accepted'  # This is provider handoff, not inbox delivery.
    assert receipt['provider_email_id'] == SUCCESS['data']['email_id']
    assert receipt['provider_request_id'] == SUCCESS['request_id']
    assert 'synthetic-smtp-secret' not in json.dumps(receipt)


@pytest.mark.asyncio
async def test_missing_configuration_cannot_report_sent_or_make_a_request():
    attempts = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: attempts.append(request))) as client:
        missing = SimpleNamespace(smtp2go_api_key='', smtp2go_sender_email='', smtp2go_sender_name='GuruConnect')
        assert email_delivery_configuration(missing) == {'configured': False, 'provider': 'smtp2go', 'missing': ['SMTP2GO_API_KEY', 'SMTP2GO_SENDER_EMAIL']}
        with pytest.raises(EmailDeliveryError) as failure:
            await send_document_email(**MESSAGE, settings_override=missing, client=client)
    assert failure.value.code == 'email_not_configured'
    assert failure.value.uncertain is False
    assert not attempts


@pytest.mark.asyncio
@pytest.mark.parametrize('updates,code', [
    ({'recipient': 'not-an-address'}, 'email_invalid_recipient'),
    ({'recipient': 'a@example.test\r\nBcc: other@example.test'}, 'email_invalid_recipient'),
    ({'subject': 'Quote\r\nBcc: other@example.test'}, 'email_invalid_message'),
    ({'message': ''}, 'email_invalid_message'),
    ({'filename': '../private.pdf'}, 'email_invalid_attachment'),
    ({'filename': 'quote.txt'}, 'email_invalid_attachment'),
    ({'pdf_bytes': b'not actually a PDF'}, 'email_invalid_attachment'),
    ({'delivery_key': 'tiny'}, 'email_invalid_delivery_key'),
])
async def test_invalid_input_is_rejected_before_transmission(updates, code):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request))) as client:
        with pytest.raises(EmailDeliveryError) as failure:
            await send_document_email(**{**MESSAGE, **updates}, settings_override=CONFIG, client=client)
    assert failure.value.code == code and failure.value.uncertain is False
    assert not calls


@pytest.mark.asyncio
async def test_oversized_pdf_can_be_downloaded_but_is_not_transmitted():
    with pytest.raises(EmailDeliveryError) as failure:
        await send_document_email(**{**MESSAGE, 'pdf_bytes': b'%PDF-' + b'x' * MAX_PDF_BYTES}, settings_override=CONFIG)
    assert failure.value.code == 'email_attachment_too_large'


@pytest.mark.asyncio
@pytest.mark.parametrize('http_status,payload,code,uncertain,retryable', [
    (401, {'data': {'error': 'secret recipient@example.test'}}, 'email_provider_authentication_failed', False, False),
    (403, {}, 'email_provider_authentication_failed', False, False),
    (400, {'data': {'error': 'bad sender'}}, 'email_provider_rejected', False, False),
    (429, {}, 'email_provider_rate_limited', False, True),
    (503, {}, 'email_delivery_uncertain', True, False),
    (200, {'data': {'succeeded': 0, 'failed': 1, 'failures': ['recipient rejected']}}, 'email_provider_rejected', False, False),
    (200, {'data': {'error_code': 'E_Example', 'error': 'private error'}}, 'email_provider_rejected', False, False),
    (200, {'data': {'succeeded': 1, 'failed': 1, 'email_id': 'accepted-maybe'}}, 'email_delivery_uncertain', True, False),
    (200, {'data': {'succeeded': 1, 'failed': 0}}, 'email_delivery_uncertain', True, False),
    (200, {'data': {'succeeded': 1, 'failed': 0, 'email_id': 'accepted-maybe', 'failures': ['unexpected']}}, 'email_delivery_uncertain', True, False),
    (200, {}, 'email_delivery_uncertain', True, False),
])
async def test_http_success_and_transport_errors_never_hide_delivery_failures(http_status, payload, code, uncertain, retryable):
    requests = []
    async def handle(request):
        requests.append(request)
        return httpx.Response(http_status, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(EmailDeliveryError) as failure:
            await send_document_email(**MESSAGE, settings_override=CONFIG, client=client)
    assert len(requests) == 1
    assert failure.value.code == code
    assert failure.value.uncertain is uncertain and failure.value.retryable is retryable
    assert 'private error' not in json.dumps(failure.value.as_dict())
    assert 'recipient@example.test' not in json.dumps(failure.value.as_dict())
    assert 'synthetic-smtp-secret' not in json.dumps(failure.value.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize('exception,uncertain,retryable', [
    (httpx.ConnectTimeout, False, True), (httpx.ConnectError, False, True),
    (httpx.ReadTimeout, True, False), (httpx.WriteTimeout, True, False),
    (httpx.RemoteProtocolError, True, False),
])
async def test_lost_acknowledgement_is_uncertain_and_never_automatically_retried(exception, uncertain, retryable):
    attempts = []
    async def handle(request):
        attempts.append(request)
        raise exception('synthetic-smtp-secret raw error should not escape', request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(EmailDeliveryError) as failure:
            await send_document_email(**MESSAGE, settings_override=CONFIG, client=client)
    assert len(attempts) == 1
    assert failure.value.uncertain is uncertain and failure.value.retryable is retryable
    assert 'synthetic-smtp-secret' not in str(failure.value)


@pytest.mark.asyncio
async def test_redirect_cannot_forward_the_provider_credential():
    requests = []
    async def handle(request):
        requests.append(request)
        return httpx.Response(307, headers={'Location': 'https://other.example.invalid/send'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        with pytest.raises(EmailDeliveryError):
            await send_document_email(**MESSAGE, settings_override=CONFIG, client=client)
    assert len(requests) == 1 and str(requests[0].url) == SMTP2GO_SEND_URL


def test_durable_receipt_replay_and_payload_conflict_prevent_duplicate_delivery():
    fields = {key: value for key, value in MESSAGE.items() if key != 'delivery_key'}
    fingerprint = delivery_fingerprint(**fields)
    assert fingerprint == delivery_fingerprint(**{**fields, 'recipient': 'CUSTOMER@EXAMPLE.TEST'})
    receipt = {'provider': 'smtp2go', 'status': 'accepted', 'provider_email_id': 'provider-email-123'}
    assert delivery_replay({'fingerprint': fingerprint, 'status': 'accepted', 'receipt': receipt}, fingerprint) == receipt
    assert delivery_replay({'fingerprint': fingerprint, 'status': 'failed'}, fingerprint) is None
    for state in ('submitting', 'uncertain', 'unrecognised-state'):
        with pytest.raises(EmailDeliveryError) as error:
            delivery_replay({'fingerprint': fingerprint, 'status': state}, fingerprint)
        assert error.value.uncertain is True
    with pytest.raises(EmailDeliveryError) as error:
        delivery_replay({'fingerprint': 'different', 'status': 'accepted', 'receipt': receipt}, fingerprint)
    assert error.value.code == 'email_idempotency_conflict'
    with pytest.raises(EmailDeliveryError) as error:
        delivery_replay({'fingerprint': fingerprint, 'status': 'accepted'}, fingerprint)
    assert error.value.uncertain is True
