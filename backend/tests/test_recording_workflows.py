from backend.recorder.processing import processing_window_seconds


def test_walkin_processing_keeps_thirty_second_window():
    assert processing_window_seconds("walkin-audio", 180) == 30


def test_sessions_processing_uses_configured_three_minute_window():
    assert processing_window_seconds("session-audio", 180) == 180


def test_unknown_bucket_does_not_become_visitor_workflow():
    assert processing_window_seconds(None, 180) == 180
