"""Local receiver used to observe real outbound destination HTTP requests."""

from collections import deque
import json
import os
import threading
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response

app = FastAPI()
records: deque[dict] = deque(maxlen=100)
mqtt_records: deque[dict] = deque(maxlen=100)
mqtt_client = None


@app.on_event("startup")
def subscribe_to_test_broker():
    global mqtt_client
    url = os.environ.get("MQTT_TEST_BROKER_URL")
    if not url:
        return
    import paho.mqtt.client as mqtt
    parsed = urlsplit(url)
    subscribed = threading.Event()
    mqtt_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.on_connect = lambda client, *_args: client.subscribe("speech/e2e/#", qos=2)
    mqtt_client.on_subscribe = lambda *_args: subscribed.set()
    mqtt_client.on_message = lambda _client, _data, message: mqtt_records.append(
        {"topic": message.topic, "qos": message.qos,
         "body": json.loads(message.payload) if message.payload else None})
    mqtt_client.connect(parsed.hostname, parsed.port or 1883)
    mqtt_client.loop_start()
    if not subscribed.wait(5):
        raise RuntimeError("MQTT test subscriber was not ready")


@app.on_event("shutdown")
def close_test_broker_subscription():
    if mqtt_client is not None:
        mqtt_client.disconnect()
        mqtt_client.loop_stop()


@app.get("/mqtt-records")
def get_mqtt_records():
    return {"records": list(mqtt_records)}


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
