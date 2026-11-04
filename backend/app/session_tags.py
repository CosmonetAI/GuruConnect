"""Business tags derived from durable conversation and enquiry context."""

from __future__ import annotations

import re
from typing import Any

from .product_training import PRODUCT_CHEAT_SHEET


_DOCUMENT_TAGS = {
    "quotation": "Quote discussed",
    "written_offer": "Quote discussed",
    "boq": "Quote discussed",
    "invoice": "Invoice discussed",
    "sales_order": "Sales order discussed",
    "purchase_order": "Purchase order discussed",
}
_INTENT_TAGS = {
    "product_recommendation": "Product recommendation",
    "product_discovery": "Product recommendation",
    "recommendation": "Product recommendation",
    "pricing": "Pricing discussed",
    "availability": "Availability discussed",
}
_ALIASES = {
    alias.casefold(): category
    for category, aliases in PRODUCT_CHEAT_SHEET.items()
    for alias in aliases
}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _title(value: str) -> str:
    return " ".join(part.capitalize() for part in value.split())


def _product_tag(value: Any) -> str | None:
    value = _text(value)
    if not value:
        return None
    lowered = value.casefold()
    # Prefer the most specific phrase, so "ceiling fan" remains useful.
    for phrase in ("ceiling fan", "exhaust fan", "cooling fan", "led bulb", "light bulb", "submersible pump", "pump set"):
        if phrase in lowered:
            return _title(phrase)
    if lowered in _ALIASES:
        return _title(_ALIASES[lowered])
    for alias, category in sorted(_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            return _title(category)
    # Requirements are already model-normalized, but avoid turning an SKU or
    # long catalogue description into a noisy contact-list tag.
    if len(value) <= 32 and not re.search(r"\b\d{3,}\b", value):
        return _title(value)
    return None


def _requirement_values(*sources: Any) -> list[Any]:
    values: list[Any] = []
    for source in sources:
        if isinstance(source, dict):
            values.extend(source.get("customer_requirements") or source.get("product_requirements") or source.get("productRequirements") or source.get("requirements") or [])
        elif isinstance(source, list):
            values.extend(source)
    return values


def derive_business_tags(
    context: dict[str, Any] | None = None,
    *,
    requirements: list[dict[str, Any]] | None = None,
    recommendations: list[dict[str, Any]] | None = None,
    payload: dict[str, Any] | None = None,
    current_intent: str | None = None,
    document_kind: str | None = None,
) -> list[str]:
    """Create stable, human-readable tags without transcription statuses."""
    context = context if isinstance(context, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    tags: list[str] = []

    def add(value: str | None) -> None:
        value = _text(value)
        if value and value.casefold() not in {tag.casefold() for tag in tags}:
            tags.append(value)

    product_values = _requirement_values(context, payload, requirements)
    for item in product_values:
        if not isinstance(item, dict) or item.get("excluded") or item.get("status") == "removed":
            continue
        add(_product_tag(item.get("product") or item.get("name") or item.get("category")))
    for key in ("product", "productOrService", "product_name"):
        if not product_values:
            add(_product_tag(payload.get(key)))

    intent = _text(current_intent or context.get("last_intent") or payload.get("intent")).casefold()
    if intent in _INTENT_TAGS:
        add(_INTENT_TAGS[intent])
    if recommendations or context.get("recent_recommendations"):
        add("Product recommendation")

    document = _text(document_kind or context.get("document_kind") or payload.get("document_kind")).casefold()
    add(_DOCUMENT_TAGS.get(document))
    for key, label in _DOCUMENT_TAGS.items():
        if payload.get(key) or payload.get(f"{key}Discussed"):
            add(label)
    return tags[:12]

