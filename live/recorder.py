"""Download a past F1 session's live-timing data from the static archive.

F1 publishes per-topic `.jsonStream` files at
    https://livetiming.formula1.com/static/{year}/{weekend}/{session}/{Topic}.jsonStream

Each line is `<12-char timestamp><json payload>` separated by \\r\\n. For the
`.z` topics (Position.z, CarData.z) the payload is zlib+base64 encoded.

This module downloads every topic we care about for a given session, parses
the per-topic streams, merges them into a single timeline sorted by session
timestamp, and writes a self-contained JSONL file that `live.replay` can
replay through the dispatch pipeline.

The output format is:
    line 0  : {"__header__": true, "year": 2025, "round": 18, ...}
    line 1+ : {"t_sec": 0.123, "topic": "TimingData", "payload": {...}}

Usage (CLI):
    python -m live.recorder 2025 18 R
    python -m live.recorder 2025 "Singapore Grand Prix" Race --out recordings/
    python -m live.recorder --list 2025          # show the schedule
"""
import argparse
import base64
import datetime as dt
import json
import logging
import os
import re
import sys
import time
import zlib
from typing import Iterable, Optional

import requests

_log = logging.getLogger("pitvisor.live.recorder")

BASE_URL = "https://livetiming.formula1.com"
BASE_MIRROR = "https://livetiming-mirror.fastf1.dev"

# The topics our parse pipeline understands — we download these and ignore
# the others (Heartbeat, TeamRadio, AudioStreams, ContentStreams, RcmSeries,
# ChampionshipPrediction) because our state store has nothing to do with them.
TOPIC_FILES = {
    "SessionInfo":        "SessionInfo.jsonStream",
    "SessionStatus":      "SessionStatus.jsonStream",
    "TrackStatus":        "TrackStatus.jsonStream",
    "LapCount":           "LapCount.jsonStream",
    "ExtrapolatedClock":  "ExtrapolatedClock.jsonStream",
    "WeatherData":        "WeatherData.jsonStream",
    "RaceControlMessages":"RaceControlMessages.jsonStream",
    "DriverList":         "DriverList.jsonStream",
    "TimingData":         "TimingData.jsonStream",
    "TimingAppData":      "TimingAppData.jsonStream",
    "TimingStats":        "TimingStats.jsonStream",
    "Position.z":         "Position.z.jsonStream",
    "CarData.z":          "CarData.z.jsonStream",
}

HEADERS = {
    "User-Agent": "pitvisor-recorder/1.0 (+https://pitvisor.ojee.net)",
}

TS_LEN = 12  # length of "HH:MM:SS.mmm" timestamp prefix


def _timestamp_to_seconds(ts: str) -> Optional[float]:
    """Parse 'HH:MM:SS.mmm' or 'HH:MM:SS:mmm' into seconds-since-stream-start."""
    if not ts or len(ts) < TS_LEN:
        return None
    ts = ts.replace(":", ".")  # second form has a colon before the millis
    # now like "HH.MM.SS.mmm" — split by dots
    try:
        parts = ts.split(".")
        if len(parts) < 4:
            return None
        h, m, s, ms = parts[0], parts[1], parts[2], parts[3]
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0
    except Exception:
        return None


def _parse_payload(body: str, is_z: bool):
    """Mirror fastf1._api.parse: either plain JSON, or zlib+base64 JSON for .z."""
    if not body:
        return None
    if body[0] == '"':
        body = body.strip('"')
    if is_z:
        try:
            raw = zlib.decompress(base64.b64decode(body), -zlib.MAX_WBITS)
            return json.loads(raw.decode("utf-8-sig"))
        except Exception:
            return None
    try:
        return json.loads(body)
    except Exception:
        return None


def _head_size(path: str, topic_file: str) -> Optional[int]:
    """HEAD a single topic file and return its Content-Length (bytes) or None."""
    for base in (BASE_URL, BASE_MIRROR):
        url = base + path + topic_file
        try:
            r = requests.head(url, headers=HEADERS, timeout=15, allow_redirects=True)
        except Exception:
            continue
        if r.status_code == 200:
            cl = r.headers.get("Content-Length")
            if cl and cl.isdigit():
                return int(cl)
            return None
        if r.status_code == 404:
            # topic doesn't exist for this session (e.g. no sprint = no sprint quali)
            return 0
    return None


def _estimate_size(path: str) -> tuple[dict[str, Optional[int]], int, int]:
    """HEAD every topic and return (per_topic_bytes, known_total, unknown_count).
    Topics where Content-Length isn't reported count as unknown."""
    per: dict[str, Optional[int]] = {}
    known = 0
    unknown = 0
    for topic, filename in TOPIC_FILES.items():
        n = _head_size(path, filename)
        per[topic] = n
        if n is None:
            unknown += 1
        else:
            known += n
    return per, known, unknown


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _fetch(path: str, topic_file: str) -> Optional[str]:
    url = BASE_URL + path + topic_file
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
    except Exception as e:
        _log.warning("fetch failed %s: %s", url, e)
        r = None
    if r is None or r.status_code >= 400:
        mirror_url = BASE_MIRROR + path + topic_file
        _log.debug("falling back to mirror %s", mirror_url)
        try:
            r = requests.get(mirror_url, headers=HEADERS, timeout=30)
        except Exception as e:
            _log.warning("mirror fetch failed %s: %s", mirror_url, e)
            return None
    if r.status_code != 200:
        _log.warning("%s → %s", url, r.status_code)
        return None
    return r.content.decode("utf-8-sig", errors="replace")


def _resolve_session(year: int, rnd, session_name: str) -> dict:
    """Use fastf1 to resolve year+round+session into an api_path and metadata."""
    import fastf1
    session = fastf1.get_session(year, rnd, session_name)
    # api_path doesn't require session.load(); it's computed from schedule.
    api_path = session.api_path
    event = session.event
    return {
        "year": int(year),
        "round": int(event.get("RoundNumber") or 0),
        "event_name": str(event.get("EventName") or "Unknown"),
        "location": str(event.get("Location") or ""),
        "session_name": session.name,
        "session_date": str(event.get("EventDate") or ""),
        "api_path": api_path,
    }


def download(year: int, rnd, session_name: str, out_dir: str = "recordings",
             assume_yes: bool = False, size_only: bool = False) -> str:
    """Download every relevant topic for a past session and write a unified
    JSONL timeline.

    Before fetching anything we HEAD every topic URL to get a size estimate
    and print it, so you know what you're about to pull down. If `assume_yes`
    is false and stdin is a TTY, we prompt for confirmation. `size_only`
    prints the estimate and returns an empty string without downloading.
    """
    meta = _resolve_session(year, rnd, session_name)
    _log.info("resolved: %s (%s)", meta["event_name"], meta["session_name"])
    _log.info("api_path: %s", meta["api_path"])

    _log.info("estimating size (HEAD requests)…")
    per_topic, known, unknown = _estimate_size(meta["api_path"])

    print()
    print(f"  {meta['event_name']} — {meta['session_name']} ({meta['year']})")
    print(f"  path: {meta['api_path']}")
    print()
    print(f"  {'topic':<22}  {'size':>10}")
    print(f"  {'─'*22}  {'─'*10}")
    for topic, n in per_topic.items():
        if n is None:
            label = "?"
        elif n == 0:
            label = "missing"
        else:
            label = _human_bytes(n)
        print(f"  {topic:<22}  {label:>10}")
    print(f"  {'─'*22}  {'─'*10}")
    total_label = _human_bytes(known)
    if unknown:
        total_label += f" + {unknown} unknown"
    print(f"  {'total':<22}  {total_label:>10}")
    print()

    if size_only:
        return ""

    # Quick sanity check: if nothing at all is available, abort early
    if known == 0 and unknown == 0:
        raise RuntimeError("no topic files exist at that path — session may not have live-timing data")

    if not assume_yes:
        try:
            if sys.stdin.isatty():
                ans = input("continue with download? [y/N] ").strip().lower()
                if ans not in ("y", "yes"):
                    print("aborted.")
                    return ""
            else:
                print("non-interactive: pass --yes to download, or --size-only for a size estimate")
                return ""
        except (EOFError, KeyboardInterrupt):
            print("\naborted.")
            return ""

    records: list[tuple[float, str, object]] = []
    for topic, filename in TOPIC_FILES.items():
        _log.info("fetching %s", topic)
        body = _fetch(meta["api_path"], filename)
        if body is None:
            _log.warning("skipping %s (no data)", topic)
            continue
        is_z = ".z." in filename
        count_ok = 0
        count_err = 0
        for line in body.split("\r\n"):
            if not line:
                continue
            ts = line[:TS_LEN]
            body_str = line[TS_LEN:]
            t_sec = _timestamp_to_seconds(ts)
            if t_sec is None:
                count_err += 1
                continue
            payload = _parse_payload(body_str, is_z)
            if payload is None:
                count_err += 1
                continue
            records.append((t_sec, topic, payload))
            count_ok += 1
        _log.info("  %-22s %5d ok  %4d err", topic, count_ok, count_err)

    if not records:
        raise RuntimeError("no records downloaded — session may not exist or may have no live data")

    records.sort(key=lambda r: r[0])
    t0 = records[0][0]

    os.makedirs(out_dir, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", f"{meta['year']}_{meta['event_name']}_{meta['session_name']}").strip("_")
    out_path = os.path.join(out_dir, f"{safe}.jsonl")

    with open(out_path, "w", encoding="utf-8") as f:
        header = {
            "__header__": True,
            "year": meta["year"],
            "round": meta["round"],
            "event_name": meta["event_name"],
            "location": meta["location"],
            "session_name": meta["session_name"],
            "session_date": meta["session_date"],
            "api_path": meta["api_path"],
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "record_count": len(records),
            "duration_sec": records[-1][0] - t0,
        }
        f.write(json.dumps(header) + "\n")
        for t_sec, topic, payload in records:
            rec = {"t_sec": round(t_sec - t0, 3), "topic": topic, "payload": payload}
            f.write(json.dumps(rec, default=str) + "\n")

    _log.info("wrote %s (%d records, %.0f sec)", out_path, len(records), records[-1][0] - t0)
    return out_path


def list_schedule(year: int):
    """Print every past session for the year so you can pick one to download."""
    import fastf1
    sched = fastf1.get_event_schedule(year, include_testing=True)
    now = dt.datetime.now(dt.timezone.utc)
    import pandas as pd
    for _, row in sched.iterrows():
        for i in range(1, 6):
            name = row.get(f"Session{i}")
            if not name or name in ("None", "none"):
                continue
            start = row.get(f"Session{i}DateUtc")
            if start is None or pd.isna(start):
                continue
            try:
                dtstart = start.to_pydatetime()
                if dtstart.tzinfo is None:
                    dtstart = dtstart.replace(tzinfo=dt.timezone.utc)
            except Exception:
                continue
            status = "past" if dtstart < now else "future"
            print(f"  R{row.get('RoundNumber'):>2}  {row.get('EventName'):<28}  {name:<16}  {dtstart.strftime('%Y-%m-%d %H:%M')} UTC  [{status}]")


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Download past F1 live timing data for local replay.")
    p.add_argument("year", type=int, nargs="?", help="season year (e.g. 2025)")
    p.add_argument("round", nargs="?", help="round number or event name (e.g. 18 or 'Singapore Grand Prix')")
    p.add_argument("session", nargs="?", help="session identifier (e.g. R, Q, FP1, Sprint, 'Race')")
    p.add_argument("--out", default="recordings", help="output directory (default: recordings/)")
    p.add_argument("--list", action="store_true", help="list schedule for the given year and exit")
    p.add_argument("--size-only", action="store_true", help="show the download size estimate and exit without downloading")
    p.add_argument("--yes", "-y", action="store_true", help="skip the interactive confirmation prompt")
    args = p.parse_args(argv)

    if args.year is None:
        p.print_help()
        return 1

    if args.list:
        list_schedule(args.year)
        return 0

    if args.round is None or args.session is None:
        print("error: round and session required unless --list is used", file=sys.stderr)
        return 2

    try:
        rnd_val = int(args.round)
    except ValueError:
        rnd_val = args.round  # event name string

    try:
        path = download(
            args.year, rnd_val, args.session,
            out_dir=args.out,
            assume_yes=args.yes,
            size_only=args.size_only,
        )
    except Exception as exc:
        _log.error("download failed: %s", exc)
        return 3

    if not path:
        return 0  # size-only mode, or user aborted

    print(f"\nRecording written: {path}")
    print(f"\nReplay it with:")
    print(f"    PITVISOR_LIVE_REPLAY={path} python live_main.py")
    print(f"\n(the frontend will show it as if it were a live session)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
