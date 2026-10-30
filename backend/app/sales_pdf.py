"""Dependency-free PDF previews for the local sales demo.

Business documents are created and rendered by Zoho Books. This writer is only
for clearly labelled demo samples; it never invents a registration number,
invoice number, tax percentage or payment status.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any


_CENT = Decimal('0.01')
_DOCUMENT_NAMES = {
    'invoice': 'Invoice', 'quote': 'Quotation', 'estimate': 'Quotation',
    'sales-order': 'Sales order', 'sales_order': 'Sales order', 'order': 'Sales order',
    'purchase-order': 'Purchase order', 'purchase_order': 'Purchase order',
}


def _decimal(value: Any, name: str, *, default: str | None = None) -> Decimal:
    if value in (None, ''):
        if default is None:
            raise ValueError(f'{name} is required')
        value = default
    try:
        result = Decimal(str(value).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f'Invalid {name}') from None
    if not result.is_finite() or result < 0 or result > Decimal('1000000000000'):
        raise ValueError(f'Invalid {name}')
    return result if result else Decimal('0')


def _money(value: Decimal, currency: str) -> str:
    return f'{currency} {value.quantize(_CENT, rounding=ROUND_HALF_UP):,.2f}'


def _clean(value: Any, maximum: int = 1000) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:maximum]


def _latin_fallback(value: str) -> tuple[str, bool]:
    """WinAnsi is portable in PDF readers; missing glyphs stay explicit.

    Original Unicode is also included as PDF ActualText, so copy/search retains
    the original name rather than the visible U+XXXX fallback. Native Zoho PDFs
    provide their own embedded fonts and do not use this demo-only fallback.
    """
    chunks = []
    changed = False
    for character in value:
        try:
            character.encode('cp1252')
            chunks.append(character)
        except UnicodeEncodeError:
            chunks.append(f'[U+{ord(character):04X}]')
            changed = True
    return ''.join(chunks), changed


def _literal(value: str) -> bytes:
    return value.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)').encode('cp1252')


def _wrap_line(value: str, width: int) -> list[str]:
    """Wrap by visible fallback width while preserving source words for copy."""
    indent = ' ' * min(8, len(value) - len(value.lstrip()))
    line, result = indent, []
    for word in value.split():
        candidate = line + (' ' if line.strip() else '') + word
        if len(_latin_fallback(candidate)[0]) <= width:
            line = candidate
            continue
        if line.strip():
            result.append(line)
            line = indent
        for character in word:
            if len(_latin_fallback(line + character)[0]) > width and line.strip():
                result.append(line)
                line = indent
            line += character
    if line.strip():
        result.append(line)
    return result or [' ']


def _draw_text(text: str, *, x: float, y: float, size: float = 10, font: str = 'F1', color: str = '0.12 0.20 0.32') -> bytes:
    visible, _ = _latin_fallback(text)
    original = ('\ufeff' + text).encode('utf-16-be', errors='replace').hex().upper().encode('ascii')
    return (f'BT /{font} {size:g} Tf {color} rg 1 0 0 1 {x:g} {y:g} Tm '.encode('ascii')
            + b'/Span << /ActualText <' + original + b'> >> BDC (' + _literal(visible)
            + b') Tj EMC ET\n')


def _build_pdf(pages: list[bytes], title: str) -> bytes:
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R /MarkInfo << /Marked true >> >>',
        b'',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>',
        b'<< /Title <' + ('\ufeff' + title).encode('utf-16-be').hex().encode('ascii')
        + b'> /Creator (GuruConnect demo preview) /Subject (Sample only - not an issued business document) >>',
    ]
    page_ids = []
    for content in pages:
        page_id = len(objects) + 1
        stream_id = page_id + 1
        page_ids.append(page_id)
        objects.append((f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] '
                        f'/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> '
                        f'/Contents {stream_id} 0 R >>').encode('ascii'))
        objects.append(f'<< /Length {len(content)} >>\nstream\n'.encode('ascii') + content + b'endstream')
    objects[1] = (f'<< /Type /Pages /Count {len(page_ids)} /Kids ['
                  + ' '.join(f'{page_id} 0 R' for page_id in page_ids) + '] >>').encode('ascii')
    output = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = [0]
    for identity, value in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f'{identity} 0 obj\n'.encode('ascii') + value + b'\nendobj\n')
    start_xref = len(output)
    output.extend(f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode('ascii'))
    for offset in offsets[1:]:
        output.extend(f'{offset:010d} 00000 n \n'.encode('ascii'))
    output.extend((f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\n'
                   f'startxref\n{start_xref}\n%%EOF\n').encode('ascii'))
    return bytes(output)


def render_demo_sales_pdf(document_type: str, customer: dict | str, lines: list[dict], currency: str = 'INR') -> bytes:
    """Render a valid, paginated sample PDF from server-validated demo items.

    Line keys: name/product, sku, rate, quantity, discount, discount_type
    (percentage/fixed), and optional tax_percentage. Unknown tax remains visibly
    uncalculated. The caller owns catalogue validation and customer authorization.
    """
    name = _DOCUMENT_NAMES.get(str(document_type).strip().lower())
    currency = str(currency).strip().upper()
    if name is None or not re.fullmatch(r'[A-Z]{3,5}', currency):
        raise ValueError('Invalid document type or currency')
    if not isinstance(lines, list) or not 1 <= len(lines) <= 500:
        raise ValueError('Choose between 1 and 500 document items')
    customer = customer if isinstance(customer, dict) else {'name': str(customer)}
    # Each tuple is a styled logical line. Pagination happens after long names
    # are wrapped; no selected products disappear when a cart spans pages.
    rows: list[tuple[str, str, int]] = [
        ('SAMPLE ONLY - NOT AN ISSUED BUSINESS DOCUMENT', 'F2', 11),
        (f'{name} preview for: {_clean(customer.get("name") or customer.get("customer_name") or "Demo customer")}', 'F2', 12),
    ]
    for field, label in [('email', 'Email'), ('phone', 'Phone')]:
        if customer.get(field):
            rows.append((f'{label}: {_clean(customer[field], 320)}', 'F1', 10))
    rows.extend([(' ', 'F1', 8), ('Selected products', 'F2', 12)])
    subtotal = discount_total = tax_total = Decimal('0')
    unknown_tax = False
    for index, line in enumerate(lines, 1):
        if not isinstance(line, dict):
            raise ValueError('Invalid document item')
        rate = _decimal(line.get('rate'), 'item rate')
        quantity = _decimal(line.get('quantity'), 'item quantity', default='1')
        if quantity == 0:
            raise ValueError('Item quantity must be positive')
        base = (rate * quantity).quantize(_CENT, rounding=ROUND_HALF_UP)
        entered_discount = _decimal(line.get('discount'), 'item discount', default='0')
        kind = str(line.get('discount_type') or 'percentage').strip().lower()
        if kind in {'percentage', 'percent', '%'}:
            if entered_discount > 100:
                raise ValueError('Percentage discount cannot exceed 100')
            discount = (base * entered_discount / 100).quantize(_CENT, rounding=ROUND_HALF_UP)
        elif kind in {'fixed', 'amount', 'flat'}:
            discount = entered_discount.quantize(_CENT, rounding=ROUND_HALF_UP)
        else:
            raise ValueError('Invalid discount type')
        if discount > base:
            raise ValueError('Discount cannot exceed the line amount')
        taxable = base - discount
        tax_value = line.get('tax_percentage')
        if tax_value in (None, ''):
            unknown_tax = True
            tax = Decimal('0')
            tax_label = 'Tax: not supplied'
        else:
            tax_rate = _decimal(str(tax_value).removesuffix('%'), 'tax percentage')
            if tax_rate > 100:
                raise ValueError('Invalid tax percentage')
            tax = (taxable * tax_rate / 100).quantize(_CENT, rounding=ROUND_HALF_UP)
            tax_label = f'Tax {tax_rate.normalize():f}%: {_money(tax, currency)}'
        subtotal += base
        discount_total += discount
        tax_total += tax
        product = _clean(line.get('name') or line.get('item_name') or line.get('product') or f'Item {index}')
        rows.append((f'{index:03d}. {product}', 'F2', 10))
        if line.get('sku'):
            rows.append((f'      SKU: {_clean(line["sku"], 120)}', 'F1', 9))
        rows.append((f'      Quantity {quantity.normalize():f}  x  {_money(rate, currency)}', 'F1', 10))
        rows.append((f'      Discount: {_money(discount, currency)} | {tax_label}', 'F1', 9))
        rows.append((f'      Line total: {_money(taxable + tax, currency)}', 'F2', 10))
        rows.append((' ', 'F1', 7))
    rows.extend([
        ('Totals', 'F2', 12),
        (f'Subtotal: {_money(subtotal, currency)}', 'F1', 11),
        (f'Discount: - {_money(discount_total, currency)}', 'F1', 11),
        (f'Calculated tax: {_money(tax_total, currency)}' if not unknown_tax else 'Tax: not fully calculated; catalogue tax details are missing.', 'F1', 10),
        (f'{"Preview total before any unprovided tax" if unknown_tax else "Preview total"}: '
         f'{_money(subtotal - discount_total + tax_total, currency)}', 'F2', 12),
        (' ', 'F1', 8),
        ('This sample creates no Zoho document, sends no email, and records no payment.', 'F1', 9),
        ('For an issued invoice, quotation or order, create the document through the connected Zoho Books account.', 'F1', 9),
    ])
    has_fallback = any(_latin_fallback(value)[1] for value, _, _ in rows)
    if has_fallback:
        rows.append(('Demo font notice: unsupported characters display as U+codes; copying the text preserves the original Unicode name.', 'F1', 9))
    wrapped = []
    for value, font, size in rows:
        # Fallback labels expand some scripts, so use their visible width to
        # reserve enough room without dropping the original ActualText.
        width = 67 if size >= 11 else 88
        wrapped.extend((paragraph, font, size) for paragraph in _wrap_line(value, width))
    chunks = [wrapped[index:index + 40] for index in range(0, len(wrapped), 40)]
    pages = []
    for number, chunk in enumerate(chunks, 1):
        content = bytearray(b'0.06 0.18 0.35 rg 0 760 595 82 re f\n')
        content.extend(_draw_text('GuruConnect', x=42, y=801, font='F2', size=22, color='1 1 1'))
        content.extend(_draw_text(f'{name.upper()} - SAMPLE PREVIEW', x=42, y=777, font='F2', size=11, color='0.72 0.84 1'))
        for index, (value, font, size) in enumerate(chunk):
            content.extend(_draw_text(value, x=42, y=730 - index * 16, font=font, size=size))
        content.extend(b'0.83 0.88 0.94 RG 42 57 m 553 57 l S\n')
        content.extend(_draw_text('SAMPLE ONLY - no document has been issued', x=42, y=40, size=9))
        content.extend(_draw_text(f'Page {number} of {len(chunks)}', x=463, y=40, size=9))
        pages.append(bytes(content))
    return _build_pdf(pages, f'GuruConnect {name} sample preview')
