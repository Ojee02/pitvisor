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

    # F1 Access tier caps Position.z to a single snapshot per auth
    # session: streaming stops after the very first 5 min of the
    # first connection after each authentication, and re-Subscribing
    # or reconnecting returns the same frozen snapshot timestamp
    # forever afterwards. There is no client-side workaround — Pro
    # or Premium is required for the continuous stream. The watchdog
    # is kept around as a soft safety net only.
    RECONNECT_AFTER_STALE_SEC = 300.0

    def __init__(self, recording_dir: str | None = None, timeout: int = 30):
        # always record; generate a filename per session
        recording_dir = recording_dir or tempfile.gettempdir()
        os.makedirs(recording_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        filename = os.path.join(recording_dir, f"pitvisor-live-{ts}.txt")
        # Default fastf1 timeout is 120 s; we drop it to 30 s so the
        # parent _supervise loop exits quickly when our watchdog
        # tears down the connection, letting the worker spawn a fresh
        # client without a 2-minute wait.
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
                # Subscribe response with a snapshot of each requested
                # topic. On F1 Access tier this is the ONLY way we get
                # Position.z (the server doesn't push it as a stream),
                # so count snapshot arrivals toward the freshness
                # timer — if snapshots stop coming the watchdog tears
                # down the connection and the worker reconnects.
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
                        # Only ongoing stream updates count; snapshot
                        # arrivals are a single Subscribe response.
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
        """Send Subscribe again for Position.z only. Empirically, re-
        Subscribing the full topic list only yields a fresh snapshot
        (one Position.z) then silence; targeting a single topic seems
        to be what F1's hub expects when nudging it to resume the
        stream. Cooldown-gated so we don't spam the server."""
        now = time.time()
        if now - self._t_last_resubscribe < self.RESUBSCRIBE_COOLDOWN_SEC:
            return
        self._t_last_resubscribe = now
        try:
            # First try: Unsubscribe then Subscribe just Position.z.
            # If F1's hub tracks subscriptions per-topic, this is the
            # cleanest reset path.
            try:
                self._connection.send(
                    "Unsubscribe", [["Position.z"]]
                )
            except Exception:
                pass
            self._connection.send(
                "Subscribe", [["Position.z"]],
                on_invocation=self._on_message,
            )
            _log.info("re-issued Position.z Subscribe (was stale)")
        except Exception:
            _log.exception("failed to re-Subscribe")

    def _position_watchdog(self):
        # Sleeps quietly until Position.z hasn't arrived for a long
        # time, then tears the connection down so the worker spawns a
        # fresh one. With Access-tier auth this rarely fires after
        # the initial 5 min — kept as a safety net for higher tiers
        # where streaming might briefly hiccup.
        time.sleep(5.0)
        if self._t_last_position == 0.0:
            self._t_last_position = time.time()
        while not self._position_watchdog_stop.is_set():
            age = time.time() - self._t_last_position
            if age > self.RECONNECT_AFTER_STALE_SEC:
                _log.warning(
                    "Position.z silent for %.0fs — tearing down SignalR "
                    "client to force a fresh connection",
                    age,
                )
                try:
                    self._exit()
                except Exception:
                    pass
                self._t_last_message = 0.0
                return
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
