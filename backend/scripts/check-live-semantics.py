"""Opt-in synthetic provider check; creates no CRM records, orders or email.

Run from backend: .venv/bin/python scripts/check-live-semantics.py --live-api
This uses the configured OpenAI account and incurs normal API usage charges.
"""
import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.catalogue_embeddings import close_embedding_clients, get_embedding_client
from app.voice_ai.orchestrator import VoiceConversationOrchestrator


def motor(context):
    matches = [row for row in context.get('customer_requirements') or []
               if 'motor' in str(row.get('product') or '').casefold() and not row.get('excluded')]
    assert len(matches) == 1, 'Motor identity was lost or duplicated'
    return matches[0]


async def check(report_path=None):
    provider = get_embedding_client()
    if provider is None:
        raise RuntimeError('Configure OPENAI_API_KEY and enable catalogue semantics before this check')
    results = []
    try:
        descriptions = ['Quiet BLDC ceiling fan with remote control', 'Fan regulator replacement control',
                        'Single phase electric water pump motor 1 HP']
        vectors = await provider.embed(descriptions)
        query = (await provider.embed(['quiet ceiling fan with remote'], query=True))[0]
        best = max(range(len(vectors)), key=lambda index: sum(a * b for a, b in zip(vectors[index], query)))
        assert best == 0 and len(query) == 384, 'Synthetic embedding retrieval check failed'
        context, dialogue = {}, []
        for index, text in enumerate([
            'I need a one horsepower single phase motor and a ten amp double pole type C MCB.',
            'Make the motor two HP instead. No fan. Also prepare a quotation, but do not send it yet.',
            'Actually cancel the breaker, keep the motor. I will confirm quantity.',
        ]):
            started = time.monotonic()
            dialogue.append({'english_text': text, 'source_text': text, 'start_seconds': index * 10,
                             'end_seconds': (index + 1) * 10})
            result = await VoiceConversationOrchestrator().run(chunk={
                'id': f'synthetic-{index}', 'conversation_id': 'synthetic-provider-check',
                'fingerprint': f'synthetic-{index}', 'english_text': text, 'source_text': text,
                'recent_dialogue': dialogue, 'end_seconds': (index + 1) * 10,
            }, durable_context=context)
            assert result.get('analysis_status') == 'complete', f'Turn {index + 1}: {result.get("analysis_error")}'
            assert result['route'].get('auto_execute') is False
            context = result['context']
            results.append(deepcopy(context))
            print(json.dumps({'turn': index + 1, 'status': 'complete',
                              'seconds': round(time.monotonic() - started, 2)}), flush=True)
        assert motor(results[0])['required_specs']['power_hp'] == 1
        assert motor(results[1])['required_specs']['power_hp'] == 2
        assert motor(results[2])['required_specs']['phases'] == 1
        assert results[1]['document_kind'] == 'quotation'
        breakers = [row for row in results[2]['customer_requirements']
                    if 'mcb' in str(row.get('product')).casefold() or 'breaker' in str(row.get('product')).casefold()]
        assert len(breakers) == 1 and breakers[0].get('excluded') is True
        print('Passed: embeddings, three-turn corrections, retained phase, quotation request and withdrawal. No external business actions.')
    finally:
        if report_path:
            Path(report_path).write_text(json.dumps({'contexts': results}, indent=2))
        await close_embedding_clients()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-api', action='store_true', help='Use the configured OpenAI API (metered)')
    parser.add_argument('--report', help='Optional path for synthetic context snapshots')
    options = parser.parse_args()
    if not options.live_api:
        parser.error('--live-api is required; ordinary regression tests make no paid calls')
    asyncio.run(check(options.report))
