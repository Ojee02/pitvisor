"""Per-client replay session registry.

Each replay session owns a private LiveState instance + replay feeder
thread + stop event. Clients subscribe to per-session SSE streams instead
of the global /live/stream, so one viewer can be watching a historical
race at 1× while another is watching the real live feed, without any
cross-talk.

A session is cleaned up automatically if no SSE subscriber has touched
it in SESSION_IDLE_TIMEOUT seconds (defaults to 10 min). SSE handlers
call `touch()` on every iteration to keep their session alive.
"""
import logging
import secrets
import threading
import time
from typing import Optional

from .state import LiveState

_log = logging.getLogger("pitvisor.live.sessions")

SESSION_IDLE_TIMEOUT = 600.0  # seconds


class ReplaySession:
    def __init__(self, session_id: str, file_path: str, speed: float, loop: bool):
        self.id = session_id
        self.file_path = file_path
        self.speed = speed
        self.loop = loop
        self.created_at = time.time()
        self.last_touched = time.time()
        self.state = LiveState()
        self._thread: Optional[threading.Thread] = None
        self._stop_evt: Optional[threading.Event] = None

    def start(self):
        # Deferred import so sessions.py doesn't circular-import with replay
        from .replay import start_replay
        t = start_replay(self.file_path, speed=self.speed, loop=self.loop, state=self.state)
        self._thread = t
        self._stop_evt = getattr(t, "_stop_evt", None)

    def stop(self):
        if self._stop_evt is not None:
            try:
                self._stop_evt.set()
            except Exception:
                pass
        self._stop_evt = None
        self._thread = None

    def touch(self):
        self.last_touched = time.time()

    def is_stale(self, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.time()
        return (now - self.last_touched) > SESSION_IDLE_TIMEOUT

    def describe(self) -> dict:
        return {
            "id": self.id,
            "file": self.file_path.split("/")[-1],
            "speed": self.speed,
            "loop": self.loop,
            "created_at": self.created_at,
            "last_touched": self.last_touched,
            "alive": bool(self._thread and self._thread.is_alive()),
        }


class ReplaySessionRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: dict[str, ReplaySession] = {}
        self._cleanup_started = False

    def create(self, file_path: str, speed: float, loop: bool) -> ReplaySession:
        with self._lock:
            sid = secrets.token_urlsafe(9)
            sess = ReplaySession(sid, file_path, speed, loop)
            sess.start()
            self._sessions[sid] = sess
            self._ensure_cleanup_locked()
            _log.info("replay session created: id=%s file=%s speed=%sx loop=%s",
                      sid, file_path, speed, loop)
            return sess

    def get(self, sid: str) -> Optional[ReplaySession]:
        with self._lock:
            return self._sessions.get(sid)

    def stop(self, sid: str) -> bool:
        with self._lock:
            sess = self._sessions.pop(sid, None)
        if sess:
            sess.stop()
            _log.info("replay session stopped: id=%s", sid)
            return True
        return False

    def list(self) -> list[dict]:
        with self._lock:
            return [s.describe() for s in self._sessions.values()]

    def _ensure_cleanup_locked(self):
        if self._cleanup_started:
            return
        self._cleanup_started = True
        t = threading.Thread(target=self._cleanup_loop, name="pitvisor-replay-gc", daemon=True)
        t.start()

    def _cleanup_loop(self):
        while True:
            time.sleep(60)
            try:
                self._sweep()
            except Exception:
                _log.exception("cleanup sweep failed")

    def _sweep(self):
        now = time.time()
        stale: list[str] = []
        with self._lock:
            for sid, sess in list(self._sessions.items()):
                if sess.is_stale(now):
                    stale.append(sid)
        for sid in stale:
            _log.info("replay session gc: expiring idle session %s", sid)
            self.stop(sid)


REGISTRY = ReplaySessionRegistry()
