"""WebSocket transport for transient workflow audio subscriptions."""

import asyncio
from queue import Empty
from urllib.parse import urlsplit

from anyio import CancelScope
from fastapi import WebSocket, WebSocketDisconnect


async def stream_workflow_audio(websocket: WebSocket, workflow_id: str):
    origin = websocket.headers.get('origin')
    origin_url = urlsplit(origin or '')
    expected_scheme = 'https' if websocket.url.scheme == 'wss' else 'http'
    if origin_url.scheme != expected_scheme or origin_url.netloc != websocket.headers.get('host'):
        await websocket.close(code=1008, reason='Same-origin audio listener required')
        return
    runtime = websocket.app.state.backend_runtime
    record = runtime.get_run(workflow_id)
    if record is None:
        await websocket.close(code=1008, reason='Workflow not found')
        return
    with record.lock:
        workflow = record.workflow
        available = record.kind == 'microphone' and record.status == 'recording'
    if not available or not callable(getattr(workflow, 'subscribe_audio', None)):
        await websocket.close(code=1008, reason='Live audio requires an active microphone workflow')
        return
    try:
        listener = workflow.subscribe_audio()
    except ValueError:
        await websocket.close(code=1008, reason='Audio subscription unavailable')
        return

    async def send():
        await websocket.send_json({'sample_rate': 16000, 'channels': 1, 'format': 'pcm_s16le'})
        while True:
            with record.lock:
                if record.status != 'recording' or record.workflow is not workflow:
                    return
            try:
                packet = await asyncio.to_thread(listener.get, True, 0.25)
            except Empty:
                with record.lock:
                    if record.status != 'recording' or record.workflow is not workflow:
                        return
                continue
            if packet is None:
                return
            await asyncio.wait_for(websocket.send_bytes(packet), timeout=2)

    async def receive():
        while True:
            message = await websocket.receive()
            if message['type'] == 'websocket.disconnect':
                return

    tasks = []
    try:
        await websocket.accept()
        tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, asyncio.TimeoutError, asyncio.CancelledError, RuntimeError, OSError):
        pass
    finally:
        workflow.unsubscribe_audio(listener)
        for task in tasks:
            task.cancel()
        with CancelScope(shield=True):
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await websocket.close()
            except (RuntimeError, WebSocketDisconnect, OSError):
                pass
