"""Orchestrator thread.

Runs in the background for the lifetime of the live service. Every minute it
checks the FastF1 event schedule for the current year and decides whether a
session is "live" (start-PRE_WINDOW to start+POST_WINDOW).

During a live window:
  • extract the track outline from the cache (once per session)
  • construct a LiveClient and call .start() on a child thread
  • if the child thread dies before the window closes, restart it
  • when the window closes, stop the client

If PITVISOR_LIVE_REPLAY is set, the scheduler is bypassed entirely and the
named file is fed through the same parse pipeline via live.replay — useful
for testing the UI between race weekends without waiting for a real session.
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
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._client: Optional[LiveClient] = None
        self._client_thread: Optional[threading.Thread] = None
        self._current_session: Optional[dict] = None
        self._replay_thread: Optional[threading.Thread] = None

    # ── public ───────────────────────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        # Replay mode: skip the scheduler entirely
        if config.REPLAY_FILE:
            _log.info("REPLAY mode: %s (speed=%sx loop=%s)",
                      config.REPLAY_FILE, config.REPLAY_SPEED, config.REPLAY_LOOP)
            # Deferred import to avoid a circular import (replay → state → config → ...)
            from .replay import start_replay
            self._replay_thread = start_replay(
                config.REPLAY_FILE,
                speed=config.REPLAY_SPEED,
                loop=config.REPLAY_LOOP,
            )
            return

        self._thread = threading.Thread(
            target=self._run, name="pitvisor-live-worker", daemon=True
        )
        self._thread.start()
        _log.info("live worker started")

    def stop(self):
        self._stop.set()
        self._stop_client()

    # ── main loop ────────────────────────────────────────────────────────

    def _run(self):
        while not self._stop.is_set():
            try:
                active = self._find_active_session(dt.datetime.now(dt.timezone.utc))
            except Exception as exc:
                _log.warning("schedule check failed: %s", exc)
                active = None

            if active:
                if self._current_session != active:
                    # new session — begin it
                    self._current_session = active
                    self._begin_session(active)
                elif not self._client_alive():
                    # mid-session: client crashed or never started. Restart it.
                    _log.warning("SignalR client not alive during active session — restarting")
                    self._begin_session(active)
                time.sleep(config.POLL_INTERVAL)
            else:
                if self._current_session is not None:
                    self._end_session()
                time.sleep(config.POLL_INTERVAL)

    # ── session transitions ─────────────────────────────────────────────

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
