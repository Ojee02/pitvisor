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
    real time. Still writes the raw stream to disk for replay.

    Tracks the last Position.z arrival separately so we can spot the
    "subscription stops sending positions but everything else keeps
    flowing" behaviour we hit on F1's new authenticated endpoint and
    kick the subscription back to life by re-issuing Subscribe."""

    # If we haven't seen a Position.z in this many seconds, re-send
    # the Subscribe call. F1's server stops sending Position.z after
    # ~5 min on a quiet subscription even though TimingData/CarData
    # keep flowing; re-subscribing nudges it back.
    POSITION_STALE_SEC = 30.0
    # Don't re-subscribe more often than this even if Position is
    # still stale right after a kick.
    RESUBSCRIBE_COOLDOWN_SEC = 15.0

    def __init__(self, recording_dir: str | None = None, timeout: int = 120):
        # always record; generate a filename per session
        recording_dir = recording_dir or tempfile.gettempdir()
        os.makedirs(recording_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        filename = os.path.join(recording_dir, f"pitvisor-live-{ts}.txt")
        super().__init__(filename=filename, filemode="w", timeout=timeout)
        self.recording_path = filename
        self._t_last_position = 0.0
        self._t_last_resubscribe = 0.0
        self._position_watchdog_thread = None
        self._position_watchdog_stop = threading.Event()

    # ── override the sole write hook ─────────────────────────────────────

    def _on_message(self, msg):
        self._t_last_message = time.time()

        try:
            if isinstance(msg, CompletionMessage):
                # initial snapshot: msg.result is {topic: payload, ...}
                result = getattr(msg, "result", None) or {}
                for topic, payload in result.items():
                    if topic == "Position.z":
                        self._t_last_position = time.time()
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
                    if topic == "Position.z":
                        self._t_last_position = time.time()
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

    # ── Position.z watchdog ─────────────────────────────────────────────
    #
    # On F1's new authenticated SignalR endpoint, Position.z stops being
    # pushed after ~5 minutes even though the websocket stays alive and
    # every other topic keeps flowing. Re-issuing the Subscribe call
    # nudges the server to start sending Position.z again. This thread
    # runs alongside _supervise and kicks the subscription whenever
    # Position.z has been silent past the threshold.

    def _resubscribe(self):
        """Send the Subscribe call again. Cooldown-gated so we don't
        spam the server if F1 ignores us for a few seconds."""
        now = time.time()
        if now - self._t_last_resubscribe < self.RESUBSCRIBE_COOLDOWN_SEC:
            return
        self._t_last_resubscribe = now
        try:
            self._connection.send(
                "Subscribe", [self.topics], on_invocation=self._on_message
            )
            _log.info("re-issued Subscribe (Position.z was stale)")
        except Exception:
            _log.exception("failed to re-Subscribe")

    def _position_watchdog(self):
        # Give the connection a moment to settle, then start checking.
        time.sleep(5.0)
        while not self._position_watchdog_stop.is_set():
            if self._t_last_position == 0.0:
                # No Position.z ever seen — wait until something else
                # bootstraps the timer instead of spamming Subscribe.
                self._position_watchdog_stop.wait(2.0)
                continue
            age = time.time() - self._t_last_position
            if age > self.POSITION_STALE_SEC:
                self._resubscribe()
            self._position_watchdog_stop.wait(5.0)

    def start(self):
        # Kick off the watchdog thread once the parent has finished
        # connecting + subscribing. Parent.start() blocks in _supervise,
        # so spawn the watchdog beforehand.
        self._position_watchdog_stop.clear()
        self._position_watchdog_thread = threading.Thread(
            target=self._position_watchdog,
            name="pitvisor-signalr-position-wd",
            daemon=True,
        )
        # Parent connects first; start the watchdog right after _run()
        # so the connection is alive. Easiest path: schedule it from a
        # tiny helper thread that waits a beat after start() begins.
        def _delayed_start():
            time.sleep(3.0)
            self._position_watchdog_thread.start()
        threading.Thread(target=_delayed_start, daemon=True).start()
        try:
            super().start()
        finally:
            self._position_watchdog_stop.set()


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
