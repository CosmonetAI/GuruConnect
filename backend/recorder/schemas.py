from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Hash = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Id = Annotated[UUID, Field(strict=False)]
Engine = Literal["elevenlabs", "faster-whisper", "azure-translator", "openai-translator"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WorkspaceCreate(Input):
    name: Name


class ProfilePatch(Input):
    display_name: Name
    preferences: dict = Field(default_factory=dict)


class CustomerCreate(Input):
    name: Name = "Name not provided"
    is_unnamed: bool = False


class ConversationCreate(Input):
    customer_id: Id | None = None
    customer_name: Name | None = None
    is_unnamed: bool = False
    title: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    language_hint: Literal["auto", "kn", "hi", "bn", "en"] = "auto"
    consent_confirmed: bool
    engine_id: Literal["elevenlabs", "faster-whisper"] | None = None
    audio_only: bool = False

    @model_validator(mode="after")
    def customer_and_consent(self):
        if not self.consent_confirmed:
            raise ValueError("Recording acknowledgement is required")
        if not self.customer_id and not self.customer_name and not self.is_unnamed:
            raise ValueError("Select a customer, enter a name, or select Name not provided")
        return self


class ConversationPatch(Input):
    title: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    customer_name_snapshot: Name | None = None


class LeaseAcquire(Input):
    tab_id: Annotated[str, StringConstraints(min_length=16, max_length=100)]
    takeover: bool = False
    purpose: Literal['capture','recover'] = 'capture'


class LeaseHeartbeat(Input):
    tab_id: Annotated[str, StringConstraints(min_length=16, max_length=100)]
    lease_token: Id


class Chunk(Input):
    conversation_id: Id | None = None
    part_id: Id
    sequence: int = Field(ge=0, le=2880)
    sample_count: int = Field(ge=1, le=160000)
    sample_rate: Literal[16000] = 16000
    start_sample: int = Field(ge=0, le=115200000)
    bytes: int = Field(ge=46, le=400000)
    sha256: Hash
    mime: Literal["audio/wav"] = "audio/wav"
    lease_token: Id


class CaptureEvent(Input):
    part_id: Id
    lease_token: Id
    kind: Literal["started", "pause", "paused", "resume", "resumed", "interrupted", "stop", "stopped", "device_removed", "storage_full", "limit_reached"]
    sample: int = Field(ge=0, le=115200000)


class Manifest(Input):
    part_id: Id
    expected_chunks: int = Field(ge=0, le=2880)
    total_samples: int = Field(ge=0, le=115200000)
    lease_token: Id


class Invitation(Input):
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")]
    role: Literal["admin", "salesperson"] = "salesperson"


class AcceptInvitation(Input):
    token: Annotated[str, StringConstraints(min_length=32, max_length=200)]


class ConnectionCreate(Input):
    engine_id: Engine
    credentials: dict[str, str] = Field(default_factory=dict, max_length=5)
    config: dict = Field(default_factory=dict)
    idempotency_key: Annotated[str, StringConstraints(min_length=8, max_length=100)]
    expected_generation: Annotated[int, Field(strict=True, ge=1)] | None = None


class TestConnection(Input):
    functional: bool = False


class Disconnect(Input):
    finish_in_flight: bool = False


class EngineSettings(Input):
    default_engine: Literal["elevenlabs", "faster-whisper"] | None = "elevenlabs"
    allow_salesperson_engine_selection: bool = False
    allow_comparison: bool = False
    allowed_engines: list[Literal["elevenlabs", "faster-whisper"]] = Field(default_factory=lambda: ["elevenlabs"])
    budget_minutes: int = Field(default=10000, ge=0, le=1000000)


class RunCreate(Input):
    engine_id: Literal["elevenlabs", "faster-whisper"]
    kind: Literal["replay", "comparison"] = "replay"
    idempotency_key: Annotated[str, StringConstraints(min_length=8, max_length=100)]


class SpeakerAssignment(Input):
    version_id: Id
    speaker_ref: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    name: Name
    role: Literal["salesperson", "customer", "other", "unknown"]


class Revision(Input):
    version_id: Id
    turn_id: Id
    english_text: str | None = Field(default=None, max_length=20000)
    source_text: str | None = Field(default=None, max_length=20000)

    @model_validator(mode="after")
    def content(self):
        if self.english_text is None and self.source_text is None:
            raise ValueError("A correction is required")
        return self


class Retry(Input):
    stage: Literal["all", "transcription", "translation", "diarization", "finalisation"] = "all"


class WorkerHeartbeat(Input):
    ready: bool
    model_version: str = Field(max_length=200)
    metrics: dict[str, float] = Field(default_factory=dict, max_length=10)


class WorkerClaim(Input):
    limit: int = Field(default=1, ge=1, le=1)


class WorkerLease(Input):
    lease_token: Id
    fencing_token: int = Field(ge=1)


class Word(Input):
    text: str = Field(max_length=1000)
    start: float = Field(ge=0, le=14400)
    end: float = Field(ge=0, le=14400)


class DiarizationInterval(Input):
    start: float = Field(ge=0, le=14400)
    end: float = Field(gt=0, le=14400)
    speaker: str = Field(min_length=1, max_length=200)
    overlap: bool = False

    @model_validator(mode="after")
    def time(self):
        if self.end <= self.start:
            raise ValueError("Interval end must be after start")
        return self


class Segment(Input):
    start: float = Field(ge=0, le=14400)
    end: float = Field(gt=0, le=14400)
    text: str = Field(max_length=20000)
    speaker: str | None = Field(default=None, max_length=200)
    words: list[Word] | None = Field(default=None, max_length=10000)
    speaker_confidence: Literal["provisional", "supported", "uncertain", "overlap"] = "uncertain"

    @model_validator(mode="after")
    def time(self):
        if self.end <= self.start:
            raise ValueError("Segment end must be after start")
        return self


class WorkerResult(Input):
    lease_token: Id
    fencing_token: int = Field(ge=1)
    language: str | None = Field(default=None, max_length=20)
    source_segments: list[Segment] | None = Field(default=None, max_length=10000)
    english_segments: list[Segment] | None = Field(default=None, max_length=10000)
    diarization_status: Literal["completed", "failed", "unavailable"]
    diarization_intervals: list[DiarizationInterval] = Field(default_factory=list, max_length=20000)
    provenance: dict = Field(default_factory=dict, max_length=30)
    artifact_id: Id | None = None


class WorkerFailure(Input):
    lease_token: Id
    fencing_token: int = Field(ge=1)
    error_code: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,80}$")]
    uncertain: bool = False


class ArtifactUpload(WorkerLease):
    sha256: Hash
    bytes: int = Field(ge=2, le=5000000)


class ComparisonReview(Input):
    review_notes: str = Field(max_length=10000)


class SpeakerGroups(Input):
    version_id: Id
    operation: Literal["merge", "split"]
    speaker_refs: list[Annotated[str, StringConstraints(min_length=1, max_length=200)]] = Field(min_length=1, max_length=20)
    turn_ids: list[Id] = Field(default_factory=list, max_length=1000)
    target_ref: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def group_operation(self):
        if len(set(self.speaker_refs)) != len(self.speaker_refs):
            raise ValueError("Speaker references must be unique")
        if self.operation == "merge" and len(self.speaker_refs) < 2:
            raise ValueError("Select at least two speaker groups to merge")
        if self.operation == "split" and (len(self.speaker_refs) != 1 or not self.turn_ids):
            raise ValueError("Select turns from one speaker group to split")
        if self.target_ref and self.operation == "merge" and self.target_ref not in self.speaker_refs:
            raise ValueError("Merge target must be a selected speaker reference")
        if self.operation == "split" and self.target_ref:
            raise ValueError("Split target is assigned by the server")
        return self
