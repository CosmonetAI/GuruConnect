from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _text(value: Any) -> str | None:
    value = str(value or '').strip()
    return value if value and value.lower() not in {'unknown', 'none', 'null'} else None


def _items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


@dataclass
class VoiceConversationContext:
    active_topic: str | None = None
    last_intent: str | None = None
    focal_product: str | None = None
    focal_product_sku: str | None = None
    customer_requirements: list[dict[str, Any]] = field(default_factory=list)
    budget_max: float | None = None
    preferred_features: list[str] = field(default_factory=list)
    brand_preferences: list[str] = field(default_factory=list)
    timeline: str | None = None
    availability_requirement: str | None = None
    discount_expectation: str | None = None
    objections: list[str] = field(default_factory=list)
    unanswered_questions: list[str] = field(default_factory=list)
    commitments: list[str] = field(default_factory=list)
    buying_signals: list[str] = field(default_factory=list)
    contact_details: dict[str, Any] = field(default_factory=dict)
    recent_recommendations: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    recent_summaries: list[str] = field(default_factory=list)
    updated_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> 'VoiceConversationContext':
        value = value if isinstance(value, dict) else {}
        known = {field_name for field_name in cls.__dataclass_fields__}
        data = {key: value[key] for key in known if key in value}
        context = cls(**data)
        context.customer_requirements = _items(context.customer_requirements)
        context.recent_recommendations = _items(context.recent_recommendations)
        context.entities = _items(context.entities)
        context.source_refs = _items(context.source_refs)
        return context

    def merge_extraction(self, extraction: dict[str, Any], source_ref: dict[str, Any]) -> None:
        """Merge the new chunk into durable context without replacing old facts with blanks."""
        fingerprint = str(source_ref.get('fingerprint') or '')
        if fingerprint and any(str(ref.get('fingerprint') or '') == fingerprint for ref in self.source_refs):
            return
        scalar_map = {
            'active_topic': 'active_topic', 'intent': 'last_intent', 'focal_product': 'focal_product',
            'focal_product_sku': 'focal_product_sku', 'budget': 'budget_max', 'timeline': 'timeline',
            'availability': 'availability_requirement', 'discounts': 'discount_expectation',
        }
        for incoming, target in scalar_map.items():
            value = extraction.get(incoming)
            if isinstance(value, (int, float)) and target == 'budget_max':
                setattr(self, target, value)
            elif isinstance(value, str) and value.strip():
                setattr(self, target, value.strip())
        for incoming, target in {
            'requirements': 'customer_requirements', 'products': 'customer_requirements',
        }.items():
            for item in _items(extraction.get(incoming)):
                self._merge_dict(self.__dict__[target], item, source_ref)
        for incoming, target in {
            'features': 'preferred_features', 'brands': 'brand_preferences', 'objections': 'objections',
            'unanswered_questions': 'unanswered_questions', 'commitments': 'commitments',
            'buying_signals': 'buying_signals',
        }.items():
            values = extraction.get(incoming)
            if isinstance(values, str): values = [values]
            if isinstance(values, list):
                existing = getattr(self, target)
                for value in values:
                    clean = str(value).strip()
                    if clean and clean not in existing: existing.append(clean)
                setattr(self, target, existing[-30:])
        contact = extraction.get('contact_details')
        if isinstance(contact, dict):
            self.contact_details = {**self.contact_details, **{k: v for k, v in contact.items() if v not in (None, '')}}
        entities = _items(extraction.get('entities'))
        for entity in entities:
            key = (str(entity.get('type') or ''), str(entity.get('id') or entity.get('display') or ''))
            if key[1] and not any((str(item.get('type') or ''), str(item.get('id') or item.get('display') or '')) == key for item in self.entities):
                self.entities.append({**entity, 'source': entity.get('source') or 'voice_chunk'})
        self.source_refs = [*self.source_refs, source_ref][-100:]
        text = str(extraction.get('summary') or '').strip()
        if text:
            self.recent_summaries = [*self.recent_summaries, text[:800]][-6:]
        self.updated_at = datetime.now(UTC).isoformat()

    @staticmethod
    def _merge_dict(target: list[dict[str, Any]], item: dict[str, Any], source_ref: dict[str, Any]) -> None:
        identity = str(item.get('sku') or item.get('product') or item.get('name') or item.get('display') or '').strip().lower()
        if not identity: return
        for existing in target:
            existing_identity = str(existing.get('sku') or existing.get('product') or existing.get('name') or existing.get('display') or '').strip().lower()
            if existing_identity == identity:
                existing.update({k: v for k, v in item.items() if v not in (None, '', [])})
                existing.setdefault('source_refs', []).append(source_ref)
                return
        target.append({**item, 'source_refs': [source_ref]})
        del target[:-30]
