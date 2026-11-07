"""Live Zoho Books/Bigin integration smoke test for ConnectCosmos.

This script intentionally uses environment variables instead of storing tokens:

  $env:ZOHO_ACCESS_TOKEN = '...'  # preferred for a live access-token test
  $env:ZOHO_REFRESH_TOKEN = '...'  # fallback when no access token is supplied
  $env:ZOHO_CLIENT_ID = '...'
  $env:ZOHO_CLIENT_SECRET = '...'
  $env:ZOHO_ORGANIZATION_ID = '...'
  $env:ZOHO_API_DOMAIN = 'https://www.zohoapis.com'  # optional
  python scripts/zoho_integration_smoke.py

It creates temporary records, exercises the live APIs, verifies the returned
state, and removes temporary records where Zoho permits deletion. It never
emails a customer or sends a document to a recipient.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal
from typing import Any


class ApiError(RuntimeError):
    def __init__(self, status: int, payload: Any, headers: dict[str, str] | None = None):
        self.status = status
        self.payload = payload
        self.headers = headers or {}
        message = payload.get("message") if isinstance(payload, dict) else str(payload)
        super().__init__(f"HTTP {status}: {message}")


class Zoho:
    def __init__(self, token: str, domain: str, organization_id: str):
        self.token = token
        self.domain = domain.rstrip("/")
        self.organization_id = organization_id
        self.calls = 0
        self.rate_headers: dict[str, str] = {}
        self.trace: list[dict[str, Any]] = []

    def call(self, method: str, path: str, *, params: dict[str, Any] | None = None,
             payload: dict[str, Any] | None = None, expected=(200, 201, 202, 204)) -> dict[str, Any]:
        query = {key: value for key, value in (params or {}).items() if value is not None}
        url = f"{self.domain}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, method=method, data=body, headers={
            "Authorization": f"Zoho-oauthtoken {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self.calls += 1
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                raw = response.read()
                status = response.status
                headers = {key.lower(): value for key, value in response.headers.items()}
        except urllib.error.HTTPError as error:
            raw = error.read()
            status = error.code
            headers = {key.lower(): value for key, value in error.headers.items()}
        try:
            result = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            result = {"raw": raw.decode(errors="replace")}
        self.rate_headers.update({key: headers[key] for key in (
            "x-rate-limit-limit", "x-rate-limit-remaining", "x-user-access-limit",
            "x-user-concurrency-limit", "x-rate-limit-reset",
        ) if key in headers})
        code = result.get("code") if isinstance(result, dict) else None
        self.trace.append({
            "method": method,
            "path": path,
            "params": query,
            "http_status": status,
            "zoho_code": code,
            "message": result.get("message") if isinstance(result, dict) else None,
            "response": result,
        })
        if status not in expected or (code not in (None, 0)):
            raise ApiError(status, result, headers)
        return result if isinstance(result, dict) else {"data": result}


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def rows(result: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = result.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    data = result.get("data")
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def first(result: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = result.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
    data = rows(result)
    return data[0] if data else {}


def identifier(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if record.get(key):
            return str(record[key])
    return ""


def norm_phone(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[-10:]


def refresh_token() -> str:
    payload = urllib.parse.urlencode({
        "refresh_token": env("ZOHO_REFRESH_TOKEN"),
        "client_id": env("ZOHO_CLIENT_ID"),
        "client_secret": env("ZOHO_CLIENT_SECRET"),
        "grant_type": "refresh_token",
    }).encode()
    request = urllib.request.Request("https://accounts.zoho.com/oauth/v2/token", method="POST", data=payload)
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        raise SystemExit(f"OAuth refresh failed: HTTP {error.code}") from error
    token = result.get("access_token")
    if not token:
        raise SystemExit(f"OAuth refresh failed: {result.get('error') or 'no access token returned'}")
    return token


def access_token() -> str:
    supplied = os.environ.get("ZOHO_ACCESS_TOKEN", "").strip()
    return supplied or refresh_token()


def report(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL':4}  {label}{': ' + detail if detail else ''}")


def run() -> int:
    token = access_token()
    zoho = Zoho(token, os.environ.get("ZOHO_API_DOMAIN", "https://www.zohoapis.com"), env("ZOHO_ORGANIZATION_ID"))
    today = date.today().isoformat()
    stamp = str(int(time.time()))
    prefix = f"ConnectCosmos QA {stamp}"
    created: dict[str, list[str]] = {"items": [], "books_contacts": [], "bigin_contacts": [], "pipelines": [], "quotes": [], "orders": [], "purchase_orders": [], "invoices": [], "payments": []}
    failures = 0

    def check(label: str, callback, *, required=True):
        nonlocal failures
        try:
            value = callback()
            report(label, True)
            return value
        except Exception as error:
            report(label, False, str(error))
            if required:
                failures += 1
            return None

    print("Zoho live smoke test")
    print(f"Organization: {zoho.organization_id}")

    # Read-only foundation used by the application.
    check("Books organizations", lambda: zoho.call("GET", "/books/v3/organizations"))
    check("Books currencies", lambda: zoho.call("GET", "/books/v3/settings/currencies", params={"organization_id": zoho.organization_id}))
    check("Books locations", lambda: zoho.call("GET", "/books/v3/locations", params={"organization_id": zoho.organization_id, "per_page": 200, "page": 1}), required=False)
    accounts = check("Books chart of accounts", lambda: zoho.call("GET", "/books/v3/chartofaccounts", params={"organization_id": zoho.organization_id, "per_page": 200, "page": 1})) or {}
    check("Books projects", lambda: zoho.call("GET", "/books/v3/projects", params={"organization_id": zoho.organization_id, "per_page": 200, "page": 1}), required=False)
    reporting_tags = check("Books reporting tags", lambda: zoho.call("GET", "/books/v3/reportingtags", params={"organization_id": zoho.organization_id}), required=False) or {}
    tag_rows = rows(reporting_tags, "tags", "data")
    if tag_rows and identifier(tag_rows[0], "tag_id", "id"):
        check("Books reporting tag options", lambda: zoho.call("GET", "/books/v3/reportingtags/options", params={"organization_id": zoho.organization_id, "tag_id": identifier(tag_rows[0], "tag_id", "id")}), required=False)
    check("Books taxes", lambda: zoho.call("GET", "/books/v3/settings/taxes", params={"organization_id": zoho.organization_id, "per_page": 200, "page": 1}), required=False)
    check("Books contacts list", lambda: zoho.call("GET", "/books/v3/contacts", params={"organization_id": zoho.organization_id, "per_page": 200, "page": 1}))
    check("Books items list", lambda: zoho.call("GET", "/books/v3/items", params={"organization_id": zoho.organization_id, "status": "active", "per_page": 200, "page": 1}))
    check("Books estimates list", lambda: zoho.call("GET", "/books/v3/estimates", params={"organization_id": zoho.organization_id, "per_page": 200, "page": 1}))
    check("Books sales orders list", lambda: zoho.call("GET", "/books/v3/salesorders", params={"organization_id": zoho.organization_id, "per_page": 200, "page": 1}))
    check("Books purchase orders list", lambda: zoho.call("GET", "/books/v3/purchaseorders", params={"organization_id": zoho.organization_id, "per_page": 200, "page": 1}), required=False)
    check("Books invoices list", lambda: zoho.call("GET", "/books/v3/invoices", params={"organization_id": zoho.organization_id, "per_page": 200, "page": 1}))
    bigin_fields = "First_Name,Last_Name,Full_Name,Email,Phone,Mobile,Home_Phone,Title,Account_Name,Owner,Description,Mailing_Street,Mailing_City,Mailing_State,Mailing_Country,Mailing_Zip,Created_Time,Modified_Time,Last_Activity_Time,Tag,Email_Opt_Out"
    check("Bigin contacts list", lambda: zoho.call("GET", "/bigin/v2/Contacts", params={"fields": bigin_fields, "per_page": 50, "page": 1}))
    layout = check("Bigin pipeline layout", lambda: zoho.call("GET", "/bigin/v2/settings/layouts", params={"module": "Pipelines"})) or {}
    layout_rows = rows(layout, "layouts", "data")
    pipeline_layout = layout_rows[0] if layout_rows else {}
    pipeline_layout_id = identifier(pipeline_layout, "id")
    if pipeline_layout_id:
        check("Bigin pipeline records", lambda: zoho.call("GET", "/bigin/v2/Pipelines", params={"pipeline_id": pipeline_layout_id, "fields": "id,Deal_Name,Stage,Contact_Name,Pipeline,Owner,Created_Time,Modified_Time", "per_page": 200, "page": 1}), required=False)
    check("Bigin accounts list", lambda: zoho.call("GET", "/bigin/v2/Accounts", params={"fields": "Account_Name,Phone,Email,Website,Owner,Created_Time,Modified_Time", "per_page": 200, "page": 1}), required=False)
    check("Bigin products list", lambda: zoho.call("GET", "/bigin/v2/Products", params={"fields": "Product_Name,Product_Code,Unit_Price,Description,Owner,Created_Time,Modified_Time", "per_page": 200, "page": 1}), required=False)
    check("Bigin tasks list", lambda: zoho.call("GET", "/bigin/v2/Tasks", params={"fields": "Subject,Due_Date,Status,Priority,Who_Id,What_Id,Owner,Created_Time,Modified_Time", "per_page": 200, "page": 1}), required=False)
    check("Bigin notes list", lambda: zoho.call("GET", "/bigin/v2/Notes", params={"fields": "Note_Title,Note_Content,Parent_Id,Created_Time,Modified_Time", "per_page": 200, "page": 1}), required=False)
    check("Bigin events list", lambda: zoho.call("GET", "/bigin/v2/Events", params={"fields": "Event_Title,Start_DateTime,End_DateTime,Owner,Who_Id,What_Id,Created_Time,Modified_Time", "per_page": 200, "page": 1}), required=False)
    check("Bigin calls list", lambda: zoho.call("GET", "/bigin/v2/Calls", params={"fields": "Subject,Call_Start_Time,Call_Duration,Call_Type,Who_Id,What_Id,Owner,Created_Time,Modified_Time", "per_page": 200, "page": 1}), required=False)

    books_contacts = check("Find reusable Books customer", lambda: rows(zoho.call("GET", "/books/v3/contacts", params={"organization_id": zoho.organization_id, "customer_type": "customer", "per_page": 200, "page": 1}), "contacts"), required=False) or []
    items = check("Find reusable Books item", lambda: rows(zoho.call("GET", "/books/v3/items", params={"organization_id": zoho.organization_id, "status": "active", "per_page": 200, "page": 1}), "items"), required=False) or []
    customer = next((item for item in books_contacts if identifier(item, "contact_id", "customer_id", "id")), {})
    item = next((item for item in items if identifier(item, "item_id", "id")), {})
    customer_id = identifier(customer, "contact_id", "customer_id", "id")
    item_id = identifier(item, "item_id", "id")
    if customer_id:
        check("Books contact detail", lambda: zoho.call("GET", f"/books/v3/contacts/{customer_id}", params={"organization_id": zoho.organization_id}), required=False)
        check("Books contact persons", lambda: zoho.call("GET", f"/books/v3/contacts/{customer_id}/contactpersons", params={"organization_id": zoho.organization_id}), required=False)
        check("Books customer search", lambda: zoho.call("GET", "/books/v3/contacts", params={"organization_id": zoho.organization_id, "search_text": customer.get("contact_name") or customer.get("customer_name") or "", "customer_type": "customer", "per_page": 10, "page": 1}), required=False)
    if item_id:
        check("Books item detail", lambda: zoho.call("GET", f"/books/v3/items/{item_id}", params={"organization_id": zoho.organization_id}), required=False)
    if not customer_id or not item_id:
        report("Reusable Books customer/item", False, "A customer and active item are required for document tests")
        failures += 1

    # Books item: single create, edit, then the same operation used by bulk upload.
    single_item_id = ""
    single = check("Books single item create", lambda: zoho.call("POST", "/books/v3/items", params={"organization_id": zoho.organization_id}, payload={
        "name": f"{prefix} single item", "sku": f"CC-QA-{stamp}-S", "rate": 17.25, "purchase_rate": 10.0, "unit": "pcs", "description": "Temporary ConnectCosmos API smoke-test item", "item_type": "sales",
    }), required=False)
    if single:
        single_item_id = identifier(first(single, "item"), "item_id", "id")
        if single_item_id: created["items"].append(single_item_id)
        check("Books single item edit", lambda: zoho.call("PUT", f"/books/v3/items/{single_item_id}", params={"organization_id": zoho.organization_id}, payload={"description": "Updated ConnectCosmos smoke-test item"}), required=False)
    else:
        failures += 1

    bulk_items: list[str] = []
    for index in range(20):
        result = check(f"Books bulk item {index + 1:02d}/20", lambda index=index: zoho.call("POST", "/books/v3/items", params={"organization_id": zoho.organization_id}, payload={
            "name": f"{prefix} bulk {index + 1:02d}", "sku": f"CC-QA-{stamp}-B{index + 1:02d}", "rate": 20 + index, "unit": "pcs", "description": "Temporary ConnectCosmos bulk smoke-test item", "item_type": "sales",
        }), required=False)
        if result:
            new_id = identifier(first(result, "item"), "item_id", "id")
            if new_id:
                bulk_items.append(new_id)
                created["items"].append(new_id)
    if len(bulk_items) != 20:
        report("Books bulk item result", False, f"created {len(bulk_items)}/20; no bulk endpoint exists, so the app uses one create call per row")
        failures += 1

    # Books contact create/edit/get/delete.
    books_contact_id = ""
    vendor_id = ""
    contact_result = check("Books contact create", lambda: zoho.call("POST", "/books/v3/contacts", params={"organization_id": zoho.organization_id}, payload={
        "contact_name": f"{prefix} Books", "contact_type": "customer", "customer_sub_type": "business", "email": f"{stamp}.books@example.com", "mobile": "9876543210",
        "contact_persons": [{"first_name": "ConnectCosmos", "last_name": "QA", "email": f"{stamp}.books@example.com", "mobile": "9876543210", "is_primary_contact": True}],
    }), required=False)
    if contact_result:
        books_contact_id = identifier(first(contact_result, "contact"), "contact_id", "customer_id", "id")
        if books_contact_id: created["books_contacts"].append(books_contact_id)
        check("Books contact edit", lambda: zoho.call("PUT", f"/books/v3/contacts/{books_contact_id}", params={"organization_id": zoho.organization_id}, payload={"contact_name": f"{prefix} Books edited"}), required=False)
        check("Books contact read after edit", lambda: zoho.call("GET", f"/books/v3/contacts/{books_contact_id}", params={"organization_id": zoho.organization_id}), required=False)
    vendor_result = check("Books vendor create", lambda: zoho.call("POST", "/books/v3/contacts", params={"organization_id": zoho.organization_id}, payload={
        "contact_name": f"{prefix} Vendor", "contact_type": "vendor", "vendor_sub_type": "business", "email": f"{stamp}.vendor@example.com", "mobile": "9876543212",
        "contact_persons": [{"first_name": "ConnectCosmos", "last_name": "Vendor QA", "email": f"{stamp}.vendor@example.com", "mobile": "9876543212", "is_primary_contact": True}],
    }), required=False)
    if vendor_result:
        vendor_id = identifier(first(vendor_result, "contact"), "contact_id", "vendor_id", "id")
        if vendor_id: created["books_contacts"].append(vendor_id)

    # Bigin contact create/edit/read/delete.
    bigin_contact_id = ""
    bigin_result = check("Bigin contact create", lambda: zoho.call("POST", "/bigin/v2/Contacts", payload={"data": [{
        "First_Name": "ConnectCosmos", "Last_Name": f"QA {stamp}", "Email": f"{stamp}.bigin@example.com", "Phone": "9876543211", "Description": "Temporary ConnectCosmos API smoke-test contact",
    }]}), required=False)
    if bigin_result:
        created_row = first(bigin_result)
        details = created_row.get("details") if isinstance(created_row.get("details"), dict) else created_row
        bigin_contact_id = identifier(details, "id", "contact_id")
        if bigin_contact_id: created["bigin_contacts"].append(bigin_contact_id)
        check("Bigin contact edit", lambda: zoho.call("PUT", f"/bigin/v2/Contacts/{bigin_contact_id}", payload={"data": [{"id": bigin_contact_id, "Description": "Updated ConnectCosmos smoke-test contact"}]}), required=False)
        check("Bigin contact read after edit", lambda: zoho.call("GET", f"/bigin/v2/Contacts/{bigin_contact_id}"), required=False)
        check("Bigin contact search", lambda: zoho.call("GET", "/bigin/v2/Contacts/search", params={"word": "ConnectCosmos", "fields": bigin_fields, "per_page": 50, "page": 1}), required=False)
        check("Bigin contact lookup", lambda: zoho.call("GET", "/bigin/v2/Contacts/search", params={"word": "ConnectCosmos"}), required=False)
        check("Bigin contact pipelines", lambda: zoho.call("GET", f"/bigin/v2/Contacts/{bigin_contact_id}/Pipelines", params={"fields": "Deal_Name,Stage,Sub_Pipeline,Pipeline,Amount,Closing_Date,Contact_Name,Owner,Description,Created_Time,Modified_Time"}), required=False)

    pipeline_record_id = ""
    if pipeline_layout_id and bigin_contact_id:
        stage = "Qualification"
        sub_pipeline = "Sales Pipeline Standard"
        closing_date = (date.today()).isoformat()
        for section in pipeline_layout.get("sections", []):
            for field in section.get("fields", []):
                if field.get("api_name") == "Stage":
                    values = field.get("pick_list_values", [])
                    stage = next((value.get("actual_value") or value.get("display_value") for value in values if value.get("actual_value") or value.get("display_value")), stage)
                if field.get("api_name") == "Sub_Pipeline":
                    values = field.get("pick_list_values", [])
                    sub_pipeline = next((value.get("actual_value") or value.get("display_value") for value in values if value.get("actual_value") or value.get("display_value")), sub_pipeline)
        pipeline_create = check("Bigin pipeline record create", lambda: zoho.call("POST", "/bigin/v2/Pipelines", payload={"data": [{
            "Deal_Name": f"ConnectCosmos QA {stamp}", "Stage": stage, "Sub_Pipeline": sub_pipeline, "Closing_Date": closing_date, "Pipeline": {"id": pipeline_layout_id}, "Contact_Name": {"id": bigin_contact_id}, "Description": "Temporary ConnectCosmos API smoke-test pipeline record",
        }]}))
        if pipeline_create:
            pipeline_row = first(pipeline_create)
            pipeline_details = pipeline_row.get("details") if isinstance(pipeline_row.get("details"), dict) else pipeline_row
            pipeline_record_id = identifier(pipeline_details, "id", "record_id")
            if pipeline_record_id:
                created["pipelines"].append(pipeline_record_id)
                check("Bigin pipeline record read", lambda: zoho.call("GET", f"/bigin/v2/Pipelines/{pipeline_record_id}"))
                check("Bigin pipeline record edit", lambda: zoho.call("PUT", f"/bigin/v2/Pipelines/{pipeline_record_id}", payload={"data": [{"id": pipeline_record_id, "Description": "Updated ConnectCosmos smoke-test pipeline record"}]}))

    # Document payload shared by quote, sales order, and invoice flows.
    line_items = [{"item_id": item_id, "quantity": 1, "rate": 100}]
    quote_id = order_id = invoice_id = ""
    if customer_id and item_id:
        if vendor_id:
            purchase_order = check("Purchase order create", lambda: zoho.call("POST", "/books/v3/purchaseorders", params={"organization_id": zoho.organization_id}, payload={"vendor_id": vendor_id, "line_items": line_items, "notes": f"{prefix} purchase order"}), required=False)
            purchase_order_id = identifier(first(purchase_order, "purchaseorder"), "purchaseorder_id", "id") if purchase_order else ""
            if purchase_order_id:
                created["purchase_orders"].append(purchase_order_id)
                check("Purchase order read", lambda: zoho.call("GET", f"/books/v3/purchaseorders/{purchase_order_id}", params={"organization_id": zoho.organization_id}), required=False)
                check("Purchase order edit", lambda: zoho.call("PUT", f"/books/v3/purchaseorders/{purchase_order_id}", params={"organization_id": zoho.organization_id}, payload={"notes": f"{prefix} purchase order edited"}), required=False)
        quote = check("Quote create", lambda: zoho.call("POST", "/books/v3/estimates", params={"organization_id": zoho.organization_id}, payload={"customer_id": customer_id, "line_items": line_items, "notes": f"{prefix} quote"}))
        if quote:
            quote_id = identifier(first(quote, "estimate"), "estimate_id", "id")
            if quote_id: created["quotes"].append(quote_id)
            check("Quote read", lambda: zoho.call("GET", f"/books/v3/estimates/{quote_id}", params={"organization_id": zoho.organization_id}))
            check("Quote edit", lambda: zoho.call("PUT", f"/books/v3/estimates/{quote_id}", params={"organization_id": zoho.organization_id}, payload={"notes": f"{prefix} quote edited"}))
            check("Quote send before acceptance", lambda: zoho.call("POST", f"/books/v3/estimates/{quote_id}/status/sent", params={"organization_id": zoho.organization_id}, payload={}))
            check("Quote mark accepted", lambda: zoho.call("POST", f"/books/v3/estimates/{quote_id}/status/accepted", params={"organization_id": zoho.organization_id}, payload={}))
        order = check("Sales order create", lambda: zoho.call("POST", "/books/v3/salesorders", params={"organization_id": zoho.organization_id}, payload={"customer_id": customer_id, "line_items": line_items, "notes": f"{prefix} sales order"}))
        if order:
            order_id = identifier(first(order, "salesorder"), "salesorder_id", "id")
            if order_id: created["orders"].append(order_id)
            check("Sales order read", lambda: zoho.call("GET", f"/books/v3/salesorders/{order_id}", params={"organization_id": zoho.organization_id}))
            check("Sales order edit", lambda: zoho.call("PUT", f"/books/v3/salesorders/{order_id}", params={"organization_id": zoho.organization_id}, payload={"notes": f"{prefix} sales order edited"}))
        invoice = check("Invoice create", lambda: zoho.call("POST", "/books/v3/invoices", params={"organization_id": zoho.organization_id}, payload={"customer_id": customer_id, "line_items": line_items, "notes": f"{prefix} invoice"}))
        if invoice:
            invoice_id = identifier(first(invoice, "invoice"), "invoice_id", "id")
            if invoice_id: created["invoices"].append(invoice_id)
            check("Invoice read", lambda: zoho.call("GET", f"/books/v3/invoices/{invoice_id}", params={"organization_id": zoho.organization_id}))
            check("Invoice edit", lambda: zoho.call("PUT", f"/books/v3/invoices/{invoice_id}", params={"organization_id": zoho.organization_id}, payload={"notes": f"{prefix} invoice edited"}))
            account_rows = rows(accounts, "chartofaccounts", "accounts")
            eligible = [row for row in account_rows if str(row.get("account_type") or row.get("account_type_name") or row.get("type") or "").lower() in {"bank", "cash"}]
            account = eligible[0] if eligible else (account_rows[0] if account_rows else {})
            account_id = identifier(account, "account_id", "id")
            if not account_id:
                report("Invoice payment account", False, "No bank/cash account was returned")
                failures += 1
            else:
                payment_base = {"customer_id": customer_id, "payment_mode": "cash", "date": today, "account_id": account_id}
                partial = check("Invoice partial payment", lambda: zoho.call("POST", "/books/v3/customerpayments", params={"organization_id": zoho.organization_id}, payload={**payment_base, "amount": 40, "invoices": [{"invoice_id": invoice_id, "amount_applied": 40}]}))
                if partial:
                    payment_id = identifier(first(partial, "payment", "customerpayment"), "payment_id", "id")
                    if payment_id: created["payments"].append(payment_id)
                after_partial = check("Invoice state after partial payment", lambda: zoho.call("GET", f"/books/v3/invoices/{invoice_id}", params={"organization_id": zoho.organization_id}))
                invoice_after_partial = first(after_partial, "invoice") if after_partial else {}
                remaining = float(invoice_after_partial.get("balance") or invoice_after_partial.get("balance_due") or 60)
                full = check("Invoice mark paid with remaining balance", lambda: zoho.call("POST", "/books/v3/customerpayments", params={"organization_id": zoho.organization_id}, payload={**payment_base, "amount": remaining, "invoices": [{"invoice_id": invoice_id, "amount_applied": remaining}]}))
                if full:
                    payment_id = identifier(first(full, "payment", "customerpayment"), "payment_id", "id")
                    if payment_id: created["payments"].append(payment_id)
                final_invoice = check("Invoice state after full payment", lambda: zoho.call("GET", f"/books/v3/invoices/{invoice_id}", params={"organization_id": zoho.organization_id}))
                status = str(first(final_invoice, "invoice").get("status") or "").lower() if final_invoice else ""
                report("Invoice is paid", status == "paid", f"status={status or 'unknown'}")
                if status != "paid": failures += 1

    # Cleanup in dependency order. Payment deletion is required before invoice deletion.
    for payment_id in reversed(created["payments"]):
        check(f"Cleanup payment {payment_id}", lambda payment_id=payment_id: zoho.call("DELETE", f"/books/v3/customerpayments/{payment_id}", params={"organization_id": zoho.organization_id}), required=False)
    for invoice_id in reversed(created["invoices"]):
        check(f"Cleanup invoice {invoice_id}", lambda invoice_id=invoice_id: zoho.call("DELETE", f"/books/v3/invoices/{invoice_id}", params={"organization_id": zoho.organization_id}), required=False)
    for order_id in reversed(created["orders"]):
        check(f"Cleanup sales order {order_id}", lambda order_id=order_id: zoho.call("DELETE", f"/books/v3/salesorders/{order_id}", params={"organization_id": zoho.organization_id}), required=False)
    for purchase_order_id in reversed(created["purchase_orders"]):
        check(f"Cleanup purchase order {purchase_order_id}", lambda purchase_order_id=purchase_order_id: zoho.call("DELETE", f"/books/v3/purchaseorders/{purchase_order_id}", params={"organization_id": zoho.organization_id}), required=False)
    for quote_id in reversed(created["quotes"]):
        check(f"Cleanup quote {quote_id}", lambda quote_id=quote_id: zoho.call("DELETE", f"/books/v3/estimates/{quote_id}", params={"organization_id": zoho.organization_id}), required=False)
    for pipeline_id in reversed(created["pipelines"]):
        check(f"Cleanup Bigin pipeline record {pipeline_id}", lambda pipeline_id=pipeline_id: zoho.call("DELETE", f"/bigin/v2/Pipelines/{pipeline_id}"), required=False)
    for item_id in reversed(created["items"]):
        check(f"Cleanup item {item_id}", lambda item_id=item_id: zoho.call("DELETE", f"/books/v3/items/{item_id}", params={"organization_id": zoho.organization_id}), required=False)
    for contact_id in reversed(created["books_contacts"]):
        check(f"Cleanup Books contact {contact_id}", lambda contact_id=contact_id: zoho.call("DELETE", f"/books/v3/contacts/{contact_id}", params={"organization_id": zoho.organization_id}), required=False)
    for contact_id in reversed(created["bigin_contacts"]):
        check(f"Cleanup Bigin contact {contact_id}", lambda contact_id=contact_id: zoho.call("DELETE", f"/bigin/v2/Contacts/{contact_id}"), required=False)

    print("\nSummary")
    print(f"API calls: {zoho.calls}")
    for key, value in zoho.rate_headers.items():
        print(f"{key}: {value}")
    print(f"Result: {'PASS' if failures == 0 else 'FAIL'} ({failures} required checks failed)")
    report_path = os.environ.get("ZOHO_SMOKE_REPORT", os.path.join("artifacts", "zoho_integration_last_run.json"))
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as report_file:
        json.dump({
            "organization_id": zoho.organization_id,
            "api_domain": zoho.domain,
            "created_records": created,
            "api_calls": zoho.calls,
            "rate_headers": zoho.rate_headers,
            "required_failures": failures,
            "trace": zoho.trace,
        }, report_file, ensure_ascii=False, indent=2, default=str)
    print(f"Response report: {report_path}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
