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
import json
import logging
import threading
import time
from typing import Optional

from . import parse
from .state import STATE

_log = logging.getLogger("pitvisor.live.replay")


def _load(path: str) -> tuple[dict, list[dict]]:
    header: dict = {}
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
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


def _prime_session(header: dict):
    """Populate session metadata and track outline before replay starts, so
    the frontend has something to show before the first SessionInfo message."""
    from .track import extract_outline

    STATE.reset()
    STATE.mark_active(True)
    STATE.set_session_info({
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
                STATE.set_track_geometry(
                    rotation=geo["rotation"],
                    outline=geo["outline"],
                    corners=geo["corners"],
                )
                _log.info("replay track outline loaded (%d points)", len(geo["outline"]))
        except Exception as exc:
            _log.warning("replay outline extraction failed: %s", exc)


def _feed(records: list[dict], speed: float, loop: bool, stop_evt: threading.Event):
    while not stop_evt.is_set():
        wall_start = time.time()
        first_t = records[0]["t_sec"] if records else 0.0

        for rec in records:
            if stop_evt.is_set():
                return
            if speed > 0:
                elapsed_rel = (rec["t_sec"] - first_t) / speed
                elapsed_wall = time.time() - wall_start
                if elapsed_rel > elapsed_wall:
                    # sleep in small chunks so stop_evt is responsive
                    sleep_s = elapsed_rel - elapsed_wall
                    while sleep_s > 0 and not stop_evt.is_set():
                        step = min(sleep_s, 0.2)
                        time.sleep(step)
                        sleep_s -= step
            STATE.set_elapsed(rec["t_sec"] - first_t)
            try:
                parse.dispatch(rec["topic"], rec["payload"])
            except Exception:
                _log.exception("dispatch failed on %s", rec.get("topic"))

        _log.info("replay timeline complete")
        if not loop:
            STATE.mark_active(False)
            return
        _log.info("looping replay")


def start_replay(path: str, speed: float = 10.0, loop: bool = False) -> threading.Thread:
    """Start a background replay thread. Returns the Thread (daemon=True).

    Raises FileNotFoundError if `path` doesn't exist.
    """
    header, records = _load(path)
    if not records:
        raise RuntimeError(f"no records found in {path}")
    _log.info("replay loaded: %s (%d records, %.0f sec)",
              header.get("event_name"), len(records), records[-1]["t_sec"])

    _prime_session(header)

    stop_evt = threading.Event()
    t = threading.Thread(
        target=_feed,
        args=(records, speed, loop, stop_evt),
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
