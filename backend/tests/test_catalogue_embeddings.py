import asyncio
import json
import math

import httpx
import pytest

from app.catalogue_embeddings import CatalogueEmbeddingClient, EmbeddingUnavailable, embedding_text


def payload(count=1, **changes):
    result = {'object': 'list', 'model': 'text-embedding-3-small', 'usage': {'prompt_tokens': 2, 'total_tokens': 2},
              'data': [{'object': 'embedding', 'index': index, 'embedding': [float(index + 1)] + [0.0] * 383}
                       for index in range(count)]}
    return {**result, **changes}


def client(handler):
    return CatalogueEmbeddingClient('synthetic-key', http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_existing_openai_endpoint_dimensions_ordering_and_no_customer_metadata():
    requests = []

    async def handler(request):
        requests.append(request)
        body = payload(2)
        body['data'].reverse()
        return httpx.Response(200, json=body)

    provider = client(handler)
    try:
        result = await provider.embed(['ceiling fan', 'electric motor'], query=True)
        body = json.loads(requests[0].content)
        assert str(requests[0].url) == 'https://api.openai.com/v1/embeddings'
        assert body == {'model': 'text-embedding-3-small', 'dimensions': 384,
                        'input': ['ceiling fan', 'electric motor'], 'encoding_format': 'float'}
        assert requests[0].headers['authorization'] == 'Bearer synthetic-key'
        assert len(result) == 2 and all(len(row) == 384 for row in result)
        assert all(math.isclose(sum(number * number for number in row), 1.0) for row in result)
        assert provider.model_id == 'openai:text-embedding-3-small:384:catalogue-v1'
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_queries_cache_and_deduplicate_without_cache_mutation():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=payload(len(requests[-1]['input'])))

    provider = client(handler)
    try:
        result = await provider.embed(['fan', 'fan'], query=True)
        assert len(requests[0]['input']) == 1
        result[0][0] = 99
        again = await provider.embed(['fan'], query=True)
        assert len(requests) == 1 and again[0][0] == 1
        await provider.embed(['motor', 'fan'], query=True)
        assert requests[-1]['input'] == ['motor']
        await provider.embed(['fan'], query=False)
        assert len(requests) == 3  # Durable index owns catalogue-text reuse.
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_failure_is_not_cached_or_retried_and_does_not_leak_private_body():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={'error': {'message': 'private-credential-details'}})
        return httpx.Response(200, json=payload())

    provider = client(handler)
    try:
        with pytest.raises(EmbeddingUnavailable) as error:
            await provider.embed(['fan'], query=True)
        assert 'private-' not in str(error.value)
        assert len(calls) == 1
        assert (await provider.embed(['fan'], query=True))[0][0] == 1
        assert len(calls) == 2
    finally:
        await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [
    payload(model='another-model'), payload(data=[]),
    payload(data=[{'index': 1, 'embedding': [1.0] * 384}]),
    payload(data=[{'index': 0, 'embedding': [1.0] * 383}]),
    payload(data=[{'index': 0, 'embedding': [0.0] * 384}]),
])
async def test_invalid_embeddings_never_become_similarity_facts(body):
    provider = client(lambda request: httpx.Response(200, json=body))
    try:
        with pytest.raises(EmbeddingUnavailable):
            await provider.embed(['fan'])
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_query_cache_is_bounded_and_input_utf8_budget_is_respected():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json=payload(len(body['input'])))

    provider = client(handler)
    try:
        for offset in range(0, 160, 32):
            await provider.embed([f'product {index}' for index in range(offset, offset + 32)], query=True)
        assert len(provider._queries) == 128
        await provider.embed(['মোটর ' * 3000])
        assert len(requests[-1]['input'][0].encode('utf-8')) <= 6000
        assert '\ufffd' not in requests[-1]['input'][0]
        for invalid in ([], [''] * 2, ['fan'] * 33):
            with pytest.raises(ValueError):
                await provider.embed(invalid)
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_cancellation_is_not_hidden_as_provider_failure():
    async def handler(request):
        raise asyncio.CancelledError

    provider = client(handler)
    try:
        with pytest.raises(asyncio.CancelledError):
            await provider.embed(['fan'])
    finally:
        await provider.close()


def test_empty_and_nontext_input_is_rejected():
    for value in (' ', None, 9):
        with pytest.raises(ValueError):
            embedding_text(value)
