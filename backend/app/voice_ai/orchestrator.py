from __future__ import annotations

from typing import Any

from .agents import ConversationMonitorAgent, ProductRecommendationAgent
from .context import VoiceConversationContext
from .intents import classify_voice_intent


class VoiceConversationOrchestrator:
    """Voice adaptation of Shoppr's loader → intent → route → agent → persist flow."""

    def __init__(self) -> None:
        self.monitor = ConversationMonitorAgent()
        self.recommendations = ProductRecommendationAgent()

    async def run(self, *, chunk: dict[str, Any], durable_context: dict[str, Any] | None) -> dict[str, Any]:
        context = VoiceConversationContext.from_mapping(durable_context)
        route = classify_voice_intent(chunk.get('english_text') or chunk.get('source_text') or '', context.as_dict())
        extraction = await self.monitor.run(chunk, context.as_dict(), route.__dict__)
        extraction['intent'] = extraction.get('intent') if extraction.get('intent') in {route.intent} else route.intent
        context.merge_extraction(extraction, {
            'conversation_id': chunk.get('conversation_id'),
            'transcript_chunk_id': chunk.get('id'),
            'fingerprint': chunk.get('fingerprint'),
        })
        context.last_intent = route.intent
        agent_result = await self.recommendations.run(context.as_dict()) if route.agent == 'product_recommendation' else {}
        return {'context': context.as_dict(), 'route': route.__dict__, 'extraction': extraction, 'agent_result': agent_result}
