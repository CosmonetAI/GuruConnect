"""Evidence-based matching for a changing, member-scoped product catalogue.

There are no catalogue products, brands or SKUs in this module. Identity terms
and accessory exclusions come from the conversation planner; technical units
are parsed deterministically from the supplied inventory records. Ranking is a
retrieval score, not a calibrated probability or a promise of compatibility.
"""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache
import math
import re
import unicodedata
from typing import Any, Iterable


_NUMBER = r'\d+(?:\.\d+)?'
_RANGE = rf'(?P<low>{_NUMBER})(?:\s*(?:-|–|to)\s*(?P<high>{_NUMBER}))?'
_SPECS = {
    'area_sqmm': (re.compile(rf'(?<![\w.]){_RANGE}\s*(?:sq\.?\s*mm|sqmm|mm\s*(?:²|\^2|2))\b', re.I), 1.0),
    'current_a': (re.compile(rf'(?<![\w.]){_RANGE}\s*(?:amps?|amperes?|a)(?![a-z])', re.I), 1.0),
    'power_hp': (re.compile(rf'(?<![\w.]){_RANGE}\s*(?:h\.?\s*p\.?|horse\s*power)(?![a-z])', re.I), 1.0),
    'power_kw': (re.compile(rf'(?<![\w.]){_RANGE}\s*(?:kw|kilowatts?)(?![a-z])', re.I), 1.0),
    'voltage_v': (re.compile(rf'(?<![\w.]){_RANGE}\s*(?:volts?|v)(?:\s*(?:ac|dc))?(?![a-z])', re.I), 1.0),
    'speed_rpm': (re.compile(rf'(?<![\w.]){_RANGE}\s*(?:rpm|r\.?p\.?m\.?)(?![a-z])', re.I), 1.0),
    'frequency_hz': (re.compile(rf'(?<![\w.]){_RANGE}\s*(?:hz|hertz)(?![a-z])', re.I), 1.0),
    'breaking_capacity_ka': (re.compile(rf'(?<![\w.]){_RANGE}\s*ka(?![a-z])', re.I), 1.0),
    'capacitance_uf': (re.compile(rf'(?<![\w.]){_RANGE}\s*(?:uf|µf|μf|microfarads?)(?![a-z])', re.I), 1.0),
    'reactive_power_kvar': (re.compile(rf'(?<![\w.]){_RANGE}\s*kvar(?![a-z])', re.I), 1.0),
    'size_mm': (re.compile(rf'(?<![\w.]){_RANGE}\s*(?:mm|millimet(?:er|re)s?)(?![a-z²\d])', re.I), 1.0),
}
_KEYS = {
    'amps': 'current_a', 'amperage': 'current_a', 'current': 'current_a', 'current_a': 'current_a',
    'hp': 'power_hp', 'horsepower': 'power_hp', 'power_hp': 'power_hp',
    'kw': 'power_kw', 'power_kw': 'power_kw', 'voltage': 'voltage_v', 'voltage_v': 'voltage_v',
    'rpm': 'speed_rpm', 'speed': 'speed_rpm', 'speed_rpm': 'speed_rpm',
    'poles': 'poles', 'pole': 'poles', 'phase': 'phases', 'phases': 'phases',
    'size_mm': 'size_mm', 'width': 'width_mm', 'width_mm': 'width_mm', 'diameter': 'diameter_mm', 'diameter_mm': 'diameter_mm',
    'area_sqmm': 'area_sqmm', 'cross_section_sqmm': 'area_sqmm',
    'frequency_hz': 'frequency_hz', 'breaking_capacity_ka': 'breaking_capacity_ka',
    'curve': 'breaker_curve', 'breaker_curve': 'breaker_curve',
    'cores': 'cores', 'core_count': 'cores', 'capacitance_uf': 'capacitance_uf',
    'reactive_power_kvar': 'reactive_power_kvar', 'ip_rating': 'ip_rating', 'efficiency_class': 'efficiency_class',
}
_POLES = {'sp': 1, 'dp': 2, 'tp': 3, 'fp': 4}
_PHASES = {'single': 1, 'one': 1, 'three': 3}
_GRAMMAR = {'a', 'an', 'the', 'of', 'for', 'with', 'and', 'or', 'to', 'need', 'want', 'looking', 'please', 'some', 'product', 'item'}
_SPEC_LABELS = {
    'current_a': ('Current', 'A'), 'power_hp': ('Power', 'HP'), 'power_kw': ('Power', 'kW'),
    'voltage_v': ('Voltage', 'V'), 'speed_rpm': ('Speed', 'RPM'), 'frequency_hz': ('Frequency', 'Hz'),
    'breaking_capacity_ka': ('Breaking capacity', 'kA'), 'size_mm': ('Size', 'mm'),
    'area_sqmm': ('Cross-section', 'sq mm'), 'width_mm': ('Width', 'mm'), 'diameter_mm': ('Diameter', 'mm'),
    'poles': ('Poles', ''), 'phases': ('Phases', ''), 'breaker_curve': ('Curve', ''),
    'cores': ('Cores', ''), 'capacitance_uf': ('Capacitance', 'uF'),
    'reactive_power_kvar': ('Reactive power', 'kVAR'), 'ip_rating': ('Protection', ''), 'efficiency_class': ('Efficiency class', ''),
}


def _first(row: dict, *keys, default=None):
    return next((row[key] for key in keys if key in row and row[key] not in (None, '')), default)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    text = str(value if value is not None else '').strip().replace(',', '')
    # CSV monetary values include an ISO currency prefix; API values are numeric.
    text = re.sub(r'^[A-Z]{3}\s*', '', text).replace('₹', '').removesuffix('%').strip()
    try:
        result = float(text)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _boolean(value: Any, default: bool | None = None) -> bool | None:
    if isinstance(value, bool):
        return value
    clean = str(value or '').strip().casefold()
    return True if clean in {'true', 'yes', '1'} else False if clean in {'false', 'no', '0'} else default


def _inventory_tracked(row: dict) -> bool | None:
    tracking = _boolean(row.get('track_inventory'))
    item_type = str(row.get('item_type') or '').strip().lower()
    product_type = str(row.get('product_type') or '').strip().lower()
    if tracking is True or item_type == 'inventory':
        return True
    if tracking is False or product_type in {'service', 'digital_service', 'capital_service'} or item_type in {'sales', 'sales_and_purchases'}:
        return False
    return None


@lru_cache(maxsize=20000)
def normalized_text(value: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[^\w]+', ' ', unicodedata.normalize('NFKC', str(value)).casefold())).strip()


@lru_cache(maxsize=20000)
def _tokens(value: str) -> list[str]:
    value = re.sub(r'(\d)(mm|sqmm|hp|kw|rpm|hz|ph|p|v|a)\b', r'\1 \2', str(value), flags=re.I)
    words = re.findall(r'\b[^\W_]+\b', normalized_text(value), re.UNICODE)
    return [word[:-1] if len(word) > 3 and word.endswith('s') and not word.endswith('ss') else word
            for word in words if word not in _GRAMMAR]


def _terms(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return list(dict.fromkeys(str(term).strip() for term in value if isinstance(term, (str, int, float)) and str(term).strip()))
    return []


def _measure(match, factor=1.0):
    low = round(float(match.group('low')) * factor, 8)
    high = match.groupdict().get('high')
    if high is None:
        return low
    high = round(float(high) * factor, 8)
    return {'min': min(low, high), 'max': max(low, high)}


def _negated_spec(source: str, start: int, end: int) -> bool:
    """Recognize local denial of a rating without erasing positive clauses.

    Negation is scoped to a sentence/contrast clause. Lists after a denied
    rating stay denied; an explicit affirmative clause starts a new scope.
    Expressions such as "not only" and "no-load speed" are not denials.
    """
    prefix = re.split(r'[;!?\n]|\.(?=\s|$)|\b(?:but|however|whereas|yet)\b', source[:start], flags=re.I)[-1]
    prefix = re.split(r',\s*(?=(?:supports?|accepts?|operates?|works?|rated|suitable|compatible)\b)', prefix, flags=re.I)[-1]
    cues = list(re.finditer(r"\b(?:not|no|without|except|excluding|unsupported|unsuitable|incompatible|cannot|can't|doesn't|don't|isn't|aren't|won't)\b|\bnon[-\s]+", prefix, re.I))
    for cue in reversed(cues):
        tail = prefix[cue.end():].lstrip(' -:')
        if cue.group().casefold() == 'not' and re.match(r'(?:only|just|merely)\b', tail, re.I):
            continue
        # These starts either point directly at this rating or introduce a
        # denied list of ratings. "No warranty, 1500 RPM" does neither.
        if not tail or re.match(r'(?:\d|single\b|one\b|three\b|[SDTF]P\b|IP\s*\d|IE\s*\d)', tail, re.I):
            return True
        if re.match(r'(?:(?:currently|presently|directly|fully|generally)\s+)*(?:support\w*|suitable|compatible|rated|recommended|available|allowed|approved|designed|intended|operate\w*|work\w*|use\w*|for|with|in|at|on|any|a|an|the)\b', tail, re.I):
            return True
    suffix = source[end:]
    return bool(re.match(r'\s*(?:(?:ac|dc|operation|installations?|applications?|inputs?|loads?|supply|use)\s+)*(?:(?:is|are|was|were)\s+)?(?:not\s+(?:supported|suitable|compatible|rated|available|allowed|intended|recommended)|unsupported|incompatible|unsuitable)\b', suffix, re.I))


def _add_spec(specs: dict, evidence: list, key: str, value, field: str, source: str, start: int, end: int):
    negated = _negated_spec(source, start, end)
    if not negated and value not in specs.setdefault(key, []):
        specs[key].append(value)
    evidence.append({'key': key, 'value': value, 'field': field, 'text': source[start:end], 'span': [start, end],
                     **({'negated': True} if negated else {})})


def _parse_specs(value: str, field: str) -> tuple[dict, list]:
    specs, evidence = {}, []
    slash_spans = []
    for key, units in [('power_hp', r'hp|horse\s*power'), ('power_kw', r'kw'), ('voltage_v', r'v|volts?'),
                       ('current_a', r'a|amps?'), ('size_mm', r'mm')]:
        for match in re.finditer(rf'(?<![\w.])(?P<first>{_NUMBER})\s*/\s*(?P<second>{_NUMBER})\s*(?:{units})(?![a-z])', value, re.I):
            first, second = float(match.group('first')), float(match.group('second'))
            if key == 'power_hp' and first.is_integer() and second in {2, 4, 8, 16} and 0 < first < second:
                values = [first / second]
            else:
                values = list(dict.fromkeys([first, second]))
            for actual in values:
                _add_spec(specs, evidence, key, actual, field, value, *match.span())
            slash_spans.append(match.span())
    for key, (pattern, factor) in _SPECS.items():
        for match in pattern.finditer(value):
            if any(start <= match.start() and match.end() <= end for start, end in slash_spans):
                continue
            if key == 'size_mm':
                before = value[max(0, match.start() - 6):match.start()]
                if re.search(r'(?:sq\.?\s*|square\s*)$', before, re.I):
                    continue
            _add_spec(specs, evidence, key, _measure(match, factor), field, value, *match.span())
    # Prefix units are converted only where the unit itself is explicit.
    for key, unit, factor in [('voltage_v', r'kv|kilovolts?', 1000), ('current_a', r'ma|milliamps?', .001),
                              ('power_kw', r'w|watts?', .001), ('size_mm', r'cm|centimet(?:er|re)s?', 10)]:
        for match in re.finditer(rf'(?<![\w.]){_RANGE}\s*(?:{unit})(?![a-z])', value, re.I):
            _add_spec(specs, evidence, key, _measure(match, factor), field, value, *match.span())
    for match in re.finditer(r'\b([1-8])\s*(?:poles?|p)\b', value, re.I):
        _add_spec(specs, evidence, 'poles', int(match.group(1)), field, value, *match.span())
    for match in re.finditer(r'\b(SP|DP|TP|FP)\b', value, re.I):
        _add_spec(specs, evidence, 'poles', _POLES[match.group(1).lower()], field, value, *match.span())
    for match in re.finditer(r'\b(\d+(?:\.5)?)\s*cores?\b', value, re.I):
        _add_spec(specs, evidence, 'cores', float(match.group(1)), field, value, *match.span())
    if re.search(r'\b(?:cable|wire)\b', value, re.I):
        for match in re.finditer(r'\b(\d+(?:\.5)?)\s*C\b', value, re.I):
            _add_spec(specs, evidence, 'cores', float(match.group(1)), field, value, *match.span())
    for key, pattern in [('ip_rating', r'\bIP\s*([0-6X][0-9X])\b'), ('efficiency_class', r'\bIE\s*([1-5])\b')]:
        for match in re.finditer(pattern, value, re.I):
            code = ('IP' if key == 'ip_rating' else 'IE') + match.group(1).upper()
            _add_spec(specs, evidence, key, code, field, value, *match.span())
    for match in re.finditer(r'\b([1-3]|single|one|three)\s*(?:phases?|ph)\b', value, re.I):
        raw = match.group(1).lower()
        _add_spec(specs, evidence, 'phases', _PHASES.get(raw, int(raw) if raw.isdigit() else None), field, value, *match.span())
    for match in re.finditer(r'\b(?:curve\s*([BCD])|([BCD])\s*curve)\b', value, re.I):
        _add_spec(specs, evidence, 'breaker_curve', (match.group(1) or match.group(2)).upper(), field, value, *match.span())
    if re.search(r'\bMCB\b', value, re.I):
        for match in re.finditer(r'\bTYPE\s*([BCD])\b', value, re.I):
            _add_spec(specs, evidence, 'breaker_curve', match.group(1).upper(), field, value, *match.span())
    for label, key in [('width|wide', 'width_mm'), ('diameter|dia', 'diameter_mm')]:
        pattern = rf'\b(?:{label})\s*[:=]?\s*(?P<low>{_NUMBER})\s*(?P<unit>mm|cm)\b|(?P<after>{_NUMBER})\s*(?P<after_unit>mm|cm)\s*(?:{label})\b'
        for match in re.finditer(pattern, value, re.I):
            factor = 10 if str(match.group('unit') or match.group('after_unit')).lower() == 'cm' else 1
            _add_spec(specs, evidence, key, float(match.group('low') or match.group('after')) * factor, field, value, *match.span())
    return specs, evidence


def normalize_item(row: dict) -> dict | None:
    """Normalize API records or a supplied CSV row without adding inventory facts.

    Repeated location rows must be deduplicated by item_id; stock is never
    summed. The original numeric/source fields and each parsed spec's span are
    retained so the caller can explain a match.
    """
    if not isinstance(row, dict):
        return None
    if row.get('_catalogue_normalized'):
        result = dict(row)
        if _inventory_tracked(row) is False:
            result.update(availability_status='not_tracked', availability_basis='not_tracked', stock_tracking_status='not_tracked')
        if row.get('_catalogue_stale') or row.get('catalogue_stale'):
            result['price_status'] = 'confirmation_required'
            if result.get('availability_status') != 'not_tracked':
                result['availability_status'] = 'needs_review'
        return result
    identity = _first(row, 'item_id', 'Item ID', 'id')
    name = _first(row, 'name', 'item_name', 'Item Name', 'Product Name')
    if identity in (None, '') or not str(name or '').strip():
        return None
    name = str(name).strip()
    source_sku = str(_first(row, 'sku', 'SKU', default='')).strip()
    description = str(_first(row, 'description', 'Description', 'Item Description', default='')).strip()
    fields = {'name': name, 'description': description}
    specs, evidence, spec_conflicts, excluded_specs = {}, [], [], {}
    for field, value in fields.items():
        parsed, sources = _parse_specs(value, field)
        if source_sku:
            code_spans = [match.span() for match in re.finditer(re.escape(source_sku), value, re.I)]
            sources = [source for source in sources if not any(start < source['span'][1] and end > source['span'][0] for start, end in code_spans)]
            parsed = {}
            for source in sources:
                if not source.get('negated') and source['value'] not in parsed.setdefault(source['key'], []):
                    parsed[source['key']].append(source['value'])
        for source in sources:
            if source.get('negated') and source['value'] not in excluded_specs.setdefault(source['key'], []):
                excluded_specs[source['key']].append(source['value'])
        for key, values in parsed.items():
            if field == 'description' and key in specs:
                if any(entry not in specs[key] for entry in values):
                    spec_conflicts.append(key)
                continue
            specs.setdefault(key, [])
            for entry in values:
                if entry not in specs[key]:
                    specs[key].append(entry)
        evidence.extend(sources)
    # Explicit structured attributes may be supplied by Books item details.
    structured_field = next((key for key in ('specifications', 'technical_specs', 'attributes') if isinstance(row.get(key), dict)), 'specifications')
    structured = row.get(structured_field) or {}
    structured = dict(structured) if isinstance(structured, dict) else {}
    structured_sources = {key: f'{structured_field}.{key}' for key in structured}
    for index in range(1, 4):
        label = _first(row, f'attribute_name{index}', f'AttributeName{index}')
        option = _first(row, f'attribute_option_name{index}', f'attribute_option{index}', f'AttributeOption{index}')
        if label and option not in (None, ''):
            structured[str(label)] = option
            structured_sources[str(label)] = next((key for key in (f'attribute_option_name{index}', f'attribute_option{index}', f'AttributeOption{index}') if key in row), f'attribute_option{index}')
    if isinstance(structured, dict):
        for raw_key, raw_value in structured.items():
            key = _KEYS.get(str(raw_key).strip().lower())
            if not key:
                continue
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            for value in values:
                parsed_value = (_number(value) if key not in {'breaker_curve', 'ip_rating', 'efficiency_class'}
                                else str(value).strip().upper().replace(' ', ''))
                if parsed_value is None:
                    parsed_from_text, parsed_evidence = _parse_specs(str(value), structured_sources[raw_key])
                    for source in parsed_evidence:
                        if not source.get('negated'):
                            continue
                        if key in {'width_mm', 'diameter_mm'} and source['key'] == 'size_mm':
                            source = {**source, 'key': key}
                        if source['value'] not in excluded_specs.setdefault(source['key'], []):
                            excluded_specs[source['key']].append(source['value'])
                        evidence.append(source)
                    values_from_text = parsed_from_text.get(key) or (parsed_from_text.get('size_mm') if key in {'width_mm', 'diameter_mm'} else []) or []
                    if values_from_text:
                        parsed_value = values_from_text[0]
                if parsed_value is not None:
                    if key in specs and parsed_value not in specs[key]:
                        spec_conflicts.append(key)
                    _add_spec(specs, evidence, key, parsed_value, structured_sources[raw_key], str(value), 0, len(str(value)))
    rate = _number(_first(row, 'rate', 'Rate'))
    explicitly_free = _boolean(_first(row, 'is_free', 'explicitly_free', 'is_complimentary'), False)
    stale = bool(row.get('_catalogue_stale') or row.get('catalogue_stale'))
    price_status = ('known' if rate is not None and (rate > 0 or rate == 0 and explicitly_free)
                    and not stale and row.get('price_status') != 'confirmation_required' else 'confirmation_required')
    stock = _number(_first(row, 'stock_on_hand', 'Stock On Hand'))
    available = _number(_first(row, 'available_stock', 'available_for_sale_stock', 'actual_available_stock'))
    basis = available if available is not None else stock
    tracked = _inventory_tracked(row)
    availability = 'not_tracked' if tracked is False else 'needs_review' if basis is None or basis < 0 else 'in_stock' if basis > 0 else 'out_of_stock'
    if stale and tracked is not False:
        availability = 'needs_review'
    status = str(_first(row, 'status', 'Status', default='active')).strip().lower()
    source_sellable = _boolean(_first(row, 'can_be_sold', 'sellable', 'Sellable'))
    sellable = source_sellable is not False
    unit = str(_first(row, 'unit', 'usage_unit', 'Usage unit', 'Unit Name', default='')).strip()
    category = str(_first(row, 'category_name', 'category', default='')).strip()
    image = str(_first(row, 'image_url', 'image', default='')).strip()
    if not image.startswith(('https://', 'http://')) and not re.fullmatch(r'/demo-products/[a-z0-9-]+\.svg', image):
        image = ''
    return {**row, '_catalogue_normalized': True, 'item_id': str(identity), 'name': name,
            'sku': source_sku, 'description': description,
            'brand': str(_first(row, 'brand', 'Brand', default='')).strip(),
            'manufacturer': str(_first(row, 'manufacturer', 'Manufacturer', default='')).strip(),
            'category': category, 'rate': rate, 'price_status': price_status, 'is_free': bool(explicitly_free),
            'category_is_product_type': _boolean(row.get('category_is_product_type'), False),
            'stock_on_hand': stock, 'available_stock': available, 'availability_status': availability,
            'availability_basis': 'not_tracked' if tracked is False else 'available_stock' if available is not None else 'stock_on_hand',
            'stock_tracking_status': 'not_tracked' if tracked is False else 'tracked' if tracked is True else 'unknown',
            'unit': unit, 'status': status, 'can_be_sold': sellable, 'image_url': image or None,
            'sellability_status': 'confirmed' if source_sellable is True else 'not_sellable' if source_sellable is False else 'unknown',
            'currency_code': _first(row, 'currency_code', 'currency', default='INR' if str(_first(row, 'Rate', default='')).startswith('INR ') else None),
            'tax_percentage': _number(_first(row, 'tax_percentage', 'Intra State Tax Rate')),
            'replacement_item_id': str(_first(row, 'replacement_item_id', 'CF.New Item ID', default='')).strip() or None,
            'specs': specs, 'excluded_specs': excluded_specs, 'spec_evidence': evidence, 'spec_conflicts': list(dict.fromkeys(spec_conflicts)),
            'explicit_attributes': structured,
            'source_facts': {key: row[key] for key in ('item_id','Item ID','name','Item Name','sku','SKU','rate','Rate',
                'stock_on_hand','Stock On Hand','available_stock','status','Status','can_be_sold','Sellable','Brand',
                'brand','unit','Usage unit','category','category_name','is_free','explicitly_free','is_complimentary') if key in row}}


def _merge_specs(target: dict, source: dict):
    for key, values in source.items():
        for value in values:
            if value not in target.setdefault(key, []):
                target[key].append(value)


def _requirement_specs(requirement: dict) -> tuple[dict, list[str]]:
    specs, text_requirements = {}, []
    product = str(requirement.get('product') or requirement.get('name') or '')
    _merge_specs(specs, _parse_specs(product, 'requirement')[0])
    for collection in ('required_specs', 'attributes', 'specifications'):
        attributes = requirement.get(collection) or {}
        if isinstance(attributes, (list, str)):
            attributes = {str(index): value for index, value in enumerate(_terms(attributes))}
        if not isinstance(attributes, dict):
            continue
        for raw_key, raw_values in attributes.items():
            key = _KEYS.get(str(raw_key).strip().lower())
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            for value in values:
                if value in (None, '') or isinstance(value, bool):
                    continue
                if str(raw_key).strip().lower() in {'model', 'model_code', 'part_number', 'mpn', 'identifier'}:
                    text_requirements.extend(_terms(value))
                    continue
                if key and isinstance(value, dict) and {'min', 'max'} <= set(value):
                    low, high = _number(value['min']), _number(value['max'])
                    if low is not None and high is not None:
                        specs.setdefault(key, []).append({'min': min(low, high), 'max': max(low, high)})
                    continue
                numeric = _number(value)
                if key and numeric is not None and not re.search(r'[a-z]', str(value), re.I):
                    specs.setdefault(key, []).append(numeric)
                    continue
                if key == 'breaker_curve':
                    found = re.search(r'\b[BCD]\b', str(value), re.I)
                    if found:
                        specs.setdefault(key, []).append(found.group().upper())
                        continue
                parsed = _parse_specs(str(value), f'requirement.{raw_key}')[0]
                if key in {'width_mm', 'diameter_mm'} and parsed.get('size_mm'):
                    parsed[key] = parsed.pop('size_mm')
                if key == 'phases' and not parsed.get('phases'):
                    raw = str(value).strip().lower()
                    if raw in _PHASES:
                        parsed['phases'] = [_PHASES[raw]]
                if key == 'poles' and not parsed.get('poles') and str(value).strip().lower() in _POLES:
                    parsed['poles'] = [_POLES[str(value).strip().lower()]]
                if parsed:
                    _merge_specs(specs, parsed)
                elif str(raw_key).lower() not in {'quantity', 'budget', 'source', 'sku'}:
                    text_requirements.extend(_terms(value))
    for key in ('features', 'required_features', 'brand', 'brands', 'color', 'colour'):
        text_requirements.extend(_terms(requirement.get(key)))
    return specs, list(dict.fromkeys(text_requirements))


def _bounds(value):
    return (value['min'], value['max']) if isinstance(value, dict) else (float(value), float(value))


def _compatible(actual, requested) -> bool:
    if isinstance(requested, str):
        return str(actual).casefold() == requested.casefold()
    if isinstance(actual, str):
        return False
    low, high = _bounds(actual)
    wanted_low, wanted_high = _bounds(requested)
    return low - 1e-6 <= wanted_low and high + 1e-6 >= wanted_high


def _excluded_spec(actual, requested) -> bool:
    if isinstance(actual, str) or isinstance(requested, str):
        return str(actual).casefold() == str(requested).casefold()
    low, high = _bounds(actual)
    wanted_low, wanted_high = _bounds(requested)
    return low - 1e-6 <= wanted_high and high + 1e-6 >= wanted_low


def _term_match(term: str, value: str, *, same_length_fuzzy=False) -> tuple[float, str | None]:
    """Match token boundaries; short words/codes never use substring fuzziness."""
    words, actual = _tokens(term), _tokens(value)
    if not words:
        return 0.0, None
    scores, evidence = [], []
    for word in words:
        if word in actual:
            scores.append(1.0)
            evidence.append(word)
            continue
        similar = [(SequenceMatcher(None, word, candidate).ratio(), candidate) for candidate in actual
                   if word.isalpha() and candidate.isalpha() and len(word) >= 5 and len(candidate) >= 5
                   and abs(len(word) - len(candidate)) <= (0 if same_length_fuzzy else 2)]
        score, candidate = max(similar, default=(0.0, ''))
        if score < .80:
            return 0.0, None
        scores.append(.65)
        evidence.append(candidate)
    return sum(scores) / len(scores), ' '.join(evidence)


def _negated(term: str, text: str) -> bool:
    normalized = normalized_text(text)
    value = normalized_text(term)
    return bool(value and re.search(rf'\b(?:no|non|not|without)\s+(?:\w+\s+)?{re.escape(value)}\b', normalized))


def _feature_match(term: str, text: str):
    if re.search(r'\d', term):
        # Model identifiers are not approximate words. Retain suffix letters
        # and zero padding instead of letting token stemming erase identity.
        found = re.search(rf'(?<!\w){re.escape(normalized_text(term))}(?!\w)', normalized_text(text))
        return (1.0, term) if found else (0.0, None)
    return _term_match(term, text)


def _feature_sources(feature: str, item: dict) -> tuple[float, dict | None]:
    fields = [(key, str(item.get(key) or '')) for key in ('name', 'description', 'brand', 'manufacturer')]
    fields.extend((f'explicit_attributes.{key}', str(value)) for key, value in item.get('explicit_attributes', {}).items())
    best, source = 0.0, None
    for field, value in fields:
        alignment, matched = _feature_match(feature, value)
        if alignment <= best or _negated(feature, value):
            continue
        locations = []
        for word in str(matched or '').split():
            found = re.search(rf'(?<!\w){re.escape(word)}s?(?!\w)', value, re.I)
            if found:
                locations.append(found.span())
        start = min((span[0] for span in locations), default=0)
        end = max((span[1] for span in locations), default=len(value))
        best, source = alignment, {'key': f'feature:{feature}', 'value': feature, 'field': field,
                                   'text': value[start:end], 'span': [start, end]}
    return best, source


def _accessory_hit(item: dict, terms: list[str]) -> str | None:
    for term in terms:
        score, _ = _term_match(term, item['name'], same_length_fuzzy=True)
        if not score:
            continue
        # An explicitly included component does not replace the main device:
        # "device with controller" differs from "device controller".
        name = normalized_text(item['name'])
        target = normalized_text(term)
        position = name.find(target)
        if position >= 0 and re.search(r'\b(?:with|including|includes)\b', name[:position]):
            continue
        return term
    return None


def _assess_item(item: dict, requirement: dict) -> dict:
    wanted_sku = str(requirement.get('sku') or '').strip()
    if wanted_sku and item['sku'].casefold() != wanted_sku.casefold():
        return {'rejected': 'sku_mismatch'}
    specs, features = _requirement_specs(requirement)
    name = str(requirement.get('product') or requirement.get('name') or requirement.get('category') or '')
    category_terms = list(dict.fromkeys(_terms(requirement.get('category_terms') or requirement.get('search_terms'))
                                       + _terms(requirement.get('category')) + [name]))
    name_matches = [(*_term_match(term, item['name']), term) for term in category_terms if term]
    category_score, category_evidence, matched_category_term = max(name_matches, default=(0.0, None, ''))
    if not category_score:
        description_matches = [(*_term_match(term, item['description']), term) for term in category_terms if term]
        category_score, category_evidence, matched_category_term = max(description_matches, default=(0.0, None, ''))
        category_score *= .5
    if not category_score and not wanted_sku:
        return {'rejected': 'product_identity_mismatch'}
    for term in _terms(requirement.get('excluded_terms')):
        if _term_match(term, item['name'] + ' ' + item['description'])[0] and not _negated(term, item['name'] + ' ' + item['description']):
            return {'rejected': f'excluded_term:{term}'}
    accessory_terms = _terms(requirement.get('accessory_terms'))
    accessory = _accessory_hit(item, accessory_terms)
    if accessory and requirement.get('allow_accessories') is not True:
        return {'rejected': f'accessory:{accessory}'}
    missing, matched, explanations = [], {}, []
    if wanted_sku and name and not category_score:
        missing.append('product_identity_confirmation')
    score = 5 * category_score + (10 if wanted_sku else 0)
    structured = {normalized_text(str(key)).replace('colour', 'color'): value for key, value in item.get('explicit_attributes', {}).items()}
    attributes = requirement.get('attributes') or {}
    if isinstance(attributes, dict):
        for raw_key, expected in attributes.items():
            if str(raw_key).lower() in _KEYS or expected in (None, ''):
                continue
            actual = structured.get(normalized_text(str(raw_key)).replace('colour', 'color'))
            if actual in (None, ''):
                if isinstance(expected, bool):
                    missing.append(f'attribute:{raw_key}')
                continue
            if isinstance(expected, bool) or isinstance(actual, bool):
                if _boolean(expected) != _boolean(actual):
                    return {'rejected': f'attribute_mismatch:{raw_key}'}
                continue
            expected_values = expected if isinstance(expected, list) else [expected]
            actual_values = actual if isinstance(actual, list) else [actual]
            if not any(normalized_text(str(wanted)) == normalized_text(str(found)) or (
                       not re.search(r'\d', str(wanted) + str(found)) and _term_match(str(wanted), str(found))[0])
                       for wanted in expected_values for found in actual_values):
                return {'rejected': f'attribute_mismatch:{raw_key}'}
    for key, values in specs.items():
        excluded = item.get('excluded_specs', {}).get(key) or []
        values = [requested for requested in values if not any(_excluded_spec(value, requested) for value in excluded)]
        if not values:
            return {'rejected': f'negated_spec:{key}'}
        actual = item['specs'].get(key) or []
        if not actual:
            missing.append(key)
            continue
        # Multiple requested values express alternatives; multiple source
        # values can be ranges/options explicitly written on the item.
        if not any(_compatible(value, requested) for value in actual for requested in values):
            return {'rejected': f'spec_mismatch:{key}'}
        if key in item.get('spec_conflicts', []) or any(not any(_compatible(value, requested) for requested in values) for value in actual):
            missing.append(f'ambiguous_spec:{key}')
        matched[key] = [evidence for evidence in item['spec_evidence'] if evidence['key'] == key and not evidence.get('negated')]
        score += 2
    for feature in features:
        source = ' '.join([item['name'], item['description'], item['brand'], item['manufacturer'],
                           ' '.join(str(value) for value in item.get('explicit_attributes', {}).values())])
        alignment, evidence = _feature_sources(feature, item)
        if _negated(feature, source):
            return {'rejected': f'negated_feature:{feature}'}
        if not alignment:
            missing.append(f'feature:{feature}')
        else:
            explanations.append({'requirement': feature, **evidence, 'match': 'exact' if alignment == 1 else 'spelling_variant'})
            score += alignment
            if alignment < 1:
                missing.append(f'confirm_spelling:{feature}')
    for term in _terms(requirement.get('required_terms')):
        if not _term_match(term, item['name'] + ' ' + item['description'])[0]:
            missing.append(f'term:{term}')
    if item['category']:
        if not any(_term_match(term, item['category'])[0] for term in category_terms):
            excluded_categories = _terms(requirement.get('excluded_categories')) + accessory_terms
            if item['category_is_product_type'] or any(_term_match(term, item['category'])[0] for term in excluded_categories):
                return {'rejected': 'explicit_category_mismatch'}
            if not wanted_sku and not accessory_terms:
                missing.append('product_category_confirmation')
    elif not wanted_sku and not accessory_terms:
        # A common word in an unclassified name is a retrieval candidate, not
        # proof that the item is a complete device rather than a component.
        missing.append('product_category_confirmation')
    if category_score < 1 and not wanted_sku:
        missing.append('product_name_confirmation')
    budget = _number(requirement.get('budget_max') or requirement.get('budget'))
    if budget is not None and item['price_status'] == 'known' and item['rate'] > budget:
        return {'rejected': 'budget_exceeded'}
    if item['price_status'] != 'known':
        missing.append('selling_price_confirmation')
    if item.get('sellability_status') == 'unknown':
        missing.append('selling_eligibility_confirmation')
    quantity = _number(requirement.get('quantity')) or 1
    stock = item['available_stock'] if item['available_stock'] is not None else item['stock_on_hand']
    if item['availability_status'] == 'needs_review':
        missing.append('stock_confirmation')
    elif item['availability_status'] == 'out_of_stock':
        missing.append('out_of_stock')
    elif item['availability_status'] != 'not_tracked' and stock < quantity:
        missing.append('requested_quantity_confirmation')
    availability_requested = str(requirement.get('availability') or '').strip().lower()
    if availability_requested in {'in_stock', 'in stock', 'available_now', 'immediate'}:
        if item['availability_status'] == 'not_tracked':
            missing.append('availability_confirmation')
        elif stock is None or stock < quantity:
            return {'rejected': 'required_stock_unavailable'}
    score += .5 if item['availability_status'] == 'in_stock' else 0
    score -= .5 * len(missing)
    return {'match_status': 'needs_confirmation' if missing else 'verified', 'missing_specs': list(dict.fromkeys(missing)),
            'matched_specs': matched, 'feature_evidence': explanations,
            'identity_evidence': {'term': matched_category_term, 'source_text': category_evidence,
                                  'match': 'exact' if category_score == 1 else 'spelling_variant' if category_score else 'sku'},
            'match_score': round(score, 4)}


def _clarifications(requirement_id: str, product: str, missing: Iterable[str], *, no_match=False) -> list[dict]:
    suggestions = []
    messages = {
        'product_category_confirmation': 'Confirm that this is the requested complete product rather than an accessory or replacement part.',
        'selling_price_confirmation': 'Confirm the current selling price; a zero or missing rate is not a free item.',
        'stock_confirmation': 'Verify live stock before promising availability; this item has missing or negative stock.',
        'out_of_stock': 'Check replenishment or offer an alternative; this item currently reports no stock.',
        'requested_quantity_confirmation': 'Confirm whether the full requested quantity can be supplied.',
        'availability_confirmation': 'Confirm when this untracked item or service can be supplied; the catalogue does not track its stock.',
        'conflicting_inventory_rows': 'Inventory records disagree; verify the current price and item details before selection.',
        'product_name_confirmation': 'Check the spelling or model identity against the customer requirement.',
    }
    for key in dict.fromkeys(missing):
        question = messages.get(key, f'Confirm {key.removeprefix("feature:").replace("_", " ")} for the requested {product}; the catalogue does not verify it.')
        suggestions.append({'id': f'{requirement_id}:{key}', 'requirement_id': requirement_id, 'product': product,
                            'reason': key, 'question': question, 'missing_fields': [key], 'evidence': [], 'audience': 'agent'})
    if no_match:
        suggestions.append({'id': f'{requirement_id}:no_verified_candidate', 'requirement_id': requirement_id, 'product': product,
                            'reason': 'no_verified_candidate', 'missing_fields': ['product_identity'], 'evidence': [],
                            'question': f'Clarify the type, specifications or model for {product}; no candidate meets the known constraints.', 'audience': 'agent'})
    return suggestions


def _spec_label(key: str, values: list) -> str:
    label, unit = _SPEC_LABELS.get(key, (key.replace('_', ' ').capitalize(), ''))
    rendered = []
    for value in values:
        if isinstance(value, dict):
            rendered.append(f"{value['min']:g}-{value['max']:g}")
        else:
            rendered.append(f'{value:g}' if isinstance(value, (int, float)) else str(value))
    return f'{label}: {", ".join(rendered)} {unit}'.strip()


def match_catalogue(items: Iterable[dict], context: dict, query_terms: Any = None) -> dict:
    """Validate and rank supplied candidates without provider calls or CSV imports.

    Dynamic requirement fields include category_terms (synonyms), accessory_terms,
    excluded_terms, required_terms, required_specs, attributes, features and sku.
    query_terms can be an ID/product-keyed mapping of additional category aliases.
    Explicit mismatches are excluded; absent evidence remains needs_confirmation.
    """
    unique, duplicate_conflicts = {}, set()
    for raw in items:
        item = normalize_item(raw)
        if not item:
            continue
        identity = item['item_id']
        if identity in unique:
            prior = unique[identity]
            # Location exports repeat global stock. Never add those quantities.
            if any(prior.get(key) != item.get(key) for key in ('rate', 'stock_on_hand', 'available_stock', 'sku', 'status')):
                duplicate_conflicts.add(identity)
            continue
        unique[identity] = item
    requirements = context.get('customer_requirements') or context.get('requirements') or []
    requirements = [dict(row) for row in requirements if isinstance(row, dict) and not row.get('excluded') and row.get('status') != 'removed']
    if not requirements and context.get('focal_product'):
        requirements = [{'product': context['focal_product']}]
    if len(requirements) == 1:
        requirement = requirements[0]
        requirement['features'] = _terms(requirement.get('features')) + _terms(context.get('preferred_features'))
        requirement['brands'] = _terms(requirement.get('brands')) + _terms(context.get('brand_preferences'))
        requirement.setdefault('budget_max', context.get('budget_max'))
        requirement.setdefault('availability', context.get('availability_requirement'))
    recommendations, assessments, clarification = {}, [], []
    for index, requirement in enumerate(requirements):
        product = str(requirement.get('product') or requirement.get('name') or requirement.get('category') or requirement.get('sku') or 'requested item')
        requirement_id = str(requirement.get('id') or requirement.get('requirement_id') or f'requirement-{index + 1}')
        additional = ((query_terms.get(requirement_id) or query_terms.get(product)) if isinstance(query_terms, dict)
                      else query_terms if len(requirements) == 1 else None)
        requirement['category_terms'] = _terms(requirement.get('category_terms')) + _terms(additional)
        outcomes, rejections = [], Counter()
        for item in unique.values():
            if item['status'] not in {'active', 'enabled'} or item['can_be_sold'] is not True:
                continue
            replacement = unique.get(item.get('replacement_item_id'))
            if replacement and replacement['status'] == 'active' and replacement['can_be_sold']:
                continue
            result = _assess_item(item, requirement)
            if 'rejected' in result:
                rejections[result['rejected']] += 1
                continue
            if item['item_id'] in duplicate_conflicts or _terms(item.get('catalogue_conflicts')):
                result['missing_specs'].append('conflicting_inventory_rows')
                result['match_status'] = 'needs_confirmation'
            result.update(requirement_id=requirement_id, product=product)
            outcomes.append((item, result))
        outcomes.sort(key=lambda pair: (pair[1]['match_status'] == 'verified', pair[1]['match_score']), reverse=True)
        chosen = outcomes[:12]
        for item, result in chosen:
            existing = recommendations.setdefault(item['item_id'], {**item, 'matched_requirements': [], 'missing_specs': [],
                                                                    'match_status': result['match_status'], 'match_score': result['match_score'],
                'matched_specs': [], 'match_evidence': [], 'conflicts': _terms(item.get('catalogue_conflicts')),
                'availability': {'status': item['availability_status'], 'basis': item['availability_basis'],
                                 'stock_on_hand': item['stock_on_hand'], 'available_stock': item['available_stock']}})
            existing['matched_requirements'].append(result)
            existing['missing_specs'] = list(dict.fromkeys(existing['missing_specs'] + result['missing_specs']))
            existing['match_score'] = max(existing['match_score'], result['match_score'])
            for key, sources in result['matched_specs'].items():
                display = _spec_label(key, item['specs'].get(key) or [])
                if display not in existing['matched_specs']:
                    existing['matched_specs'].append(display)
                existing['match_evidence'].extend(source for source in sources if source not in existing['match_evidence'])
            for source in result['feature_evidence']:
                if source['requirement'] not in existing['matched_specs']:
                    existing['matched_specs'].append(source['requirement'])
                if source not in existing['match_evidence']:
                    existing['match_evidence'].append(source)
            if item['item_id'] in duplicate_conflicts or existing['conflicts']:
                existing['conflicts'] = list(dict.fromkeys(existing['conflicts'] + ['Inventory records disagree; verify the current item details.']))
                existing['price_status'] = 'confirmation_required'
                if existing['availability_status'] != 'not_tracked':
                    existing['availability_status'] = 'needs_review'
                    existing['availability']['status'] = 'needs_review'
            if result['match_status'] == 'needs_confirmation':
                existing['match_status'] = 'needs_confirmation'
            existing['match_reason'] = ('Matches the requested category and stated specifications.' if existing['match_status'] == 'verified'
                                        else 'Candidate from catalogue text; confirm the highlighted details before selection.')
        missing = list(dict.fromkeys(reason for _, result in chosen[:3] for reason in result['missing_specs']))
        verified = sum(result['match_status'] == 'verified' for _, result in outcomes)
        status = 'matched' if verified else 'needs_confirmation' if chosen else 'no_match'
        assessments.append({'requirement_id': requirement_id, 'product': product, 'status': status,
                            'id': requirement.get('id') or requirement_id, 'sku': requirement.get('sku'),
                            'candidate_count': len(outcomes), 'verified_count': verified, 'missing_specs': missing,
                            'attributes': requirement.get('attributes') or {}, 'required_specs': requirement.get('required_specs') or {},
                            'quantity': requirement.get('quantity'),
                            'rejected_reasons': dict(rejections)})
        clarification.extend(_clarifications(requirement_id, product, missing, no_match=not chosen))
    # Interleave requirements so a broad first category cannot hide another
    # requested product. Each unique item is returned once.
    ranked = sorted(recommendations.values(), key=lambda item: (item['match_status'] == 'verified', item['match_score']), reverse=True)
    ordered, seen = [], set()
    for depth in range(12):
        for assessment in assessments:
            group = [item for item in ranked if any(row['requirement_id'] == assessment['requirement_id'] for row in item['matched_requirements'])]
            if depth < len(group) and group[depth]['item_id'] not in seen:
                ordered.append(group[depth])
                seen.add(group[depth]['item_id'])
    return {'items': ordered[:24], 'requirements': assessments, 'clarification_suggestions': clarification,
            'scoring': 'deterministic_retrieval_score_not_probability', 'catalogue_item_count': len(unique)}
