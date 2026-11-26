"""Sample PDFs and native Books downloads use no live provider credentials."""
from decimal import Decimal
import re

import httpx
import pytest

from app.sales_pdf import render_demo_sales_pdf
from app import zoho as zoho_module


def sample_lines():
    return [{'name': 'BLDC fan', 'sku': 'FAN-1200', 'rate': '4499.00', 'quantity': 2,
             'discount': 10, 'discount_type': 'percentage', 'tax_percentage': 18},
            {'name': 'Motor', 'rate': '1000.50', 'quantity': 1, 'discount': 100,
             'discount_type': 'fixed', 'tax_percentage': 0}]


def assert_valid_pdf_layout(pdf):
    assert pdf.startswith(b'%PDF-1.4') and pdf.endswith(b'%%EOF\n')
    xref = int(re.search(rb'startxref\n(\d+)\n%%EOF', pdf).group(1))
    assert pdf[xref:xref + 5] == b'xref\n'
    entries = re.search(rb'xref\n0 (\d+)\n(.*?)trailer', pdf, re.S)
    lines = entries.group(2).splitlines()
    assert len(lines) == int(entries.group(1))
    for identity, entry in enumerate(lines[1:], 1):
        offset = int(entry[:10])
        assert pdf[offset:].startswith(f'{identity} 0 obj\n'.encode())
    for match in re.finditer(rb'<< /Length (\d+) >>\nstream\n', pdf):
        length = int(match.group(1))
        assert pdf[match.end() + length:].startswith(b'endstream')


def test_sample_pdf_has_correct_totals_and_explicit_sample_labels():
    pdf = render_demo_sales_pdf('invoice', {'name': 'Demo customer', 'email': 'demo@example.test'}, sample_lines())
    assert_valid_pdf_layout(pdf)
    assert b'SAMPLE ONLY - NOT AN ISSUED BUSINESS DOCUMENT' in pdf
    assert b'INVOICE - SAMPLE PREVIEW' in pdf
    assert b'Subtotal: INR 9,998.50' in pdf
    assert b'Discount: - INR 999.80' in pdf
    assert b'Calculated tax: INR 1,457.68' in pdf
    # 8998.00 - 899.80 + 1457.68 + 1000.50 - 100 = 10456.38.
    assert b'Preview total: INR 10,456.38' in pdf
    assert b'no document has been issued' in pdf
    assert b'Invoice number' not in pdf and b'GSTIN' not in pdf


def test_sample_escapes_pdf_controls_and_preserves_unicode_actual_text():
    customer = 'समीर (test) \\ customer'
    pdf = render_demo_sales_pdf('quote', {'name': customer}, [
        {'name': 'Fan ) Tj /OpenAction (evil', 'rate': 10, 'quantity': 1},
    ])
    assert_valid_pdf_layout(pdf)
    assert b'Fan \\) Tj /OpenAction \\(evil' in pdf
    assert b'\\(test\\)' in pdf and b'\\\\' in pdf
    assert b'[U+0938]' in pdf and b'Demo font notice' in pdf
    # Unicode names are losslessly present as UTF-16 PDF ActualText even if
    # the portable demo font cannot draw the requested script.
    encoded_name = customer.split(' (', 1)[0].encode('utf-16-be').hex().upper().encode()
    assert encoded_name in pdf


def test_sample_missing_tax_is_not_silently_presented_as_tax_free():
    pdf = render_demo_sales_pdf('sales-order', 'Demo customer', [{'name': 'Motor', 'rate': '99.99', 'quantity': 2}])
    assert b'Tax: not supplied' in pdf
    assert b'Tax: not fully calculated' in pdf
    assert b'before any unprovided tax' in pdf
    assert b'GST' not in pdf


def test_sample_paginates_long_carts_and_repeats_sample_label():
    lines = [{'name': f'Item {index} ' + 'long product description ' * 6, 'rate': Decimal('0.10'), 'quantity': 3,
              'tax_percentage': 0} for index in range(25)]
    pdf = render_demo_sales_pdf('sales-order', 'Demo customer', lines)
    assert_valid_pdf_layout(pdf)
    count = int(re.search(rb'/Type /Pages /Count (\d+)', pdf).group(1))
    assert count > 2
    assert pdf.count(b'SAMPLE ONLY - no document has been issued') == count
    assert b'025. Item 24' in pdf and b'Preview total: INR 7.50' in pdf


@pytest.mark.parametrize('line', [
    {'rate': -1}, {'rate': 'NaN'}, {'rate': 'Infinity'}, {'rate': 1, 'quantity': 0},
    {'rate': 1, 'discount': 101}, {'rate': 1, 'discount': 2, 'discount_type': 'fixed'},
    {'rate': 1, 'tax_percentage': -1}, {'rate': 1, 'tax_percentage': 101},
])
def test_invalid_sample_money_and_quantities_fail_instead_of_misleading_totals(line):
    with pytest.raises(ValueError):
        render_demo_sales_pdf('invoice', 'Demo', [{'name': 'Fan', **line}])


_PDF = b'%PDF-1.4\nexample content\n%%EOF\n'


class DownloadClient(zoho_module.ZohoClient):
    def __init__(self, domain='https://www.zohoapis.in', selected='12345'):
        self.domain, self.selected, self.refreshed = domain, selected, []

    async def refresh(self, force=False):
        self.refreshed.append(force)
        return True

    def _connection(self):
        return {'organization_id': self.selected, 'api_domain': self.domain,
                'access_token': 'synthetic-refreshed' if True in self.refreshed else 'synthetic-original'}


@pytest.fixture
def mock_download_http(monkeypatch):
    real_client = httpx.AsyncClient
    calls = []

    def install(handler):
        def factory(**kwargs):
            assert kwargs['follow_redirects'] is False
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        monkeypatch.setattr(zoho_module.httpx, 'AsyncClient', factory)
        return calls

    return install


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,resource', [('invoice', 'invoices'), ('quote', 'estimates'),
                                         ('sales-order', 'salesorders'), ('purchase-order', 'purchaseorders')])
async def test_download_uses_native_pdf_endpoint_and_connected_member_credentials(mock_download_http, kind, resource):
    def handler(request):
        assert str(request.url).startswith(f'https://www.zohoapis.in/books/v3/{resource}/98765?')
        assert dict(request.url.params) == {'organization_id': '12345', 'accept': 'pdf'}
        assert request.headers['Accept'] == 'application/pdf'
        assert request.headers['Authorization'] == 'Zoho-oauthtoken synthetic-original'
        return httpx.Response(200, headers={'Content-Type': 'application/pdf; charset=binary'}, content=_PDF)

    mock_download_http(handler)
    assert await DownloadClient().download_sales_document('12345', kind, '98765') == _PDF


@pytest.mark.asyncio
async def test_download_refreshes_once_on_401_without_leaking_old_token(mock_download_http):
    attempts = []

    def handler(request):
        attempts.append(request.headers['Authorization'])
        return (httpx.Response(401) if len(attempts) == 1 else
                httpx.Response(200, headers={'Content-Type': 'application/pdf'}, content=_PDF))

    mock_download_http(handler)
    client = DownloadClient()
    assert await client.download_sales_document('12345', 'invoice', '98765') == _PDF
    assert attempts == ['Zoho-oauthtoken synthetic-original', 'Zoho-oauthtoken synthetic-refreshed']
    assert client.refreshed == [False, True]


@pytest.mark.asyncio
@pytest.mark.parametrize('domain', [
    'http://www.zohoapis.in', 'https://www.zohoapis.in.evil.test', 'https://www.zohoapis.in@evil.test',
    'https://www.zohoapis.in/path', 'https://www.zohoapis.in?next=evil', 'https://www.zohoapis.in:444',
])
async def test_download_rejects_nonallowlisted_domains_before_sending_credentials(mock_download_http, domain):
    def forbidden(request):
        pytest.fail('Credentials must never be sent to a nonallowlisted endpoint')
    mock_download_http(forbidden)
    with pytest.raises(ValueError):
        await DownloadClient(domain=domain).download_sales_document('12345', 'invoice', '98765')


@pytest.mark.asyncio
async def test_download_rejects_unselected_books_organization_before_network(mock_download_http):
    mock_download_http(lambda request: pytest.fail('Wrong Books organization must not be queried'))
    with pytest.raises(PermissionError):
        await DownloadClient().download_sales_document('99999', 'invoice', '98765')


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [
    httpx.Response(302, headers={'location': 'https://untrusted.example/download'}),
    httpx.Response(200, headers={'content-type': 'text/html'}, content=b'<html>Error</html>'),
    httpx.Response(200, headers={'content-type': 'application/pdf'}, content=b'{"error":true}'),
    httpx.Response(200, headers={'content-type': 'application/pdf'}, content=b'%PDF-1.4 incomplete'),
    httpx.Response(200, headers={'content-type': 'application/pdf', 'content-length': str(8 * 1024 * 1024 + 1)}, content=_PDF),
])
async def test_download_rejects_redirects_nonpdf_truncation_and_declared_oversize(mock_download_http, response):
    attempts = []

    def handler(request):
        attempts.append(request.url)
        return response

    mock_download_http(handler)
    with pytest.raises(ValueError):
        await DownloadClient().download_sales_document('12345', 'invoice', '98765')
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_download_bounds_stream_when_content_length_is_missing(mock_download_http):
    class LargeStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(130):
                yield b'x' * 65536

    mock_download_http(lambda request: httpx.Response(200, headers={'content-type': 'application/pdf'}, stream=LargeStream()))
    with pytest.raises(ValueError, match='size limit'):
        await DownloadClient().download_sales_document('12345', 'invoice', '98765')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,identity', [('invoice', '../98765'), ('invoice', 'https://evil.test/'), ('unknown', '98765')])
async def test_download_document_path_is_allowlisted(mock_download_http, kind, identity):
    mock_download_http(lambda request: pytest.fail('Invalid paths must not reach the network'))
    with pytest.raises(ValueError):
        await DownloadClient().download_sales_document('12345', kind, identity)
