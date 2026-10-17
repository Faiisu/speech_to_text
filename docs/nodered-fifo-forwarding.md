## Data structure

```text
JSON message
├── vision_analytics            
│   └── data: array             
└── speech_analytics            
    ├── transcript: array       
    └── matches: array          
```

| Path | Type | Meaning |
| --- | --- | --- |
| `vision_analytics` | object | vision payload, passed through unchanged. It contains `data` as an array. |
| `vision_analytics.data` | array | vision report data. The matcher does not validate the array items. |
| `speech_analytics` | object | speech to text input payload, passed through unchanged. It contains `transcript` and `matches` as arrays. |
| `speech_analytics.transcript` | array | speech to text transcript data. |
| `speech_analytics.matches` | array | speech to text match data. |

## JSON example

```json
{
  "topic": "merged/v1/report",
  "payload": {
    "vision_analytics": {
      "data": [
        {
          "camera_id": "cam-01", 
          "objects": [
            {"label": "person", "count": 1}
          ]
        }
      ],
      "reported_at": "2026-10-16T09:30:00Z"
    },
    "speech_analytics": {
      "transcript": [
        "ซับเวย์สวัสดีค่ะ รับอะไรดีคะ",
        "จ่ายเงินด้านนี้ค่ะ",
        "รับคุ้กกี้เพิ่มนะคะ"
      ],
      "matches": [
          {"keyword": "สวัสดี", "count": 1},
          {"keyword": "ซับเวย์", "count": 1},
          {"keyword": "คุ้กกี้", "count": 1}
        ],
      "reported_at": "2026-10-16T09:30:02Z"
    }
  }
}
```

Each emitted message contains one Nara batch and one EchoDesk batch, paired in FIFO order. If one input makes multiple pairs available, the node emits multiple messages individually; it does not combine them into one payload array.
