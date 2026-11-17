"""Read-only Zoho API audit for the endpoints used by the backend.

Credentials are read only from process environment variables.  No token or
client secret is written to the report.  Run one or both regions with:

  $env:ZOHO_IN_REFRESH_TOKEN = '...'
  $env:ZOHO_IN_CLIENT_ID = '...'
  $env:ZOHO_IN_CLIENT_SECRET = '...'
  $env:ZOHO_IN_ORGANIZATION_ID = '...'
  $env:ZOHO_US_REFRESH_TOKEN = '...'
  $env:ZOHO_US_CLIENT_ID = '...'
  $env:ZOHO_US_CLIENT_SECRET = '...'
  $env:ZOHO_US_ORGANIZATION_ID = '...'
  python zoho-api-test-script/zoho_api_audit.py

The generated JSON is an evidence report, not a data export.  Response values
that may contain customer or message data are replaced with ``<redacted>``;
IDs, status fields, response codes, and the response shape are retained.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable


REGIONS = {
    "us": {
        "accounts": "https://accounts.zoho.com",
        "api": "https://www.zohoapis.com",
        "mail": "https://mail.zoho.com",
    },
    "in": {
        "accounts": "https://accounts.zoho.in",
        "api": "https://www.zohoapis.in",
        "mail": "https://mail.zoho.in",
    },
}

SENSITIVE_KEY = re.compile(
    r"(?:access[_-]?token|refresh[_-]?token|client[_-]?secret|authorization|password|"
    r"secret|api[_-]?key|email|phone|mobile|address|description|notes?|content|body|"
    r"message|subject|first[_-]?name|last[_-]?name|full[_-]?name|account[_-]?name|"
    r"contact[_-]?name|company[_-]?name|mailbox|username|incoming)", re.I
)


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing {name}")
    return value


def safe_value(value: Any, key: str = "", depth: int = 0) -> Any:
    """Keep response shape and operational values without retaining PII."""
    if SENSITIVE_KEY.search(key):
        return "<redacted>"
    if depth > 5:
        return "<truncated>"
    if isinstance(value, dict):
        return {str(k): safe_value(v, str(k), depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) > 5:
            return [safe_value(item, key, depth + 1) for item in value[:5]] + [f"<redacted {len(value) - 5} more>" ]
        return [safe_value(item, key, depth + 1) for item in value]
    if isinstance(value, str) and len(value) > 240:
        return value[:80] + "<truncated>"
    return value


def rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    value = payload.get("data")
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def identifier(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


class ApiFailure(RuntimeError):
    def __init__(self, status: int, payload: Any):
        self.status = status
        self.payload = payload
        message = payload.get("message") if isinstance(payload, dict) else str(payload)
        super().__init__(f"HTTP {status}: {message}")


class ZohoAuditClient:
    def __init__(self, region: str, access_token: str, organization_id: str):
        self.region = region
        self.token = access_token
        self.organization_id = organization_id
        self.endpoints = REGIONS[region]
        self.trace: list[dict[str, Any]] = []
        self.rate_headers: dict[str, str] = {}

    def call(
        self,
        service: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        accept: str = "application/json",
    ) -> Any:
        domain = self.endpoints["mail"] if service == "mail" else self.endpoints["api"]
        query = {key: value for key, value in (params or {}).items() if value is not None}
        url = f"{domain}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            url,
            method=method,
            data=body,
            headers={
                "Authorization": f"Zoho-oauthtoken {self.token}",
                "Accept": accept,
                "Content-Type": "application/json",
            },
        )
        started = time.perf_counter()
        status = 0
        headers: dict[str, str] = {}
        raw = b""
        error: str | None = None
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                status = response.status
                headers = {key.lower(): value for key, value in response.headers.items()}
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            headers = {key.lower(): value for key, value in exc.headers.items()}
            raw = exc.read()
        except Exception as exc:  # network/timeout errors are part of the evidence
            error = type(exc).__name__
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if accept == "application/pdf":
            parsed: Any = {
                "content_type": content_type,
                "bytes": len(raw),
                "pdf_header": raw.startswith(b"%PDF-"),
                "pdf_eof": b"%%EOF" in raw[-1024:],
            }
        else:
            try:
                parsed = json.loads(raw.decode() or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = {"response_type": "non_json", "bytes": len(raw)}
        zoho_code = parsed.get("code") if isinstance(parsed, dict) else None
        request_id = headers.get("x-request-id") or headers.get("x-ratelimit-request-id")
        self.rate_headers.update(
            {key: headers[key] for key in (
                "x-rate-limit-limit", "x-rate-limit-remaining", "x-user-access-limit",
                "x-user-concurrency-limit", "x-rate-limit-reset",
            ) if key in headers}
        )
        entry = {
            "service": service,
            "method": method,
            "path": path,
            "query_keys": sorted(query),
            "http_status": status or None,
            "zoho_code": zoho_code,
            "request_id": request_id,
            "duration_ms": duration_ms,
            "response": safe_value(parsed),
        }
        if error:
            entry["error_type"] = error
        self.trace.append(entry)
        if error:
            raise RuntimeError(error)
        if status >= 400:
            raise ApiFailure(status, parsed)
        if isinstance(parsed, dict) and parsed.get("code") not in (None, 0, "0"):
            raise ApiFailure(status, parsed)
        return parsed


def refresh_token(region: str) -> str:
    prefix = f"ZOHO_{region.upper()}_"
    form = urllib.parse.urlencode({
        "refresh_token": required(prefix + "REFRESH_TOKEN"),
        "client_id": required(prefix + "CLIENT_ID"),
        "client_secret": required(prefix + "CLIENT_SECRET"),
        "grant_type": "refresh_token",
    }).encode()
    request = urllib.request.Request(
        f"{REGIONS[region]['accounts']}/oauth/v2/token",
        method="POST",
        data=form,
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            result = json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"OAuth refresh HTTP {exc.code}") from exc
    token = result.get("access_token")
    if not token:
        raise RuntimeError(f"OAuth refresh failed: {result.get('error', 'no access token')}")
    return token


def run_region(region: str) -> dict[str, Any]:
    prefix = f"ZOHO_{region.upper()}_"
    organization_id = required(prefix + "ORGANIZATION_ID")
    client = ZohoAuditClient(region, refresh_token(region), organization_id)
    checks: list[dict[str, Any]] = []

    def check(label: str, fn: Callable[[], Any], *, required_check: bool = True) -> Any:
        try:
            value = fn()
            checks.append({"label": label, "status": "pass", "required": required_check})
            return value
        except Exception as exc:
            checks.append({
                "label": label,
                "status": "fail",
                "required": required_check,
                "error_type": type(exc).__name__,
                "error": str(exc)[:240],
            })
            return None

    orgs = check("Books organizations", lambda: client.call("books", "GET", "/books/v3/organizations")) or {}
    check("Books currencies", lambda: client.call("books", "GET", "/books/v3/settings/currencies", params={"organization_id": organization_id}))
    check("Books locations", lambda: client.call("books", "GET", "/books/v3/locations", params={"organization_id": organization_id, "per_page": 200, "page": 1}), required_check=False)
    accounts = check("Books chart of accounts", lambda: client.call("books", "GET", "/books/v3/chartofaccounts", params={"organization_id": organization_id, "per_page": 200, "page": 1})) or {}
    check("Books projects", lambda: client.call("books", "GET", "/books/v3/projects", params={"organization_id": organization_id, "per_page": 200, "page": 1}), required_check=False)
    tags = check("Books reporting tags", lambda: client.call("books", "GET", "/books/v3/reportingtags", params={"organization_id": organization_id}), required_check=False) or {}
    tag_rows = rows(tags, "tags", "data")
    if tag_rows:
        tag_id = identifier(tag_rows[0], "tag_id", "id")
        if tag_id:
            check("Books reporting tag options", lambda: client.call("books", "GET", "/books/v3/reportingtags/options", params={"organization_id": organization_id, "tag_id": tag_id}), required_check=False)
    check("Books taxes", lambda: client.call("books", "GET", "/books/v3/settings/taxes", params={"organization_id": organization_id, "per_page": 200, "page": 1}), required_check=False)
    check("Books item masters", lambda: client.call("books", "GET", "/books/v3/itemmasters", params={"organization_id": organization_id, "status": "active", "per_page": 200, "page": 1}), required_check=False)
    check("Books item variants", lambda: client.call("books", "GET", "/books/v3/itemvariants", params={"organization_id": organization_id, "status": "active", "per_page": 200, "page": 1}), required_check=False)
    contacts = check("Books contacts", lambda: client.call("books", "GET", "/books/v3/contacts", params={"organization_id": organization_id, "per_page": 200, "page": 1})) or {}
    items = check("Books items", lambda: client.call("books", "GET", "/books/v3/items", params={"organization_id": organization_id, "status": "active", "per_page": 200, "page": 1})) or {}
    estimates = check("Books estimates", lambda: client.call("books", "GET", "/books/v3/estimates", params={"organization_id": organization_id, "per_page": 200, "page": 1})) or {}
    sales_orders = check("Books sales orders", lambda: client.call("books", "GET", "/books/v3/salesorders", params={"organization_id": organization_id, "per_page": 200, "page": 1})) or {}
    purchase_orders = check("Books purchase orders", lambda: client.call("books", "GET", "/books/v3/purchaseorders", params={"organization_id": organization_id, "per_page": 200, "page": 1}), required_check=False) or {}
    invoices = check("Books invoices", lambda: client.call("books", "GET", "/books/v3/invoices", params={"organization_id": organization_id, "per_page": 200, "page": 1})) or {}

    book_detail_checks = [
        ("Books contact detail", contacts, "contacts", ("contact_id", "customer_id", "id"), "/books/v3/contacts/{}", {}),
        ("Books item detail", items, "items", ("item_id", "id"), "/books/v3/items/{}", {}),
        ("Books estimate detail", estimates, "estimates", ("estimate_id", "id"), "/books/v3/estimates/{}", {}),
        ("Books sales order detail", sales_orders, "salesorders", ("salesorder_id", "id"), "/books/v3/salesorders/{}", {}),
        ("Books purchase order detail", purchase_orders, "purchaseorders", ("purchaseorder_id", "id"), "/books/v3/purchaseorders/{}", {}),
        ("Books invoice detail", invoices, "invoices", ("invoice_id", "id"), "/books/v3/invoices/{}", {}),
    ]
    for label, collection, collection_key, id_keys, path, _ in book_detail_checks:
        row = rows(collection, collection_key, "data")[:1]
        if row:
            record_id = identifier(row[0], *id_keys)
            if record_id:
                check(label, lambda path=path, record_id=record_id: client.call("books", "GET", path.format(record_id), params={"organization_id": organization_id}), required_check=False)
    contact_rows = rows(contacts, "contacts", "data")[:1]
    if contact_rows:
        contact_id = identifier(contact_rows[0], "contact_id", "customer_id", "id")
        if contact_id:
            check("Books contact persons", lambda: client.call("books", "GET", f"/books/v3/contacts/{contact_id}/contactpersons", params={"organization_id": organization_id}), required_check=False)
    invoice_rows = rows(invoices, "invoices", "data")[:1]
    if invoice_rows:
        invoice_id = identifier(invoice_rows[0], "invoice_id", "id")
        if invoice_id:
            check("Books invoice PDF", lambda: client.call("books", "GET", f"/books/v3/invoices/{invoice_id}", params={"organization_id": organization_id, "accept": "pdf"}, accept="application/pdf"), required_check=False)

    fields = "First_Name,Last_Name,Full_Name,Email,Phone,Mobile,Home_Phone,Title,Account_Name,Owner,Description,Mailing_Street,Mailing_City,Mailing_State,Mailing_Country,Mailing_Zip,Created_Time,Modified_Time,Last_Activity_Time,Tag,Email_Opt_Out"
    bigin_contacts = check("Bigin contacts", lambda: client.call("bigin", "GET", "/bigin/v2/Contacts", params={"fields": fields, "per_page": 50, "page": 1})) or {}
    check("Bigin contacts search", lambda: client.call("bigin", "GET", "/bigin/v2/Contacts/search", params={"word": "ConnectCosmos", "fields": fields, "per_page": 50, "page": 1}), required_check=False)
    bigin_contact_rows = rows(bigin_contacts, "data")[:1]
    if bigin_contact_rows:
        bigin_contact_id = identifier(bigin_contact_rows[0], "id")
        if bigin_contact_id:
            check("Bigin contact detail", lambda: client.call("bigin", "GET", f"/bigin/v2/Contacts/{bigin_contact_id}"), required_check=False)
            check("Bigin contact pipelines", lambda: client.call("bigin", "GET", f"/bigin/v2/Contacts/{bigin_contact_id}/Pipelines", params={"fields": "Deal_Name,Stage,Pipeline,Owner,Created_Time,Modified_Time"}), required_check=False)
    layouts = check("Bigin pipeline layouts", lambda: client.call("bigin", "GET", "/bigin/v2/settings/layouts", params={"module": "Pipelines"}), required_check=False) or {}
    layout_rows = rows(layouts, "layouts", "data")
    pipeline_id = identifier(layout_rows[0], "id") if layout_rows else ""
    if pipeline_id:
        check("Bigin pipeline records", lambda: client.call("bigin", "GET", "/bigin/v2/Pipelines", params={"pipeline_id": pipeline_id, "fields": "id,Deal_Name,Stage,Pipeline,Owner,Created_Time,Modified_Time", "per_page": 200, "page": 1}), required_check=False)
    check("Bigin pipeline search", lambda: client.call("bigin", "GET", "/bigin/v2/Pipelines/search", params={"word": "ConnectCosmos"}), required_check=False)
    check("Bigin accounts", lambda: client.call("bigin", "GET", "/bigin/v2/Accounts", params={"fields": "Account_Name,Phone,Email,Website,Owner,Created_Time,Modified_Time", "per_page": 200, "page": 1}), required_check=False)
    check("Bigin products", lambda: client.call("bigin", "GET", "/bigin/v2/Products", params={"fields": "Product_Name,Product_Code,Unit_Price,Description,Owner,Created_Time,Modified_Time", "per_page": 200, "page": 1}), required_check=False)
    check("Bigin tasks", lambda: client.call("bigin", "GET", "/bigin/v2/Tasks", params={"fields": "Subject,Due_Date,Status,Priority,Who_Id,What_Id,Owner,Created_Time,Modified_Time", "per_page": 200, "page": 1}), required_check=False)
    check("Bigin notes", lambda: client.call("bigin", "GET", "/bigin/v2/Notes", params={"fields": "Note_Title,Note_Content,Parent_Id,Created_Time,Modified_Time", "per_page": 200, "page": 1}), required_check=False)
    check("Bigin events", lambda: client.call("bigin", "GET", "/bigin/v2/Events", params={"fields": "Event_Title,Start_DateTime,End_DateTime,Owner,Who_Id,What_Id,Created_Time,Modified_Time", "per_page": 200, "page": 1}), required_check=False)
    check("Bigin calls", lambda: client.call("bigin", "GET", "/bigin/v2/Calls", params={"fields": "Subject,Call_Start_Time,Call_Duration,Call_Type,Who_Id,What_Id,Owner,Created_Time,Modified_Time", "per_page": 200, "page": 1}), required_check=False)
    check("Bigin deleted contacts", lambda: client.call("bigin", "GET", "/bigin/v2/Contacts/deleted", params={"type": "recycle", "page": 1, "per_page": 200}), required_check=False)
    check("Zoho Mail accounts", lambda: client.call("mail", "GET", "/api/accounts"), required_check=False)

    required_failures = [item for item in checks if item["required"] and item["status"] == "fail"]
    return {
        "region": region,
        "organization_id": organization_id,
        "api_domain": client.endpoints["api"],
        "mail_api_domain": client.endpoints["mail"],
        "required_failures": len(required_failures),
        "checks": checks,
        "trace": client.trace,
        "rate_headers": client.rate_headers,
    }


def main() -> int:
    requested = [value.strip().lower() for value in os.environ.get("ZOHO_AUDIT_REGIONS", "in,us").split(",") if value.strip()]
    results = []
    for region in requested:
        if region not in REGIONS:
            print(f"SKIP {region}: unsupported region", file=sys.stderr)
            continue
        try:
            print(f"Auditing Zoho {region.upper()} read-only endpoints...")
            result = run_region(region)
            results.append(result)
            passed = sum(item["status"] == "pass" for item in result["checks"])
            failed = sum(item["status"] == "fail" for item in result["checks"])
            print(f"{region.upper()}: {passed} passed, {failed} failed; required failures={result['required_failures']}")
        except Exception as exc:
            results.append({"region": region, "status": "setup_failed", "error_type": type(exc).__name__, "error": str(exc)[:240]})
            print(f"{region.upper()}: setup failed ({type(exc).__name__})", file=sys.stderr)
    output = os.environ.get("ZOHO_AUDIT_OUTPUT", "").strip()
    if not output:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = os.path.join("zoho-api-reponse-artifacts", f"zoho_api_audit_{stamp}.json")
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "regions": results}
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
    print(f"Redacted evidence report: {output}")
    return 0 if results and all(item.get("required_failures", 1) == 0 for item in results if item.get("status") != "setup_failed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
