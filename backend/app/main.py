from datetime import datetime

from fastapi import FastAPI, Query
from pydantic import BaseModel

from app.db import get_connection

ROW_FIELDS = ("word", "detected_at", "model", "session_id")

app = FastAPI()


class DetectionEvent(BaseModel):
    word: str
    detected_at: datetime
    model: str
    session_id: str


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
            INSERT INTO detection_events (word, detected_at, model, session_id)
            VALUES (%s, %s, %s, %s)
            """,
            (event.word, event.detected_at, event.model, event.session_id),
        )
    return {"status": "stored"}


def _build_filter(
    word: str | None, from_: datetime | None, to: datetime | None, session_id: str | None
) -> tuple[str, list]:
    clauses = []
    params: list = []
    if word is not None:
        clauses.append("word = %s")
        params.append(word)
    if from_ is not None:
        clauses.append("detected_at >= %s")
        params.append(from_)
    if to is not None:
        clauses.append("detected_at <= %s")
        params.append(to)
    if session_id is not None:
        clauses.append("session_id = %s")
        params.append(session_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


@app.get("/counts")
def count_events(
    word: str | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
) -> dict:
    where, params = _build_filter(word, from_, to, session_id=None)
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM detection_events {where}", params
        ).fetchone()
    return {"count": row[0]}


@app.get("/events")
def list_events(
    word: str | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    session_id: str | None = None,
) -> list[dict]:
    where, params = _build_filter(word, from_, to, session_id)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT word, detected_at, model, session_id FROM detection_events {where} "
            "ORDER BY detected_at",
            params,
        ).fetchall()
    return [dict(zip(ROW_FIELDS, row)) for row in rows]
