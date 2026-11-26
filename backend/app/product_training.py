"""Generic product-language hints learned from the shared item catalogue.

This is intentionally a small, brand-neutral vocabulary.  The workbook is a
training aid for extracting product intent, not a customer or brand-specific
configuration.
"""

from __future__ import annotations


PRODUCT_CHEAT_SHEET: dict[str, tuple[str, ...]] = {
    "fan": ("fan", "fans", "ceiling fan", "exhaust fan", "cooling fan"),
    "bulb": ("bulb", "bulbs", "led bulb", "light bulb", "lamp", "lamps"),
    "light": ("light", "lights", "led", "led light", "lighting"),
    "pump": ("pump", "pumps", "pumpset", "pump set", "submersible pump"),
    "motor": ("motor", "motors"),
    "cable": ("cable", "cables"),
    "wire": ("wire", "wires"),
    "pipe": ("pipe", "pipes"),
    "mcb": ("mcb", "miniature circuit breaker"),
    "mccb": ("mccb", "moulded case circuit breaker"),
    "contactor": ("contactor", "contactors"),
    "relay": ("relay", "relays"),
    "switch": ("switch", "switches"),
    "socket": ("socket", "sockets"),
    "capacitor": ("capacitor", "capacitors"),
    "panel": ("panel", "panels"),
    "valve": ("valve", "valves"),
    "bearing": ("bearing", "bearings"),
}


def product_cheat_sheet_for_prompt() -> dict[str, list[str]]:
    """Return JSON-safe extraction hints without exposing workbook rows."""
    return {category: list(aliases) for category, aliases in PRODUCT_CHEAT_SHEET.items()}

