from __future__ import annotations

import json
from typing import Any

from ..agent import _invoke_with_retry, parse_json
from ..settings import settings


class ConversationMonitorAgent:
    async def run(self, chunk: dict[str, Any], context: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
        if not settings.openai_api_key:
            return {'requirements': [], 'entities': [], 'intents': [route['intent']]}
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        prompt = ('Return JSON only. Extract only facts explicitly stated by the customer or clearly grounded in the chunk. '
                  'Do not invent products, SKUs, prices, stock, names, or commitments. Preserve changes to requirements. '
                  'Keys: requirements (objects with product, sku, category, attributes, quantity, budget, source), products, '
                  'features, brands, timeline, availability, discounts, objections, unanswered_questions, commitments, '
                  'contact_details, buying_signals, entities, focal_product, focal_product_sku, budget, intent. '
                  f'Current durable context: {json.dumps(context, ensure_ascii=False, default=str)[:9000]} '
                  f'Route: {json.dumps(route, ensure_ascii=False)} Chunk: {json.dumps(chunk, ensure_ascii=False, default=str)}')
        response = await _invoke_with_retry(client, model=settings.voice_extraction_model, temperature=0, max_tokens=1200,
            messages=[{'role': 'system', 'content': prompt}, {'role': 'user', 'content': chunk.get('english_text') or chunk.get('source_text') or ''}])
        parsed = parse_json(response.choices[0].message.content or '')
        parsed.setdefault('intent', route['intent'])
        return parsed


class ProductRecommendationAgent:
    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        # Catalogue lookup remains in the existing /products + Zoho path. This
        # agent emits grounded query terms and preserves recent results.
        queries = []
        if context.get('focal_product'): queries.append(str(context['focal_product']))
        for item in context.get('customer_requirements') or []:
            value = item.get('product') or item.get('name') or item.get('category')
            if value and value not in queries: queries.append(str(value))
        return {'catalogue_queries': queries[:5], 'recommendations': context.get('recent_recommendations') or []}
