# FIFO Matcher Output

The Function node emits one Node-RED message for each matched pair of batches. The output topic is `merged/v1/report`.

## Data structure

```text
Output message
├── topic: "merged/v1/report"
└── payload
    ├── vision_analytics              # Complete Nara payload
    │   └── data: array               # Required by the matcher
    └── speech_analytics              # Complete EchoDesk payload
        ├── transcript: array        # Required by the matcher
        └── matches: array           # Required by the matcher
```

| Path | Type | Meaning |
| --- | --- | --- |
| `msg.topic` | string | Always `merged/v1/report`. |
| `msg.payload.vision_analytics` | object | Complete Nara input payload, passed through unchanged. It contains `data` as an array. |
| `msg.payload.vision_analytics.data` | array | Nara report data. The matcher does not validate the array items. |
| `msg.payload.speech_analytics` | object | Complete EchoDesk input payload, passed through unchanged. It contains `transcript` and `matches` as arrays. |
| `msg.payload.speech_analytics.transcript` | array | EchoDesk transcript data. |
| `msg.payload.speech_analytics.matches` | array | EchoDesk match data. |

## JSON example

```json
{
  "topic": "merged/v1/report",
  "payload": {
    "vision_analytics": {
      "data": [
        {"camera_id": "cam-01", "objects": [{"label": "person", "count": 1}]}
      ],
      "reported_at": "2026-10-16T09:30:00Z"
    },
    "speech_analytics": {
      "transcript": ["พบคนเดินเข้าประตู"],
      "matches": [{"keyword": "ประตู", "count": 1}],
      "reported_at": "2026-10-16T09:30:02Z"
    }
  }
}
```

Each emitted message contains one Nara batch and one EchoDesk batch, paired in FIFO order. If one input makes multiple pairs available, the node emits multiple messages individually; it does not combine them into one payload array.
