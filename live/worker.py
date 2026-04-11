"""Orchestrator thread.

Runs in the background for the lifetime of the live service. The worker
has two modes, switchable at runtime via the HTTP API:

    mode = "live"    — scheduler checks the FastF1 event schedule once a
                       minute and connects a LiveClient during active
                       session windows (start-PRE_WINDOW to start+POST_WINDOW)

    mode = "replay"  — the scheduler is paused; a feeder thread replays
                       a recorded JSONL file through the same parse
                       pipeline as live data

Switching happens via `start_replay_mode(path, speed, loop)` and
`start_live_mode()` — the endpoints in live/server.py wrap these.

If PITVISOR_LIVE_REPLAY is set at startup, the worker boots directly
into replay mode so existing dev workflows still work.
"""
import datetime as dt
import logging
import threading
import time
import traceback
from typing import Optional

import fastf1
import pandas as pd

from . import config
from .state import STATE
from .track import extract_outline
from .client import LiveClient

_log = logging.getLogger("pitvisor.live.worker")

PRE_WINDOW = dt.timedelta(minutes=config.PRE_WINDOW_MINUTES)
POST_WINDOW = dt.timedelta(hours=config.POST_WINDOW_HOURS)


class LiveWorker:
    def __init__(self, cache_dir: str | None = None):
        cache_dir = cache_dir or config.CACHE_DIR
        if cache_dir:
            try:
                fastf1.Cache.enable_cache(cache_dir)
            except Exception as exc:
                _log.warning("cache enable failed: %s", exc)

        self._lock = threading.RLock()

        # live-mode state
        self._live_thread: Optional[threading.Thread] = None
        self._live_stop = threading.Event()
        self._client: Optional[LiveClient] = None
        self._client_thread: Optional[threading.Thread] = None
        self._current_session: Optional[dict] = None

        # replay-mode state
        self._replay_thread: Optional[threading.Thread] = None
        self._replay_stop_evt: Optional[threading.Event] = None

        # public mode + associated metadata
        self.mode: str = "live"          # "live" | "replay"
        self.current_replay_file: Optional[str] = None
        self.current_replay_speed: float = 1.0
        self.current_replay_loop: bool = False

    # ── public ───────────────────────────────────────────────────────────

    def start(self):
        """Called once at app startup. Enters replay mode immediately if
        PITVISOR_LIVE_REPLAY is set in the env, otherwise enters live mode."""
        if config.REPLAY_FILE:
            _log.info("boot: REPLAY mode %s (speed=%sx loop=%s)",
                      config.REPLAY_FILE, config.REPLAY_SPEED, config.REPLAY_LOOP)
            self.start_replay_mode(
                config.REPLAY_FILE,
                speed=config.REPLAY_SPEED,
                loop=config.REPLAY_LOOP,
            )
        else:
            self.start_live_mode()

    def start_live_mode(self):
        """Switch to live mode: stop any replay, start the scheduler."""
        with self._lock:
            self._stop_replay_locked()
            self.mode = "live"
            self.current_replay_file = None
            STATE.reset()
            STATE.mark_active(False)
            if not (self._live_thread and self._live_thread.is_alive()):
                self._live_stop.clear()
                self._live_thread = threading.Thread(
                    target=self._live_run, name="pitvisor-live-worker", daemon=True
                )
                self._live_thread.start()
            _log.info("mode: LIVE")

    def start_replay_mode(self, file_path: str, speed: float = 1.0, loop: bool = True):
        """Switch to replay mode: stop the live scheduler, stop any current
        replay, and start feeding the given file through the parse pipeline.
        Raises FileNotFoundError if path doesn't exist."""
        with self._lock:
            # Stop live scheduler + any SignalR client
            self._live_stop.set()
            self._stop_client()
            self._current_session = None

            # Stop any previous replay
            self._stop_replay_locked()

            from .replay import start_replay  # deferred import — avoids cycle
            STATE.reset()
            t = start_replay(file_path, speed=speed, loop=loop)
            self._replay_thread = t
            self._replay_stop_evt = getattr(t, "_stop_evt", None)
            self.mode = "replay"
            self.current_replay_file = file_path
            self.current_replay_speed = speed
            self.current_replay_loop = loop
            _log.info("mode: REPLAY %s (speed=%sx loop=%s)", file_path, speed, loop)

    def stop(self):
        """Full shutdown — used when the service is exiting."""
        self._live_stop.set()
        self._stop_client()
        self._stop_replay_locked()

    # ── internal: live loop ─────────────────────────────────────────────

    def _live_run(self):
        while not self._live_stop.is_set():
            try:
                active = self._find_active_session(dt.datetime.now(dt.timezone.utc))
            except Exception as exc:
                _log.warning("schedule check failed: %s", exc)
                active = None

            if active:
                if self._current_session != active:
                    self._current_session = active
                    self._begin_session(active)
                elif not self._client_alive():
                    _log.warning("SignalR client not alive during active session — restarting")
                    self._begin_session(active)
                time.sleep(config.POLL_INTERVAL)
            else:
                if self._current_session is not None:
                    self._end_session()
                time.sleep(config.POLL_INTERVAL)

    def _begin_session(self, active: dict):
        _log.info("session active: %s", active.get("label"))
        STATE.reset()
        STATE.mark_active(True)

        STATE.set_session_info({
            "Name": active.get("session_name"),
            "Type": active.get("session_name"),
            "Meeting": {
                "OfficialName": active.get("event_name"),
                "Number": active.get("round"),
            },
            "Key": None,
            "StartDate": active.get("start_utc").isoformat() if active.get("start_utc") else None,
        })

        try:
            geo = extract_outline(active["year"], active["round"])
            if geo:
                STATE.set_track_geometry(
                    rotation=geo["rotation"],
                    outline=geo["outline"],
                    corners=geo["corners"],
                )
                _log.info("loaded track outline (%d points, %d corners)",
                          len(geo["outline"]), len(geo["corners"]))
        except Exception:
            _log.warning("track outline extraction failed\n%s", traceback.format_exc())

        self._stop_client()
        try:
            self._client = LiveClient(
                recording_dir=config.RECORDING_DIR,
                timeout=config.CLIENT_TIMEOUT,
            )
        except Exception:
            _log.warning("failed to construct LiveClient\n%s", traceback.format_exc())
            self._client = None
            return

        def _client_loop():
            try:
                self._client.start()
            except Exception:
                _log.warning("SignalR client crashed\n%s", traceback.format_exc())

        self._client_thread = threading.Thread(
            target=_client_loop, name="pitvisor-signalr", daemon=True
        )
        self._client_thread.start()

    def _end_session(self):
        _log.info("session ended")
        self._stop_client()
        STATE.mark_active(False)
        self._current_session = None

    def _stop_client(self):
        if self._client is not None:
            try:
                self._client._exit()
            except Exception:
                pass
        self._client = None
        self._client_thread = None

    def _client_alive(self) -> bool:
        return (self._client is not None
                and self._client_thread is not None
                and self._client_thread.is_alive())

    def _stop_replay_locked(self):
        """Tell the current replay thread to wind down. Caller must hold _lock."""
        if self._replay_stop_evt is not None:
            try:
                self._replay_stop_evt.set()
            except Exception:
                pass
        self._replay_stop_evt = None
        self._replay_thread = None

    # ── schedule logic ──────────────────────────────────────────────────

    def _find_active_session(self, now_utc: dt.datetime) -> Optional[dict]:
        """Scan this year's schedule for a session whose [start-PRE, start+POST]
        window contains `now_utc`. Returns a dict describing it, or None."""
        year = now_utc.year
        try:
            sched = fastf1.get_event_schedule(year, include_testing=True)
        except Exception:
            return None
        if sched is None or sched.empty:
            return None

        for _, row in sched.iterrows():
            for i in range(1, 6):
                name = row.get(f"Session{i}")
                if not name or name in ("None", "none"):
                    continue
                start = row.get(f"Session{i}DateUtc")
                if start is None or pd.isna(start):
                    continue
                try:
                    start_utc = start.to_pydatetime()
                    if start_utc.tzinfo is None:
                        start_utc = start_utc.replace(tzinfo=dt.timezone.utc)
                except Exception:
                    continue
                window_start = start_utc - PRE_WINDOW
                window_end = start_utc + POST_WINDOW
                if window_start <= now_utc <= window_end:
                    return {
                        "year": int(year),
                        "round": int(row.get("RoundNumber") or 0),
                        "event_name": row.get("EventName"),
                        "session_name": name,
                        "start_utc": start_utc,
                        "label": f"{row.get('EventName')} — {name}",
                    }
        return None


# module-level singleton
WORKER = LiveWorker()
