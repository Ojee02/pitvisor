"""Replay a recorded JSONL session through the live parse pipeline.

The `live.recorder` CLI produces a JSONL file where each line is either a
header record or a `{t_sec, topic, payload}` tuple sorted by session time.
This module feeds those records into `parse.dispatch()` in order, with
real-time or accelerated pacing, so the live SSE endpoints see the session
progress exactly as if it were happening now.

Replay mode also:
  • Marks the session as active in STATE so the frontend exits its offline
    view.
  • Pre-loads the track outline from the cache using the header's year+round.
  • Populates session metadata (name, type, round, year) from the header so
    it's visible in the UI before SessionInfo messages catch up.

Usage (API):
    from live.replay import start_replay
    t = start_replay("recordings/2025_Singapore_Race.jsonl", speed=10.0)
    t.join()  # optional — the thread is a daemon

Usage (CLI):
    python -m live.replay recordings/2025_Singapore_Race.jsonl --speed 10

Env-var integration happens in live.worker: if PITVISOR_LIVE_REPLAY is set,
the worker bypasses the scheduler and calls start_replay() instead.
"""
import argparse
import gzip
import json
import logging
import threading
import time
from typing import Optional

from . import config, parse
from .state import STATE

_log = logging.getLogger("pitvisor.live.replay")


def _open_recording(path: str):
    """Open a recording for text reading. Transparently handles both
    gzip-compressed files (.jsonl.gz) and plain .jsonl files so older
    recordings still work after the recorder started writing gzip."""
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def _load(path: str) -> tuple[dict, list[dict]]:
    header: dict = {}
    records: list[dict] = []
    with _open_recording(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if i == 0 and obj.get("__header__"):
                header = obj
                continue
            if "t_sec" in obj and "topic" in obj:
                records.append(obj)
    return header, records


def _prime_session(header: dict, state=None):
    """Populate session metadata and track outline before replay starts, so
    the frontend has something to show before the first SessionInfo message.

    `state` is the LiveState instance to prime. Defaults to the global
    STATE for backward compatibility (boot-time env-var replay)."""
    from .track import extract_outline

    target = state if state is not None else STATE
    target.reset()
    target.mark_active(True)
    target.set_session_info({
        "Name": header.get("session_name") or "Replay",
        "Type": header.get("session_name") or "Replay",
        "Meeting": {
            "OfficialName": header.get("event_name") or "Replay Session",
            "Number": header.get("round"),
        },
        "Key": None,
        "StartDate": f"{header.get('year')}-01-01",
    })

    year = header.get("year")
    rnd = header.get("round")
    if year and rnd:
        try:
            geo = extract_outline(int(year), int(rnd))
            if geo:
                target.set_track_geometry(
                    rotation=geo["rotation"],
                    outline=geo["outline"],
                    corners=geo["corners"],
                )
                _log.info("replay track outline loaded (%d points)", len(geo["outline"]))
        except Exception as exc:
            _log.warning("replay outline extraction failed: %s", exc)


def _find_seek_offset(records: list[dict]) -> tuple[int, float]:
    """Decide which record index to start real-time playback from.

    Returns (seek_index, seek_t_sec). All earlier records are still
    dispatched (to build up DriverList, TrackStatus, weather, initial
    positions, etc.) but as fast as possible — only the records from
    seek_index onward are paced at `speed`.

    Precedence:
        1. PITVISOR_LIVE_REPLAY_SEEK_SEC > 0 → use that t_sec
        2. PITVISOR_LIVE_REPLAY_SKIP_TO_START → find first SessionStatus
           record whose payload.Status == "Started" (green flag)
        3. Otherwise → start from the very beginning (index 0)
    """
    if not records:
        return 0, 0.0

    if config.REPLAY_SEEK_SEC and config.REPLAY_SEEK_SEC > 0:
        target = float(config.REPLAY_SEEK_SEC)
        for i, rec in enumerate(records):
            if rec["t_sec"] >= target:
                return i, rec["t_sec"]
        return len(records) - 1, records[-1]["t_sec"]

    if config.REPLAY_SKIP_TO_START:
        for i, rec in enumerate(records):
            if rec.get("topic") != "SessionStatus":
                continue
            payload = rec.get("payload") or {}
            if isinstance(payload, dict) and payload.get("Status") == "Started":
                return i, rec["t_sec"]

    return 0, 0.0


def _feed(
    path: str,
    loop: bool,
    stop_evt: threading.Event,
    state=None,
    speed_ref=None,
    pause_evt: Optional[threading.Event] = None,
    seek_ref=None,
    on_loaded=None,
):
    """Feeder thread: loads the recording, primes the session metadata
    and track outline, then pumps records through the parse pipeline.

    speed_ref is a single-element list [speed] that the thread reads on
    each record, so the HTTP handlers can change speed mid-session by
    mutating speed_ref[0] atomically.
    pause_evt is set externally to pause playback mid-session.
    seek_ref is a single-element list [t_sec or None]; the thread
    checks it once per record and if set, jumps iteration to that
    session-time.
    on_loaded(duration_sec) is called once after _load() completes so
    the session object can expose duration to HTTP clients."""
    # Bind the replay thread's parse context to the target state (defaults
    # to global STATE for boot-time env-var replay; per-client replay
    # sessions pass their own LiveState instance).
    target = state if state is not None else STATE
    parse.bind_state(target)
    _log.info("feeder thread started: state_id=%s path=%s", id(target), path)

    # Load + prime here, on the feeder thread, so the caller never blocks.
    try:
        header, records = _load(path)
    except Exception:
        _log.exception("replay load failed for %s", path)
        target.mark_active(False)
        return
    if not records:
        _log.warning("replay %s has no records", path)
        target.mark_active(False)
        return
    duration = float(records[-1]["t_sec"])
    _log.info("replay loaded: %s (%d records, %.0f sec)",
              header.get("event_name"), len(records), duration)
    if on_loaded is not None:
        try:
            on_loaded(duration)
        except Exception:
            pass
    _log.info("feeder: calling _prime_session")
    try:
        _prime_session(header, state=target)
    except Exception:
        _log.exception("_prime_session failed")
    _log.info("feeder: _prime_session returned, entering feed loop")

    default_seek_index, default_seek_t = _find_seek_offset(records)
    if default_seek_index > 0:
        _log.info(
            "replay seek: fast-forwarding %d records (%.0fs session time) to build initial state",
            default_seek_index, default_seek_t,
        )

    def _speed():
        return speed_ref[0] if speed_ref else 10.0

    def _find_index_for_t(t_target):
        lo, hi = 0, len(records)
        while lo < hi:
            mid = (lo + hi) >> 1
            if records[mid]["t_sec"] < t_target:
                lo = mid + 1
            else:
                hi = mid
        return max(0, min(lo, len(records) - 1))

    while not stop_evt.is_set():
        seek_index = default_seek_index
        seek_t = default_seek_t
        wall_start = None
        start_t = seek_t

        i = 0
        while i < len(records):
            if stop_evt.is_set():
                return

            # Apply a pending seek request atomically.
            if seek_ref is not None and seek_ref[0] is not None:
                requested = seek_ref[0]
                seek_ref[0] = None
                new_i = _find_index_for_t(float(requested))
                # Clear state so stale driver/position data from BEFORE the
                # seek target doesn't linger visually.
                target.reset()
                target.mark_active(True)
                try:
                    _prime_session(header, state=target)
                except Exception:
                    _log.exception("_prime_session failed during seek")
                # Fast-forward from index 0 up to the new target so
                # DriverList (which arrives early in the file, typically
                # well before the default seek_index) and every other
                # pre-target event gets dispatched and the state is
                # fully rebuilt as-of the seek target.
                for j in range(0, new_i):
                    try:
                        parse.dispatch(records[j]["topic"], records[j]["payload"])
                    except Exception:
                        _log.exception("dispatch failed on %s", records[j].get("topic"))
                i = new_i
                start_t = records[new_i]["t_sec"] if new_i < len(records) else 0.0
                wall_start = time.time()
                _log.info("replay: seeked to t=%.0fs (record %d)", start_t, new_i)
                continue

            rec = records[i]

            # Honor pause events. Block on a short wait so stop_evt /
            # seek_ref / speed changes still respond quickly.
            if pause_evt is not None and pause_evt.is_set():
                while pause_evt.is_set() and not stop_evt.is_set():
                    if seek_ref is not None and seek_ref[0] is not None:
                        break
                    time.sleep(0.1)
                # Reset wall_start so the pace pick-up is smooth after resume
                if wall_start is not None:
                    wall_start = None
                continue

            if i < seek_index:
                # Fast-forward phase (pre-session dead time)
                try:
                    parse.dispatch(rec["topic"], rec["payload"])
                except Exception:
                    _log.exception("dispatch failed on %s", rec.get("topic"))
                i += 1
                continue

            if wall_start is None:
                wall_start = time.time()
                start_t = rec["t_sec"]
                _log.info("replay: real-time playback starting at t=%.0fs", start_t)

            spd = _speed()
            if spd > 0:
                elapsed_rel = (rec["t_sec"] - start_t) / spd
                elapsed_wall = time.time() - wall_start
                if elapsed_rel > elapsed_wall:
                    sleep_s = elapsed_rel - elapsed_wall
                    while sleep_s > 0 and not stop_evt.is_set():
                        # Bail out of the sleep if a pause/seek/speed
                        # change arrives so we respond responsively.
                        if pause_evt is not None and pause_evt.is_set():
                            break
                        if seek_ref is not None and seek_ref[0] is not None:
                            break
                        step = min(sleep_s, 0.2)
                        time.sleep(step)
                        sleep_s -= step
                    if (pause_evt is not None and pause_evt.is_set()) or (
                        seek_ref is not None and seek_ref[0] is not None
                    ):
                        continue
            target.set_elapsed(rec["t_sec"] - start_t)
            try:
                parse.dispatch(rec["topic"], rec["payload"])
            except Exception:
                _log.exception("dispatch failed on %s", rec.get("topic"))
            i += 1

        _log.info("replay timeline complete")
        if not loop:
            target.mark_active(False)
            return
        _log.info("looping replay")


def start_replay(
    path: str,
    speed: float = 10.0,
    loop: bool = False,
    state=None,
    speed_ref=None,
    pause_evt: Optional[threading.Event] = None,
    seek_ref=None,
    on_loaded=None,
) -> threading.Thread:
    """Spawn a background feeder thread for `path` and return immediately.

    Loading the recording, priming the session metadata, and extracting
    the track outline all happen INSIDE the feeder thread — this call
    is effectively instant, so HTTP handlers that kick off a replay
    session can return a session_id to the client without blocking on
    fastf1's ~60 s data load.

    Accepts either a fixed `speed` value (legacy path, used by env-var
    boot-time replay) or a `speed_ref` list-of-one that the feeder
    re-reads per record so HTTP clients can change speed at runtime.
    `pause_evt` and `seek_ref` are optional threading primitives for
    pause/resume and seek control. `on_loaded(duration_sec)` is called
    once after _load completes.
    """
    import os
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    if speed_ref is None:
        speed_ref = [float(speed)]

    stop_evt = threading.Event()
    t = threading.Thread(
        target=_feed,
        args=(path, loop, stop_evt),
        kwargs={
            "state": state,
            "speed_ref": speed_ref,
            "pause_evt": pause_evt,
            "seek_ref": seek_ref,
            "on_loaded": on_loaded,
        },
        name="pitvisor-replay",
        daemon=True,
    )
    t._stop_evt = stop_evt  # type: ignore[attr-defined]  # expose for stop()
    t.start()
    return t


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Replay a recorded live-timing JSONL through the pitvisor state store (stand-alone — no HTTP server).")
    p.add_argument("path", help="path to a recorded JSONL file")
    p.add_argument("--speed", type=float, default=10.0, help="playback speed multiplier (default 10.0, 0 = max)")
    p.add_argument("--loop", action="store_true", help="loop back to the start when the file ends")
    args = p.parse_args(argv)

    t = start_replay(args.path, speed=args.speed, loop=args.loop)
    try:
        while t.is_alive():
            t.join(timeout=1.0)
    except KeyboardInterrupt:
        stop_evt = getattr(t, "_stop_evt", None)
        if stop_evt:
            stop_evt.set()
        _log.info("stopping replay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
