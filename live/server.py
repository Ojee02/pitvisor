"""Flask app for the replay service.

This service used to also carry a live SignalR feed; F1 changed their
endpoint and the feed no longer produces usable data, so the live path
has been removed entirely. What remains is the replay player:

Routes:
    GET  /health                          — health check
    GET  /config                          — effective config (no secrets)
    GET  /replays                         — downloaded recordings + metadata
    GET  /replays/schedule?year=          — F1 schedule for the download picker
    POST /replays/download                — pull a past session from F1's archive
    POST /replays/session/start           — create a private replay session
    POST /replays/session/<sid>/stop      — tear one down
    GET  /replays/session/<sid>/snapshot  — one-shot state JSON
    GET  /replays/session/<sid>/stream    — SSE snapshots
    GET  /replays/session/<sid>/telemetry/stream — SSE telemetry
    POST /replays/session/<sid>/{pause,resume,speed,seek} — playback control
    GET  /replays/sessions                — active sessions

Every replay session owns its own state and feeder thread, so one viewer
can watch a historical race at 1x while another watches a different one
with no cross-talk.

Gunicorn should run this with:
    --workers 1 --threads 32 --worker-class gthread --timeout 0
"""
import datetime as dt
import json
import logging
import os
import threading
import time
from typing import Optional

import fastf1
import pandas as pd
from flask import Flask, Response, jsonify, request
from flask_cors import CORS

from . import config
from .sessions import REGISTRY as REPLAY_REGISTRY

_log = logging.getLogger("pitvisor.live.server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STREAM_INTERVAL = config.STREAM_INTERVAL
TEL_INTERVAL = config.TEL_INTERVAL
KEEPALIVE_INTERVAL = config.KEEPALIVE_INTERVAL

# /replays/download is a hot link straight to F1's CDN. It is public, so
# bound how often one address can pull a new recording — enough headroom
# for a curious visitor, not enough to be a useful mirror.
DOWNLOAD_LIMIT = int(os.environ.get("PITVISOR_DOWNLOAD_LIMIT", "6"))
DOWNLOAD_WINDOW = 3600.0
_download_hits: dict[str, list[float]] = {}
_download_lock = threading.Lock()


def _rate_limited(key: str) -> bool:
    now = time.time()
    with _download_lock:
        hits = [t for t in _download_hits.get(key, []) if now - t < DOWNLOAD_WINDOW]
        if len(hits) >= DOWNLOAD_LIMIT:
            _download_hits[key] = hits
            return True
        hits.append(now)
        _download_hits[key] = hits
        # Opportunistic cleanup so the dict can't grow without bound.
        if len(_download_hits) > 4096:
            _download_hits.clear()
    return False


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

    # ── basic ───────────────────────────────────────────────────────────

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify(status="UP", service="replay"), 200

    @app.route("/config", methods=["GET"])
    def config_dump():
        return jsonify(config.describe()), 200

    # ── replay library ──────────────────────────────────────────────────

    @app.route("/replays", methods=["GET"])
    def list_replays():
        """List downloaded recording files in RECORDING_DIR with their
        session metadata (pulled from each file's header line). Accepts
        both .jsonl and .jsonl.gz (recorder switched to gzip to drop
        recording sizes ~80%)."""
        import gzip as _gz
        rdir = config.RECORDING_DIR
        out = []
        if os.path.isdir(rdir):
            for name in sorted(os.listdir(rdir)):
                if not (name.endswith(".jsonl") or name.endswith(".jsonl.gz")):
                    continue
                path = os.path.join(rdir, name)
                try:
                    st = os.stat(path)
                    header = {}
                    opener = (_gz.open if name.endswith(".gz") else open)
                    with opener(path, "rt", encoding="utf-8") as f:
                        first = f.readline().strip()
                        if first:
                            try:
                                header = json.loads(first)
                            except Exception:
                                header = {}
                    out.append({
                        "name": name,
                        "size": st.st_size,
                        "compressed": name.endswith(".gz"),
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

    @app.route("/replays/download", methods=["POST"])
    def download_replay():
        """Download a past F1 session from the static archive and write
        a JSONL recording into RECORDING_DIR. Body: { year, round, session }.
        This is synchronous — the HTTP request blocks until the download
        completes (typically a few seconds at ~20 MB for a race)."""
        client = request.headers.get("X-Forwarded-For", request.remote_addr or "?")
        client = client.split(",")[0].strip()
        if _rate_limited(client):
            return jsonify({"error": "Too many downloads from this address — try again later."}), 429
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

    # ── per-client replay sessions ─────────────────────────────────────
    #
    # Each session gets its own LiveState instance + feeder thread, and the
    # client reads SSE from /replays/session/<id>/stream. Sessions are
    # capped and garbage-collected on idle (see sessions.py).

    @app.route("/replays/session/start", methods=["POST"])
    def replay_session_start():
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
            sess = REPLAY_REGISTRY.create(path, speed=speed, loop=loop)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({
            "session_id": sess.id,
            "file": os.path.basename(path),
            "speed": speed,
            "loop": loop,
            # Peeled from the recording's header line at create time, so the
            # seek bar is usable before the feeder thread has parsed a byte.
            "duration_sec": sess.duration_sec,
        }), 200

    @app.route("/replays/session/<sid>/stop", methods=["POST", "DELETE"])
    def replay_session_stop(sid):
        ok = REPLAY_REGISTRY.stop(sid)
        if not ok:
            return jsonify({"error": "not found"}), 404
        return jsonify({"status": "ok"}), 200

    @app.route("/replays/session/<sid>/snapshot", methods=["GET"])
    def replay_session_snapshot(sid):
        sess = REPLAY_REGISTRY.get(sid)
        if not sess:
            return jsonify({"error": "not found"}), 404
        sess.touch()
        return jsonify(sess.state.snapshot()), 200

    @app.route("/replays/session/<sid>/stream", methods=["GET"])
    def replay_session_stream(sid):
        sess = REPLAY_REGISTRY.get(sid)
        if not sess:
            return jsonify({"error": "not found"}), 404

        def gen():
            last_ping = time.time()
            last_push = 0.0
            yield ": connected\n\n"
            while True:
                current = REPLAY_REGISTRY.get(sid)
                if current is None:
                    yield "event: closed\ndata: {}\n\n"
                    return
                current.touch()
                now = time.time()
                if now - last_push >= STREAM_INTERVAL:
                    snap = current.state.snapshot()
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

    @app.route("/replays/session/<sid>/telemetry/stream", methods=["GET"])
    def replay_session_telemetry_stream(sid):
        sess = REPLAY_REGISTRY.get(sid)
        if not sess:
            return jsonify({"error": "not found"}), 404
        drivers_arg = request.args.get("drivers", "").strip()
        if not drivers_arg:
            return jsonify({"error": "drivers query param required"}), 400
        requested = [s.strip().upper() for s in drivers_arg.split(",") if s.strip()]

        def _resolve():
            current = REPLAY_REGISTRY.get(sid)
            if current is None:
                return [], None
            snap = current.state.snapshot()
            by_tla = {d.get("tla"): d.get("number") for d in snap["drivers"] if d.get("tla")}
            nums: list[str] = []
            for r in requested:
                if r.isdigit():
                    nums.append(r)
                elif r in by_tla:
                    nums.append(by_tla[r])
            return nums, current

        def gen():
            last_seen: dict[str, int] = {}
            last_ping = time.time()
            yield ": connected\n\n"
            while True:
                nums, current = _resolve()
                if current is None:
                    yield "event: closed\ndata: {}\n\n"
                    return
                current.touch()
                tel = current.state.telemetry_since(nums, last_seen)
                for num, bundle in tel.items():
                    last_seen[num] = bundle.get("seq", 0)
                if any(t.get("samples") for t in tel.values()):
                    snap = current.state.snapshot()
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

    @app.route("/replays/sessions", methods=["GET"])
    def replay_session_list():
        return jsonify({"sessions": REPLAY_REGISTRY.list()}), 200

    # ── replay session playback controls ─────────────────────────────────
    @app.route("/replays/session/<sid>/pause", methods=["POST"])
    def replay_session_pause(sid):
        sess = REPLAY_REGISTRY.get(sid)
        if not sess:
            return jsonify({"error": "not found"}), 404
        sess.pause()
        return jsonify({"status": "ok", "paused": True}), 200

    @app.route("/replays/session/<sid>/resume", methods=["POST"])
    def replay_session_resume(sid):
        sess = REPLAY_REGISTRY.get(sid)
        if not sess:
            return jsonify({"error": "not found"}), 404
        sess.resume()
        return jsonify({"status": "ok", "paused": False}), 200

    @app.route("/replays/session/<sid>/speed", methods=["POST"])
    def replay_session_speed(sid):
        sess = REPLAY_REGISTRY.get(sid)
        if not sess:
            return jsonify({"error": "not found"}), 404
        try:
            body = request.get_json(force=True) or {}
        except Exception:
            body = {}
        speed = body.get("speed")
        if speed is None:
            return jsonify({"error": "speed required"}), 400
        sess.set_speed(speed)
        return jsonify({"status": "ok", "speed": sess.speed}), 200

    @app.route("/replays/session/<sid>/seek", methods=["POST"])
    def replay_session_seek(sid):
        sess = REPLAY_REGISTRY.get(sid)
        if not sess:
            return jsonify({"error": "not found"}), 404
        try:
            body = request.get_json(force=True) or {}
        except Exception:
            body = {}
        t_sec = body.get("t_sec")
        if t_sec is None:
            return jsonify({"error": "t_sec required"}), 400
        sess.seek(t_sec)
        return jsonify({"status": "ok", "t_sec": float(t_sec)}), 200

    return app
