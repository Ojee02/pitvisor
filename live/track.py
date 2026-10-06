"""Track outline extraction.

For the track map we need a pre-computed SVG-friendly outline of the
circuit. We pull it from the local FastF1 cache: pick the most recent
cached session for this circuit, load the fastest lap's position data, and
return the rotated (X, Y) polyline.

Geometry for a given (year, round) never changes, so every outline is also
written to a tiny JSON file next to the recordings. The first extraction
costs a full FastF1 session load (~40 s); every later one is a file read.
"""
import json
import logging
import math
import os
import threading
from typing import Optional

import fastf1
import pandas as pd

_log = logging.getLogger("pitvisor.live.track")

OUTLINE_CACHE_DIR = os.environ.get(
    "PITVISOR_TRACK_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "track_cache"),
)
_OUTLINE_LOCKS: dict[str, threading.Lock] = {}
_OUTLINE_LOCKS_GUARD = threading.Lock()


def _downsample(points: list, max_points: int = 400) -> list:
    if len(points) <= max_points:
        return points
    step = max(1, len(points) // max_points)
    return points[::step]


def _cache_key(year, round_or_name) -> Optional[str]:
    try:
        return f"{int(year)}_{int(round_or_name)}.json"
    except (TypeError, ValueError):
        if isinstance(round_or_name, str):
            safe = "".join(c if c.isalnum() else "-" for c in round_or_name)
            return f"{int(year)}_{safe}.json"
        return None


def _read_cache(key: str) -> Optional[dict]:
    path = os.path.join(OUTLINE_CACHE_DIR, key)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("outline"):
            return data
    except Exception:
        pass
    return None


def _write_cache(key: str, data: dict) -> None:
    try:
        os.makedirs(OUTLINE_CACHE_DIR, exist_ok=True)
        path = os.path.join(OUTLINE_CACHE_DIR, key)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception as exc:
        _log.debug("outline cache write failed for %s: %s", key, exc)


def _lock_for(key: str) -> threading.Lock:
    with _OUTLINE_LOCKS_GUARD:
        lock = _OUTLINE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _OUTLINE_LOCKS[key] = lock
        return lock


def extract_outline(year: int, round_or_name, use_cache: bool = True) -> Optional[dict]:
    """Return {outline: [[x, y], ...], corners: [{number, x, y}], rotation}
    for the given race. Coordinates are RAW device coordinates — same frame
    as Position.z messages — so the frontend can apply the rotation
    angle once to both the outline and the moving driver dots in lockstep.
    Downsampled to ~400 points. Returns None if no session has lap data
    yet.

    Results are memoised on disk keyed by (year, round) so only the very
    first extraction for a circuit pays for a FastF1 session load. A
    per-key lock stops two replay sessions starting at once from both
    doing the 40 s load.

    Walks through session types in roughly reverse-completed order so
    we still return a track outline during a session weekend where the
    race hasn't run yet (this is called on a Friday for Sprint Qualifying,
    where 'R' has no lap data so we'd previously bail). If the requested
    year has nothing, fall back to the previous year's race at the same
    round — track geometry is stable year-on-year for the same circuit,
    so it's a safe last-resort outline."""
    key = _cache_key(year, round_or_name)
    if not (use_cache and key):
        return _extract_outline_uncached(year, round_or_name)

    cached = _read_cache(key)
    if cached is not None:
        _log.info("extract_outline: cache hit %s", key)
        return cached

    # One extraction per (year, round) at a time: the second caller blocks
    # here and then finds the cache the first one just wrote, instead of
    # stacking an identical FastF1 load behind it.
    with _lock_for(key):
        cached = _read_cache(key)
        if cached is not None:
            return cached
        result = _extract_outline_uncached(year, round_or_name)
        if result is not None:
            _write_cache(key, result)
        return result


def _extract_outline_uncached(year: int, round_or_name) -> Optional[dict]:
    session = None
    for session_type in ("R", "Q", "SQ", "S", "FP3", "FP2", "FP1"):
        try:
            candidate = fastf1.get_session(year, round_or_name, session_type)
        except Exception as exc:
            _log.debug("extract_outline: get_session(%s, %s, %s) failed: %s",
                       year, round_or_name, session_type, exc)
            continue
        try:
            _log.info("extract_outline: loading fastf1 data for %s %s %s",
                      year, round_or_name, session_type)
            candidate.load(telemetry=True, laps=True, weather=False, messages=False)
        except Exception as exc:
            _log.debug("extract_outline: session.load(%s, %s, %s) failed: %s",
                       year, round_or_name, session_type, exc)
            continue
        try:
            lap = candidate.laps.pick_fastest()
        except Exception:
            lap = None
        if lap is None:
            continue
        try:
            pos = lap.get_pos_data()
        except Exception:
            continue
        if pos is None or len(pos) < 10:
            continue
        # found a session with usable lap data
        session = candidate
        break

    if session is None and isinstance(round_or_name, int) and year > 2018:
        # Last resort: previous year's race at the same round number.
        # Geometry is the same circuit, so the outline still works.
        _log.info("extract_outline: no %s session has lap data — falling back to %s",
                  year, year - 1)
        return extract_outline(year - 1, round_or_name)

    if session is None:
        _log.warning("extract_outline: no session with lap data for %s %s", year, round_or_name)
        return None

    _log.info("extract_outline: fastf1 load complete")
    lap = session.laps.pick_fastest()
    pos = lap.get_pos_data()

    try:
        ci = session.get_circuit_info()
        rotation = float(ci.rotation) if ci and ci.rotation is not None else 0.0
    except Exception:
        ci = None
        rotation = 0.0

    # raw device coordinates — same frame Position.z messages arrive in
    outline: list[list[float]] = []
    for _, r in pos[["X", "Y"]].iterrows():
        x = r["X"]
        y = r["Y"]
        if pd.isna(x) or pd.isna(y):
            continue
        outline.append([float(x), float(y)])
    outline = _downsample(outline, 400)

    corners_out: list[dict] = []
    if ci and ci.corners is not None:
        for _, c in ci.corners.iterrows():
            try:
                corners_out.append({
                    "number": int(c["Number"]),
                    "letter": c.get("Letter") or "",
                    "x": float(c["X"]),
                    "y": float(c["Y"]),
                })
            except Exception:
                continue

    xs = [p[0] for p in outline]
    ys = [p[1] for p in outline]
    bbox = [min(xs), min(ys), max(xs), max(ys)] if xs and ys else [0, 0, 0, 0]

    return {
        "outline": outline,
        "corners": corners_out,
        "rotation": rotation,
        "bbox": bbox,
    }


def extract_outline_by_location(year: int, location: str) -> Optional[dict]:
    """Look up the race for a given circuit location and extract the outline."""
    try:
        sched = fastf1.get_event_schedule(year, include_testing=False)
    except Exception:
        return None
    if sched is None or sched.empty:
        return None
    match = sched[sched["Location"].str.contains(location, case=False, na=False)]
    if match.empty:
        match = sched[sched["EventName"].str.contains(location, case=False, na=False)]
    if match.empty:
        return None
    ev = match.iloc[0]
    return extract_outline(year, int(ev["RoundNumber"]))
