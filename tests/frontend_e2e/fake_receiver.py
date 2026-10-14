"""Local receiver used to observe real outbound destination HTTP requests."""

from collections import deque

from fastapi import FastAPI, Request, Response

app = FastAPI()
records: deque[dict] = deque(maxlen=100)


@app.post("/success")
async def receive_success(request: Request):
    records.append({"path": "/success", "headers": dict(request.headers), "body": await request.json()})
    return Response(status_code=204)


@app.post("/fail")
async def receive_failure(request: Request):
    records.append({"path": "/fail", "headers": dict(request.headers), "body": await request.json()})
    return Response(status_code=503)


@app.get("/records")
def get_records():
    return {"records": list(records)}


@app.post("/reset")
def reset_records():
    records.clear()
    return {"status": "ok"}
