"""Flask app for the live service.

Routes:
    GET /health              — health check
    GET /status              — quick state check (active?, session name, next session)
    GET /snapshot            — one-shot JSON of the current state
    GET /stream              — SSE: full snapshots every STREAM_INTERVAL seconds
    GET /telemetry/stream    — SSE: high-rate telemetry for ?drivers=VER,LEC,... (by TLA or number)
    GET /schedule            — upcoming sessions (for the "no live session" view)

The SignalR worker is started on import. Gunicorn should run this with:
    --workers 1 --threads 32 --worker-class gthread --preload
"""
import datetime as dt
import json
import logging
import os
import time
from typing import Optional

import fastf1
import pandas as pd
from flask import Flask, Response, jsonify, request
from flask_cors import CORS

from . import config
from .state import STATE
from .worker import WORKER

_log = logging.getLogger("pitvisor.live.server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STREAM_INTERVAL = config.STREAM_INTERVAL
TEL_INTERVAL = config.TEL_INTERVAL
KEEPALIVE_INTERVAL = config.KEEPALIVE_INTERVAL


class _StripLivePrefix:
    """WSGI middleware that transparently strips a leading ``/live`` from the
    request path. Lets the same Flask routes work both behind the production
    nginx rewrite (which already strips ``/live/`` before proxying) and in
    local dev (where the browser hits the backend directly and keeps the
    ``/live`` prefix). No-op for paths that don't start with ``/live``."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == "/live" or path == "/live/":
            environ["PATH_INFO"] = "/"
        elif path.startswith("/live/"):
            environ["PATH_INFO"] = path[len("/live"):]
        return self.app(environ, start_response)


def create_app(cache_dir: str | None = None) -> Flask:
    app = Flask(__name__)
    CORS(app, resources={r"/*": {"origins": "*"}})
    app.wsgi_app = _StripLivePrefix(app.wsgi_app)

    # kick off the background worker
    WORKER.start()

    # ── basic ───────────────────────────────────────────────────────────

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify(status="UP", live_active=STATE.session.get("active", False)), 200

    @app.route("/config", methods=["GET"])
    def config_dump():
        return jsonify(config.describe()), 200

    @app.route("/status", methods=["GET"])
    def status():
        snap = STATE.snapshot()
        next_ses = _next_session_utc()
        return jsonify({
            "active": snap["session"].get("active", False),
            "session": snap["session"],
            "track_status": snap["track_status"],
            "driver_count": len(snap["drivers"]),
            "next_session": next_ses,
        }), 200

    @app.route("/snapshot", methods=["GET"])
    def snapshot():
        return jsonify(STATE.snapshot()), 200

    @app.route("/schedule", methods=["GET"])
    def schedule():
        """Upcoming sessions for the next ~14 days. Used by the frontend
        when no session is live."""
        return jsonify({"sessions": _upcoming_sessions(limit=10)}), 200

    # ── replay management ──────────────────────────────────────────────

    @app.route("/mode", methods=["GET"])
    def mode():
        """Current worker mode (live or replay) + replay metadata."""
        return jsonify({
            "mode": WORKER.mode,
            "replay_file": (
                os.path.basename(WORKER.current_replay_file)
                if WORKER.current_replay_file else None
            ),
            "replay_speed": WORKER.current_replay_speed,
            "replay_loop": WORKER.current_replay_loop,
        }), 200

    @app.route("/replays", methods=["GET"])
    def list_replays():
        """List downloaded recording files in RECORDING_DIR with their
        session metadata (pulled from each file's header line)."""
        rdir = config.RECORDING_DIR
        out = []
        if os.path.isdir(rdir):
            for name in sorted(os.listdir(rdir)):
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(rdir, name)
                try:
                    st = os.stat(path)
                    header = {}
                    with open(path, "r", encoding="utf-8") as f:
                        first = f.readline().strip()
                        if first:
                            try:
                                header = json.loads(first)
                            except Exception:
                                header = {}
                    out.append({
                        "name": name,
                        "size": st.st_size,
                        "year": header.get("year"),
                        "event_name": header.get("event_name"),
                        "session_name": header.get("session_name"),
                        "duration_sec": header.get("duration_sec"),
                        "record_count": header.get("record_count"),
                        "recorded_at": header.get("recorded_at"),
                    })
                except Exception as exc:
                    _log.warning("replay metadata read failed %s: %s", name, exc)
        return jsonify({"replays": out}), 200

    @app.route("/replays/load", methods=["POST"])
    def load_replay():
        """Switch the worker into replay mode using an already-downloaded
        recording. Body: { name, speed?, loop? }."""
        try:
            body = request.get_json(force=True) or {}
        except Exception:
            body = {}
        name = body.get("name")
        if not name:
            return jsonify({"error": "name required"}), 400
        if "/" in name or ".." in name:
            return jsonify({"error": "invalid name"}), 400
        path = os.path.join(config.RECORDING_DIR, name)
        if not os.path.isfile(path):
            return jsonify({"error": "not found"}), 404
        speed = float(body.get("speed") or 1.0)
        loop = bool(body.get("loop") if body.get("loop") is not None else True)
        try:
            WORKER.start_replay_mode(path, speed=speed, loop=loop)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"status": "ok", "file": name, "mode": WORKER.mode}), 200

    @app.route("/replays/stop", methods=["POST"])
    def stop_replay():
        """Switch the worker back to live mode (also stops the replay)."""
        try:
            WORKER.start_live_mode()
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"status": "ok", "mode": WORKER.mode}), 200

    @app.route("/replays/download", methods=["POST"])
    def download_replay():
        """Download a past F1 session from the static archive and write
        a JSONL recording into RECORDING_DIR. Body: { year, round, session }.
        This is synchronous — the HTTP request blocks until the download
        completes (typically a few seconds at ~20 MB for a race)."""
        try:
            body = request.get_json(force=True) or {}
        except Exception:
            body = {}
        year = body.get("year")
        rnd = body.get("round")
        sess = body.get("session")
        if year is None or rnd is None or not sess:
            return jsonify({"error": "year, round, session required"}), 400
        try:
            year = int(year)
            try:
                rnd = int(rnd)
            except (TypeError, ValueError):
                rnd = str(rnd)
            sess = str(sess)
        except Exception as exc:
            return jsonify({"error": f"invalid params: {exc}"}), 400

        from .recorder import download  # deferred import
        try:
            out_path = download(
                year, rnd, sess,
                out_dir=config.RECORDING_DIR,
                assume_yes=True,
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({
            "status": "ok",
            "file": os.path.basename(out_path) if out_path else None,
        }), 200

    @app.route("/replays/schedule", methods=["GET"])
    def replay_schedule():
        """Return the F1 schedule for a given year so the frontend can
        build a dropdown for the download form. /replays/schedule?year=2025"""
        try:
            year = int(request.args.get("year") or dt.datetime.now().year)
        except ValueError:
            return jsonify({"error": "invalid year"}), 400
        try:
            sched = fastf1.get_event_schedule(year, include_testing=False)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        if sched is None or sched.empty:
            return jsonify({"year": year, "events": []}), 200
        now = dt.datetime.now(dt.timezone.utc)
        events = []
        for _, row in sched.iterrows():
            sessions = []
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
                sessions.append({
                    "name": name,
                    "start_utc": start_utc.isoformat(),
                    "past": start_utc < now,
                })
            events.append({
                "round": int(row.get("RoundNumber") or 0),
                "event_name": str(row.get("EventName") or ""),
                "location": str(row.get("Location") or ""),
                "country": str(row.get("Country") or ""),
                "sessions": sessions,
            })
        return jsonify({"year": year, "events": events}), 200

    # ── SSE streams ─────────────────────────────────────────────────────

    @app.route("/stream", methods=["GET"])
    def stream():
        def gen():
            last_ping = time.time()
            last_push = 0.0
            yield ": connected\n\n"
            while True:
                now = time.time()
                if now - last_push >= STREAM_INTERVAL:
                    snap = STATE.snapshot()
                    yield f"event: snapshot\ndata: {json.dumps(snap, default=str)}\n\n"
                    last_push = now
                if now - last_ping >= KEEPALIVE_INTERVAL:
                    yield ": keepalive\n\n"
                    last_ping = now
                time.sleep(0.25)

        return Response(
            gen(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.route("/telemetry/stream", methods=["GET"])
    def telemetry_stream():
        drivers_arg = request.args.get("drivers", "").strip()
        if not drivers_arg:
            return jsonify({"error": "drivers query param required"}), 400
        # Accept TLAs (VER, LEC) or numbers (1, 16) — resolve to numbers
        requested = [s.strip().upper() for s in drivers_arg.split(",") if s.strip()]

        def _resolve():
            snap = STATE.snapshot()
            by_tla = {d.get("tla"): d.get("number") for d in snap["drivers"] if d.get("tla")}
            nums: list[str] = []
            for r in requested:
                if r.isdigit():
                    nums.append(r)
                elif r in by_tla:
                    nums.append(by_tla[r])
            return nums

        def gen():
            last_seen: dict[str, int] = {}
            last_ping = time.time()
            yield ": connected\n\n"
            while True:
                nums = _resolve()
                tel = STATE.telemetry_since(nums, last_seen)
                for num, bundle in tel.items():
                    last_seen[num] = bundle.get("seq", 0)
                if any(t.get("samples") for t in tel.values()):
                    # also piggyback latest driver cards so numerical readouts update
                    snap = STATE.snapshot()
                    cards = {d["number"]: d for d in snap["drivers"] if d["number"] in nums}
                    payload = {"telemetry": tel, "drivers": cards, "ts": time.time()}
                    yield f"event: telemetry\ndata: {json.dumps(payload, default=str)}\n\n"
                now = time.time()
                if now - last_ping >= KEEPALIVE_INTERVAL:
                    yield ": keepalive\n\n"
                    last_ping = now
                time.sleep(TEL_INTERVAL)

        return Response(
            gen(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


# ─── schedule helpers ───────────────────────────────────────────────────

def _next_session_utc() -> Optional[dict]:
    now = dt.datetime.now(dt.timezone.utc)
    upcoming = _upcoming_sessions(limit=1)
    return upcoming[0] if upcoming else None


def _upcoming_sessions(limit: int = 10) -> list[dict]:
    now = dt.datetime.now(dt.timezone.utc)
    out: list[dict] = []
    try:
        sched = fastf1.get_event_schedule(now.year, include_testing=False)
    except Exception:
        return out
    if sched is None or sched.empty:
        return out
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
            if start_utc < now:
                continue
            out.append({
                "event_name": row.get("EventName"),
                "session": name,
                "round": int(row.get("RoundNumber") or 0),
                "start_utc": start_utc.isoformat(),
            })
            if len(out) >= limit:
                return out
    return out
