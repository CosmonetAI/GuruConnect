import pytest

from app.voice_ai.context import VoiceConversationContext
from app.voice_ai.intents import classify_voice_intent
from app.voice_ai.orchestrator import VoiceConversationOrchestrator


def source(chunk_id):
    return {'conversation_id': 'conversation-a', 'transcript_chunk_id': chunk_id, 'fingerprint': f'fp-{chunk_id}'}


def test_context_accumulates_three_chunks_and_keeps_sources():
    context = VoiceConversationContext()
    context.merge_extraction({'intent': 'customer_requirements', 'focal_product': 'ceiling fan', 'requirements': [{'product': 'ceiling fan', 'category': 'fan'}], 'features': ['energy efficient'], 'summary': 'Customer needs a ceiling fan.'}, source('one'))
    context.merge_extraction({'intent': 'pricing', 'budget': 5000, 'summary': 'Customer budget is ₹5,000.'}, source('two'))
    context.merge_extraction({'intent': 'customer_requirements', 'features': ['quiet operation'], 'summary': 'Customer prefers a quiet model.'}, source('three'))

    assert context.focal_product == 'ceiling fan'
    assert context.budget_max == 5000
    assert context.preferred_features == ['energy efficient', 'quiet operation']
    assert [item['transcript_chunk_id'] for item in context.source_refs] == ['one', 'two', 'three']
    assert len(context.recent_summaries) == 3


def test_context_updates_existing_requirement_without_duplication():
    context = VoiceConversationContext()
    context.merge_extraction({'requirements': [{'product': 'fan', 'quantity': 1}]}, source('one'))
    context.merge_extraction({'requirements': [{'product': 'fan', 'quantity': 2}]}, source('two'))
    context.merge_extraction({'features': ['quiet'], 'features_again': ['quiet']}, source('three'))
    assert len(context.customer_requirements) == 1
    assert context.customer_requirements[0]['quantity'] == 2


def test_contextual_planner_routes_follow_up_availability_question():
    route = classify_voice_intent('Is that model available?', {'focal_product': 'ceiling fan', 'recent_recommendations': [{'sku': 'FAN-1'}]})
    assert route.intent == 'availability'
    assert route.agent == 'pricing_availability'


@pytest.mark.asyncio
async def test_orchestrator_uses_durable_context_and_isolated_sessions(monkeypatch):
    async def fake_run(self, chunk, context, route):
        return {'intent': route['intent'], 'focal_product': chunk.get('focal_product'), 'requirements': chunk.get('requirements', []), 'features': chunk.get('features', []), 'summary': chunk.get('english_text', '')}

    monkeypatch.setattr('app.voice_ai.orchestrator.ConversationMonitorAgent.run', fake_run)
    orchestrator = VoiceConversationOrchestrator()
    first = await orchestrator.run(chunk={'id': 'one', 'conversation_id': 'a', 'english_text': 'Need a ceiling fan.', 'focal_product': 'ceiling fan', 'requirements': [{'product': 'ceiling fan'}]}, durable_context={})
    second = await orchestrator.run(chunk={'id': 'two', 'conversation_id': 'a', 'english_text': 'Budget is 5000.', 'budget': 5000, 'features': ['quiet']}, durable_context=first['context'])
    other = await orchestrator.run(chunk={'id': 'one', 'conversation_id': 'b', 'english_text': 'Need a lamp.', 'focal_product': 'lamp'}, durable_context={})

    assert second['context']['focal_product'] == 'ceiling fan'
    assert second['context']['preferred_features'] == ['quiet']
    assert other['context']['focal_product'] == 'lamp'
    assert 'ceiling fan' not in str(other['context'])
