"""The production multi-microphone service.

Separate from the PoC modules (server.py, transcribe.py's session functions),
which stay as the testing and benchmarking tools. What lives here runs
continuously: bounded memory, one shared model, and a station that dies must
not take the others with it.
"""
