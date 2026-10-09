[← Back to README](../README.md)

# Callable Interfaces

The current package exposes Python callable interfaces. The previous Control Center frontend, FastAPI application, and HTTP routes have been removed. There is no inbound HTTP backend in this phase.

## Feature 01: model deployment

The public interface provides model discovery, model loading, finite clip transcription, and microphone sessions. See the [Feature 01 callable contract](../.scratch/new-speech-to-text/spec.md#callable-interface) for inputs, outputs, configuration, errors, session events, and process deployment.

```python
from speech_to_text.features.model_deployment import load_model, transcribe_clip

model = load_model({"model": "turbo", "runtime": "openvino-gpu"})
try:
    transcript = transcribe_clip("sample.wav", model, {"language": "th"})
finally:
    model.close()
```

## Thai word matching and forwarding

The matching feature and HTTP forwarding integration are composed by the [transcript matching workflow](../.scratch/transcript-matching-forwarding/spec.md). The HTTP forwarder is outbound: it sends completed results to a configured receiver. It is not an inbound API for the frontend.

## Future frontend and backend

A future backend can expose HTTP routes that validate requests, call these feature/workflow interfaces, and return documented responses. The frontend should call that backend over HTTP; it should not import or duplicate feature logic. The current package does not implement that backend yet.

## See also

- [Architecture](architecture.md) for module ownership.
- [Configuration](configuration.md) for optional runtime and matching dependencies.
