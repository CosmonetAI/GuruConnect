"""Regression tests for real-world provider completion order, without provider calls."""
from app.voice_ai.context import VoiceConversationContext
from app.voice_ai.live import analysis_turns, pending_turns, turn_fingerprint


def turn(start, text, **extra):
    return {'id': f'turn-{start}', 'start_seconds': start, 'end_seconds': start + 10,
            'source_text': text, 'english_text': text, **extra}


def completed(*turns):
    return [{'status': 'complete', 'data': {'_live': {
        'turn_fingerprints': [turn_fingerprint(item) for item in turns],
    }}}]


def test_later_correction_waits_for_earlier_translation_then_is_applied_last():
    first = turn(0, 'I need one fan.', english_text=None)
    correction = turn(10, 'Actually make that two fans.')
    # Providers finished the later window first. Neither the polling path nor a
    # webhook may apply "two" now and then apply the delayed old "one" later.
    assert pending_turns([correction, first], []) == []
    first['english_text'] = 'I need one fan.'
    ordered = pending_turns([correction, first], [])
    assert ordered == [first, correction]
    context = VoiceConversationContext()
    for item, quantity in zip(ordered, [1, 2]):
        context.merge_extraction({'requirements': [{'product': 'fan', 'quantity': quantity}]},
                                 {'fingerprint': turn_fingerprint(item)})
    assert context.customer_requirements[0]['quantity'] == 2
    assert pending_turns([correction, first], completed(*ordered)) == []


def test_source_window_hole_blocks_later_text_even_when_it_is_already_translated():
    earlier = turn(0, 'I need one fan.')
    later = turn(20, 'Cancel the fan.', analysis_ready=False)
    assert pending_turns([later, earlier], []) == [earlier]
    assert pending_turns([later, earlier], completed(earlier)) == []
    middle = turn(10, 'Show me a motor too.')
    ready_later = {**later, 'analysis_ready': True}
    assert pending_turns([ready_later, middle, earlier], completed(earlier)) == [middle, ready_later]


def test_processed_history_does_not_allow_skipping_an_untranslated_middle_turn():
    old = turn(0, 'I need a fan.')
    middle = turn(10, 'Make it BLDC.', english_text=None)
    later = turn(20, 'And two motors.')
    assert pending_turns([later, old, middle], completed(old)) == []
    middle['english_text'] = 'Make it BLDC.'
    assert pending_turns([later, old, middle], completed(old)) == [middle, later]


def test_failed_extractions_are_retried_only_within_the_ready_prefix():
    first = turn(0, 'A motor, please.')
    missing = turn(10, 'Something else.', english_text=None)
    later = turn(20, 'A fan too.')
    failed = [{'status': 'failed', 'fingerprint': turn_fingerprint(first)}]
    assert pending_turns([later, missing, first], failed) == [first]


def test_coverage_marks_copies_and_missing_frontier_never_claims_ready_speech():
    first, later = turn(0, 'One fan.'), turn(20, 'Make it two.')
    rows = analysis_turns([first, later], 10 * 16000)
    assert [item['analysis_ready'] for item in rows] == [True, False]
    assert 'analysis_ready' not in first
    assert pending_turns(rows, []) == [rows[0]]
    assert pending_turns(analysis_turns([first, later], None), []) == []


def test_native_independent_source_rows_do_not_block_translated_english_rows():
    native_source = turn(0, 'Source pass text.', english_text=None, provenance={'native_english': True})
    native_english = turn(0, 'English translation.', source_text=None, provenance={'native_english': True}, ordinal=1)
    rows = analysis_turns([native_source, native_english], 10 * 16000)
    assert rows[0]['analysis_skip'] is True
    assert rows[1]['analysis_skip'] is False
    assert pending_turns(rows, []) == [rows[1]]
    # Frontier checks still apply to the native English row itself.
    assert pending_turns(analysis_turns([native_source, native_english], 0), []) == []


def test_a_non_native_source_row_still_blocks_later_translated_speech():
    pending_source = turn(0, 'Needs managed translation.', english_text=None)
    later = turn(10, 'Later translated speech.')
    rows = analysis_turns([pending_source, later], 20 * 16000)
    assert rows[0]['analysis_skip'] is False
    assert pending_turns(rows, []) == []
