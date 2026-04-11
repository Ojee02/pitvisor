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
import time
from typing import Optional

import fastf1
import pandas as pd
from flask import Flask, Response, jsonify, request
from flask_cors import CORS

from .state import STATE
from .worker import WORKER

_log = logging.getLogger("pitvisor.live.server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STREAM_INTERVAL = 1.0       # seconds between full snapshot pushes
TEL_INTERVAL = 0.25         # seconds between telemetry pushes (4 Hz)
KEEPALIVE_INTERVAL = 15.0   # SSE comment ping to keep proxies happy


def create_app(cache_dir: str | None = None) -> Flask:
    app = Flask(__name__)
    CORS(app, resources={r"/*": {"origins": "*"}})

    # kick off the background worker
    WORKER.start()

    # ── basic ───────────────────────────────────────────────────────────

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify(status="UP", live_active=STATE.session.get("active", False)), 200

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
