"""Thread-safe in-memory state store for live timing.

The SignalR client thread mutates this; HTTP/SSE handler threads read from it.
All access is guarded by a single RLock. Readers call snapshot() to get a
deep-copied dict they can safely serialize.

Telemetry is kept as a rolling per-driver buffer (~60s at the native ~3Hz rate
we receive from CarData.z). Position is the very last sample only; the SSE
stream sends driver (x, y) coordinates at its own cadence.
"""
import copy
import threading
import time
from collections import deque
from typing import Any

from . import config

_REPLAY_ACTIVE = bool(config.REPLAY_FILE)

TEL_BUFFER_LEN = config.TEL_BUFFER_LEN
# ~30 samples ≈ 10 seconds of Position.z history at 3 Hz. The frontend
# only needs enough to reconstruct smooth motion between snapshot pushes
# (plus a safety margin for packet loss / late snapshots).
POS_BUFFER_LEN = 30


class LiveState:
    def __init__(self):
        self._lock = threading.RLock()
        self._tel: dict[str, deque] = {}          # driver number -> deque of samples
        self._tel_seq: dict[str, int] = {}        # driver number -> monotonic seq
        self._pos: dict[str, deque] = {}          # driver number -> deque of {t, x, y}
        self.reset()

    # ── lifecycle ────────────────────────────────────────────────────────

    def reset(self):
        """Clear all state. Called on session start."""
        with self._lock:
            self.session = {
                "active": False,
                "name": None,
                "type": None,
                "round": None,
                "year": None,
                "meeting_key": None,
                "status": None,        # Started | Aborted | Finished | Finalised | Inactive
                "lap": None,
                "total_laps": None,
                "clock_remaining": None,
                "elapsed_sec": 0.0,    # session-time elapsed since start (for replay or real)
                "track_rotation": None,
                "track_outline": None,  # list of [x, y] pairs
                "corners": None,        # list of {number, x, y}
                "updated_at": None,
            }
            self.track_status = {"status": "1", "message": "AllClear"}
            self.weather = {}
            self.race_control: list[dict] = []
            self.drivers: dict[str, dict] = {}
            self._tel.clear()
            self._tel_seq.clear()
            self._pos.clear()

    def mark_active(self, active: bool):
        with self._lock:
            self.session["active"] = active
            if active:
                self.session["_started_wall"] = time.time()
            self.session["updated_at"] = time.time()

    # ── session-level setters ────────────────────────────────────────────

    def set_session_info(self, info: dict):
        with self._lock:
            meeting = info.get("Meeting") or {}
            self.session.update({
                "name": meeting.get("OfficialName") or info.get("Name"),
                "type": info.get("Type") or info.get("Name"),
                "round": meeting.get("Number"),
                "year": None,
                "meeting_key": info.get("Key") or meeting.get("Key"),
                "updated_at": time.time(),
            })
            start = info.get("StartDate")
            if isinstance(start, str) and len(start) >= 4:
                try:
                    self.session["year"] = int(start[:4])
                except ValueError:
                    pass

    def set_session_status(self, status: str):
        with self._lock:
            self.session["status"] = status
            self.session["updated_at"] = time.time()

    def set_lap_count(self, current: int | None, total: int | None):
        with self._lock:
            if current is not None:
                self.session["lap"] = current
            if total is not None:
                self.session["total_laps"] = total
            self.session["updated_at"] = time.time()

    def set_clock(self, remaining: str | None):
        with self._lock:
            if remaining is not None:
                self.session["clock_remaining"] = remaining

    def set_elapsed(self, elapsed_sec: float):
        """Set session-time elapsed since start. Called by the replay feeder
        on every record and can be called from the live SignalR client too
        to track session time independently of wall clock."""
        with self._lock:
            self.session["elapsed_sec"] = float(elapsed_sec)

    def set_track_status(self, status: str, message: str):
        with self._lock:
            self.track_status = {"status": status, "message": message}

    def set_weather(self, w: dict):
        with self._lock:
            self.weather = w

    def set_track_geometry(self, rotation: float | None, outline: list | None, corners: list | None):
        with self._lock:
            if rotation is not None:
                self.session["track_rotation"] = rotation
            if outline is not None:
                self.session["track_outline"] = outline
            if corners is not None:
                self.session["corners"] = corners

    def append_race_control(self, messages: list[dict]):
        with self._lock:
            for m in messages:
                if m not in self.race_control:
                    self.race_control.append(m)
            self.race_control = self.race_control[-config.RACE_CONTROL_KEEP:]

    # ── driver-level setters ─────────────────────────────────────────────

    def upsert_driver(self, number: str, patch: dict):
        with self._lock:
            cur = self.drivers.setdefault(number, {"number": number})
            cur.update({k: v for k, v in patch.items() if v is not None})

    def update_driver_timing(self, number: str, timing: dict):
        with self._lock:
            cur = self.drivers.setdefault(number, {"number": number})
            for k, v in timing.items():
                if v is None:
                    continue
                cur[k] = v

    def update_driver_position(self, number: str, x: float, y: float, status: str | None = None):
        with self._lock:
            cur = self.drivers.setdefault(number, {"number": number})
            cur["x"] = x
            cur["y"] = y
            if status is not None:
                cur["pos_status"] = status

    def append_driver_position_sample(self, number: str, t_ms: int, x: float, y: float, status: str | None = None):
        """Append a timestamped position sample to the driver's rolling
        buffer. Used by Position.z which sends multiple samples per message
        — the frontend needs all of them to interpolate continuously
        between snapshots. Also updates the driver card's current x/y."""
        with self._lock:
            buf = self._pos.get(number)
            if buf is None:
                buf = deque(maxlen=POS_BUFFER_LEN)
                self._pos[number] = buf
            # dedupe on timestamp — the backend sometimes sees the same
            # sample replayed when Position.z messages overlap
            if buf and buf[-1].get("t") == t_ms:
                return
            buf.append({"t": t_ms, "x": x, "y": y})

            cur = self.drivers.setdefault(number, {"number": number})
            cur["x"] = x
            cur["y"] = y
            if status is not None:
                cur["pos_status"] = status

    def append_driver_telemetry(self, number: str, sample: dict):
        """sample: {t, speed, rpm, gear, throttle, brake, drs}. Also mirrors
        the latest values onto the driver card for quick reads."""
        with self._lock:
            buf = self._tel.get(number)
            if buf is None:
                buf = deque(maxlen=TEL_BUFFER_LEN)
                self._tel[number] = buf
            buf.append(sample)
            self._tel_seq[number] = self._tel_seq.get(number, 0) + 1

            cur = self.drivers.setdefault(number, {"number": number})
            for k in ("speed", "rpm", "gear", "throttle", "brake", "drs"):
                if k in sample:
                    cur[k] = sample[k]

    # ── readers ──────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return a deep-copied, JSON-safe state snapshot for SSE clients.
        Excludes per-driver telemetry buffers (use telemetry() for those)."""
        with self._lock:
            # Auto-update session elapsed time from wall clock when we're
            # running against a real feed. In replay mode the feeder writes
            # elapsed_sec directly from each record's t_sec so we don't touch
            # it here.
            if not _REPLAY_ACTIVE:
                started = self.session.get("_started_wall")
                if self.session.get("active") and started is not None:
                    self.session["elapsed_sec"] = time.time() - started

            sess = copy.deepcopy(self.session)
            sess.pop("_started_wall", None)

            drivers = []
            for num, d in self.drivers.items():
                drv = copy.deepcopy(d)
                # Attach recent position history so the frontend can drive a
                # smooth interpolation buffer. Bounded to POS_BUFFER_LEN
                # samples (≈10s of history at 3Hz) so the snapshot payload
                # stays small.
                pbuf = self._pos.get(num)
                if pbuf:
                    drv["pos_history"] = list(pbuf)
                drivers.append(drv)
            # sort by position when available, then by number
            drivers.sort(key=lambda x: (x.get("position") or 99, int(x.get("number") or 99)))
            return {
                "session": sess,
                "track_status": dict(self.track_status),
                "weather": copy.deepcopy(self.weather),
                "race_control": copy.deepcopy(self.race_control[-15:]),
                "drivers": drivers,
                "ts": time.time(),
            }

    def telemetry(self, numbers: list[str]) -> dict:
        """Return the full rolling telemetry buffer for the given drivers.

        Shape: {driver_number: {seq, samples: [{t, speed, rpm, gear, throttle, brake, drs}, ...]}}
        """
        out: dict = {}
        with self._lock:
            for n in numbers:
                buf = self._tel.get(n)
                if buf is None:
                    continue
                out[n] = {
                    "seq": self._tel_seq.get(n, 0),
                    "samples": list(buf),
                }
            return out

    def telemetry_since(self, numbers: list[str], seqs: dict[str, int]) -> dict:
        """Like telemetry() but only returns samples newer than the caller's
        last-seen sequence number per driver, to minimize SSE payload."""
        out: dict = {}
        with self._lock:
            for n in numbers:
                buf = self._tel.get(n)
                if buf is None:
                    continue
                cur_seq = self._tel_seq.get(n, 0)
                last = seqs.get(n, 0)
                # deque is FIFO; newest samples are at the end. Approximate:
                # send (cur_seq - last) newest samples, capped at buffer length.
                delta = max(0, min(len(buf), cur_seq - last))
                if delta == 0:
                    out[n] = {"seq": cur_seq, "samples": []}
                else:
                    out[n] = {"seq": cur_seq, "samples": list(buf)[-delta:]}
            return out


# module-level singleton
STATE = LiveState()
