from __future__ import annotations

from dataclasses import dataclass
import re
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
    """Conservative offline hint, never an authorization for a business action.

    The semantic model owns the final route. Avoid substring document triggers:
    a price question mentioning a rejected quotation is still a price question.
    """
    value = str(text or '').lower().strip()
    # Remove locally negated clauses before looking for positive requests. The
    # fallback deliberately abstains on other ambiguous language.
    positive = re.sub(r"\b(?:no|not|don't|do not|without|cancel|skip)\b[^,.;!?]*?(?=\b(?:just|only|but|instead)\b|[,.;!?]|$)", ' ', value)
    def matches(pattern: str) -> bool:
        return re.search(pattern, positive) is not None
    if matches(r'\b(?:speak|talk|connect|transfer)\b.{0,25}\b(?:human|agent|salesperson)\b|\bcall me\b'):
        return ROUTES['human_handoff']
    if matches(r'\b(?:send|prepare|create|need|want|issue)\b.{0,35}\b(?:invoice|tax invoice)\b|\bbill me\b'):
        return ROUTES['invoice_request']
    if matches(r'\b(?:place|confirm)\s+(?:the\s+|my\s+)?order\b|\b(?:need|create|prepare)\b.{0,25}\bsales order\b'):
        return ROUTES['sales_order_request']
    if matches(r'\b(?:need|create|prepare|send|want)\b.{0,25}\bpurchase order\b'):
        return ROUTES['purchase_order_request']
    if matches(r'\b(?:send|prepare|create|need|want|give)\b.{0,35}\b(?:quote|quotation|estimate|written offer|boq|bill of quantities)\b'):
        return ROUTES['quote_request']
    if matches(r'\b(?:discount|best price|reduce the price)\b'):
        return ROUTES['discount']
    if matches(r'\b(?:available|availability|stock)\b'):
        return ROUTES['availability'] if context.get('focal_product') or context.get('recent_recommendations') else ROUTES['product_information']
    if matches(r'\b(?:prices?|costs?|rates?)\b|\bhow much\b'):
        return ROUTES['pricing']
    if matches(r'\b(?:recommend|suggest|which one|best option|show me)\b'):
        return ROUTES['product_recommendation']
    if matches(r'\b(?:add|remove|update)\b.{0,30}\bcart\b'):
        return ROUTES['cart_update']
    if re.search(r'\b(?:need|looking for|prefer|budget|quiet|want|cancel|instead|make that|make it|make two)\b', value):
        return ROUTES['customer_requirements']
    if re.fullmatch(r'(?:that one|this one)[.! ]*', value):
        return ROUTES['buying_signal'] if len(context.get('recent_recommendations') or []) == 1 else ROUTES['clarification']
    return ROUTES['general_conversation']


def model_route(extraction: dict[str, Any]) -> dict[str, Any]:
    """Route a validated semantic result while retaining confidence and evidence."""
    intent = extraction.get('intent')
    route = ROUTES[intent] if intent in ROUTES else ROUTES['unknown']
    return {**route.__dict__, 'confidence': extraction.get('intent_confidence', 0),
            'evidence': extraction.get('intent_evidence') or [], 'source': 'model',
            'requires_agent_approval': route.agent == 'business_actions', 'auto_execute': False}
