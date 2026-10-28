from __future__ import annotations

from dataclasses import dataclass
from typing import Any


INTENTS = {
    'customer_requirements', 'product_recommendation', 'product_information', 'pricing',
    'availability', 'discount', 'clarification', 'buying_signal', 'follow_up', 'cart_update',
    'quote_request', 'invoice_request', 'sales_order_request', 'purchase_order_request',
    'general_conversation', 'human_handoff', 'unknown',
}


@dataclass(frozen=True)
class Route:
    intent: str
    agent: str
    confidence_floor: float


ROUTES = {
    'customer_requirements': Route('customer_requirements', 'conversation_monitor', .65),
    'product_recommendation': Route('product_recommendation', 'product_recommendation', .72),
    'product_information': Route('product_information', 'product_information', .72),
    'pricing': Route('pricing', 'pricing_availability', .78),
    'availability': Route('availability', 'pricing_availability', .78),
    'discount': Route('discount', 'pricing_availability', .78),
    'clarification': Route('clarification', 'conversation_monitor', .65),
    'buying_signal': Route('buying_signal', 'conversation_monitor', .65),
    'follow_up': Route('follow_up', 'conversation_monitor', .65),
    'cart_update': Route('cart_update', 'business_actions', .88),
    'quote_request': Route('quote_request', 'business_actions', .9),
    'invoice_request': Route('invoice_request', 'business_actions', .9),
    'sales_order_request': Route('sales_order_request', 'business_actions', .9),
    'purchase_order_request': Route('purchase_order_request', 'business_actions', .9),
    'human_handoff': Route('human_handoff', 'human_handoff', .9),
    'general_conversation': Route('general_conversation', 'conversation_monitor', .55),
    'unknown': Route('unknown', 'conversation_monitor', .55),
}


def classify_voice_intent(text: str, context: dict[str, Any]) -> Route:
    value = str(text or '').lower().strip()
    if any(term in value for term in ('human', 'agent', 'call me', 'salesperson')): return ROUTES['human_handoff']
    if any(term in value for term in ('invoice', 'tax invoice', 'bill me')): return ROUTES['invoice_request']
    if any(term in value for term in ('sales order', 'place the order', 'confirm order')): return ROUTES['sales_order_request']
    if any(term in value for term in ('quote', 'quotation', 'estimate')): return ROUTES['quote_request']
    if any(term in value for term in ('discount', 'offer', 'best price', 'reduce the price')): return ROUTES['discount']
    if any(term in value for term in ('available', 'availability', 'stock', 'in stock')):
        return ROUTES['availability'] if context.get('focal_product') or context.get('recent_recommendations') else ROUTES['product_information']
    if any(term in value for term in ('price', 'cost', 'rate', 'how much')): return ROUTES['pricing']
    if any(term in value for term in ('recommend', 'suggest', 'which one', 'best option', 'show me')): return ROUTES['product_recommendation']
    if any(term in value for term in ('cart', 'add this', 'remove this', 'quantity')): return ROUTES['cart_update']
    if any(term in value for term in ('need', 'looking for', 'prefer', 'budget', 'quiet', 'want')): return ROUTES['customer_requirements']
    return ROUTES['general_conversation']
