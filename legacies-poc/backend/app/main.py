from datetime import datetime

from fastapi import FastAPI, Query
from pydantic import BaseModel

from app.db import get_connection

ROW_FIELDS = ("word", "detected_at", "model", "session_id", "station")

app = FastAPI()


class DetectionEvent(BaseModel):
    word: str
    detected_at: datetime
    model: str
    session_id: str
    # Which labelled microphone this came from. Defaulted rather than required
    # so the CLI and the PoC panel, which have no station, keep working.
    station: str = ""


@app.get("/health")
def health() -> dict:
    with get_connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "db": "ok"}


@app.post("/events", status_code=201)
def create_event(event: DetectionEvent) -> dict:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO detection_events (word, detected_at, model, session_id, station)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (event.word, event.detected_at, event.model, event.session_id, event.station),
        )
    return {"status": "stored"}


def _or_none(value: str | None) -> str | None:
    """Treats an omitted param and an empty-string param the same way.

    Some clients send `?from=&to=` instead of leaving the key out entirely,
    which would otherwise fail datetime parsing rather than mean "no filter".
    """
    return value if value else None


def _parse_datetime(value: str | None) -> datetime | None:
    value = _or_none(value)
    return datetime.fromisoformat(value) if value else None


def _build_filter(
    word: str | None,
    from_: str | None,
    to: str | None,
    session_id: str | None,
    station: str | None = None,
) -> tuple[str, list]:
    clauses = []
    params: list = []
    word = _or_none(word)
    session_id = _or_none(session_id)
    station = _or_none(station)
    from_dt = _parse_datetime(from_)
    to_dt = _parse_datetime(to)
    if word is not None:
        clauses.append("word = %s")
        params.append(word)
    if from_dt is not None:
        clauses.append("detected_at >= %s")
        params.append(from_dt)
    if to_dt is not None:
        clauses.append("detected_at <= %s")
        params.append(to_dt)
    if session_id is not None:
        clauses.append("session_id = %s")
        params.append(session_id)
    if station is not None:
        clauses.append("station = %s")
        params.append(station)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


@app.get("/counts")
def count_events(
    word: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    station: str | None = None,
) -> dict:
    where, params = _build_filter(word, from_, to, session_id=None, station=station)
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM detection_events {where}", params
        ).fetchone()
    return {"count": row[0]}


@app.get("/events")
def list_events(
    word: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    session_id: str | None = None,
    station: str | None = None,
    limit: int = Query(default=500, ge=1, le=10000),
) -> list[dict]:
    """Most recent matching events first.

    The PoC returned every match oldest-first, which was fine for a run that
    lasted minutes. Stations run for weeks, so the operator UI wants the recent
    end and a bound on how much comes back.
    """
    where, params = _build_filter(word, from_, to, session_id, station)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT word, detected_at, model, session_id, station FROM detection_events "
            f"{where} ORDER BY detected_at DESC LIMIT %s",
            [*params, limit],
        ).fetchall()
    return [dict(zip(ROW_FIELDS, row)) for row in rows]
