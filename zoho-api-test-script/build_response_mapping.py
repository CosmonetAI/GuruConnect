"""Build a region-by-region response mapping from live Zoho smoke reports.

The input reports contain API responses only; OAuth credentials are deliberately
not read or copied into this artifact.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "zoho-api-test-script-artifacts"
REGIONS = ("in", "us")
ID_PATTERN = re.compile(r"\d{8,}")


def normalized_path(path: str) -> str:
    return ID_PATTERN.sub("{id}", path)


def top_level_keys(value: Any) -> list[str]:
    return sorted(value.keys()) if isinstance(value, dict) else []


def first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), {})
    return {}


def field_sample(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    fields: set[str] = set()
    for key, child in value.items():
        fields.add(key)
        if key in {"data", "organizations", "contacts", "items", "estimate", "salesorder", "purchaseorder", "invoice", "payment"}:
            fields.update(first_dict(child).keys())
        if key == "data" and isinstance(child, list) and child:
            fields.update(first_dict(first_dict(child).get("details")).keys())
    return sorted(fields)


def contract_for(path: str, response_keys: set[str]) -> dict[str, Any]:
    if path.endswith("/organizations"):
        return {"collection": "organizations", "id": "organization_id"}
    if "/books/v3/contacts" in path:
        return {"collection": "contacts", "id": "contact_id", "document_customer_id": "customer_id", "document_vendor_id": "vendor_id"}
    if "/books/v3/items" in path:
        return {"collection": "items", "id": "item_id"}
    if "/books/v3/estimates" in path:
        return {"collection": "estimate", "id": "estimate_id"}
    if "/books/v3/salesorders" in path:
        return {"collection": "salesorder", "id": "salesorder_id"}
    if "/books/v3/purchaseorders" in path:
        return {"collection": "purchaseorder", "id": "purchaseorder_id"}
    if "/books/v3/invoices" in path:
        return {"collection": "invoice", "id": "invoice_id"}
    if "/books/v3/customerpayments" in path:
        return {"collection": "payment", "id": "payment_id"}
    if path.startswith("/bigin/v2/"):
        return {"collection": "data", "mutation_id": "data[].details.id", "list_id": "data[].id"}
    if path == "/api/accounts":
        return {"collection": "data", "id": "data[].accountId", "email_ids": "data[].emailAddress[].mailId"}
    if path.endswith("/messages"):
        return {"collection": "data", "id": "data.messageId"}
    return {"top_level_keys": sorted(response_keys)}


def main() -> None:
    reports: dict[str, dict[str, Any]] = {}
    for region in REGIONS:
        report_path = ARTIFACTS / f"zoho_live_{region}.json"
        reports[region] = json.loads(report_path.read_text(encoding="utf-8"))

    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for region, report in reports.items():
        for trace in report.get("trace", []):
            key = f"{trace.get('method', 'GET')} {normalized_path(trace.get('path', ''))}"
            grouped[key][region].append(trace)

    endpoints: list[dict[str, Any]] = []
    for endpoint, by_region in sorted(grouped.items()):
        method, path = endpoint.split(" ", 1)
        regions: dict[str, Any] = {}
        all_keys: set[str] = set()
        for region in REGIONS:
            traces = by_region.get(region, [])
            keys = {key for trace in traces for key in top_level_keys(trace.get("response"))}
            all_keys.update(keys)
            regions[region] = {
                "calls": len(traces),
                "http_statuses": dict(Counter(trace.get("http_status") for trace in traces)),
                "top_level_keys": sorted(keys),
                "sample_fields": sorted({field for trace in traces for field in field_sample(trace.get("response"))}),
                "response_shapes": dict(Counter("object" if isinstance(trace.get("response"), dict) else type(trace.get("response")).__name__ for trace in traces)),
            }
        endpoints.append({
            "method": method,
            "path": path,
            "services": sorted({trace.get("service", "api") for traces in by_region.values() for trace in traces}),
            "regions": regions,
            "same_top_level_keys": regions["in"]["top_level_keys"] == regions["us"]["top_level_keys"] if regions["in"]["calls"] and regions["us"]["calls"] else None,
            "canonical_mapping": contract_for(path, all_keys),
        })

    shape_differences = [
        {
            "method": endpoint["method"],
            "path": endpoint["path"],
            "india_keys": endpoint["regions"]["in"]["top_level_keys"],
            "usa_keys": endpoint["regions"]["us"]["top_level_keys"],
            "handling": "Treat collection keys as optional and normalize a missing collection to an empty list.",
        }
        for endpoint in endpoints
        if endpoint["regions"]["in"]["calls"]
        and endpoint["regions"]["us"]["calls"]
        and not endpoint["same_top_level_keys"]
    ]

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_reports": {region: str(ARTIFACTS / f"zoho_live_{region}.json") for region in REGIONS},
        "regions": {
            region: {
                "api_domain": reports[region].get("api_domain"),
                "mail_api_domain": reports[region].get("mail_api_domain"),
                "organization_id": reports[region].get("organization_id"),
                "api_calls": reports[region].get("api_calls"),
                "required_failures": reports[region].get("required_failures"),
                "trace_entries": len(reports[region].get("trace", [])),
            }
            for region in REGIONS
        },
        "mapping_contract": {
            "books": {
                "list_rows": "Use the resource collection key when present (contacts/items/organizations), otherwise data[].",
                "single_resource": "Use the singular resource key (contact/item/estimate/salesorder/purchaseorder/invoice/payment).",
                "resource_ids": {
                    "organization": "organization_id",
                    "contact": "contact_id; use customer_id or vendor_id only when returned for that role",
                    "item": "item_id",
                    "estimate": "estimate_id",
                    "sales_order": "salesorder_id",
                    "purchase_order": "purchaseorder_id",
                    "invoice": "invoice_id",
                    "customer_payment": "payment_id",
                },
            },
            "bigin": {
                "list_rows": "data[]",
                "create_update_result": "data[].details",
                "record_id": "data[].details.id for mutations; data[].id for list rows",
                "module_marker_for_notes": "$se_module=Contacts when attaching a note to a contact",
            },
            "mail": {
                "accounts": "data[].accountId; sender candidates are data[].emailAddress[].mailId",
                "sent_message": "data.messageId",
                "mail_response_status": "status.code and status.description",
            },
            "regional_rule": "Keep the response mapping identical between regions; only the API host, Mail host, organization IDs, record IDs, and account-specific values change.",
        },
        "observations": {
            "response_shape_differences": shape_differences,
            "normalization_rule": "For every collection, read the documented resource key first, then data, and default missing/empty collections to []. For mutation responses, unwrap data[].details when present.",
        },
        "endpoints": endpoints,
    }
    output_path = ARTIFACTS / "zoho_response_mapping.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)
    print(f"endpoints={len(endpoints)}")
    print(f"both_regions={sum(1 for endpoint in endpoints if endpoint['regions']['in']['calls'] and endpoint['regions']['us']['calls'])}")


if __name__ == "__main__":
    main()
