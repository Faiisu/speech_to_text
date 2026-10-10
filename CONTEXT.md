# Speech-to-Text Context

This glossary defines the application terms used by the speech-to-text workflow and its deployment.

**Profile**:
A saved, reusable microphone workflow configuration shared by users of one deployment.
_Avoid_: User profile

**Workflow run**:
One clip transcription or microphone session, with status and events held in the active backend process.
_Avoid_: Persistent job

**Forwarding**:
Sending a completed transcript and its keyword-match results to a downstream HTTP service.
_Avoid_: Data storage
