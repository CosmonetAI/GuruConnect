"""Real batch ASR and standard text translation; no inference-model imports."""
from __future__ import annotations

import html
import io
import re
import wave
from dataclasses import asdict, dataclass
from html.parser import HTMLParser

import httpx

LANGUAGES = {"kan": "kn", "hin": "hi", "ben": "bn", "eng": "en", "kn": "kn", "hi": "hi", "bn": "bn", "en": "en"}


class ProviderError(Exception):
    def __init__(self, code: str, retryable: bool = False, uncertain: bool = False):
        super().__init__(code)
        self.code, self.retryable, self.uncertain = code, retryable, uncertain


@dataclass(frozen=True)
class Capabilities:
    engine_id: str
    model: str
    native_english: bool
    diarization: bool
    timestamps: str
    cancellation: bool = False
    strategy: str = "batch"


REGISTRY = {
    "elevenlabs": Capabilities("elevenlabs", "scribe_v2", False, True, "source_word"),
    "faster-whisper": Capabilities("faster-whisper", "large-v3/community-1", True, True, "segment"),
    "azure-translator": Capabilities("azure-translator", "standard-v3", True, False, "source_turn"),
    "openai-translator": Capabilities("openai-translator", "gpt-5.6-luna", True, False, "source_turn"),
}


def descriptors() -> list[dict]:
    return [asdict(value) for value in REGISTRY.values()]


def language_code(value: str | None) -> str | None:
    if not value or value == "auto":
        return None
    return LANGUAGES.get(value.lower(), value.lower())


def _error_tag(response: httpx.Response, *, metadata: bool = False) -> str | None:
    """Read an error tag without exposing provider messages or response bodies."""
    try:
        payload = response.json()
    except ValueError:
        return None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if not isinstance(detail, dict):
        return None
    # New responses use code; status remains a legacy compatibility field.
    tag = detail.get("code")
    if not isinstance(tag, str) or not tag:
        tag = detail.get("status")
    # ElevenLabs can return legacy unauthorized for this exact account-read
    # restriction. The exception is metadata-only and cannot establish ASR access.
    message = detail.get("message")
    if (metadata and response.status_code in (401, 403) and tag == "unauthorized"
            and isinstance(message, str)
            and message.strip() == "The API key you used is missing the permission user_read to execute this operation."):
        return "missing_permissions"
    return tag if isinstance(tag, str) else None


def checked_response(response: httpx.Response, *, metadata: bool = False) -> dict | list:
    if response.status_code == 429:
        raise ProviderError("provider_rate_limited", retryable=True)
    if response.status_code >= 500:
        # A server failure may occur after billable execution. Do not auto-resubmit speech.
        raise ProviderError("provider_unavailable", retryable=True, uncertain=True)
    if not response.is_success:
        if 400 <= response.status_code < 500:
            tag = _error_tag(response,metadata=metadata)
            # Only these known tags influence classification. An arbitrary provider
            # message/tag must never become an application error or leak credentials.
            if tag in {"invalid_api_key", "missing_api_key", "invalid_authorization_header", "unauthorized", "sign_in_required"}:
                raise ProviderError("invalid_credentials")
            if tag in {"missing_permissions", "insufficient_permissions"}:
                raise ProviderError("metadata_permission_denied" if metadata else "provider_permission_denied")
            if tag in {"quota_exceeded", "insufficient_credits"}:
                raise ProviderError("provider_quota_exceeded")
            if tag == "payment_required":
                raise ProviderError("provider_payment_required")
            if tag in {"paid_plan_required", "subscription_required", "feature_not_available"}:
                raise ProviderError("provider_plan_access_denied")
            if tag == "model_access_denied":
                raise ProviderError("provider_model_access_denied")
            if tag in {"forbidden", "workspace_access_denied"}:
                raise ProviderError("provider_access_denied")
        if response.status_code == 401:
            raise ProviderError("invalid_credentials")
        if response.status_code == 402:
            raise ProviderError("provider_payment_required")
        if response.status_code == 403:
            raise ProviderError("provider_access_denied")
        raise ProviderError("provider_request_rejected")
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError("provider_invalid_response", uncertain=True) from exc


class ElevenLabsAdapter:
    capabilities = REGISTRY["elevenlabs"]

    def __init__(self, key: str):
        if not key:
            raise ProviderError("engine_unconfigured")
        self.key = key

    async def test(self, functional: bool = False) -> dict:
        if functional:
            audio = io.BytesIO()
            with wave.open(audio, "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
                wav.writeframes(b"\0\0" * 16000)
            await self.process(audio.getvalue(), None, "smoke-test")
            return {"connected": True, "functional": True, "accuracy_tested": False, "note": "Charged one-second silence decoding test; no accuracy claim."}
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                response=await client.get("https://api.elevenlabs.io/v1/user",headers={"xi-api-key":self.key})
                account=checked_response(response,metadata=True)
                if not isinstance(account,dict) or not isinstance(account.get('user_id'),str) or not account['user_id'].strip():raise ProviderError('provider_invalid_response')
        except httpx.RequestError as exc:
            raise ProviderError("provider_connection_failed", retryable=True) from exc
        return {"connected": True, "functional": False, "accuracy_tested": False}

    async def process(self, audio, language_hint: str | None = None, scope: str = "") -> dict:
        fields = {"model_id": "scribe_v2", "diarize": "true", "timestamps_granularity": "word", "use_multi_channel": "false", "tag_audio_events": "false", "use_speaker_library": "false", "detect_speaker_roles": "false", "no_verbatim": "false"}
        if hint := language_code(language_hint):
            fields["language_code"] = hint
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=15), follow_redirects=False) as client:
                response = await client.post("https://api.elevenlabs.io/v1/speech-to-text", headers={"xi-api-key": self.key}, data=fields, files={"file": ("recording.wav", audio, "audio/wav")})
                result = checked_response(response)
        except (httpx.TimeoutException, httpx.ReadError, httpx.RemoteProtocolError) as exc:
            raise ProviderError("external_execution_uncertain", uncertain=True) from exc
        except httpx.RequestError as exc:
            raise ProviderError("provider_connection_failed", retryable=True) from exc
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise ProviderError("provider_invalid_response", uncertain=True)
        segments: list[dict] = []
        for word in result.get("words", []):
            if word.get("type") not in ("word", "spacing"):
                continue
            if not isinstance(word.get("text"), str):
                raise ProviderError("provider_invalid_timestamps")
            start, end = word.get("start"), word.get("end")
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or start < 0 or end < start:
                raise ProviderError("provider_invalid_timestamps")
            speaker = word.get("speaker_id")
            normalized = {"start": float(start), "end": float(end), "text": word["text"]}
            if segments and segments[-1]["speaker"] == speaker and start - segments[-1]["end"] < 1.5:
                segments[-1]["words"].append(normalized); segments[-1]["end"] = float(end); segments[-1]["text"] += word["text"]
            else:
                segments.append({"start": float(start), "end": float(end), "text": word["text"], "speaker": speaker, "words": [normalized]})
        if not segments and result["text"].strip():
            from .audio import validate_wav, MAX_SESSION_SAMPLES
            if isinstance(audio, bytes): duration = validate_wav(audio, max_samples=MAX_SESSION_SAMPLES).samples / 16000
            else:
                audio.seek(0)
                with wave.open(audio, 'rb') as recording: duration = recording.getnframes()/16000
            segments = [{"start": 0.0, "end": duration, "text": result["text"], "speaker": None, "words": None}]
        return {"language": language_code(result.get("language_code")), "language_probability": result.get("language_probability"), "source_segments": segments, "english_segments": None, "diarization_status": "completed", "provenance": {"engine": "elevenlabs", "model": "scribe_v2", "request_id": response.headers.get("request-id") or response.headers.get("x-request-id"), "speaker_scope": scope}, "raw": result}


_TERMS = re.compile(r"(?<!\w)(?:(?=[A-Za-z0-9._/-]*\d)[A-Za-z][A-Za-z0-9._/-]*|\d+(?:[.,]\d+)*(?:[A-Za-z_/-][A-Za-z0-9._/-]*)?(?:\s?(?:kg|cm|mm|km|m|L|ml|%|₹|Rs))?)(?!\w)")


def protect_terms(text: str) -> tuple[str, list[str]]:
    terms: list[str] = []
    fragments = []; position = 0
    for match in _TERMS.finditer(text):
        fragments.append(html.escape(text[position:match.start()])); terms.append(match.group())
        fragments.append('<span class="notranslate">' + html.escape(match.group()) + '</span>'); position = match.end()
    fragments.append(html.escape(text[position:])); return "".join(fragments), terms


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.fragments: list[str] = []
    def handle_data(self, data):
        self.fragments.append(data)


class AzureTranslator:
    capabilities = REGISTRY["azure-translator"]
    def __init__(self, key: str, region: str):
        if not key:
            raise ProviderError("translator_unconfigured")
        self.key, self.region = key, region

    async def test(self, functional: bool = False) -> dict:
        # Public languages endpoint proves reachability only, not credentials.
        if not functional:
            async with httpx.AsyncClient(timeout=20) as client:
                checked_response(await client.get("https://api.cognitive.microsofttranslator.com/languages", params={"api-version": "3.0", "scope": "translation"}))
            return {"connected": False, "functional": False, "accuracy_tested": False, "note": "Reachability checked. Run the labelled functional test to validate credentials."}
        text = await self.translate("नमस्ते", "hi")
        return {"connected": bool(text), "functional": True, "accuracy_tested": False, "note": "Charged standard text translation smoke test."}

    async def translate(self, text: str, source_language: str | None = None) -> str:
        if not text.strip():
            return text
        protected, terms = protect_terms(text)
        if len(protected)>45000:
            # Bound standard Translator requests even for a long single-speaker
            # turn. Keep the source-turn interval; do not invent word alignment.
            parts=[];remaining=text
            while remaining:
                limit=min(10000,len(remaining));boundary=remaining.rfind(' ',0,limit)
                if limit<len(remaining) and boundary>limit//2:limit=boundary+1
                parts.append(remaining[:limit]);remaining=remaining[limit:]
            return ' '.join([await self.translate(part,source_language) for part in parts])
        params = {"api-version": "3.0", "to": "en", "textType": "html"}
        if source := language_code(source_language):
            params["from"] = source
        headers = {"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/json"}
        if self.region: headers["Ocp-Apim-Subscription-Region"] = self.region
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                response = await client.post("https://api.cognitive.microsofttranslator.com/translate", params=params, headers=headers, json=[{"Text": protected}])
                result = checked_response(response)
        except (httpx.TimeoutException,httpx.ReadError,httpx.RemoteProtocolError) as exc:
            raise ProviderError('translator_execution_uncertain',uncertain=True) from exc
        except httpx.RequestError as exc:
            raise ProviderError("translator_unavailable", retryable=True) from exc
        try:
            translated = result[0]["translations"][0]["text"]
            parser = PlainText(); parser.feed(translated); plain = "".join(parser.fragments)
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("translator_invalid_response") from exc
        # Never silently accept a changed quantity/model code; preserve source for retry/review.
        from collections import Counter
        expected = Counter(terms)
        observed = Counter(_TERMS.findall(plain))
        if any(observed[term] < count for term, count in expected.items()):
            raise ProviderError("translation_term_preservation_failed")
        return plain
