from __future__ import annotations

from typing import Any

from .agents import ConversationMonitorAgent, ProductRecommendationAgent
from .context import VoiceConversationContext
from .intents import classify_voice_intent, model_route


class VoiceConversationOrchestrator:
    """Voice adaptation of Shoppr's loader → intent → route → agent → persist flow."""

    def __init__(self) -> None:
        self.monitor = ConversationMonitorAgent()
        self.recommendations = ProductRecommendationAgent()

    async def run(self, *, chunk: dict[str, Any], durable_context: dict[str, Any] | None) -> dict[str, Any]:
        context = VoiceConversationContext.from_mapping(durable_context)
        hint = classify_voice_intent(chunk.get('english_text') or chunk.get('source_text') or '', context.as_dict())
        extraction = await self.monitor.run(chunk, context.as_dict(), hint.__dict__)
        status = extraction.get('analysis_status', 'complete')
        if status != 'complete':
            # An offline route is a hint for the agent, never a completed
            # extraction. Keep fingerprints pending for a later valid run.
            return {'context': context.as_dict(), 'extraction': extraction,
                    'analysis_status': status, 'analysis_error': extraction.get('analysis_error'),
                    'route': {**hint.__dict__, 'source': 'rule_fallback', 'confidence': 0,
                              'evidence': [], 'auto_execute': False, 'requires_agent_approval': True},
                    'agent_result': await self.recommendations.run(context.as_dict())}
        route = model_route(extraction)
        context.merge_extraction(extraction, {
            'conversation_id': chunk.get('conversation_id'),
            'transcript_chunk_id': chunk.get('id'),
            'fingerprint': chunk.get('fingerprint'),
            'turn_fingerprints': chunk.get('turn_fingerprints') or [],
            'end_seconds': chunk.get('end_seconds'),
        })
        agent_result = await self.recommendations.run(context.as_dict())
        return {'context': context.as_dict(), 'route': route, 'extraction': extraction, 'agent_result': agent_result,
                'analysis_status': 'complete'}
