import json
import time
from uuid import uuid4
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError, SecretStr
from recorder.config import Settings
from recorder.main import create_app
from recorder.security import JWTVerifier, SecretBox
from recorder.schemas import Chunk, ConversationCreate, Segment
from recorder.storage import Storage


@pytest.fixture
def settings():
    return Settings(supabase_url="https://test-project.supabase.co", credential_encryption_key=Fernet.generate_key().decode())


@pytest.fixture
def signing_key(settings):
    key = ec.generate_private_key(ec.SECP256R1())
    public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    public.update(kid="rotating-key", alg="ES256")
    verifier = JWTVerifier(settings)
    verifier.keys = {public["kid"]: public}
    verifier.cached_at = time.monotonic()
    return key, public, verifier


def signed(key, settings, **claims):
    payload = {"sub": str(uuid4()), "aud": "authenticated", "iss": settings.issuer, "iat": int(time.time()), "exp": int(time.time())+3600, "role": "authenticated", "email": "a@example.test"}
    payload.update(claims)
    return jwt.encode(payload, key, algorithm="ES256", headers={"kid": "rotating-key"})


@pytest.mark.asyncio
async def test_verified_signature_audience_issuer_expiry_and_role(settings, signing_key):
    key, _, verifier = signing_key
    assert (await verifier.verify(signed(key, settings))).email == "a@example.test"
    for claims in ({"aud": "anon"}, {"iss": "https://evil.example/auth/v1"}, {"exp": int(time.time())-60}, {"role": "service_role"}, {"sub": "not-a-uuid"}):
        with pytest.raises(HTTPException) as error:
            await verifier.verify(signed(key, settings, **claims))
        assert error.value.status_code == 401
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(HTTPException):
        await verifier.verify(signed(other, settings))
    with pytest.raises(HTTPException):
        await verifier.verify(jwt.encode({"sub": str(uuid4())}, "attacker"*8, algorithm="HS256", headers={"kid": "rotating-key"}))


@pytest.mark.asyncio
async def test_jwks_rotation_refreshes_once(settings, signing_key, respx_mock):
    key, public, verifier = signing_key
    verifier.keys = {"old-key": public}
    route = respx_mock.get(f"{settings.issuer}/.well-known/jwks.json").mock(return_value=httpx.Response(200, json={"keys": [public]}))
    await verifier.verify(signed(key, settings))
    await verifier.verify(signed(key, settings))
    assert route.call_count == 1


def test_missing_configuration_keeps_health_and_fails_closed():
    with TestClient(create_app(Settings())) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        assert client.get("/api/v1/workspaces").status_code == 401
        assert client.get("/api/v1/workspaces", headers={"Authorization": "Bearer untrusted"}).status_code == 503
        assert client.get("/internal/v1/claim").status_code == 405
        response = client.post("/api/v1/conversations", content=b"x"*1_000_001)
        assert response.status_code == 413
        def chunks():
            yield b"x"*500000
            yield b"x"*500001
        response = client.post("/api/v1/conversations", content=chunks(), headers={"Transfer-Encoding": "chunked"})
        assert response.status_code == 413


def test_strict_schemas_no_client_owner_and_real_uuid_json():
    payload = {"customer_name": "Sam", "phone": "9876543210", "consent_confirmed": True}
    assert ConversationCreate.model_validate(payload).customer_name == "Sam"
    with pytest.raises(ValidationError):
        ConversationCreate.model_validate({**payload, "owner_id": str(uuid4())})
    with pytest.raises(ValidationError):
        ConversationCreate.model_validate({"customer_name": "Sam", "consent_confirmed": False})
    assert ConversationCreate.model_validate({"customer_name": "Sam", "email": "sam@example.com", "consent_confirmed": True}).email == "sam@example.com"
    with pytest.raises(ValidationError):
        ConversationCreate.model_validate({"customer_name": "Sam", "consent_confirmed": True})
    data = {"part_id": str(uuid4()), "sequence": 0, "sample_count": 80000, "start_sample": 0, "bytes": 160044, "sha256": "a"*64, "lease_token": str(uuid4())}
    assert Chunk.model_validate(data).sample_count == 80000
    for bad in ({"sample_count": "80000"}, {"sequence": -1}, {"sha256": "../secret"}, {"sample_rate": 44100}, {"bytes": 10}):
        with pytest.raises(ValidationError):
            Chunk.model_validate({**data, **bad})
    for bad in ({"start": 4.0, "end": 3.0, "text": "bad"}, {"start": float("nan"), "end": 2.0, "text": "bad"}):
        with pytest.raises(ValidationError):
            Segment.model_validate(bad)


def test_secret_encryption_authenticates_and_is_not_deterministic(settings):
    box = SecretBox(settings)
    secret = {"api_key": "private-value"}
    a, b = box.encrypt(secret), box.encrypt(secret)
    assert a != b and "private-value" not in a
    assert box.decrypt(a) == secret
    with pytest.raises(HTTPException):
        box.decrypt(a[:-3] + "zzz")


@pytest.mark.asyncio
async def test_immutable_storage_conflict_checks_hash(settings, respx_mock):
    settings.supabase_service_role_key = SecretStr("test-service-key")
    storage = Storage(settings)
    path = f"{uuid4()}/{uuid4()}/{uuid4()}/results/{uuid4()}.json"
    url = f"{storage.base}/object/{storage.path(path)}"
    respx_mock.post(url).mock(return_value=httpx.Response(409))
    respx_mock.get(f"{storage.base}/object/authenticated/{storage.path(path)}").mock(return_value=httpx.Response(200, content=b"old"))
    with pytest.raises(HTTPException) as error:
        await storage.put(path, b"new")
    assert error.value.status_code == 409
    assert storage.bucket(path) == "recorder-artifacts"
    assert storage.bucket(path.split("/results/")[0]+"/playback/file.wav") == "recorder-playback"


def test_openapi_contains_all_authenticated_workflows():
    schema = create_app(Settings()).openapi()
    assert len(schema["paths"]) >= 43
    assert "post" in schema["paths"]["/api/v1/conversations/{conversation_id}/chunks/confirm"]
    assert "post" in schema["paths"]["/internal/v1/jobs/{job_id}/result"]
    assert schema["components"]["schemas"]["WorkerResult"]["additionalProperties"] is False
