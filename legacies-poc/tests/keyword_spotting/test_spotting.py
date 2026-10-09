import numpy as np
import pytest

import transcribe
from stations.config import Settings, Station
from stations.engine import Engine


def test_thai_multiple_and_phrase_matches_emit_one_event_per_keyword(monkeypatch):
    reports = []
    monkeypatch.setattr(transcribe, "report_event", lambda *args: reports.append(args) or True)
    events = []
    last = {}

    transcribe.spot_keywords(
        "สวัสดีครับ ขอบคุณครับ สวัสดีอีกครั้ง",
        ["สวัสดี", "ขอบคุณครับ", "ไม่พบ"], last, 3.0,
        model_key="turbo", session_id="session-a", backend_url="http://backend",
        station="Line 1", on_event=events.append, debounce_seconds=4.5,
    )

    assert [event["keyword"] for event in events] == ["สวัสดี", "ขอบคุณครับ"]
    assert [report[1] for report in reports] == ["สวัสดี", "ขอบคุณครับ"]
    assert all(report[2:5] == ("turbo", "session-a", "Line 1") for report in reports)


@pytest.mark.parametrize("text, keywords", [
    ("", ["hello"]),
    ("a sentence without the configured phrase", ["target phrase"]),
    ("target is spoken", []),
])
def test_empty_or_nonmatching_transcripts_emit_nothing(monkeypatch, text, keywords):
    reports = []
    monkeypatch.setattr(transcribe, "report_event", lambda *args: reports.append(args))
    events = []
    transcribe.spot_keywords(
        text, keywords, {}, 0.0, model_key="turbo", session_id="s",
        backend_url="http://backend", on_event=events.append,
    )
    assert events == []
    assert reports == []


def test_english_matching_is_case_insensitive_and_occurrence_count_is_spotting():
    events = []
    transcribe.spot_keywords(
        "HELLO, hello, and Hello again", ["Hello"], {}, 1.0,
        model_key="turbo", session_id="s", backend_url="",
        on_event=events.append, debounce_seconds=4.5,
    )
    assert [event["keyword"] for event in events] == ["Hello"]


def test_debounce_suppresses_overlap_repeat_but_accepts_exact_boundary_and_later(monkeypatch):
    events = []
    monkeypatch.setattr(transcribe, "report_event", lambda *args: True)
    last = {}
    args = dict(text="สวัสดี", keywords=["สวัสดี"], last_alerted=last,
                model_key="turbo", session_id="s", backend_url="",
                on_event=events.append, debounce_seconds=4.5, station="Line 1")
    transcribe.spot_keywords(now=0.0, **args)
    transcribe.spot_keywords(now=4.0, **args)  # adjacent 5s/1s-overlap chunk
    transcribe.spot_keywords(now=4.5, **args)  # boundary is accepted
    transcribe.spot_keywords(now=10.0, **args)
    assert [event["time"] for event in events] == [0.0, 4.5, 10.0]


def test_debounce_state_is_station_local_and_health_counts_emitted_events(monkeypatch):
    reports = []
    monkeypatch.setattr(transcribe, "report_event", lambda *args: reports.append(args) or True)
    one = Station(id="one", label="Line 1", device="mic", keywords=["สวัสดี"],
                  silence_threshold=0.0, chunk_seconds=1.0, overlap_seconds=0.0)
    two = Station(id="two", label="Line 2", device="other", keywords=["สวัสดี"],
                  silence_threshold=0.0, chunk_seconds=1.0, overlap_seconds=0.0)
    events = []
    engine = Engine(Settings(model="turbo", runtime="ctranslate2", stations=[one, two]),
                    on_event=events.append)
    engine.register(one)
    engine.register(two)
    engine._runtime = type("Runtime", (), {"transcribe": lambda self, audio, language: "สวัสดี สวัสดี"})()

    engine._process(engine._runtime, one, np.ones(16_000, dtype="float32"), 0.0)
    engine._process(engine._runtime, one, np.ones(16_000, dtype="float32"), 1.0)
    engine._process(engine._runtime, two, np.ones(16_000, dtype="float32"), 1.0)

    detections = [event for event in events if event["type"] == "keyword"]
    assert [(event["station_id"], event["keyword"]) for event in detections] == [
        ("one", "สวัสดี"), ("two", "สวัสดี")
    ]
    assert engine.health["one"].keywords_found == 1
    assert engine.health["two"].keywords_found == 1
    assert len(reports) == 2


@pytest.mark.xfail(strict=True, reason="AUDIT: report_event ignores HTTP non-success responses")
def test_non_success_backend_response_is_reported_as_not_persisted(monkeypatch, capsys):
    class Response:
        def raise_for_status(self):
            raise transcribe.requests.HTTPError("500 server error")

    monkeypatch.setattr(transcribe.requests, "post", lambda *args, **kwargs: Response())
    transcribe.report_event("http://backend", "hello", "turbo", "s")
    assert "failed to report event" in capsys.readouterr().out


def test_successful_report_uses_endpoint_and_carries_event_metadata(monkeypatch):
    sent = []

    class Response:
        status_code = 201

    monkeypatch.setattr(transcribe.requests, "post",
                        lambda url, **kwargs: sent.append((url, kwargs)) or Response())
    transcribe.report_event("http://backend", "สวัสดี", "turbo", "session", "Line 1")
    assert sent[0][0] == "http://backend/events"
    assert sent[0][1]["json"]["word"] == "สวัสดี"
    assert sent[0][1]["json"]["model"] == "turbo"
    assert sent[0][1]["json"]["session_id"] == "session"
    assert sent[0][1]["json"]["station"] == "Line 1"


def test_local_detection_is_emitted_even_when_backend_rejects_ingest(monkeypatch):
    class Rejected:
        status_code = 503

    monkeypatch.setattr(transcribe.requests, "post", lambda *args, **kwargs: Rejected())
    events = []
    transcribe.spot_keywords(
        "hello", ["hello"], {}, 0.0, model_key="turbo", session_id="s",
        backend_url="http://backend", on_event=events.append,
    )
    assert [event["keyword"] for event in events] == ["hello"]
