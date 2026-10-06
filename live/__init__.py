"""Replay module for pitvisor.

Feeds recorded F1 live-timing JSONL files through the parse pipeline and
exposes an SSE API for the frontend to consume. The SignalR live feed this
module originally wrapped no longer works, so the client/worker layers are
gone; only the recording, parsing, state and replay playback remain.

Subpackages:
    state     - thread-safe state store
    parse     - per-topic message decoders
    replay    - feeder thread that paces a recording through parse
    sessions  - per-client replay session registry
    recorder  - downloader for F1's static archive + recording CLI
    server    - Flask app with /replays/*
    track     - track outline extraction + on-disk memoisation
"""
