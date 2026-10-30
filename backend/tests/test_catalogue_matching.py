"""Inventory facts and typed constraints win over lexical or semantic similarity."""
import csv
import os
from pathlib import Path

import pytest

from app.catalogue_matching import match_catalogue, normalize_item


def item(name, identity='item-1', **fields):
    return {'item_id': identity, 'name': name, 'rate': 100, 'stock_on_hand': 20,
            'status': 'active', 'can_be_sold': True, **fields}


def requirement(product, **fields):
    return {'customer_requirements': [{'id': 'request-1', 'product': product, **fields}]}


def test_csv_row_normalizes_source_facts_without_inventing_brand_or_specs():
    raw = {'Item ID': '123', 'Item Name': 'Example 10A DP 6KA TYPE C MCB', 'SKU': 'MCB-1',
           'Rate': 'INR 1,025.00', 'Stock On Hand': '0.00', 'Status': 'Active', 'Sellable': 'true',
           'Usage unit': 'Nos', 'Brand': '', 'Description': ''}
    result = normalize_item(raw)
    assert result['rate'] == 1025 and result['stock_on_hand'] == 0
    assert result['availability_status'] == 'out_of_stock' and result['price_status'] == 'known'
    assert result['brand'] == '' and result['description'] == ''
    assert result['specs'] == {'current_a': [10.0], 'breaking_capacity_ka': [6.0], 'poles': [2], 'breaker_curve': ['C']}
    for evidence in result['spec_evidence']:
        start, end = evidence['span']
        assert raw['Item Name'][start:end] == evidence['text']
    assert result['source_facts']['Item ID'] == '123'


@pytest.mark.parametrize('requested,candidate', [
    ({'current_a': 10}, '16A DP MCB'),
    ({'current_a': 1}, '10A DP MCB'),
    ({'power_hp': 1}, 'Motor 10HP'),
    ({'power_kw': 1.5}, 'Motor 15kW'),
    ({'phases': 1}, 'Motor 3PH'),
    ({'poles': 2}, '16A SP MCB'),
    ({'voltage_v': 240}, 'Motor 24V DC'),
    ({'size_mm': 1200}, 'Fan 120mm'),
    ({'area_sqmm': 1.5}, 'Cable 15SQMM'),
    ({'speed_rpm': 1500}, 'Motor 150RPM'),
    ({'width_mm': 20}, 'Rail width 25mm'),
    ({'breaker_curve': 'C'}, '10A DP 6KA TYPE B MCB'),
    ({'cores': 4}, 'Cable 3 Core 10SQMM'),
    ({'ip_rating': 'IP55'}, 'Motor IP54'),
    ({'efficiency_class': 'IE3'}, 'Motor IE2'),
    ({'capacitance_uf': 10}, 'Motor capacitor 100uF'),
])
def test_explicit_technical_mismatches_are_never_suggested(requested, candidate):
    product = 'MCB' if 'MCB' in candidate else 'motor' if 'Motor' in candidate else 'fan' if 'Fan' in candidate else 'cable' if 'Cable' in candidate else 'rail'
    result = match_catalogue([item(candidate)], requirement(product, required_specs=requested, accessory_terms=['spare']))
    assert result['items'] == []
    assert result['requirements'][0]['status'] == 'no_match'


def test_missing_spec_is_confirmation_required_instead_of_invented_or_hard_rejected():
    result = match_catalogue([item('Ceiling fan 1200mm', category='ceiling fan')],
                             requirement('ceiling fan', required_specs={'size_mm': 1200}, features=['BLDC', 'quiet']))
    assert len(result['items']) == 1
    result = result['items'][0]
    assert result['match_status'] == 'needs_confirmation'
    assert result['missing_specs'] == ['feature:BLDC', 'feature:quiet']
    assert result['specs']['size_mm'] == [1200]
    assert 'BLDC' not in result['name']


def test_exact_sku_is_required_and_cannot_be_replaced_by_similar_name_or_substring():
    rows = [item('Same motor model', 'correct', sku='MOD-001'),
            item('Same motor model', 'wrong', sku='MOD-0010'), item('MOD-001 motor', 'missing', sku='')]
    result = match_catalogue(rows, requirement('motor', sku='mod-001'))
    assert [row['item_id'] for row in result['items']] == ['correct']
    assert result['items'][0]['match_status'] == 'verified'


def test_dynamic_accessory_exclusions_separate_products_from_components():
    rows = [item('Ceiling fan 1200mm', 'fan'), item('Ceiling fan regulator', 'regulator'),
            item('Ceiling fan regukator', 'misspelled-regulator'), item('Ceiling fan blade', 'blade'),
            item('Ceiling fan with remote controller', 'included-controller')]
    result = match_catalogue(rows, requirement('ceiling fan', accessory_terms=['regulator', 'blade', 'controller']))
    assert {row['item_id'] for row in result['items']} == {'fan', 'included-controller'}
    assert all(row['match_status'] == 'verified' for row in result['items'])


def test_regular_is_not_fuzzily_misread_as_regulator():
    result = match_catalogue([item('Example ceiling fan regular')],
                             requirement('ceiling fan', accessory_terms=['regulator']))
    assert len(result['items']) == 1


def test_pump_and_motor_accessories_are_rejected_using_dynamic_type_terms():
    rows = [item('Submersible pump 1HP', 'pump'), item('Submersible pump cable', 'cable'),
            item('Submersible pump controller', 'controller'), item('Submersible pump with controller', 'bundle')]
    result = match_catalogue(rows, requirement('submersible pump', accessory_terms=['cable', 'controller']))
    assert {row['item_id'] for row in result['items']} == {'pump', 'bundle'}
    result = match_catalogue([item('1HP bare motor', 'motor'), item('Motor flange 1HP', 'flange'),
                               item('Motorised changeover switch 100A', 'motorized')],
                             requirement('motor', accessory_terms=['flange', 'operator', 'switch']))
    assert [row['item_id'] for row in result['items']] == ['motor']


def test_unclassified_common_word_match_cannot_claim_verified_device_identity():
    result = match_catalogue([item('Fan regulator')], requirement('fan'))
    assert result['items'][0]['match_status'] == 'needs_confirmation'
    assert 'product_category_confirmation' in result['items'][0]['missing_specs']
    assert result['clarification_suggestions'][0]['audience'] == 'agent'


def test_spelling_variants_are_retrieved_with_visible_confirmation():
    rows = [item('Example Celeing Fan 1200mm', 'typo'), item('Fancy batten holder', 'false-substring')]
    result = match_catalogue(rows, requirement('ceiling fan', accessory_terms=['regulator']))
    assert [row['item_id'] for row in result['items']] == ['typo']
    assert 'product_name_confirmation' in result['items'][0]['missing_specs']


@pytest.mark.parametrize('stock,expected', [(None, 'needs_review'), (-3, 'needs_review'), (0, 'out_of_stock'), (4, 'in_stock')])
def test_stock_is_reported_as_source_fact_and_not_invented(stock, expected):
    row = normalize_item(item('Example fan', stock_on_hand=stock))
    assert row['availability_status'] == expected
    assert row['stock_on_hand'] == stock


def test_zero_price_needs_confirmation_unless_inventory_explicitly_marks_item_free():
    rows = [item('Fan', 'zero', rate=0, category='fan'),
            item('Fan', 'free', rate=0, is_free=True, category='fan'),
            item('Fan', 'negative', rate=-1, category='fan')]
    result = match_catalogue(rows, requirement('fan'))
    records = {row['item_id']: row for row in result['items']}
    assert records['zero']['price_status'] == 'confirmation_required'
    assert records['negative']['match_status'] == 'needs_confirmation'
    assert records['free']['price_status'] == 'known' and records['free']['match_status'] == 'verified'


def test_location_duplicates_never_sum_stock_and_conflicting_rows_require_review():
    rows = [item('Fan', 'same', stock_on_hand=3, category='fan', location_name='Location A'),
            item('Fan', 'same', stock_on_hand=3, category='fan', location_name='Location B')]
    result = match_catalogue(rows, requirement('fan', quantity=5))
    assert len(result['items']) == 1 and result['items'][0]['stock_on_hand'] == 3
    assert 'requested_quantity_confirmation' in result['items'][0]['missing_specs']
    rows[1]['stock_on_hand'] = 9
    result = match_catalogue(rows, requirement('fan'))
    assert 'conflicting_inventory_rows' in result['items'][0]['missing_specs']


def test_inactive_unsellable_and_explicitly_replaced_records_are_not_suggested():
    rows = [item('Fan', 'inactive', status='Inactive'), item('Fan', 'unsellable', can_be_sold=False),
            item('Fan', 'old', replacement_item_id='new'), item('Fan', 'new', category='fan')]
    result = match_catalogue(rows, requirement('fan'))
    assert [row['item_id'] for row in result['items']] == ['new']


def test_units_and_specs_use_distinct_dimensions_and_exact_evidence():
    row = normalize_item(item('Cable 1.5SQ.MM 240V; rail 2cm wide; Motor 750W single phase 45-50RPM'))
    assert row['specs']['area_sqmm'] == [1.5]
    assert row['specs']['size_mm'] == [20]
    assert row['specs']['power_kw'] == [.75]
    assert row['specs']['phases'] == [1]
    assert row['specs']['speed_rpm'] == [{'min': 45, 'max': 50}]
    row = normalize_item(item('Fan 1200mm'))
    assert 'area_sqmm' not in row['specs']
    assert normalize_item(item('1200mm 48C FAN'))['specs'].get('cores') is None
    assert normalize_item(item('Cable 120SQMM x 3.5C'))['specs']['cores'] == [3.5]


def test_structured_variant_attributes_are_dynamic_source_evidence():
    row = item('Example motor', category='motor', specifications={'power_hp': '1HP', 'phases': 'single phase', 'colour': 'Blue'})
    result = match_catalogue([row], requirement('motor', attributes={'power': '1HP', 'phase': 'single phase', 'colour': 'Blue'}))
    assert result['items'][0]['match_status'] == 'verified'
    assert result['items'][0]['specs']['power_hp'] == [1]
    assert result['items'][0]['explicit_attributes']['colour'] == 'Blue'
    evidence = next(row for row in result['items'][0]['match_evidence'] if row['key'] == 'feature:Blue')
    assert evidence['field'] == 'explicit_attributes.colour' and evidence['text'] == 'Blue'
    assert evidence['span'] == [0, 4]


def test_explicitly_negated_features_cannot_match_positive_request():
    result = match_catalogue([item('Ceiling fan non-BLDC', category='ceiling fan')], requirement('ceiling fan', features=['BLDC']))
    assert result['items'] == []


@pytest.mark.parametrize(('description', 'spec'), [
    ('Not suitable for 3 phase installations.', {'phases': 3}),
    ('Does not support 3 phase installations.', {'phases': 3}),
    ("Doesn't support 3 phase supply.", {'phases': 3}),
    ('Incompatible with 3 phase supply.', {'phases': 3}),
    ('Not rated for 230 V operation.', {'voltage_v': 230}),
    ('230 V AC operation is not supported.', {'voltage_v': 230}),
    ('Not rated for 200-240 V operation.', {'voltage_v': 230}),
    ('Does not support 1 HP or 2 HP loads.', {'power_hp': 2}),
])
def test_negated_numeric_description_specs_cannot_prove_a_positive_requirement(description, spec):
    row = item('Industrial motor', category='motor', description=description)
    normalized = normalize_item(row)
    assert normalized['excluded_specs']
    assert any(source.get('negated') for source in normalized['spec_evidence'])
    result = match_catalogue([row], requirement('motor', required_specs=spec, accessory_terms=['starter']))
    assert result['items'] == []
    assert result['requirements'][0]['rejected_reasons'][f'negated_spec:{next(iter(spec))}'] == 1


@pytest.mark.parametrize('description', [
    'Supports 1 phase, not 3 phase.',
    'Does not support 3 phase; supports 1 phase.',
    'Not suitable for 3 phase installations, but supports 1 phase.',
    'Not 3 phase, supports 1 phase.',
])
def test_supported_rating_survives_a_separate_negated_rating(description):
    row = item('Industrial motor', category='motor', description=description)
    normalized = normalize_item(row)
    assert normalized['specs']['phases'] == [1]
    assert normalized['excluded_specs']['phases'] == [3]
    positive = match_catalogue([row], requirement('motor', required_specs={'phases': 1}, accessory_terms=['starter']))
    assert positive['items'][0]['match_status'] == 'verified'
    assert positive['items'][0]['matched_specs'] == ['Phases: 1']
    assert not any(source.get('negated') for source in positive['items'][0]['match_evidence'])
    assert match_catalogue([row], requirement('motor', required_specs={'phases': 3}))['items'] == []


def test_not_only_and_no_load_language_does_not_negate_supported_ratings():
    row = normalize_item(item('Industrial motor', description='Not only 1 phase but also 3 phase. No-load speed 1500 RPM.', category='motor'))
    assert row['specs']['phases'] == [1, 3]
    assert row['specs']['speed_rpm'] == [1500]
    assert row['excluded_specs'] == {}
    result = match_catalogue([row], requirement('motor', required_specs={'speed_rpm': 1500}, accessory_terms=['starter']))
    assert result['items'][0]['match_status'] == 'verified'
    assert normalize_item(item('Motor', description='No warranty, no-load speed 1500 RPM.'))['excluded_specs'] == {}


def test_negated_structured_value_and_description_override_claims_of_compatibility():
    row = item('3 phase motor', description='Not suitable for 3 phase installations.', category='motor')
    assert match_catalogue([row], requirement('motor', required_specs={'phases': 3}))['items'] == []
    row = item('Industrial motor', category='motor', specifications={'phases': 'not 3 phase'})
    assert normalize_item(row)['excluded_specs']['phases'] == [3]
    assert match_catalogue([row], requirement('motor', required_specs={'phases': 3}))['items'] == []


def test_alternative_requested_ratings_can_use_an_explicitly_supported_option():
    row = item('Industrial motor', category='motor', description='Supports 1 phase, not 3 phase.')
    result = match_catalogue([row], requirement('motor', required_specs={'phases': [1, 3]}, accessory_terms=['starter']))
    assert result['items'][0]['match_status'] == 'verified'


def test_multiple_requirements_retain_distinct_results_and_per_requirement_constraints():
    rows = [item('Fan 1200mm', 'fan', category='fan'), item('Motor 1HP', 'motor', category='motor')]
    result = match_catalogue(rows, {'customer_requirements': [
        {'id': 'fan-request', 'product': 'fan', 'required_specs': {'size_mm': 1200}},
        {'id': 'motor-request', 'product': 'motor', 'required_specs': {'power_hp': 1}},
    ]})
    assert {row['item_id'] for row in result['items']} == {'fan', 'motor'}
    assert [row['status'] for row in result['requirements']] == ['matched', 'matched']


def test_explicit_category_conflict_cannot_be_overridden_by_keyword_similarity():
    result = match_catalogue([item('Fan regulator', category='regulator', category_is_product_type=True)], requirement('fan'))
    assert result['items'] == []


def test_stale_snapshot_preserves_values_but_requires_fresh_price_and_stock_confirmation():
    canonical = normalize_item(item('Fan', rate=1500, stock_on_hand=10, category='fan'))
    result = match_catalogue([{**canonical, '_catalogue_stale': True}], requirement('fan'))
    candidate = result['items'][0]
    assert candidate['rate'] == 1500 and candidate['stock_on_hand'] == 10
    assert candidate['price_status'] == 'confirmation_required'
    assert candidate['availability_status'] == 'needs_review'
    assert candidate['match_status'] == 'needs_confirmation'


def test_unknown_selling_eligibility_is_not_claimed_confirmed():
    raw = item('Fan', category='fan')
    raw.pop('can_be_sold')
    result = match_catalogue([raw], requirement('fan'))
    assert 'selling_eligibility_confirmation' in result['items'][0]['missing_specs']


@pytest.mark.parametrize('metadata', [
    {'track_inventory': False, 'product_type': 'service'},
    {'product_type': 'digital_service'},
    {'item_type': 'sales', 'product_type': 'goods'},
    {'item_type': 'sales_and_purchases', 'product_type': 'goods'},
])
def test_explicit_untracked_service_and_goods_counters_are_not_stock_promises(metadata):
    raw = item('Installation service', category='service', stock_on_hand=0, available_stock=0, **metadata)
    result = match_catalogue([raw], requirement('service', quantity=10, accessory_terms=['spare']))
    candidate = result['items'][0]
    assert candidate['match_status'] == 'verified'
    assert candidate['availability_status'] == 'not_tracked'
    assert candidate['stock_tracking_status'] == 'not_tracked'
    assert candidate['stock_on_hand'] == candidate['available_stock'] == 0
    assert candidate['availability']['status'] == 'not_tracked'
    assert not {'out_of_stock', 'stock_confirmation', 'requested_quantity_confirmation'} & set(candidate['missing_specs'])
    normalized = normalize_item({**candidate, '_catalogue_stale': True})
    assert normalized['availability_status'] == 'not_tracked'
    assert normalized['price_status'] == 'confirmation_required'


def test_untracked_item_immediate_availability_still_requires_human_confirmation():
    raw = item('Installation service', category='service', product_type='service', track_inventory=False, stock_on_hand=None)
    result = match_catalogue([raw], requirement('service', availability='immediate'))
    assert result['items'][0]['availability_status'] == 'not_tracked'
    assert result['items'][0]['match_status'] == 'needs_confirmation'
    assert 'availability_confirmation' in result['items'][0]['missing_specs']


@pytest.mark.parametrize('metadata', [
    {'track_inventory': True, 'product_type': 'service'},
    {'track_inventory': False, 'item_type': 'inventory'},
    {'product_type': 'goods'},
])
def test_tracked_or_unknown_goods_still_cannot_claim_zero_stock_available(metadata):
    normalized = normalize_item(item('Example goods', category='goods', stock_on_hand=0, **metadata))
    assert normalized['availability_status'] == 'out_of_stock'
    result = match_catalogue([normalized], requirement('goods', availability='in_stock'))
    assert result['items'] == []


def test_index_duplicate_conflicts_survive_staging_deduplication_into_match_review():
    conflict = 'Inventory rows disagree about available stock and price.'
    raw = item('Ceiling fan', category='ceiling fan', catalogue_conflicts=[conflict],
               duplicate_source_evidence={'rate': [100, 200], 'stock_on_hand': [5, 10]})
    candidate = match_catalogue([raw], requirement('ceiling fan'))['items'][0]
    assert candidate['match_status'] == 'needs_confirmation'
    assert conflict in candidate['conflicts']
    assert 'conflicting_inventory_rows' in candidate['missing_specs']
    assert candidate['price_status'] == 'confirmation_required'
    assert candidate['availability_status'] == 'needs_review'
    assert candidate['duplicate_source_evidence']['rate'] == [100, 200]


def test_organizational_category_does_not_disqualify_a_device_in_a_broader_taxonomy():
    result = match_catalogue([item('Ceiling fan 1200mm', category='Electrical')],
                             requirement('ceiling fan', accessory_terms=['regulator']))
    assert len(result['items']) == 1


def test_width_constraint_cannot_be_satisfied_by_a_different_dimension():
    result = match_catalogue([item('Rail width 25mm, diameter 20mm', category='rail')],
                             requirement('rail', attributes={'width': '20mm'}))
    assert result['items'] == []
    result = match_catalogue([item('Rail 2cm wide', category='rail')], requirement('rail', attributes={'width': '20mm'}))
    assert result['items'][0]['match_status'] == 'verified'


def test_referenced_description_rating_cannot_override_primary_name_rating():
    records = [item('16A DP MCB', description='Use with 10A equipment.', category='MCB')]
    assert match_catalogue(records, requirement('MCB', required_specs={'current_a': 10}))['items'] == []
    result = match_catalogue(records, requirement('MCB', required_specs={'current_a': 16}))
    assert result['items'][0]['match_status'] == 'needs_confirmation'
    assert 'ambiguous_spec:current_a' in result['items'][0]['missing_specs']


def test_fractional_horsepower_is_not_misread_as_its_denominator():
    row = normalize_item(item('Motor 1/2HP'))
    assert row['specs']['power_hp'] == [.5]
    assert match_catalogue([row], requirement('motor', required_specs={'power_hp': 2}))['items'] == []
    result = match_catalogue([row], requirement('motor', required_specs={'power_hp': .5}, accessory_terms=['flange']))
    assert result['items'][0]['match_status'] == 'verified'


def test_slash_voltage_options_do_not_falsely_become_a_continuous_range():
    row = normalize_item(item('Motor 230/415V', category='motor'))
    assert row['specs']['voltage_v'] == [230, 415]
    assert match_catalogue([row], requirement('motor', required_specs={'voltage_v': 300}))['items'] == []
    result = match_catalogue([row], requirement('motor', required_specs={'voltage_v': 230}))
    assert 'ambiguous_spec:voltage_v' in result['items'][0]['missing_specs']


def test_conflicting_structured_color_or_model_is_an_explicit_mismatch():
    row = item('Fan', category='fan', specifications={'colour': 'Blue', 'model': 'MOD-0010'})
    assert match_catalogue([row], requirement('fan', attributes={'color': 'Red'}))['items'] == []
    assert match_catalogue([row], requirement('fan', attributes={'model': 'MOD-001'}))['items'] == []


def test_sku_characters_are_not_invented_into_technical_ratings():
    raw = item('10A Motor MOD-1A', sku='MOD-1A', category='motor')
    normalized = normalize_item(raw)
    assert normalized['specs']['current_a'] == [10]
    assert match_catalogue([raw], requirement('motor', required_specs={'current_a': 1}))['items'] == []


def test_unknown_model_suffix_is_not_verified_from_a_prefix_or_unit_token():
    result = match_catalogue([item('Motor MOD-1', category='motor')], requirement('motor', attributes={'model': 'MOD-1A'}))
    assert 'feature:MOD-1A' in result['items'][0]['missing_specs']


def test_dynamic_search_terms_expand_compound_names_without_runtime_product_lists():
    result = match_catalogue([item('Example SUB PUMPSET 2HP', category='pump')],
                             requirement('submersible pump', category='pump', search_terms=['SUB PUMPSET'], required_specs={'power_hp': 2}))
    assert len(result['items']) == 1


def test_unscoped_search_terms_cannot_make_one_requested_product_match_another():
    rows = [item('Fan', 'fan', category='fan'), item('Motor', 'motor', category='motor')]
    result = match_catalogue(rows, {'customer_requirements': [{'id': 'f', 'product': 'fan'}, {'id': 'm', 'product': 'motor'}]},
                             query_terms=['fan', 'motor'])
    for candidate in result['items']:
        assert len(candidate['matched_requirements']) == 1
        assert candidate['matched_requirements'][0]['product'] == candidate['name'].lower()


@pytest.mark.skipif(not os.environ.get('CATALOGUE_REPLAY_CSV'), reason='Optional user-supplied read-only catalogue replay')
def test_external_csv_replay_uses_real_records_without_importing_or_hardcoding_runtime_catalogue():
    # These IDs are benchmark expectations, not runtime catalogue rules.
    path = Path(os.environ['CATALOGUE_REPLAY_CSV'])
    with path.open(newline='', encoding='utf-8-sig') as handle:
        records = list(csv.DictReader(handle))
    result = match_catalogue(records, requirement('MCB', required_specs={'current_a': 10, 'poles': 2, 'breaker_curve': 'C'},
                                                accessory_terms=['accessory', 'operator', 'contact']))
    selected = {row['item_id']: row for row in result['items']}
    assert '3366620000000045383' in selected
    assert selected['3366620000000045383']['name'] == '10A DP 6KA TYPE C MCB - BA20100C'
    assert all(10 in row['specs'].get('current_a', [10]) and 2 in row['specs'].get('poles', [2]) for row in result['items'])
