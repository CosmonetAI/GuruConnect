import pytest

from app import main


class BooksActionsFake:
    def __init__(self):
        self.payment = None
        self.accepted = None

    def status(self):
        return {'connected': True, 'organization_id': 'org-1'}

    async def books_invoice(self, org, invoice_id):
        assert (org, invoice_id) == ('org-1', 'inv-1')
        return {'invoice': {'invoice_id': invoice_id, 'customer_id': 'customer-1', 'balance': 1250}}

    async def list_books_chart_of_accounts(self, org):
        assert org == 'org-1'
        return {'chartofaccounts': [{'account_id': 'cash-1', 'account_type': 'cash'}]}

    async def create_customer_payment(self, org, payload):
        self.payment = (org, payload)
        return {'code': 0, 'message': 'Payment recorded'}

    async def accept_estimate(self, org, estimate_id):
        self.accepted = (org, estimate_id)
        return {'code': 0, 'message': 'Estimate status has been changed to Accepted.'}


@pytest.mark.asyncio
async def test_mark_invoice_paid_creates_full_customer_payment(monkeypatch):
    fake = BooksActionsFake()
    monkeypatch.setattr(main, 'zoho', fake)

    result = await main.mark_invoice_paid('inv-1')

    assert result['invoice_id'] == 'inv-1'
    assert fake.payment[0] == 'org-1'
    assert fake.payment[1]['customer_id'] == 'customer-1'
    assert fake.payment[1]['payment_mode'] == 'cash'
    assert fake.payment[1]['amount'] == 1250.0
    assert fake.payment[1]['account_id'] == 'cash-1'
    assert fake.payment[1]['invoices'] == [{'invoice_id': 'inv-1', 'amount_applied': 1250.0}]


@pytest.mark.asyncio
async def test_accept_quote_calls_books_status_endpoint(monkeypatch):
    fake = BooksActionsFake()
    monkeypatch.setattr(main, 'zoho', fake)

    result = await main.accept_quote('est-1')

    assert result['code'] == 0
    assert fake.accepted == ('org-1', 'est-1')


@pytest.mark.asyncio
async def test_partial_invoice_payment_uses_selected_account_and_amount(monkeypatch):
    fake = BooksActionsFake()
    monkeypatch.setattr(main, 'zoho', fake)

    await main.mark_invoice_paid('inv-1', {'account_id': 'bank-1', 'amount': 300, 'amount_applied': 300})

    assert fake.payment[0] == 'org-1'
    assert fake.payment[1]['customer_id'] == 'customer-1'
    assert fake.payment[1]['amount'] == 300.0
    assert fake.payment[1]['account_id'] == 'bank-1'
    assert fake.payment[1]['invoices'] == [{'invoice_id': 'inv-1', 'amount_applied': 300.0}]
