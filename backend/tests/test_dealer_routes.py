from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import dealer_routes


@pytest.mark.asyncio
async def test_invoice_preview_applies_dealer_discount_and_gst_group(monkeypatch):
    async def catalogue_item(_org, item_id):
        return {"item_id": item_id, "item_name": "Widget", "status": "active"}, Decimal("100")

    async def taxes_for_documents(_org):
        return {"taxes": [{"tax_id": "gst-18", "tax_name": "GST 18%", "tax_percentage": 18}]}

    monkeypatch.setattr(dealer_routes, "_catalogue_item", catalogue_item)
    monkeypatch.setattr(dealer_routes.zoho, "taxes_for_documents", taxes_for_documents)
    cart = dealer_routes.DealerCart(action_key=uuid4(), lines=[{"item_id": "123", "quantity": 2}])
    mapping = {
        "books_contact_id": "contact-1",
        "discount_percent": Decimal("10"),
        "gst_group_id": "gst-18",
    }

    payload, preview = await dealer_routes._invoice_payload("books-org", mapping, cart)

    assert payload["customer_id"] == "contact-1"
    assert payload["line_items"] == [{
        "item_id": "123", "quantity": 2.0, "rate": 100.0,
        "discount": "10%", "tax_id": "gst-18",
    }]
    assert preview["lines"][0]["discount"] == 20
    assert preview["lines"][0]["gst"] == 32.4
    assert preview["total"] == 212.4


@pytest.mark.asyncio
async def test_invoice_links_dealer_customer_in_selected_account_and_records_invoice(monkeypatch):
    account, dealer = str(uuid4()), str(uuid4())
    mapping = {"account_member_id": account, "books_contact_id": "", "discount_percent": Decimal("0")}
    writes = []

    class Result:
        rowcount = 1

        def mappings(self):
            return self

        def first(self):
            return {"name": "Dealer", "email": "dealer@example.test"}

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, query, params):
            writes.append((str(query), params))
            return Result()

        def commit(self):
            pass

    async def payload(_org, _mapping, _cart):
        return {"customer_id": "", "line_items": [{"item_id": "123"}]}, {}

    async def ensure_contact(org, *, name, email):
        assert dealer_routes.get_member_id() == account
        assert (org, name, email) == ("books-org", "Dealer", "dealer@example.test")
        return {"contact_id": "contact-123"}

    async def create_invoice(org, data):
        assert dealer_routes.get_member_id() == account
        assert org == "books-org"
        assert data["customer_id"] == "contact-123"
        return {"invoice": {"invoice_id": "invoice-123"}}

    monkeypatch.setattr(dealer_routes, "require_db", DB)
    monkeypatch.setattr(dealer_routes, "_existing_action", lambda *_args: None)
    monkeypatch.setattr(dealer_routes, "_account_status", lambda _account: {"organization_id": "books-org"})
    monkeypatch.setattr(dealer_routes, "_invoice_payload", payload)
    monkeypatch.setattr(dealer_routes, "_claim_action", lambda *_args: ({"id": uuid4()}, True))
    monkeypatch.setattr(dealer_routes.zoho, "ensure_books_contact", ensure_contact)
    monkeypatch.setattr(dealer_routes.zoho, "create_invoice", create_invoice)
    dealer_routes.set_member_id("original")
    try:
        cart = dealer_routes.DealerCart(action_key=uuid4(), lines=[{"item_id": "123", "quantity": 1}])
        result = await dealer_routes._create_and_record(str(uuid4()), dealer, mapping, cart)
        assert result["invoice_id"] == "invoice-123"
        assert any("update dealer_account_mappings" in sql and params["contact"] == "contact-123" for sql, params in writes)
        assert any("zoho_invoice_id=:invoice" in sql and params["invoice"] == "invoice-123" for sql, params in writes)
        assert dealer_routes.get_member_id() == "original"
    finally:
        dealer_routes.set_member_id(None)


@pytest.mark.asyncio
async def test_failed_customer_link_does_not_submit_invoice(monkeypatch):
    writes = []

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, query, params):
            writes.append(str(query))

        def commit(self):
            pass

    async def payload(*_args):
        return {"customer_id": "", "line_items": []}, {}

    async def fail_link(*_args):
        raise RuntimeError("customer link failed")

    async def create_invoice(*_args):
        pytest.fail("invoice must not be submitted after customer linking fails")

    monkeypatch.setattr(dealer_routes, "require_db", DB)
    monkeypatch.setattr(dealer_routes, "_existing_action", lambda *_args: None)
    monkeypatch.setattr(dealer_routes, "_account_status", lambda _account: {"organization_id": "books-org"})
    monkeypatch.setattr(dealer_routes, "_invoice_payload", payload)
    monkeypatch.setattr(dealer_routes, "_dealer_identity", lambda _dealer: {"name": "Dealer", "email": "dealer@example.test"})
    monkeypatch.setattr(dealer_routes, "_claim_action", lambda *_args: ({"id": uuid4()}, True))
    monkeypatch.setattr(dealer_routes, "_ensure_dealer_contact", fail_link)
    monkeypatch.setattr(dealer_routes.zoho, "create_invoice", create_invoice)
    cart = dealer_routes.DealerCart(action_key=uuid4(), lines=[{"item_id": "123", "quantity": 1}])
    with pytest.raises(dealer_routes.HTTPException, match="No invoice was submitted"):
        await dealer_routes._create_and_record(str(uuid4()), str(uuid4()), {"account_member_id": uuid4()}, cart)
    assert any("state='uncertain'" in sql for sql in writes)


@pytest.mark.asyncio
async def test_gst_groups_use_selected_account_and_restore_context(monkeypatch):
    dealer, account = uuid4(), uuid4()
    context = SimpleNamespace(actor_role="owner")
    monkeypatch.setattr(dealer_routes, "_account_access", lambda _dealer: (context, "org"))
    monkeypatch.setattr(dealer_routes, "_account_status", lambda _account: {"organization_id": "books-org"})

    async def taxes(org):
        assert org == "books-org"
        assert dealer_routes.get_member_id() == str(account)
        return {"taxes": [{"tax_id": "selected-account-gst"}]}

    monkeypatch.setattr(dealer_routes.zoho, "taxes_for_documents", taxes)
    dealer_routes.set_member_id("original")
    try:
        assert await dealer_routes.dealer_account_gst_groups(dealer, account) == {"items": [{"tax_id": "selected-account-gst"}]}
        assert dealer_routes.get_member_id() == "original"
    finally:
        dealer_routes.set_member_id(None)


@pytest.mark.asyncio
async def test_existing_customer_link_is_reused(monkeypatch):
    async def ensure_contact(*_args, **_kwargs):
        pytest.fail("an existing customer link must not create another contact")

    monkeypatch.setattr(dealer_routes.zoho, "ensure_books_contact", ensure_contact)
    assert await dealer_routes._ensure_dealer_contact("org", "dealer", {"books_contact_id": "existing"}, "books-org", None) == "existing"


@pytest.mark.asyncio
async def test_missing_dealer_email_does_not_claim_invoice_action(monkeypatch):
    async def payload(*_args):
        return {"line_items": []}, {}

    def missing_identity(_dealer):
        raise dealer_routes.HTTPException(409, "The Dealer needs an email address")

    def claim(*_args):
        pytest.fail("missing dealer details must not lock an invoice action")

    monkeypatch.setattr(dealer_routes, "_existing_action", lambda *_args: None)
    monkeypatch.setattr(dealer_routes, "_account_status", lambda _account: {"organization_id": "books-org"})
    monkeypatch.setattr(dealer_routes, "_invoice_payload", payload)
    monkeypatch.setattr(dealer_routes, "_dealer_identity", missing_identity)
    monkeypatch.setattr(dealer_routes, "_claim_action", claim)
    cart = dealer_routes.DealerCart(action_key=uuid4(), lines=[{"item_id": "123", "quantity": 1}])
    with pytest.raises(dealer_routes.HTTPException, match="needs an email"):
        await dealer_routes._create_and_record("org", "dealer", {"account_member_id": uuid4(), "books_contact_id": ""}, cart)


@pytest.mark.asyncio
async def test_invoice_list_reports_unresolved_actions_without_invoice_ids(monkeypatch):
    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, _query, _params):
            return self

        def all(self):
            return [SimpleNamespace(_mapping={
                "zoho_invoice_id": None, "account_member_id": "account",
                "previous_zoho_invoice_id": None, "state": "uncertain", "created_at": None,
            })]

    monkeypatch.setattr(dealer_routes, "require_db", DB)
    monkeypatch.setattr(dealer_routes, "_dealer_access", lambda: (None, "org", "dealer", {}))
    assert await dealer_routes.dealer_invoices() == {"items": [], "unresolved_count": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,editable,mapped,expected", [
    ("draft", True, True, True),
    ("due", True, True, True),
    ("paid", True, True, False),
    ("draft", False, True, False),
    ("draft", True, False, False),
])
async def test_invoice_list_only_offers_eligible_updates(monkeypatch, status, editable, mapped, expected):
    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def execute(self, *_args): return self
        def all(self):
            return [SimpleNamespace(_mapping={
                "zoho_invoice_id": "123", "account_member_id": "account",
                "previous_zoho_invoice_id": None, "state": "active", "created_at": None,
            })]

    async def invoice(*_args):
        return {"invoice": {"status": status, "is_editable": editable}}

    monkeypatch.setattr(dealer_routes, "require_db", DB)
    monkeypatch.setattr(dealer_routes, "_dealer_access", lambda: (None, "org", "dealer", {"account_member_id": "account" if mapped else "other"}))
    monkeypatch.setattr(dealer_routes, "_account_status", lambda _account: {"organization_id": "books-org"})
    monkeypatch.setattr(dealer_routes.zoho, "books_invoice", invoice)
    assert (await dealer_routes.dealer_invoices())["items"][0]["can_update"] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_draft_invoice_updates_in_place_and_locks_uncertain_results(monkeypatch, failure):
    account = str(uuid4())
    action_id = uuid4()
    writes = []
    calls = []

    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def execute(self, query, params):
            sql = str(query)
            if "select account_member_id,state" in sql:
                return self
            writes.append((sql, params))
            return self
        def mappings(self): return self
        def first(self): return {"account_member_id": account, "state": "active"}
        def commit(self): pass

    async def invoice(*_args):
        return {"invoice": {"status": "draft", "customer_id": "customer", "is_editable": True}}

    async def payload(*_args):
        return {"line_items": [{"item_id": "123", "quantity": 2}]}, {"total": 200}

    async def update(org, invoice_id, data):
        calls.append((org, invoice_id, data))
        if failure:
            raise RuntimeError("timeout")
        return {"invoice": {"invoice_id": invoice_id}}

    monkeypatch.setattr(dealer_routes, "require_db", DB)
    monkeypatch.setattr(dealer_routes, "_dealer_access", lambda: (None, "org", "dealer", {"account_member_id": account}))
    monkeypatch.setattr(dealer_routes, "_existing_action", lambda *_args: None)
    monkeypatch.setattr(dealer_routes, "_claim_action", lambda *_args: ({"id": action_id}, True))
    monkeypatch.setattr(dealer_routes, "_account_status", lambda _account: {"organization_id": "books-org"})
    monkeypatch.setattr(dealer_routes, "_invoice_payload", payload)
    monkeypatch.setattr(dealer_routes.zoho, "books_invoice", invoice)
    monkeypatch.setattr(dealer_routes.zoho, "update_invoice", update)
    cart = dealer_routes.DealerCart(action_key=uuid4(), lines=[{"item_id": "123", "quantity": 2}])
    if failure:
        with pytest.raises(dealer_routes.HTTPException, match="could not be confirmed"):
            await dealer_routes.replace_dealer_invoice("invoice-123", cart)
        assert any("state='uncertain'" in sql for sql, _ in writes)
    else:
        result = await dealer_routes.replace_dealer_invoice("invoice-123", cart)
        assert result["invoice_id"] == "invoice-123"
        assert any("state='active'" in sql for sql, _ in writes)
    assert calls == [("books-org", "invoice-123", {
        "line_items": [{"item_id": "123", "quantity": 2}], "customer_id": "customer",
    })]


@pytest.mark.asyncio
async def test_completed_draft_update_retry_returns_original_invoice(monkeypatch):
    monkeypatch.setattr(dealer_routes, "_dealer_access", lambda: (None, "org", "dealer", {}))
    monkeypatch.setattr(dealer_routes, "_existing_action", lambda *_args: {
        "zoho_invoice_id": None, "previous_zoho_invoice_id": "invoice-123",
    })
    cart = dealer_routes.DealerCart(action_key=uuid4(), lines=[{"item_id": "123", "quantity": 2}])
    assert await dealer_routes.replace_dealer_invoice("invoice-123", cart) == {
        "invoice_id": "invoice-123", "duplicate_request": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status,editable,allowed", [
    ("due", True, True),
    ("paid", True, False),
    ("draft", False, False),
])
async def test_invoice_update_keeps_due_replacement_and_rejects_ineligible(monkeypatch, status, editable, allowed):
    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def execute(self, *_args): return self
        def mappings(self): return self
        def first(self): return {"account_member_id": "account", "state": "active"}

    async def invoice(*_args):
        return {"invoice": {"status": status, "is_editable": editable}}

    async def replace(_org, _dealer, _mapping, _cart, previous):
        assert previous == "invoice-123"
        return {"invoice_id": "replacement"}

    monkeypatch.setattr(dealer_routes, "require_db", DB)
    monkeypatch.setattr(dealer_routes, "_dealer_access", lambda: (None, "org", "dealer", {"account_member_id": "account"}))
    monkeypatch.setattr(dealer_routes, "_existing_action", lambda *_args: None)
    monkeypatch.setattr(dealer_routes, "_account_status", lambda _account: {"organization_id": "books-org"})
    monkeypatch.setattr(dealer_routes.zoho, "books_invoice", invoice)
    monkeypatch.setattr(dealer_routes, "_create_and_record", replace)
    cart = dealer_routes.DealerCart(action_key=uuid4(), lines=[{"item_id": "123", "quantity": 1}])
    if allowed:
        assert await dealer_routes.replace_dealer_invoice("invoice-123", cart) == {"invoice_id": "replacement"}
    else:
        with pytest.raises(dealer_routes.HTTPException, match="Only editable Draft or Due"):
            await dealer_routes.replace_dealer_invoice("invoice-123", cart)
