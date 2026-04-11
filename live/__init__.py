"""Live timing module for pitvisor.

Connects to F1's SignalR live timing feed, parses messages, maintains in-memory
session state, and exposes an SSE API for the frontend to consume.

Subpackages:
    state   — thread-safe state store
    parse   — per-topic message decoders
    client  — SignalR subclass that routes messages into the state store
    worker  — schedule-aware orchestrator (starts client during sessions)
    server  — Flask blueprint with /status, /snapshot, /stream, /telemetry
    track   — track outline extraction from the cache
"""
