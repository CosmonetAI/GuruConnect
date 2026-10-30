"""Bounded catalogue embeddings through the application's existing OpenAI API.

No additional inference process or model download is required. Only inventory
text and product queries are embedded; full conversations and customer contact
details are not accepted by the catalogue retrieval interface.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import hashlib
import math
import time

from openai import AsyncOpenAI

from .settings import settings


DIMENSIONS = 384
MAX_BATCH = 32
MAX_INPUT_BYTES = 6000
_CLIENTS: dict[tuple, 'CatalogueEmbeddingClient'] = {}


class EmbeddingUnavailable(RuntimeError):
    """Safe provider error: never includes credentials or response bodies."""


def embedding_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('An embedding input must contain catalogue text')
    # Each byte is at most one token. This conservative limit also bounds the
    # total request below the API's token limit without a tokenizer dependency.
    return value.strip().encode('utf-8')[:MAX_INPUT_BYTES].decode('utf-8', errors='ignore')


class CatalogueEmbeddingClient:
    dimensions = DIMENSIONS

    def __init__(self, api_key: str, *, model='text-embedding-3-small', timeout=20, http_client=None):
        if model not in {'text-embedding-3-small', 'text-embedding-3-large'}:
            raise ValueError('Select a supported text-embedding-3 catalogue model')
        if not api_key:
            raise ValueError('OPENAI_API_KEY is required for semantic retrieval')
        self.model = model
        self.model_id = f'openai:{model}:{DIMENSIONS}:catalogue-v1'
        self.timeout = max(2, min(60, float(timeout)))
        self._client = AsyncOpenAI(api_key=api_key, timeout=self.timeout, max_retries=0, http_client=http_client)
        self._gate = asyncio.Semaphore(2)
        self._queries: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()

    async def close(self):
        self._queries.clear()
        await self._client.close()

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if not isinstance(texts, list) or not 1 <= len(texts) <= MAX_BATCH:
            raise ValueError(f'Embed between 1 and {MAX_BATCH} catalogue texts per request')
        inputs = [embedding_text(value) for value in texts]
        keys = [hashlib.sha256(value.encode()).hexdigest() for value in inputs]
        vectors, missing = {}, {}
        now = time.monotonic()
        for index, key in enumerate(keys):
            cached = self._queries.get(key) if query else None
            if cached and now - cached[0] < 300:
                vectors[index] = list(cached[1])
                self._queries.move_to_end(key)
            else:
                missing.setdefault(key, {'text': inputs[index], 'indices': []})['indices'].append(index)
        if not missing:
            return [vectors[index] for index in range(len(inputs))]
        requested = list(missing.values())
        try:
            async with self._gate:
                async with asyncio.timeout(self.timeout):
                    response = await self._client.embeddings.create(
                        model=self.model, dimensions=DIMENSIONS, encoding_format='float',
                        input=[row['text'] for row in requested],
                    )
            if response.model != self.model or len(response.data) != len(requested):
                raise ValueError('Unexpected embedding model or batch length')
            returned = {}
            for row in response.data:
                if type(row.index) is not int or not 0 <= row.index < len(requested) or row.index in returned:
                    raise ValueError('Invalid embedding response ordering')
                if len(row.embedding) != DIMENSIONS or any(isinstance(value, bool) for value in row.embedding):
                    raise ValueError('Invalid embedding dimensions')
                vector = [float(value) for value in row.embedding]
                if not all(math.isfinite(value) for value in vector):
                    raise ValueError('Non-finite embedding')
                norm = math.sqrt(math.fsum(value * value for value in vector))
                if not math.isfinite(norm) or norm <= 0:
                    raise ValueError('Empty embedding')
                returned[row.index] = [value / norm for value in vector]
            if len(returned) != len(requested):
                raise ValueError('Incomplete embedding response')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise EmbeddingUnavailable('Semantic search is temporarily unavailable; catalogue text search continues.') from exc
        for index, row in enumerate(requested):
            vector = returned[index]
            for original in row['indices']:
                vectors[original] = list(vector)
            if query:
                key = keys[row['indices'][0]]
                self._queries[key] = (time.monotonic(), list(vector))
                self._queries.move_to_end(key)
                while len(self._queries) > 128:
                    self._queries.popitem(last=False)
        return [vectors[index] for index in range(len(inputs))]


def get_embedding_client() -> CatalogueEmbeddingClient | None:
    if not settings.catalogue_semantics_enabled or not settings.openai_api_key:
        return None
    identity = (settings.openai_api_key, settings.catalogue_embedding_model, settings.catalogue_embedding_timeout_seconds)
    if identity not in _CLIENTS:
        _CLIENTS[identity] = CatalogueEmbeddingClient(identity[0], model=identity[1], timeout=identity[2])
    return _CLIENTS[identity]


async def close_embedding_clients():
    clients = list(_CLIENTS.values())
    _CLIENTS.clear()
    await asyncio.gather(*(client.close() for client in clients), return_exceptions=True)
