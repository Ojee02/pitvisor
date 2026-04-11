"""SignalR client subclass.

Reuses fastf1's SignalRClient plumbing (handshake, auth, reconnection) but
overrides the message-sink to route into our parse.dispatch() in addition to
writing the raw JSONL file for later replay into the regular fastf1 cache.

The recording file is optional; if no filename is supplied, a temp file is
used. We keep it around because having the raw stream is invaluable when
debugging a parse bug after a session has ended.
"""
import json
import logging
import os
import tempfile
import threading
import time

from fastf1.livetiming.client import SignalRClient
from signalrcore.messages.completion_message import CompletionMessage

from . import parse

_log = logging.getLogger("pitvisor.live.client")


class LiveClient(SignalRClient):
    """F1 SignalR client that parses messages into the live state store in
    real time. Still writes the raw stream to disk for replay."""

    def __init__(self, recording_dir: str | None = None, timeout: int = 120):
        # always record; generate a filename per session
        recording_dir = recording_dir or tempfile.gettempdir()
        os.makedirs(recording_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        filename = os.path.join(recording_dir, f"pitvisor-live-{ts}.txt")
        super().__init__(filename=filename, filemode="w", timeout=timeout)
        self.recording_path = filename

    # ── override the sole write hook ─────────────────────────────────────

    def _on_message(self, msg):
        self._t_last_message = time.time()

        try:
            if isinstance(msg, CompletionMessage):
                # initial snapshot: msg.result is {topic: payload, ...}
                result = getattr(msg, "result", None) or {}
                for topic, payload in result.items():
                    parse.dispatch(topic, payload)
                    try:
                        self._output_file.write(
                            json.dumps([topic, payload, None]) + "\n"
                        )
                    except Exception:
                        pass

            elif isinstance(msg, list):
                # streamed update: [topic, payload, timestamp]
                if len(msg) >= 2:
                    topic = msg[0]
                    payload = msg[1]
                    parse.dispatch(topic, payload)
                try:
                    self._output_file.write(str(msg) + "\n")
                except Exception:
                    pass

            else:
                _log.warning("unknown message type: %s", type(msg))
                return

            try:
                self._output_file.flush()
            except Exception:
                pass

        except Exception:
            _log.exception("failed to handle SignalR message")


# ─── replay mode (for testing without a live session) ───────────────────

def replay(path: str, rate: float = 1.0):
    """Replay a recorded JSONL/text file through the parse pipeline at the
    given rate multiplier. `rate=1.0` is real-time (well, best-effort since
    the original file has no precise timestamps), `rate=10` is 10x speed.

    Accepts both the new JSON-per-line format written by LiveClient and the
    legacy fastf1 `str(list)` format via a fallback. Useful during dev.
    """
    import ast
    import datetime

    def _parse_line(line: str):
        line = line.strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except Exception:
            pass
        try:
            return ast.literal_eval(line)
        except Exception:
            return None

    delay = 0.02 / max(rate, 0.001)
    with open(path, "r", errors="replace") as f:
        n = 0
        for line in f:
            rec = _parse_line(line)
            if not isinstance(rec, list) or len(rec) < 2:
                continue
            topic = rec[0]
            payload = rec[1]
            parse.dispatch(topic, payload)
            n += 1
            if delay:
                time.sleep(delay)
        _log.info("replay complete: %d messages from %s", n, path)
