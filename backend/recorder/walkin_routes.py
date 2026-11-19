"""Workspace-authorized walk-ins; browser storage is never a hosted fallback."""

import base64
import hashlib
import json
import logging
import re
import secrets
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .db import many, one
from .errors import fail
from .routes import audit
from .security import Workspace, administrator, owned_conversation, workspace
from .assignment import current_ticket_day, format_ticket_number, least_loaded_salesperson, next_daily_ticket
from app.zoho import set_member_id, set_organisation_id, zoho
from app.crm_provisioning import provision_enquiry_lead
from app.crm_lead import normalize_email, normalize_phone
from .providers import ElevenLabsAdapter

router = APIRouter(prefix="/api/v1/walkins", tags=["walkins"])
log = logging.getLogger(__name__)
Purpose = Literal["Product enquiry", "New business enquiry", "Product demonstration", "Pricing or quotation request", "Existing order enquiry", "Service or support request", "Meeting with a specific employee", "Follow-up meeting", "Other"]
Status = Literal["Registered", "Waiting for Assignment", "Assigned", "Waiting for Sales Agent", "In Discussion", "Requirements Captured", "Follow-up Required", "Converted to Enquiry", "Completed", "Cancelled"]
Availability = Literal["Available", "Busy", "Unavailable"]
Text200 = Annotated[str, Field(max_length=200)]
TRANSITIONS = {
    "Registered": {"Waiting for Assignment", "Cancelled"},
    "Waiting for Assignment": {"Cancelled"},
    "Assigned": {"Waiting for Sales Agent", "In Discussion", "Cancelled"},
    "Waiting for Sales Agent": {"In Discussion", "Cancelled"},
    "In Discussion": {"Requirements Captured", "Follow-up Required", "Completed", "Cancelled"},
    "Requirements Captured": {"Follow-up Required", "Completed"},
    "Follow-up Required": {"In Discussion", "Requirements Captured", "Completed", "Cancelled"},
    "Converted to Enquiry": {"Completed"}, "Completed": set(), "Cancelled": set(),
}
CLOSED = {"Completed", "Cancelled", "Converted to Enquiry"}

VOICE_FIELD_PROMPTS = {
    "name": "the person's full name",
    "phone": "the person's phone number",
    "email": "the person's email address",
    "organisation": "the person's organisation or company name",
    "requirement": "the person's project or purchase requirement",
    "customerType": "whether the person is here to buy/customer or sell/vendor",
}

VOICE_FIELD_QUESTION_HINTS = {
    "name": "What is your name? / May I have the visitor's full name?",
    "phone": "What is your phone number? / Please share your mobile number.",
    "email": "What is your email address?",
    "organisation": "What is your organisation or company name? / Which company are you from?",
    "requirement": "What do you need? / What is your project or purchase requirement?",
    "customerType": "Are you here to buy or sell? / Are you a customer or vendor?",
}

VOICE_FIELD_EXTRACTION_PROMPT = """Extract only the requested field from a short multilingual speech-to-text transcript.
The transcript may contain just the answer or a short question followed by the answer. Ignore
the question, greetings, filler, and phrases such as "my name is"; return only the answer.
Understand English, Hindi, Kannada, and common Latin transliterations, but return the
final value in English/Latin text only. For names, keep the complete first-and-last name.
For phone numbers, preserve digit order: zero/oh/o are
0, double eight is 88, triple five is 555, Hindi सात/saat is 7, Hindi आठ/aath is 8,
Kannada ಏಳು/elu is 7, and Kannada ಎಂಟು/entu is 8. Do not invent missing information.
Return JSON only with exactly one string key: value."""

VOICE_FIELD_EXTRACTION_FIELD_RULES = {
    "name": "For CRM search, return the complete name using Latin/English letters. If the transcript is in Hindi or Kannada script, transliterate it without translating or inventing. Example: अक्षत सक्सेना -> Akshat Saxena.",
    "phone": "Return ASCII digits only, preserving the exact digit sequence represented by the transcript. Never add, remove, reorder, or guess digits.",
    "email": "Return an ASCII Latin email address with exact spelling. Convert spoken at/dot/underscore/hyphen words, but never autocorrect, add, or remove characters.",
    "organisation": "Return the complete organisation name in English/Latin text. Transliterate a proper name when needed; do not add details.",
    "requirement": "Return the complete requirement in English. Translate faithfully when needed; do not summarize or invent details.",
    "customerType": "Return exactly customer or vendor based on the answer; do not infer from unrelated words.",
}

VOICE_FIELD_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "walkin_voice_field_answer",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    },
}

# The prompt asks the model to return ``value``, but older/fallback models may
# use the field name instead. Accept those equivalent keys so a valid answer
# is not discarded just because the JSON envelope differs.
VOICE_FIELD_VALUE_KEYS = {
    "name": ("value", "name", "full_name", "fullName", "customer_name", "customerName", "answer"),
    "phone": ("value", "phone", "phone_number", "phoneNumber", "mobile", "mobile_number", "mobileNumber", "number", "answer"),
    "email": ("value", "email", "email_address", "emailAddress", "answer"),
    "organisation": ("value", "organisation", "organization", "company", "company_name", "companyName", "answer"),
    "requirement": ("value", "requirement", "project", "project_name", "projectName", "answer"),
    "customerType": ("value", "customerType", "customer_type", "type", "answer"),
}

PHONE_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "nought": "0", "nil": "0", "one": "1", "two": "2", "to": "2", "too": "2", "three": "3", "tree": "3", "four": "4", "for": "4", "fore": "4",
    "five": "5", "six": "6", "sics": "6", "seven": "7", "saven": "7", "eight": "8", "ate": "8", "ait": "8", "nine": "9", "niner": "9",
    # Hindi, including common Latin transliterations used by speech-to-text.
    "शून्य": "0", "जीरो": "0", "एक": "1", "इक": "1", "दो": "2", "तीन": "3", "चार": "4",
    "पांच": "5", "पाँच": "5", "छह": "6", "छः": "6", "छ": "6", "छे": "6", "सात": "7", "सत": "7",
    "आठ": "8", "अठ": "8", "नौ": "9", "नव": "9",
    "shunya": "0", "shoonya": "0", "ek": "1", "ik": "1", "do": "2", "teen": "3",
    "char": "4", "chaar": "4", "panch": "5", "paanch": "5", "chhe": "6", "cheh": "6",
    "saat": "7", "sat": "7", "aath": "8", "ath": "8", "nau": "9", "nav": "9",
    # Kannada, including common Latin transliterations.
    "ಶೂನ್ಯ": "0", "ಸೊನ್ನೆ": "0", "ಒಂದು": "1", "ಎರಡು": "2", "ಮೂರು": "3", "ನಾಲ್ಕು": "4",
    "ಐದು": "5", "ಆರು": "6", "ಏಳು": "7", "ಎಂಟು": "8", "ಒಂಬತ್ತು": "9",
    "sonne": "0", "ondu": "1", "eradu": "2", "mooru": "3", "naalku": "4",
    "aidu": "5", "aaru": "6", "elu": "7", "yelu": "7", "entu": "8", "ombattu": "9",
}
PHONE_REPEAT_WORDS = {"double", "triple", "डबल", "ट्रिपल", "ಡಬಲ್", "ಟ್ರಿಪಲ್"}
PHONE_REPEAT_MULTIPLIERS = {"double": 2, "डबल": 2, "ಡಬಲ್": 2, "triple": 3, "ट्रिपल": 3, "ಟ್ರಿಪಲ್": 3}
EMAIL_LITERAL = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", re.IGNORECASE)


def _phone_digits(value: object) -> str:
    """Normalize model/transcript phone output, including spoken digit sequences."""
    text = str(value or "").strip().lower()
    direct = re.sub(r"\D", "", text)
    if len(direct) >= 7:
        digits = direct[-15:]
        # The walk-in form stores the country code separately. Keep an Indian
        # +91 number as the ten local digits it can accept.
        return digits[2:] if len(digits) == 12 and digits.startswith("91") else digits
    tokens = [token.strip(".,;:!?।؟") for token in re.findall(r"\S+", text, flags=re.UNICODE)]
    spoken_parts = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in PHONE_REPEAT_WORDS and index + 1 < len(tokens):
            next_token = tokens[index + 1]
            digit = PHONE_WORDS.get(next_token, next_token if next_token.isdigit() and len(next_token) == 1 else "")
            if digit:
                spoken_parts.append(digit * PHONE_REPEAT_MULTIPLIERS[token])
                index += 2
                continue
        digit = PHONE_WORDS.get(token, token if token.isdigit() and len(token) == 1 else "")
        if digit:
            spoken_parts.append(digit)
        index += 1
    spoken = "".join(spoken_parts)
    digits = spoken[-15:] if len(spoken) >= 7 else ""
    return digits[2:] if len(digits) == 12 and digits.startswith("91") else digits


def _phone_from_dialogue(value: object) -> str:
    """Find a phone answer in a translated dialogue without using other numbers."""
    text = str(value or "")
    patterns = (
        r"(?:phone|mobile|contact)\s*(?:number|no\.?|is|:)?\s*([^.!?\n]{7,100})",
        r"(?:number|नंबर|ಸಂಖ್ಯೆ)\s*(?:is|है|:)?\s*([^.!?\n]{7,100})",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.UNICODE):
            digits = _phone_digits(match.group(1))
            if digits:
                return digits
    return ""


def _explicit_dialogue_value(value: object, labels: str, *, prefer_last: bool = False) -> str:
    """Read a value only when the translated dialogue labels that field."""
    text = str(value or "")
    # A fixed question and its answer can arrive in one translated turn,
    # e.g. ``What is your name? Saumishri Patil``. Keep the question mark as
    # a separator, but never treat a question-only turn as an answer.
    pattern = rf"(?:{labels})\s*(?:name\s*)?(?:is|:|है|\?)?\s*([^.!?\n]{{2,140}})"
    candidate = ""
    for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.UNICODE):
        # Do not let the generic ``name`` label match the ``name`` inside
        # ``organisation name`` or ``company name``.
        prefix = text[max(0, match.start() - 32):match.start()]
        if re.search(r"(?:organisation|organization|company)\s+$", prefix, flags=re.IGNORECASE):
            continue
        # Speech-to-text often puts several answers in one turn without a
        # full stop (for example, "Groovy and our project requirement is ...").
        # Stop at the next first-person/possessive field label so one answer
        # cannot consume the remainder of the registration dialogue.
        answer = re.split(r"\s+(?:(?:(?:and|with)\s+)?(?:my|our|your|मेरा|मेरी|ನನ್ನ)\s+(?:name|email|phone|mobile|company|organisation|organization|number|project|requirement)|(?:and|with)\s+(?:name|email|phone|mobile|company|organisation|organization|number|project|requirement)|(?:and|with)\s+(?:uh+|um+|erm+|hmm+)\s*[,;:-]?)", match.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
        answer = answer.strip(" \t,;:-")
        # An organisation answer may follow a broken turn boundary as
        # ``organization\nMy name is Groove``. Remove the nested field label
        # before extracting the actual value.
        answer = re.sub(r"^(?:my|your)\s+(?:name|email|phone|mobile|company|organisation|organization|number)\s*(?:is|:|है|\?)?\s*", "", answer, flags=re.IGNORECASE)
        # Speech-to-text commonly inserts hesitation words between a field
        # label and the actual answer: ``organisation name is, uh, Groove``.
        answer = re.sub(r"^(?:uh+|um+|erm+|hmm+)\s*[,;:-]?\s*", "", answer, flags=re.IGNORECASE)
        answer = re.sub(r"\s+(?:and|with)\s*$", "", answer, flags=re.IGNORECASE).strip(" \t,;:-")
        if re.search(r"project|requirement|need|looking\s+for|want", labels, flags=re.IGNORECASE):
            answer = re.sub(r"^(?:require(?:ment)?|is|for)\s+", "", answer, flags=re.IGNORECASE).strip(" \t,;:-")
        if answer:
            candidate = answer
            if not prefer_last:
                return candidate
    return candidate


def _email_value(value: object) -> str:
    """Extract an email without allowing the model to alter a literal address."""
    text = str(value or "").strip()
    literal = EMAIL_LITERAL.search(text)
    if literal:
        return literal.group(0).rstrip(".,;:!?：")
    spoken = text.casefold()
    spoken = re.sub(r"\b(?:at the rate of|at the rate|at sign|at)\b", "@", spoken)
    spoken = re.sub(r"\b(?:dot|period)\b", ".", spoken)
    spoken = re.sub(r"\b(?:underscore)\b", "_", spoken)
    spoken = re.sub(r"\b(?:dash|hyphen)\b", "-", spoken)
    spoken = re.sub(r"\s*([@._-])\s*", r"\1", spoken)
    literal = EMAIL_LITERAL.search(spoken)
    return literal.group(0).rstrip(".,;:!?：") if literal else ""


def _completion_text(message: object) -> str:
    """Read text from both legacy string and newer content-part messages."""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    return ""


def _voice_value(field: str, raw: str, spoken: str) -> str:
    """Extract a field answer while tolerating equivalent JSON key names."""
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    value = next((parsed.get(key) for key in VOICE_FIELD_VALUE_KEYS[field] if parsed.get(key) not in (None, "")), "")
    if field == "phone":
        return _phone_digits(spoken) or _phone_digits(value)
    if field == "email":
        return _email_value(spoken) or _email_value(value)
    if field == "customerType":
        normalized = str(value).strip().lower()
        return normalized if normalized in {"customer", "vendor"} else ""
    return str(value).strip()

class VoiceFieldCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    field: str
    audioBase64: str = Field(min_length=1, max_length=8_000_000)
    contentType: str = Field(default="audio/webm", max_length=100)


def safe_text(value):
    if isinstance(value, str) and any(character == "\x00" or 0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("Text must contain valid Unicode without null characters")
    return value


@router.post("/voice-field")
async def extract_voice_field(
    body: VoiceFieldCreate,
    request: Request,
    context: Workspace = Depends(workspace),
):
    """Extract one registration value from a transient microphone clip.

    The clip is held in memory only for the provider request. It is never
    attached to a conversation, uploaded to storage, or written to Supabase.
    """
    field = body.field
    if field not in VOICE_FIELD_PROMPTS:
        fail("voice_field_invalid", 422, "This registration field cannot be extracted from voice")
    settings = request.app.state.settings
    if not settings.elevenlabs_api_key:
        fail("voice_extraction_unconfigured", 503, "ElevenLabs voice transcription is not configured")
    if not settings.openai_api_key:
        fail("voice_extraction_unconfigured", 503, "OpenAI voice extraction is not configured")
    elevenlabs_model = (settings.elevenlabs_transcription_model or "").strip()
    if not elevenlabs_model:
        fail("voice_extraction_unconfigured", 503, "ELEVENLABS_TRANSCRIPTION_MODEL is not configured")
    try:
        content = base64.b64decode(body.audioBase64, validate=True)
    except ValueError:
        fail("voice_field_audio_invalid", 422, "Record a short voice answer and try again")
    if not content or len(content) > 6_000_000:
        fail("voice_field_audio_invalid", 422, "Record a short voice answer and try again")
    try:
        speech = ElevenLabsAdapter(settings.elevenlabs_api_key.get_secret_value(), model_id=elevenlabs_model)
        spoken = await speech.transcribe_text(
            content,
            filename=f"walkin-{field}.webm",
            content_type=body.contentType or "audio/webm",
        )
        if not spoken:
            fail("voice_field_empty", 422, "No speech was detected. Try again")
        from openai import AsyncOpenAI
        from .activity_log import monitored_call

        client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value(), max_retries=1)
        extraction = await monitored_call(client.chat.completions.create(
            model=settings.openai_translation_model,
            temperature=0,
            response_format=VOICE_FIELD_RESPONSE_FORMAT,
            messages=[
                {"role": "system", "content": f"{VOICE_FIELD_EXTRACTION_PROMPT}\n\nField-specific rule: {VOICE_FIELD_EXTRACTION_FIELD_RULES[field]}\nField question hint: {VOICE_FIELD_QUESTION_HINTS[field]}"},
                {"role": "user", "content": f"Target field: {VOICE_FIELD_PROMPTS[field]}\nTranscript: {spoken}"},
            ],
        ), "openai", "chat.completions.create", metadata={"model": settings.openai_translation_model, "component": "voice_field_extraction"})
        raw = _completion_text(extraction.choices[0].message)
        value = _voice_value(field, raw, spoken)
        if not value:
            fail("voice_field_unresolved", 422, "The answer could not be understood. Try again")
        return {"field": field, "value": value, "transcript": spoken}
    except Exception as error:
        if isinstance(error, HTTPException):
            raise
        log.warning("walkin_voice_field_failed field=%s exception_type=%s", field, type(error).__name__)
        fail("voice_field_failed", 502, "Voice extraction failed. Try again")


def _walkin_extracted_fields(extractions, context):
    """Flatten only grounded registration fields for the live form preview."""
    fields = {}
    aliases = {
        "name": "customerName", "customername": "customerName", "customer_name": "customerName", "contact_name": "customerName", "full_name": "customerName",
        "phone": "mobileNumber", "phonenumber": "mobileNumber", "phone_number": "mobileNumber", "contact_phone": "mobileNumber", "mobile": "mobileNumber", "mobilenumber": "mobileNumber", "mobile_number": "mobileNumber",
        "email": "email", "customeremail": "email", "customer_email": "email", "email_address": "email", "contact_email": "email", "company": "companyName",
        "companyname": "companyName", "company_name": "companyName", "contact_company": "companyName", "organisation": "companyName", "organization": "companyName", "organization_name": "companyName",
        "project": "projectName", "projectname": "projectName", "project_name": "projectName", "requirement": "projectName", "active_topic": "projectName",
        "customer_type": "customerType", "customertype": "customerType", "contact_type": "customerType",
    }

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                target = aliases.get(str(key).lower())
                if target and isinstance(item, (str, int, float)) and str(item).strip():
                    text = str(item).strip()
                    fields[target] = ("vendor" if text.lower() in {"vendor", "sell", "selling"} else "customer") if target == "customerType" else text
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    # walkin_preview reads rows newest-first. Apply them oldest-first so a
    # later correction (for example a corrected name or phone number) wins.
    for row in reversed(extractions or []):
        if row.get("status") == "complete":
            visit(row.get("data") or {})
    visit(context or {})

    # The live model can return a literal phone/email or a spoken rendering.
    # Normalize here as well as in the offline fallback so the browser never
    # receives ``nine three...`` or ``+91 ...`` where it expects CRM values.
    if "mobileNumber" in fields:
        phone = _phone_digits(fields["mobileNumber"])
        if phone:
            fields["mobileNumber"] = phone
        else:
            fields.pop("mobileNumber", None)
    if "email" in fields:
        email = _email_value(fields["email"])
        if email:
            fields["email"] = email
        else:
            fields.pop("email", None)
    return fields


def _walkin_dialogue_fields(dialogue: object) -> dict[str, str]:
    """Extract high-confidence registration values directly from ordered text."""
    text = str(dialogue or "")
    if not text.strip():
        return {}
    name = _explicit_dialogue_value(text, r"my\s+name|name|नाम|ಹೆಸರು")
    if not name:
        # Individual answers may arrive without a label. A short first line
        # before phone/email/company language is the visitor's name.
        first_line = re.split(r"[.!?\n]", text.strip(), maxsplit=1)[0].strip()
        first_line = re.sub(r"^(?:hi|hello|hey)\s*[,:-]?\s*", "", first_line, flags=re.IGNORECASE).strip()
        first_line = re.sub(r"^(?:this is|i am|i'm)\s+", "", first_line, flags=re.IGNORECASE).strip()
        if (2 <= len(first_line.split()) <= 5
                and re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,120}", first_line)
                and not re.search(r"\b(?:phone|mobile|email|company|organisation|organization|project|requirement)\b", first_line, flags=re.IGNORECASE)):
            name = first_line
    company = _explicit_dialogue_value(text, r"company|organisation|organization|firm|संस्था|ಸಂಸ್ಥೆ", prefer_last=True)
    # Some translations put the company before the word ``Company``:
    # ``I am from Groove Company, and ...``.
    before_company = list(re.finditer(
        r"\b(?:from|at|of)\s+([A-Za-z][A-Za-z0-9& .'-]{1,100}?\s+(?:company|organisation|organization|firm))\b",
        text, flags=re.IGNORECASE,
    ))
    if before_company:
        company = before_company[-1].group(1).strip(" \t,;:-")
    project = _explicit_dialogue_value(
        text,
        r"project(?:\s+(?:requirement|require))?|requirement|require|need|looking\s+for|want|source|sourcing|purchase|buy",
        prefer_last=True,
    )
    # Keep a complete first-person requirement sentence when the generic
    # field-label parser encounters a disfluent list such as "we need ... for
    # our organisation". The sentence itself is the strongest evidence.
    requirement_sentence = re.search(
        r"\b(?:we|i)\s+(?:need|require|want|am\s+(?:looking\s+for|sourcing))\s+([^.!?\n]+)",
        text,
        flags=re.IGNORECASE,
    )
    if requirement_sentence:
        project = requirement_sentence.group(1).strip(" \t,;:-")
    fields = {
        "customerName": name,
        "mobileNumber": _phone_from_dialogue(text),
        "email": _email_value(text),
        "companyName": company,
        "projectName": project,
    }
    if re.search(r"\b(?:vendor|selling|sell)\b", text, flags=re.IGNORECASE):
        fields["customerType"] = "vendor"
    elif re.search(r"\b(?:customer|buying|buy|purchase)\b", text, flags=re.IGNORECASE):
        fields["customerType"] = "customer"
    return {key: value for key, value in fields.items() if value}


def _walkin_extraction_context(turns: list[dict]) -> str:
    """Give the model ordered, multilingual context instead of trigger phrases."""
    rows = []
    for index, turn in enumerate(turns, start=1):
        source = str(turn.get("source_text") or "").strip()
        english = str(turn.get("english_text") or "").strip()
        if not source and not english:
            continue
        speaker = str(turn.get("speaker_ref") or "unknown").strip()
        parts = [f"Turn {index} | speaker={speaker}"]
        if source:
            parts.append(f"source: {source}")
        if english and english.casefold() != source.casefold():
            parts.append(f"english: {english}")
        rows.append(" | ".join(parts))
    return "\n".join(rows)


def _merge_registration_fallback(fields: dict[str, str], dialogue: str) -> dict[str, str]:
    """Use deterministic parsing only to fill gaps or exact contact values."""
    grounded = _walkin_dialogue_fields(dialogue)
    for key, value in grounded.items():
        # The model is the semantic authority for names, organisations, and
        # requirements. Exact phone/email normalization is safer from the
        # transcript because models can accidentally alter those characters.
        if not fields.get(key) or key in {"mobileNumber", "email"}:
            fields[key] = value
    return fields


WALKIN_FULL_EXTRACTION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "walkin_registration_fields",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "customerName": {"type": "string"},
                "mobileNumber": {"type": "string"},
                "email": {"type": "string"},
                "companyName": {"type": "string"},
                "projectName": {"type": "string"},
                "customerType": {"type": "string", "enum": ["customer", "vendor", ""]},
            },
            "required": ["customerName", "mobileNumber", "email", "companyName", "projectName", "customerType"],
            "additionalProperties": False,
        },
    },
}


WALKIN_FULL_EXTRACTION_PROMPT = """You are a careful sales-reception assistant completing a visitor registration from an ordered conversation.
This is semantic understanding, not keyword matching. Read the whole available context and infer which
answer belongs to which question the way a human salesperson would: use turn order, question-and-answer
adjacency, first-person statements, corrections, and the meaning of the answer. The same field may be
phrased differently every time. For example, a person saying only `Groovy` immediately after being asked
which organisation they are from is a company answer even though the answer contains no word like company.

Extract exact contact values before doing broader semantic interpretation. If the ordered context contains
an address matching `name@domain.tld`, return that literal address unchanged in `email` when it is clearly
introduced as the visitor's email (for example, `my email address is ...`, `email id ...`, or `email ...`).
Do not let a missing/failed model field or a nearby product sentence erase a literal email. Spoken forms
such as `name at example dot com` must be converted exactly, but never autocorrect or invent characters.

The transcript is the complete ordered conversation after recording has ended. Preserve facts stated in
the conversation and let later explicit corrections supersede earlier values. A question without an answer
is not a value. Do not fill a field from a greeting, acknowledgement, filler, the receptionist's own
details, an email username/domain, or digits that are not answering the phone question.

Language handling is essential: source speech may be Kannada, Hindi, English, or mixed speech, while an
English translation may be present, delayed, imperfect, or absent. Use the English translation when it is
available, use the source text to resolve names/digits/email spelling, understand Kannada/Hindi number
words and natural transliteration, and return CRM text in English/Latin characters. Transliterate proper
names and organisations; do not translate a proper name into a different name. Convert spoken email words
such as at, dot, underscore, and hyphen exactly, without autocorrecting. Convert spoken phone digits in
order, including zero/oh and double/triple words, without adding a country code.

Use speaker labels only as evidence: do not assume a particular speaker id is always the visitor. Prefer
values said by the visitor, but use a receptionist question to understand the expected answer when the
visitor's response follows it. If a response is ambiguous between two fields, leave both empty rather
than guessing.

Return only the six requested fields. Each value must be a concise fact explicitly supported by the
conversation, or an empty string when it is not available:
- customerName: the visitor's full name, not a greeting or an email username.
- mobileNumber: ASCII local digits only; preserve every spoken digit and do not use unrelated numbers.
- email: the exact ASCII email address; never invent a domain or repair spelling.
- companyName: the organisation/company/firm the visitor identifies with; a bare answer can be valid from context.
- projectName: what the visitor needs, wants, is sourcing, or is planning to buy; preserve useful detail.
- customerType: exactly `customer` for buying/customer/purchasing or `vendor` for selling/supplying; empty if unclear.
For a disfluent requirement list, preserve the complete request rather than returning an empty value:
`We need around uh, 20, 23 ceiling fans, some hand pumps and some switches` is a valid projectName
because it explicitly states the visitor's need. Do not require the words `project` or `requirement`.
"""


async def _extract_walkin_registration_fields(request: Request, context: Workspace, conversation_id: UUID, turns: list[dict]) -> dict[str, str]:
    """Extract the completed visitor registration once after recording stops."""
    # Translation can lag or fail after transcription. The source transcript
    # is still useful for registration extraction, so do not discard a turn
    # merely because english_text is null.
    dialogue = "\n".join(str(turn.get("english_text") or turn.get("source_text") or "").strip() for turn in turns if turn.get("english_text") or turn.get("source_text"))
    model_context = _walkin_extraction_context(turns)
    settings = request.app.state.settings
    if not dialogue or not settings.openai_api_key:
        return {}
    fingerprint = "walkin-fields:" + hashlib.sha256(dialogue.encode("utf-8")).hexdigest()
    async with request.app.state.db.connection() as connection:
        # This is an extraction read/claim, not a recording mutation. Do not
        # hold the conversation row lock while chunk authorization/confirmation
        # is trying to save the audio.
        await owned_conversation(connection, context, conversation_id, lock=False)
        claimed = await one(connection, """
            insert into public.conversation_extractions(conversation_id,organisation_id,fingerprint,status,data)
            values(%s,%s,%s,'running','{}'::jsonb)
            on conflict(conversation_id,fingerprint) do nothing
            returning id
        """, (conversation_id, context.id, fingerprint))
    # Another preview request or the Supabase webhook already owns this exact
    # fingerprint. It will publish the result; do not pay for a duplicate call.
    if not claimed:
        return {}
    try:
        from openai import AsyncOpenAI
        from .activity_log import monitored_call

        client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value(), max_retries=1)
        completion = await monitored_call(client.chat.completions.create(
            model=settings.openai_translation_model,
            temperature=0,
            response_format=WALKIN_FULL_EXTRACTION_RESPONSE_FORMAT,
            messages=[
                {"role": "system", "content": WALKIN_FULL_EXTRACTION_PROMPT},
                {"role": "user", "content": f"Ordered conversation context:\n{model_context[:30000]}"},
            ],
        ), "openai", "chat.completions.create", metadata={"model": settings.openai_translation_model, "component": "walkin_extraction"})
        raw = _completion_text(completion.choices[0].message)
        parsed = json.loads(raw)
        fields = {key: str(parsed.get(key) or "").strip() for key in ("customerName", "mobileNumber", "email", "companyName", "projectName", "customerType")}
        # Prefer values grounded in the complete dialogue over a model-only
        # guess. This is especially important for exact phone/email fields.
        fields = _merge_registration_fallback(fields, dialogue)
        fields["mobileNumber"] = _phone_digits(fields["mobileNumber"])
        fields["email"] = _email_value(fields["email"])
        email_local = fields["email"].split("@", 1)[0].casefold() if "@" in fields["email"] else ""
        if email_local and re.sub(r"[^a-z0-9]", "", fields["customerName"].casefold()) == re.sub(r"[^a-z0-9]", "", email_local):
            fields["customerName"] = ""
        if fields["customerType"] not in {"customer", "vendor"}:
            fields["customerType"] = ""
    except Exception as error:
        log.warning("walkin_registration_extraction_failed exception_type=%s", type(error).__name__)
        async with request.app.state.db.connection() as connection:
            await connection.execute("update public.conversation_extractions set status='failed',error_message=%s,updated_at=now() where conversation_id=%s and fingerprint=%s", (type(error).__name__, conversation_id, fingerprint))
        return {}
    async with request.app.state.db.connection() as connection:
        await connection.execute("update public.conversation_extractions set status='complete',data=%s,updated_at=now() where conversation_id=%s and fingerprint=%s", (Jsonb(fields), conversation_id, fingerprint))
    return fields


@router.get("/{conversation_id}/walkin-preview")
async def walkin_preview(conversation_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    """Return live source transcription and final-only registration extraction."""
    async with request.app.state.db.connection() as connection:
        # Preview is read-only. A row lock here contends with every audio chunk
        # authorize/confirm update and can turn a healthy recording into a DB
        # statement-timeout failure.
        recording = await owned_conversation(connection, context, conversation_id, lock=False)
        version = None
        turns = []
        if recording.get("active_version_id"):
            version = await one(connection, "select id,english_state,state,coverage_samples from public.transcript_versions where id=%s and conversation_id=%s", (recording["active_version_id"], conversation_id))
            if version:
                turns = await many(connection, """
                    select t.ordinal,t.start_seconds,t.end_seconds,t.source_text,
                           coalesce(t.english_text,tr.english_text) as english_text,
                           t.source_language,t.speaker_ref
                      from public.transcript_turns t
                      left join lateral (
                          select english_text
                            from public.translations
                           where source_turn_id=t.id and state='complete'
                           order by created_at desc limit 1
                      ) tr on true
                     where t.version_id=%s
                     order by t.ordinal
                """, (version["id"],))
        extractions = await many(connection, "select status,data,created_at from public.conversation_extractions where conversation_id=%s order by created_at desc limit 20", (conversation_id,))
        context_row = await one(connection, "select context from public.voice_ai_contexts where conversation_id=%s", (conversation_id,))
    public_extractions = [{"status": row.get("status"), "data": row.get("data") or {}, "created_at": row.get("created_at")} for row in extractions]
    dialogue = "\n".join(str(turn.get("english_text") or turn.get("source_text") or "").strip() for turn in turns if turn.get("english_text") or turn.get("source_text"))
    finalized = recording.get("capture_state") in {"stopped", "interrupted"} and recording.get("final_state") in {"complete", "blocked", "failed", "uncertain"}
    fields = {}
    if finalized:
        fields = _walkin_extracted_fields(extractions, (context_row or {}).get("context") if context_row else {})
        # Apply deterministic values from the complete ordered transcript last
        # so exact phone/email values remain grounded in the audio.
        fields = _merge_registration_fallback(fields, dialogue)
        email_local = str(fields.get("email") or "").split("@", 1)[0].casefold() if "@" in str(fields.get("email") or "") else ""
        email_domain = str(fields.get("email") or "").split("@", 1)[1].casefold() if "@" in str(fields.get("email") or "") else ""
        if email_local and re.sub(r"[^a-z0-9]", "", str(fields.get("customerName") or "").casefold()) == re.sub(r"[^a-z0-9]", "", email_local):
            fields.pop("customerName", None)
        if email_domain and str(fields.get("companyName") or "").casefold().strip(" .") == email_domain:
            fields.pop("companyName", None)
    # A visitor recording is intentionally transcript-only while capture is
    # active. Once final processing reaches a terminal state, one fingerprinted
    # extraction runs against the complete ordered transcript.
    if finalized and turns:
        fallback = await _extract_walkin_registration_fields(request, context, conversation_id, turns)
        if fallback:
            # The fallback sees the whole ordered prefix, so a non-empty
            # fallback value is more trustworthy than an earlier partial/live
            # extraction. Keep the earlier value only when fallback is blank.
            fields = {key: value or fields.get(key, "") for key, value in fallback.items()}
            public_extractions.append({"status": "complete", "data": fallback, "created_at": datetime.now().isoformat()})
    return {
        "conversation": {key: recording.get(key) for key in ("id", "capture_state", "upload_state", "transcription_state", "english_state", "final_state", "duration_seconds", "active_version_id")},
        "version": version,
        "transcript": turns,
        "extractions": public_extractions,
        "fields": fields,
    }


class WalkinCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customerName: Annotated[str, Field(min_length=2, max_length=150)]
    mobileNumber: Annotated[str, Field(min_length=7, max_length=20)]
    email: Annotated[str, Field(max_length=320)] = ""
    companyName: Annotated[str, Field(max_length=150)] = ""
    projectName: Annotated[str, Field(max_length=150)] = ""
    customerType: Literal["customer", "vendor"] = "customer"
    assignedAgentId: UUID | None = None
    # Only the completed main registration recording uses this. Individual
    # field-mic clips remain transient and are never attached to a visitor.
    conversationId: UUID | None = None
    purpose: Purpose = "Other"
    purposeDescription: Annotated[str, Field(max_length=2000)] = ""
    productOrService: Text200 = ""
    department: Text200 = ""
    requestedEmployee: Text200 = ""
    appointmentStatus: Literal["Walk-in", "Scheduled Appointment"] = "Walk-in"
    appointmentReference: Text200 = ""
    internalNotes: Annotated[str, Field(max_length=4000)] = ""

    _safe_text = field_validator("*", mode="before")(safe_text)

    @field_validator("mobileNumber")
    @classmethod
    def valid_phone(cls, value):
        if not re.fullmatch(r"\+?[0-9().\-\s]{7,20}", value) or not 7 <= len(re.sub(r"\D", "", value)) <= 15:
            raise ValueError("Enter a valid mobile number")
        return value

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value


class Versioned(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expectedUpdatedAt: datetime

    @field_validator("expectedUpdatedAt")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("Use a timestamp with a timezone")
        return value


class Assignment(Versioned):
    agentId: UUID | None
    note: Annotated[str, Field(max_length=2000)] = ""

    _safe_note = field_validator("note", mode="before")(safe_text)


class StatusChange(Versioned):
    status: Status


class ContactChange(Versioned):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customerName: Annotated[str, Field(min_length=2, max_length=150)]
    mobileNumber: Annotated[str, Field(min_length=7, max_length=20)]
    email: Annotated[str, Field(max_length=320)] = ""

    _safe_text = field_validator("*", mode="before")(safe_text)

    @field_validator("mobileNumber")
    @classmethod
    def valid_phone(cls, value):
        if not re.fullmatch(r"\+?[0-9().\-\s]{7,20}", value) or not 7 <= len(re.sub(r"\D", "", value)) <= 15:
            raise ValueError("Enter a valid mobile number")
        return value

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    detailedRequirement: Annotated[str, Field(max_length=10000)] = ""
    productOrService: Text200 = ""
    quantity: Text200 = ""
    budgetRange: Text200 = ""
    expectedPurchaseTimeline: Text200 = ""
    preferredSolution: Annotated[str, Field(max_length=2000)] = ""
    competitorOrAlternative: Text200 = ""
    specialRequests: Annotated[str, Field(max_length=4000)] = ""
    followUpAt: Annotated[str, Field(max_length=10)] = ""
    agentNotes: Annotated[str, Field(max_length=4000)] = ""
    nextAction: Annotated[str, Field(max_length=2000)] = ""

    _safe_text = field_validator("*", mode="before")(safe_text)

    @field_validator("followUpAt")
    @classmethod
    def valid_date(cls, value):
        if value:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("Use an ISO calendar date")
            date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def bounded_payload(self):
        if len(json.dumps(self.model_dump(), ensure_ascii=False).encode()) > 47_000:
            raise ValueError("Requirement details are too large; shorten notes")
        return self


class RequirementSave(Versioned):
    requirement: Requirement


class AvailabilityChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    availability: Availability


class RecordingLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversationId: UUID


async def ensure_walkin_books_contact(body: WalkinCreate, context: Workspace):
    """Register a walk-in in the selected Books organization when available."""
    set_organisation_id(str(context.id))
    set_member_id(str(context.user.id))
    status = zoho.status()
    books_org = status.get("organization_id")
    if not status.get("connected") or not books_org:
        return None
    contact = await zoho.ensure_books_contact(
        books_org,
        name=body.customerName,
        email=body.email,
        phone=body.mobileNumber,
        contact_type=body.customerType,
        company=body.companyName,
        notes=body.purposeDescription or body.internalNotes,
    )
    if not isinstance(contact, dict):
        return None
    return contact


async def fetch_walkin_crm(body: WalkinCreate, books_contact=None):
    """Call external CRM providers without holding a database transaction open."""
    try:
        result = await provision_enquiry_lead(
            zoho, name=body.customerName, phone=body.mobileNumber, email=body.email or "", contact_type=body.customerType,
            details={
                "company": body.companyName,
                "description": body.purposeDescription or body.internalNotes or "Created from ConnectCosmos visitor registration",
                "requirement": body.projectName,
            },
            books_contact=books_contact,
        )
        return result, None
    except Exception as error:
        log.warning("walkin_crm_provision_failed exception_type=%s", type(error).__name__)
        return None, type(error).__name__


async def sync_walkin_crm(request: Request, context: Workspace, visit_id, enquiry_id, body: WalkinCreate):
    """Synchronize Zoho after the local visitor registration is returned."""
    books_contact = {}
    try:
        books_contact = await ensure_walkin_books_contact(body, context) or {}
    except Exception as error:
        # A Books rejection must not prevent the local visitor or Bigin lead
        # from being available. Passing an empty dict also prevents the CRM
        # provisioning helper from repeating the same failing Books call.
        log.warning("walkin_books_contact_unavailable visit_id=%s exception_type=%s", visit_id, type(error).__name__)
    books_contact_id = books_contact.get("contact_id") or books_contact.get("id")
    if books_contact_id:
        async with request.app.state.db.connection() as connection:
            await connection.execute("update public.guruconnect_enquiries set books_contact_id=%s where id=%s and organisation_id=%s", (str(books_contact_id), enquiry_id, context.id))
    crm_result, crm_error = await fetch_walkin_crm(body, books_contact=books_contact)
    crm_persisted = False
    if crm_result:
        async with request.app.state.db.connection() as connection:
            if isinstance(await persist_walkin_crm(connection, context, enquiry_id, body, crm_result), dict):
                crm_persisted = True
    if crm_result and not crm_persisted:
        crm_error = crm_error or "identity_persist_failed"
    if crm_error:
        async with request.app.state.db.connection() as connection:
            await connection.execute("update public.guruconnect_enquiries set crm_sync_error=%s where id=%s and organisation_id=%s", (crm_error, enquiry_id, context.id))


async def persist_walkin_crm(connection, context, enquiry_id, body, crm_result):
    """Persist provider identity after the local visitor/enquiry transaction committed."""
    if not isinstance(crm_result, dict):
        return None
    try:
        result = crm_result
        contact, record = result["contact"], result["record"]
        books_contact = result.get("books_contact") or {}
        identity = result.get("identity") or {}
        bigin_contact_id = str(contact.get("id")) if contact.get("id") else None
        books_contact_id = books_contact.get("contact_id") or books_contact.get("id")
        canonical_name = identity.get("name") or body.customerName
        canonical_phone = identity.get("phone") or body.mobileNumber
        canonical_email = identity.get("email") or body.email or ""
        canonical_company = identity.get("company") or body.companyName or ""
        canonical_description = identity.get("description") or body.purposeDescription or body.internalNotes or ""
        existing = await one(connection, "select conversation_id,payload from public.guruconnect_enquiries where id=%s and organisation_id=%s", (enquiry_id, context.id))
        payload = dict(existing.get("payload") or {}) if existing else {}
        payload.update({
            "customerName": canonical_name,
            "phone": canonical_phone,
            "email": canonical_email,
            "company": canonical_company,
            "companyName": canonical_company,
        })
        if canonical_description:
            payload["customerNotes"] = canonical_description
        await connection.execute("""update public.guruconnect_enquiries
            set bigin_contact_id=%s,bigin_pipeline_id=%s,bigin_deal_id=%s,books_contact_id=coalesce(%s,books_contact_id),
                customer_name=%s,phone=%s,payload=%s,crm_sync_error=null
            where id=%s and organisation_id=%s""",
            (bigin_contact_id, result["pipeline_id"], str(record.get("id")), books_contact_id,
             canonical_name, canonical_phone, Jsonb(payload), enquiry_id, context.id))
        await connection.execute("""update public.guruconnect_walkin_visits
            set customer_name=%s,mobile_number=%s,payload=%s,updated_at=clock_timestamp()
            where organisation_id=%s and converted_enquiry_id=%s""",
            (canonical_name, canonical_phone, Jsonb({**payload, "mobileNumber": canonical_phone, "customerName": canonical_name,
                                                      "email": canonical_email}), context.id, enquiry_id))
        conversation_id = existing.get("conversation_id") if existing else None
        if conversation_id:
            customer = await one(connection, "select customer_id from public.conversations where id=%s and organisation_id=%s", (conversation_id, context.id))
            if customer and customer.get("customer_id"):
                customer_id = customer["customer_id"]
                await connection.execute("""
                    update public.customers
                       set name=%s,email=%s,phone=%s,normalized_email=%s,normalized_phone=%s,
                           bigin_contact_id=coalesce(%s,bigin_contact_id),
                           books_contact_id=coalesce(%s,books_contact_id),updated_at=now()
                     where id=%s and organisation_id=%s
                """, (canonical_name, canonical_email or None, canonical_phone or None,
                       normalize_email(canonical_email) or None, normalize_phone(canonical_phone) or None,
                       bigin_contact_id, str(books_contact_id) if books_contact_id else None, customer_id, context.id))
                await connection.execute("""
                    update public.chat_sessions
                       set bigin_contact_id=coalesce(%s,bigin_contact_id),
                           books_contact_id=coalesce(%s,books_contact_id),updated_at=now()
                     where organisation_id=%s and customer_id=%s
                """, (bigin_contact_id, str(books_contact_id) if books_contact_id else None, context.id, customer_id))
        return result
    except Exception as error:
        # CRM sync must never undo a successfully committed local registration.
        # The provider result is still returned to the caller only when its local
        # identity update succeeds; a failed persistence transaction is retried
        # by the normal CRM reconciliation path.
        log.warning("walkin_crm_identity_persist_failed enquiry_id=%s exception_type=%s", enquiry_id, type(error).__name__)
        return None, "identity_persist_failed"


SELECT_VISIT = """select v.*,p.display_name as assigned_agent_name,
                         e.ticket_day,e.daily_sequence
    from public.guruconnect_walkin_visits v
    left join public.profiles p on p.id=v.assigned_agent_id
    left join public.guruconnect_enquiries e
      on e.id=v.converted_enquiry_id and e.organisation_id=v.organisation_id """
ACCESS = "(%s or v.assigned_agent_id=%s or v.registered_by=%s)"


def serialize_visit(row):
    result = dict(row["payload"])
    ticket_day = row.get("ticket_day")
    daily_sequence = row.get("daily_sequence")
    visit_number = (
        format_ticket_number(ticket_day, daily_sequence)
        if ticket_day is not None and daily_sequence is not None
        else f"WI-{row['created_at']:%d%m%Y}-{row['visit_number']:06d}"
    )
    result.update(id=str(row["id"]), visitNumber=visit_number,
                  registeredBy=str(row["registered_by"]), assignedAgentId=str(row["assigned_agent_id"]) if row["assigned_agent_id"] else None,
                  assignedAgentName=row.get("assigned_agent_name") or ("Salesperson" if row["assigned_agent_id"] else None),
                  assignmentNote=row["assignment_note"], status=row["status"], checkInAt=row["check_in_at"].isoformat(),
                  assignedAt=row["assigned_at"].isoformat() if row["assigned_at"] else None,
                  convertedEnquiryReference=str(row["converted_enquiry_id"]) if row["converted_enquiry_id"] else None,
                  createdAt=row["created_at"].isoformat(), updatedAt=row["updated_at"].isoformat())
    return result


def serialize_requirement(row):
    result = Requirement.model_validate(row["requirements"]).model_dump()
    result["updatedAt"] = row["requirements_updated_at"].isoformat() if row["requirements_updated_at"] else None
    return result


def missed_cutoff(now_local: datetime | None = None) -> datetime:
    """Return the most recent 9 PM India cutoff for the daily rollup."""
    current = now_local or datetime.now(ZoneInfo("Asia/Kolkata"))
    cutoff = current.replace(hour=21, minute=0, second=0, microsecond=0)
    return cutoff if current >= cutoff else cutoff - timedelta(days=1)


async def rollup_missed_visitors(connection, cutoff: datetime | None = None) -> int:
    """Snapshot visits with no linked recording and purge expired snapshots.

    The unique visit key makes this safe when the nightly task and a page load
    happen at the same time.
    """
    await connection.execute("delete from public.guruconnect_missed_visitors where expires_at <= now()")
    rows = await many(connection, """
        select v.*,
               coalesce(v.assigned_agent_id, v.registered_by) as member_id
        from public.guruconnect_walkin_visits v
        where v.check_in_at < %s
          and v.status <> 'Cancelled'
          and not exists (
            select 1 from public.guruconnect_walkin_recordings r
            where r.organisation_id=v.organisation_id and r.visit_id=v.id
          )
          and not exists (
            select 1 from public.guruconnect_missed_visitors m
            where m.organisation_id=v.organisation_id and m.visit_id=v.id
          )
        order by v.check_in_at asc
        limit 5000
    """, (cutoff or missed_cutoff(),))
    for row in rows:
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        await connection.execute("""
            insert into public.guruconnect_missed_visitors
              (organisation_id,member_id,visit_id,registered_by,assigned_agent_id,
               visit_number,customer_name,mobile_number,email,purpose,payload,check_in_at)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (organisation_id,visit_id) do nothing
        """, (row["organisation_id"], row["member_id"], row["id"], row["registered_by"],
              row["assigned_agent_id"], row["visit_number"], row["customer_name"],
              row["mobile_number"], payload.get("email", ""), row["purpose"], Jsonb(payload),
              row["check_in_at"]))
    return len(rows)


async def active_context(connection, context):
    if context.admin:
        return
    # Hold membership stable until this write commits; concurrent revocation or
    # demotion must serialize with the operation rather than reuse cached roles.
    member = await one(connection, "select role from public.members where organisation_id=%s and user_id=%s and status='active' for share", (context.id, context.user.id))
    if not member or member["role"] != context.role:
        fail("workspace_access_denied", 403)


async def visit(connection, context, visit_id, *, lock=False, editing=False):
    await active_context(connection, context)
    row = await one(connection, SELECT_VISIT + " where v.id=%s and v.organisation_id=%s and " + ACCESS + (" for update of v" if lock else ""),
                    (visit_id, context.id, context.admin, context.user.id, context.user.id))
    if not row:
        fail("walkin_not_found", 404)
    if editing and not context.admin and row["assigned_agent_id"] != context.user.id:
        fail("walkin_assigned_agent_required", 403)
    return row


def check_version(row, expected):
    if row["updated_at"] != expected:
        fail("stale_walkin", 409, "This visit changed while you were editing. Refresh the visit before saving again.")


async def history(connection, context, row, event_type, old_value=None, new_value=None, notes=""):
    await connection.execute("""insert into public.guruconnect_walkin_history
        (organisation_id,visit_id,actor_id,event_type,old_value,new_value,notes) values(%s,%s,%s,%s,%s,%s,%s)""",
                             (context.id, row["id"], context.user.id, event_type, old_value, new_value, notes))
    await audit(connection, context, "walkin." + event_type.lower().replace(" ", "_"), {"visit_id": str(row["id"])})


@router.get("/agents")
async def list_agents(request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await connection.execute("""
            update public.profiles p
            set availability_status='inactive', final_status='inactive', final_status_until=null, updated_at=now()
            where coalesce(p.final_status, p.availability_status)='active'
              and (p.last_seen_at is null or p.last_seen_at < now()-interval '90 seconds')
              and not exists (select 1 from public.profile_presence_sessions s where s.user_id=p.id and s.last_seen_at >= now()-interval '90 seconds')
              and exists (select 1 from public.members m where m.organisation_id=%s and m.user_id=p.id and m.status='active')
        """, (context.id,))
        rows = await many(connection, """with candidates as (
            select m.user_id,m.role,p.display_name,coalesce(a.availability,'Available') as availability,
              case
                when coalesce(p.final_status, p.availability_status) in ('inactive','on_leave') then coalesce(p.final_status, p.availability_status)
                when coalesce(p.final_status, p.availability_status)='be_right_back' and p.updated_at > now()-interval '15 minutes' then 'be_right_back'
                when exists (select 1 from public.profile_presence_sessions ps where ps.user_id=p.id and ps.last_seen_at >= now()-interval '90 seconds') then 'active'
                else 'inactive'
              end as presence_status,
              count(distinct e.id) filter (where e.stage in ('new','in_process')) as open_tickets,
              case when %s or m.user_id=%s then count(distinct v.id) else null end as open_visits
            from public.members m join public.profiles p on p.id=m.user_id
            left join public.guruconnect_walkin_agents a on a.organisation_id=m.organisation_id and a.user_id=m.user_id
            left join public.guruconnect_walkin_visits v on v.organisation_id=m.organisation_id and v.assigned_agent_id=m.user_id and v.status not in ('Completed','Cancelled','Converted to Enquiry')
            left join public.guruconnect_enquiries e on e.organisation_id=m.organisation_id and e.assigned_to=m.user_id
            where m.organisation_id=%s and m.status='active' and m.role in ('owner','salesperson')
            group by m.user_id,m.role,p.id,p.display_name,a.availability,p.availability_status,p.final_status,p.final_status_until
          )
          select * from candidates c
          where c.role in ('salesperson','owner')
          order by case when c.role='salesperson' then 0 else 1 end,
            case c.presence_status when 'active' then 0 when 'on_leave' then 1 when 'be_right_back' then 2 else 3 end,
            case c.availability when 'Available' then 0 when 'Busy' then 1 else 2 end,
            c.open_tickets asc,c.display_name,c.user_id limit 1000""", (context.admin, context.user.id, context.id))
    return {"items": [{"id": str(row["user_id"]), "name": row["display_name"] or "Salesperson", "department": "Sales", "specialization": "", "role": row["role"], "availability": row["availability"], "presenceStatus": row["presence_status"], "openVisits": row["open_visits"], "openTickets": row["open_tickets"]} for row in rows],
            "currentAgentId": str(context.user.id), "canManage": context.admin}


@router.patch("/agents/{agent_id}")
async def update_availability(agent_id: UUID, body: AvailabilityChange, request: Request, context: Workspace = Depends(administrator)):
    async with request.app.state.db.connection() as connection:
        await active_context(connection, context)
        member = await one(connection, "select user_id from public.members where organisation_id=%s and user_id=%s and status='active' for share", (context.id, agent_id))
        if not member:
            fail("walkin_agent_not_found", 404)
        await connection.execute("insert into public.guruconnect_walkin_agents(organisation_id,user_id,availability) values(%s,%s,%s) on conflict(organisation_id,user_id) do update set availability=excluded.availability,updated_at=clock_timestamp()", (context.id, agent_id, body.availability))
        await audit(connection, context, "walkin.agent_availability_changed", {"agent_id": str(agent_id), "availability": body.availability})
    return {"ok": True}


@router.get("/history")
async def list_history(request: Request, context: Workspace = Depends(workspace),
                       visitId: UUID | None = None, page: int = Query(default=1, ge=1, le=100000), pageSize: int = Query(default=100, ge=1, le=100)):
    where = " where h.organisation_id=%s and " + ACCESS + " and (%s::uuid is null or h.visit_id=%s)"
    params = (context.id, context.admin, context.user.id, context.user.id, visitId, visitId)
    async with request.app.state.db.connection() as connection:
        if visitId:
            await visit(connection, context, visitId)
        rows = await many(connection, """with matches as materialized (
            select h.*,p.display_name as actor_name from public.guruconnect_walkin_history h
            join public.guruconnect_walkin_visits v on v.id=h.visit_id and v.organisation_id=h.organisation_id
            join public.profiles p on p.id=h.actor_id""" + where + """), totals as (select count(*) as total from matches),
            page_rows as (select * from matches order by created_at desc,id desc limit %s offset %s)
            select page_rows.*,totals.total from totals left join page_rows on true order by page_rows.created_at desc,page_rows.id desc""", params + (pageSize, (page - 1) * pageSize))
    return {"items": [{"id": str(row["id"]), "visitId": str(row["visit_id"]), "eventType": row["event_type"], "oldValue": row["old_value"], "newValue": row["new_value"], "notes": row["notes"], "performedBy": row["actor_name"] or "Salesperson", "createdAt": row["created_at"].isoformat()} for row in rows if row["id"]],
            "total": rows[0]["total"], "page": page, "pageSize": pageSize}


@router.get("")
async def list_visits(request: Request, context: Workspace = Depends(workspace),
                      page: int = Query(default=1, ge=1, le=100000), pageSize: int = Query(default=100, ge=1, le=100),
                      query: str = Query(default="", max_length=120), purpose: Purpose | None = None, status: Status | None = None,
                      assignedAgentId: UUID | None = None, fromDate: date | None = None, toDate: date | None = None):
    if fromDate and toDate and fromDate > toDate:
        fail("invalid_date_range", 422)
    search = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    where = " where v.organisation_id=%s and " + ACCESS + """
        and (v.customer_name ilike %s or v.mobile_number ilike %s
             or ('WI-'||to_char(coalesce(e.ticket_day,(v.check_in_at at time zone 'Asia/Kolkata')::date),'DDMMYYYY')||'-'||lpad(coalesce(e.daily_sequence,v.visit_number)::text,6,'0')) ilike %s)
        and (%s::text is null or v.purpose=%s) and (%s::text is null or v.status=%s)
        and (%s::uuid is null or v.assigned_agent_id=%s)
        and (%s::date is null or v.check_in_at>=%s::date)
        and (%s::date is null or v.check_in_at<%s::date+interval '1 day')"""
    params = (context.id, context.admin, context.user.id, context.user.id, search, search, search, purpose, purpose, status, status, assignedAgentId, assignedAgentId, fromDate, fromDate, toDate, toDate)
    async with request.app.state.db.connection() as connection:
        rows = await many(connection, "with matches as materialized (" + SELECT_VISIT + where + """), totals as (
            select count(*) as total,
            count(*) filter(where status in ('Registered','Waiting for Assignment')) as waiting_count,
            count(*) filter(where status in ('Assigned','Waiting for Sales Agent')) as assigned_count,
            count(*) filter(where status='In Discussion') as discussion_count,
            count(*) filter(where status='Requirements Captured') as requirements_count,
            count(*) filter(where converted_enquiry_id is not null) as converted_count from matches),
            page_rows as (select * from matches order by check_in_at desc,id desc limit %s offset %s)
            select page_rows.*,totals.* from totals left join page_rows on true order by page_rows.check_in_at desc,page_rows.id desc""", params + (pageSize, (page - 1) * pageSize))
    return {"items": [serialize_visit(row) for row in rows if row["id"]], "total": rows[0]["total"], "page": page, "pageSize": pageSize,
            "summary": {"waiting": rows[0]["waiting_count"], "assigned": rows[0]["assigned_count"], "discussion": rows[0]["discussion_count"], "requirements": rows[0]["requirements_count"], "converted": rows[0]["converted_count"]}}


@router.post("", status_code=201)
async def register_visit(body: WalkinCreate, request: Request, response: Response, background_tasks: BackgroundTasks, context: Workspace = Depends(workspace),
                         idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128)):
    if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", idempotency_key):
        fail("idempotency_key_required", 400)
    payload = body.model_dump(mode="json", exclude={"conversationId"})
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    crm_result = None
    crm_error = None
    async with request.app.state.db.connection() as connection:
        await active_context(connection, context)
        await connection.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (f"walkin:{context.id}:{context.user.id}:{idempotency_key}",))
        prior = await one(connection, SELECT_VISIT + " where v.organisation_id=%s and v.registered_by=%s and v.idempotency_key=%s", (context.id, context.user.id, idempotency_key))
        if prior:
            if prior["payload_hash"] != payload_hash:
                fail("idempotency_conflict", 409)
            # Reassignment must not make a replay reveal another agent's visit.
            visible = await visit(connection, context, prior["id"])
            response.status_code = 200
            return serialize_visit(visible)
        recording = None
        if body.conversationId:
            recording = await owned_conversation(connection, context, body.conversationId)
            if recording["audio_bucket"] != "walkin-audio":
                fail("walkin_recording_bucket_invalid", 422, "Only the main walk-in recording can be attached here.")
            if recording["upload_state"] != "saved" or recording["total_samples"] <= 0 or recording["capture_state"] not in {"stopped", "interrupted"}:
                fail("recording_not_saved", 409, "Finish saving the visitor recording before registering the visitor.")
            linked = await one(connection, "select visit_id from public.guruconnect_walkin_recordings where organisation_id=%s and conversation_id=%s", (context.id, body.conversationId))
            if linked:
                fail("recording_already_linked", 409)
        ticket_day = current_ticket_day(request.app.state.settings.app_timezone)
        salesperson = None
        if context.role == "owner" and not body.assignedAgentId:
            salesperson = await one(connection, """
                select m.user_id,coalesce(p.display_name,'') as display_name,coalesce(u.email,'') as email,m.role
                from public.members m
                join public.profiles p on p.id=m.user_id
                join auth.users u on u.id=m.user_id
                where m.organisation_id=%s and m.user_id=%s and m.role='owner' and m.status='active'
                for share of m
            """, (context.id, context.user.id))
            if not salesperson:
                fail("walkin_assignment_not_allowed", 403)
        elif context.role == "salesperson":
            if body.assignedAgentId and body.assignedAgentId != context.user.id:
                fail("walkin_assignment_not_allowed", 403)
            salesperson = await one(connection, """
                select m.user_id,coalesce(p.display_name,'') as display_name,coalesce(u.email,'') as email,m.role
                from public.members m
                join public.profiles p on p.id=m.user_id
                join auth.users u on u.id=m.user_id
                where m.organisation_id=%s and m.user_id=%s and m.role='salesperson' and m.status='active'
                for share of m
            """, (context.id, context.user.id))
            if not salesperson:
                fail("walkin_assignment_not_allowed", 403)
        elif body.assignedAgentId:
            if context.role not in {"receptionist", "owner"}:
                fail("walkin_assignment_not_allowed", 403)
            allowed_roles = "('owner')" if context.role == "receptionist" else "('owner','salesperson')"
            salesperson = await one(connection, """
                select m.user_id,coalesce(p.display_name,'') as display_name,coalesce(u.email,'') as email,m.role
                from public.members m
                join public.profiles p on p.id=m.user_id
                join auth.users u on u.id=m.user_id
                where m.organisation_id=%s and m.user_id=%s and m.role in """ + allowed_roles + """ and m.status='active'
                for share of m
            """, (context.id, body.assignedAgentId))
            if not salesperson:
                fail("walkin_assignment_not_allowed", 422, "The selected executive is not active in this workspace.")
        else:
            salesperson = await least_loaded_salesperson(connection, context.id, ticket_day)
        assigned = salesperson["user_id"] if salesperson else None
        status = "Assigned" if assigned else "Waiting for Assignment"
        row = await one(connection, """insert into public.guruconnect_walkin_visits
            (organisation_id,registered_by,assigned_agent_id,assigned_at,idempotency_key,payload_hash,payload,customer_name,mobile_number,purpose,status)
            values(%s,%s,%s,case when %s::uuid is null then null else now() end,%s,%s,%s,%s,%s,%s,%s) returning id""",
                        (context.id, context.user.id, assigned, assigned, idempotency_key, payload_hash, Jsonb(payload), body.customerName, body.mobileNumber, body.purpose, status))
        daily_sequence = await next_daily_ticket(connection, context.id, ticket_day)
        tracking_token = secrets.token_urlsafe(12)
        tracking_hash = hashlib.sha256(tracking_token.encode()).hexdigest()
        ticket_payload = {
            "customerName": body.customerName, "phone": body.mobileNumber, "email": body.email,
            "channel": "Retail walk-in", "branch": body.department or "Walk-in",
            "assignedExecutive": salesperson["display_name"] if salesperson else "Unassigned",
            "product": body.productOrService, "quantity": 1, "unit": "nos", "notes": body.internalNotes or body.purposeDescription,
            "sourceEventId": f"walkin:{row['id']}", "sourceWalkinId": str(row["id"]), "customerType": "Vendor" if body.customerType == "vendor" else "Retail customer",
            "company": body.companyName, "projectName": body.projectName,
            "priority": "Normal"
        }
        ticket_payload["ticketNumber"] = daily_sequence
        ticket_hash = hashlib.sha256(json.dumps(ticket_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        enquiry = await one(connection, """insert into public.guruconnect_enquiries
            (organisation_id,owner_id,conversation_id,idempotency_key,payload_hash,payload,customer_name,phone,channel,branch,assigned_to,stage,unread,ticket_day,daily_sequence,public_token_hash,books_contact_id)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'new',true,%s,%s,%s,%s) returning id""",
            (context.id, context.user.id, body.conversationId, f"walkin-enquiry:{row['id']}", ticket_hash, Jsonb(ticket_payload), body.customerName, body.mobileNumber, "Retail walk-in", body.department or "Walk-in", salesperson["user_id"] if salesperson else None, ticket_day, daily_sequence, tracking_hash, None))
        if recording:
            await connection.execute("insert into public.guruconnect_walkin_recordings(organisation_id,visit_id,conversation_id,owner_id) values(%s,%s,%s,%s)", (context.id, row["id"], body.conversationId, context.user.id))
        await connection.execute("update public.guruconnect_walkin_visits set converted_enquiry_id=%s where id=%s and organisation_id=%s", (enquiry["id"], row["id"], context.id))
        await history(connection, context, row, "Registered", new_value=status)
        saved = await visit(connection, context, row["id"])
        enquiry_id = enquiry["id"]

    # Return the committed local registration immediately. Zoho/Bigin are
    # best-effort integrations and must not hold the browser request open
    # long enough for the frontend's save timeout to fire.
    background_tasks.add_task(sync_walkin_crm, request, context, saved["id"], enquiry_id, body)
    result = serialize_visit(saved)
    result["ticket"] = {"number": format_ticket_number(ticket_day, daily_sequence), "token": tracking_token, "stage": "new"}
    result["crm"] = {"status": "pending"}
    return result


@router.get("/missed-visitors")
async def list_missed_visitors(request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        await rollup_missed_visitors(connection)
        rows = await many(connection, """
            select m.*, v.updated_at as visit_updated_at, p.display_name as assigned_agent_name,
                   e.daily_sequence, e.ticket_day,
                   latest_followup.sent_at as last_follow_up_sent_at,
                   latest_followup.source as last_follow_up_source
            from public.guruconnect_missed_visitors m
            left join public.profiles p on p.id=m.assigned_agent_id
            left join public.guruconnect_walkin_visits v
              on v.id=m.visit_id and v.organisation_id=m.organisation_id
            left join public.guruconnect_enquiries e
              on e.id=v.converted_enquiry_id and e.organisation_id=v.organisation_id
            left join lateral (
              select d.sent_at, d.source
              from public.guruconnect_followup_email_deliveries d
              where d.organisation_id=m.organisation_id and d.visit_id=m.visit_id
                and d.status='sent'
              order by d.sent_at desc nulls last, d.created_at desc
              limit 1
            ) latest_followup on true
            where m.organisation_id=%s
              and m.expires_at > now()
              and (%s or m.member_id=%s or m.registered_by=%s or m.assigned_agent_id=%s)
              and not exists (
                select 1 from public.guruconnect_walkin_recordings r
                where r.organisation_id=m.organisation_id and r.visit_id=m.visit_id
              )
            order by m.missed_at desc, m.id desc
            limit 200
        """, (context.id, context.admin, context.user.id, context.user.id, context.user.id))
        followups = await many(connection, """select e.id,e.customer_name,e.phone,e.payload,e.created_at,
                    e.assigned_to,e.follow_up_required,
                    latest_followup.sent_at as last_follow_up_sent_at,
                    latest_followup.source as last_follow_up_source
             from public.guruconnect_enquiries e
             left join lateral (
               select d.sent_at, d.source
               from public.guruconnect_followup_email_deliveries d
               where d.organisation_id=e.organisation_id
                 and d.visit_id=e.id and d.status='sent'
               order by d.sent_at desc nulls last, d.created_at desc
               limit 1
             ) latest_followup on true
             where e.organisation_id=%s and e.follow_up_required
               and e.stage <> 'closed'
               and (%s or e.assigned_to=%s or e.owner_id=%s)
             order by e.created_at desc limit 200""", (context.id, context.admin, context.user.id, context.user.id))
    timezone_name = request.app.state.settings.app_timezone
    items = [{
        "id": str(row["id"]), "visitId": str(row["visit_id"]),
        "visitNumber": (
            format_ticket_number(row["ticket_day"], row["daily_sequence"])
            if row["ticket_day"] is not None and row["daily_sequence"] is not None
            else f"WI-{row['check_in_at']:%d%m%Y}-{row['visit_number']:06d}"
        ),
        "ticketNumber": format_ticket_number(row["ticket_day"], row["daily_sequence"]) if row["ticket_day"] is not None and row["daily_sequence"] is not None else None,
        "ticketDate": str(row["ticket_day"]) if row["ticket_day"] else row["check_in_at"].astimezone(ZoneInfo(timezone_name)).date().isoformat(),
        "ticketTime": row["check_in_at"].astimezone(ZoneInfo(timezone_name)).strftime("%H:%M:%S"),
        "customerName": row["customer_name"], "mobileNumber": row["mobile_number"],
        "email": row["email"], "purpose": row["purpose"], "payload": row["payload"],
        "assignedAgentName": row["assigned_agent_name"] or "Salesperson",
        "checkInAt": row["check_in_at"].isoformat(), "missedAt": row["missed_at"].isoformat(),
        "updatedAt": row["visit_updated_at"].isoformat() if row.get("visit_updated_at") else row["check_in_at"].isoformat(),
        "expiresAt": row["expires_at"].isoformat(),
        "lastFollowUpSentAt": row["last_follow_up_sent_at"].isoformat() if row.get("last_follow_up_sent_at") else None,
        "lastFollowUpSource": row.get("last_follow_up_source"),
        "followUp": False, "reason": "Session not started",
    } for row in rows]
    items.extend({
        "id": f"followup:{row['id']}", "visitId": str(row["id"]), "visitNumber": "Follow-up enquiry",
        "ticketNumber": None, "ticketDate": row["created_at"].date().isoformat(),
        "ticketTime": row["created_at"].strftime("%H:%M:%S"), "customerName": row["customer_name"],
        "mobileNumber": row["phone"], "email": (row["payload"] or {}).get("email", ""),
        "purpose": (row["payload"] or {}).get("notes") or "No business intent detected",
        "assignedAgentName": (row["payload"] or {}).get("assignedExecutive") or "Salesperson",
        "checkInAt": row["created_at"].isoformat(), "missedAt": row["created_at"].isoformat(),
        "expiresAt": (row["created_at"] + timedelta(days=14)).isoformat(),
        "lastFollowUpSentAt": row["last_follow_up_sent_at"].isoformat() if row.get("last_follow_up_sent_at") else None,
        "lastFollowUpSource": row.get("last_follow_up_source"),
        "payload": row["payload"] or {}, "followUp": True,
        "reason": "Nothing useful detected",
    } for row in followups)
    return {"items": items}


@router.get("/{visit_id}")
async def get_visit(visit_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await visit(connection, context, visit_id)
    return serialize_visit(row)


@router.patch("/{visit_id}/contact")
async def update_contact(visit_id: UUID, body: ContactChange, request: Request,
                         context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await visit(connection, context, visit_id, lock=True)
        if row["status"] in CLOSED:
            fail("walkin_closed", 409)
        missed = await one(connection, """
            select id from public.guruconnect_missed_visitors m
            where m.organisation_id=%s and m.visit_id=%s and m.expires_at > now()
              and (%s or m.member_id=%s or m.registered_by=%s or m.assigned_agent_id=%s)
              and not exists (
                select 1 from public.guruconnect_walkin_recordings r
                where r.organisation_id=m.organisation_id and r.visit_id=m.visit_id
              )
        """, (context.id, visit_id, context.admin, context.user.id, context.user.id, context.user.id))
        if not missed:
            fail("follow_up_not_found", 404, "This missed visitor is no longer available")
        check_version(row, body.expectedUpdatedAt)
        payload = dict(row.get("payload") or {})
        payload.update(customerName=body.customerName, mobileNumber=body.mobileNumber, email=body.email)
        await connection.execute("""
            update public.guruconnect_walkin_visits
               set customer_name=%s, mobile_number=%s, payload=%s, updated_at=clock_timestamp()
             where id=%s and organisation_id=%s
        """, (body.customerName, body.mobileNumber, Jsonb(payload), visit_id, context.id))
        await connection.execute("""
            update public.guruconnect_missed_visitors
               set customer_name=%s, mobile_number=%s, email=%s, payload=%s
             where organisation_id=%s and visit_id=%s
        """, (body.customerName, body.mobileNumber, body.email, Jsonb(payload), context.id, visit_id))
        if row.get("converted_enquiry_id"):
            await connection.execute("""
                update public.guruconnect_enquiries
                   set customer_name=%s, phone=%s,
                       payload=jsonb_set(jsonb_set(jsonb_set(payload,'{customerName}',to_jsonb(%s::text),true),'{phone}',to_jsonb(%s::text),true),'{email}',to_jsonb(%s::text),true)
                 where organisation_id=%s and id=%s
            """, (body.customerName, body.mobileNumber, body.customerName, body.mobileNumber,
                   body.email, context.id, row["converted_enquiry_id"]))
        await history(connection, context, row, "Contact details changed",
                      old_value=row["customer_name"],
                      new_value=body.customerName)
        saved = await visit(connection, context, visit_id)
    return serialize_visit(saved)


@router.patch("/{visit_id}/assignment")
async def assign_visit(visit_id: UUID, body: Assignment, request: Request, context: Workspace = Depends(administrator)):
    async with request.app.state.db.connection() as connection:
        row = await visit(connection, context, visit_id, lock=True)
        check_version(row, body.expectedUpdatedAt)
        if row["status"] in CLOSED:
            fail("walkin_closed", 409)
        if body.agentId:
            member = await one(connection, "select user_id from public.members where organisation_id=%s and user_id=%s and status='active' for share", (context.id, body.agentId))
            if not member:
                fail("walkin_agent_not_found", 404)
        if row["assigned_agent_id"] == body.agentId and row["assignment_note"] == body.note:
            return serialize_visit(row)
        status = row["status"] if row["assigned_agent_id"] == body.agentId else ("Assigned" if body.agentId else "Waiting for Assignment")
        await connection.execute("update public.guruconnect_walkin_visits set assigned_agent_id=%s,assigned_at=case when %s::uuid is null then null when assigned_agent_id=%s then assigned_at else clock_timestamp() end,assignment_note=%s,status=%s,updated_at=clock_timestamp() where id=%s and organisation_id=%s",
                                 (body.agentId, body.agentId, body.agentId, body.note, status, visit_id, context.id))
        if body.agentId and not row["converted_enquiry_id"]:
            from .enquiry_routes import create_walkin_enquiry
            agent = await one(connection, "select display_name from public.profiles where id=%s", (body.agentId,))
            enquiry_id = await create_walkin_enquiry(connection, context, row, body.agentId, agent["display_name"] if agent else "Salesperson", request.app.state.settings.app_timezone)
            await connection.execute("update public.guruconnect_walkin_visits set converted_enquiry_id=%s where id=%s and organisation_id=%s", (enquiry_id, visit_id, context.id))
        elif body.agentId and row["converted_enquiry_id"]:
            agent = await one(connection, "select display_name from public.profiles where id=%s", (body.agentId,))
            await connection.execute("""update public.guruconnect_enquiries
                set assigned_to=%s,
                    payload=jsonb_set(payload,'{assignedExecutive}',to_jsonb(%s::text),true)
                where id=%s and organisation_id=%s""",
                (body.agentId, agent["display_name"] if agent else "Salesperson", row["converted_enquiry_id"], context.id))
        await history(connection, context, row, "Assignment changed", str(row["assigned_agent_id"]) if row["assigned_agent_id"] else None, str(body.agentId) if body.agentId else None, body.note)
        saved = await visit(connection, context, visit_id)
    return serialize_visit(saved)


@router.patch("/{visit_id}/status")
async def change_status(visit_id: UUID, body: StatusChange, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await visit(connection, context, visit_id, lock=True, editing=True)
        check_version(row, body.expectedUpdatedAt)
        if body.status not in TRANSITIONS[row["status"]]:
            fail("invalid_walkin_transition", 409)
        if body.status == "Requirements Captured" and not row["requirements_updated_at"]:
            fail("walkin_requirements_required", 409)
        await connection.execute("update public.guruconnect_walkin_visits set status=%s,updated_at=clock_timestamp() where id=%s and organisation_id=%s", (body.status, visit_id, context.id))
        await history(connection, context, row, "Status changed", row["status"], body.status)
        saved = await visit(connection, context, visit_id)
    return serialize_visit(saved)


@router.get("/{visit_id}/requirements")
async def get_requirements(visit_id: UUID, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await visit(connection, context, visit_id)
    return serialize_requirement(row)


@router.get("/{visit_id}/recordings")
async def list_recordings(visit_id: UUID, request: Request, context: Workspace = Depends(workspace),
                          page: int = Query(default=1, ge=1, le=100000), pageSize: int = Query(default=100, ge=1, le=100)):
    async with request.app.state.db.connection() as connection:
        await visit(connection, context, visit_id)
        rows = await many(connection, """with matches as materialized (
            select c.*, c.active_version_id as transcript_version_id,
                   coalesce(active_transcript.turns, '[]'::jsonb) as transcript_turns
            from public.guruconnect_walkin_recordings r
            join public.conversations c on c.id=r.conversation_id and c.organisation_id=r.organisation_id and c.owner_id=r.owner_id
            left join lateral (
              select v.id as version_id,
                     jsonb_agg(jsonb_build_object(
                       'id', t.id,
                       'ordinal', t.ordinal,
                       'start_seconds', t.start_seconds,
                       'end_seconds', t.end_seconds,
                       'source_text', t.source_text,
                       'english_text', t.english_text,
                       'source_language', t.source_language,
                       'speaker_ref', t.speaker_ref,
                       'speaker_confidence', t.speaker_confidence
                     ) filter (where t.id is not null) order by t.ordinal) as turns
              from public.transcript_versions v
              left join public.transcript_turns t on t.version_id=v.id
              where v.id=c.active_version_id
              group by v.id
            ) active_transcript on true
            where r.organisation_id=%s and r.visit_id=%s and (%s or r.owner_id=%s) and c.deleted_at is null),
            totals as (select count(*) as total from matches), page_rows as (
            select * from matches order by created_at desc,id desc limit %s offset %s)
            select page_rows.*,totals.total from totals left join page_rows on true order by page_rows.created_at desc,page_rows.id desc""",
                          (context.id, visit_id, context.admin, context.user.id, pageSize, (page - 1) * pageSize))
    # Return only public metadata needed by the review UI, without provider
    # snapshots, hashes, lease values or internal processing configuration.
    fields = {"id", "customer_name_snapshot", "salesperson_name_snapshot", "title", "created_at", "duration_seconds", "total_samples", "capture_state", "upload_state", "transcription_state", "english_state", "speaker_state", "final_state", "language_hint", "owner_id", "active_version_id", "transcript_version_id"}
    items = []
    for row in rows:
        if not row["id"]:
            continue
        item = {key: value for key, value in row.items() if key in fields}
        item["transcript"] = row.get("transcript_turns") or []
        items.append(item)
    return {"items": items, "total": rows[0]["total"], "page": page, "pageSize": pageSize}


@router.post("/{visit_id}/recordings")
async def link_recording(visit_id: UUID, body: RecordingLink, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await visit(connection, context, visit_id, lock=True)
        recording = await owned_conversation(connection, context, body.conversationId)
        creator_capture = context.role in {"receptionist", "owner"} and row["registered_by"] == context.user.id and recording["audio_bucket"] == "walkin-audio"
        if row["assigned_agent_id"] != context.user.id and not creator_capture:
            fail("walkin_assigned_agent_required", 403)
        if recording["upload_state"] != "saved" or recording["total_samples"] <= 0:
            fail("recording_not_saved", 409)
        prior = await one(connection, "select visit_id,organisation_id from public.guruconnect_walkin_recordings where conversation_id=%s", (body.conversationId,))
        if prior:
            if prior["visit_id"] != visit_id or prior["organisation_id"] != context.id:
                fail("recording_already_linked", 409)
            return {"ok": True}
        if row["status"] in CLOSED:
            fail("walkin_closed", 409)
        already_enquiry = await one(connection, "select id from public.guruconnect_enquiries where conversation_id=%s", (body.conversationId,))
        if already_enquiry:
            fail("recording_already_linked", 409)
        await connection.execute("insert into public.guruconnect_walkin_recordings(organisation_id,visit_id,conversation_id,owner_id) values(%s,%s,%s,%s)", (context.id, visit_id, body.conversationId, context.user.id))
        # Registration creates the walk-in enquiry before the browser can
        # upload/link the finished main recording. Complete that relationship
        # here so the assigned user's enquiry detail receives the playable
        # conversation id as soon as the recording is saved.
        await connection.execute("""
            update public.guruconnect_enquiries
               set conversation_id=%s
             where organisation_id=%s
               and id=(select converted_enquiry_id
                        from public.guruconnect_walkin_visits
                       where id=%s and organisation_id=%s)
               and (conversation_id is null or conversation_id=%s)
        """, (body.conversationId, context.id, visit_id, context.id, body.conversationId))
        await history(connection, context, row, "Recording attached", new_value=str(body.conversationId))
    return {"ok": True}


@router.put("/{visit_id}/requirements")
async def save_requirements(visit_id: UUID, body: RequirementSave, request: Request, context: Workspace = Depends(workspace)):
    async with request.app.state.db.connection() as connection:
        row = await visit(connection, context, visit_id, lock=True, editing=True)
        check_version(row, body.expectedUpdatedAt)
        if row["status"] in CLOSED:
            fail("walkin_closed", 409)
        if not row["assigned_agent_id"]:
            fail("walkin_assigned_agent_required", 409)
        if not (body.requirement.detailedRequirement or body.requirement.productOrService):
            fail("walkin_requirements_required", 422, "Add a customer requirement or product before saving.")
        status = "Requirements Captured" if "Requirements Captured" in TRANSITIONS[row["status"]] else row["status"]
        await connection.execute("update public.guruconnect_walkin_visits set requirements=%s,requirements_updated_at=clock_timestamp(),status=%s,updated_at=clock_timestamp() where id=%s and organisation_id=%s", (Jsonb(body.requirement.model_dump()), status, visit_id, context.id))
        await history(connection, context, row, "Requirements updated")
        if status != row["status"]:
            await history(connection, context, row, "Status changed", row["status"], status)
        saved = await visit(connection, context, visit_id)
    return serialize_requirement(saved)


async def conversion_visit(connection, context: Workspace, source_event_id: str):
    """Called by enquiry creation before insert, using the same transaction."""
    if not source_event_id.startswith("walkin:"):
        return None
    try:
        visit_id = UUID(source_event_id.removeprefix("walkin:"))
    except ValueError:
        fail("invalid_walkin_source", 422)
    # UUID strings must use one canonical spelling for the unique source index.
    if source_event_id != f"walkin:{visit_id}":
        fail("invalid_walkin_source", 422)
    row = await visit(connection, context, visit_id, lock=True, editing=True)
    if row["assigned_agent_id"] != context.user.id:
        fail("walkin_assigned_agent_required", 403)
    if row["status"] in CLOSED:
        fail("walkin_already_converted", 409)
    if row["status"] not in {"Requirements Captured", "Follow-up Required"} or not row["requirements_updated_at"]:
        fail("walkin_requirements_required", 409)
    if row["converted_enquiry_id"]:
        # Registration already issues the visitor's ticket. A linked queue item
        # is not an explicit requirements conversion until the visit says so.
        enquiry = await one(connection, "select * from public.guruconnect_enquiries where id=%s and organisation_id=%s for update", (row["converted_enquiry_id"], context.id))
        if not enquiry or not (
            enquiry["payload"].get("sourceEventId") == source_event_id
            or (enquiry["payload"].get("sourceWalkinId") == str(visit_id)
                and enquiry["idempotency_key"] == f"walkin-assignment:{visit_id}")
        ):
            fail("walkin_enquiry_mismatch", 409)
        row["conversion_enquiry"] = enquiry
    return row


async def complete_conversion(connection, context: Workspace, row, enquiry_id):
    if row is None:
        return
    await connection.execute("update public.guruconnect_walkin_visits set converted_enquiry_id=%s,status='Converted to Enquiry',updated_at=clock_timestamp() where id=%s and organisation_id=%s", (enquiry_id, row["id"], context.id))
    await history(connection, context, row, "Converted to enquiry", new_value=str(enquiry_id))


async def validate_conversion_recording(connection, context: Workspace, row, conversation_id):
    if conversation_id is None:
        return
    if row is None:
        existing = await one(connection, "select visit_id from public.guruconnect_walkin_recordings where organisation_id=%s and conversation_id=%s and owner_id=%s", (context.id, conversation_id, context.user.id))
        if existing:
            fail("walkin_conversion_required", 409, "Create this recording's enquiry from its assigned walk-in visit.")
        return
    link = await one(connection, "select conversation_id from public.guruconnect_walkin_recordings where organisation_id=%s and visit_id=%s and conversation_id=%s and owner_id=%s", (context.id, row["id"], conversation_id, context.user.id))
    if not link:
        fail("walkin_recording_mismatch", 409, "Choose a saved recording attached to this visit.")
