"""Gunicorn entry point for the pitvisor live service.

Run with:
    gunicorn --workers 1 --threads 32 --worker-class gthread --preload \
             --timeout 0 --bind 127.0.0.1:5101 live_main:app

Why these flags:
    --workers 1   → exactly one SignalR connection (multiple workers = multiple
                    duplicate connections to F1's feed, which is bad).
    --threads 32  → each SSE client pins a thread; 32 is enough for our scale.
    --preload     → import the app in the master so the worker thread only
                    starts once per reload cycle.
    --timeout 0   → SSE responses are long-lived; we don't want gunicorn to
                    kill them on its idle timer.

Replay mode (dev):
    PITVISOR_LIVE_REPLAY=recordings/2025_Singapore_Race.jsonl python live_main.py
    PITVISOR_LIVE_REPLAY=... PITVISOR_LIVE_REPLAY_SPEED=20 python live_main.py
    PITVISOR_LIVE_REPLAY=... PITVISOR_LIVE_REPLAY_LOOP=1 python live_main.py

Knobs (see live/config.py for the full list):
    PITVISOR_LIVE_STREAM_INTERVAL       default 1.0
    PITVISOR_LIVE_TEL_INTERVAL          default 0.25
    PITVISOR_LIVE_PRE_WINDOW_MINUTES    default 15
    PITVISOR_LIVE_POST_WINDOW_HOURS     default 3
    PITVISOR_LIVE_CLIENT_TIMEOUT        default 120
    PITVISOR_LIVE_TEL_BUFFER_LEN        default 180
    PITVISOR_CACHE_DIR                  default /home/disinteg/pitvisor/doc_cache
"""
import logging
import os

from live import config
from live.server import create_app

_log = logging.getLogger("pitvisor.live.main")


def _print_config():
    cfg = config.describe()
    _log.info("── pitvisor-live config ──")
    for k, v in cfg.items():
        _log.info("  %-20s %s", k, v)
    if cfg.get("replay_file"):
        _log.info("  ▶ REPLAY MODE ACTIVE — scheduler bypassed")


app = create_app(cache_dir=config.CACHE_DIR)
_print_config()


if __name__ == "__main__":
    # Dev run — bypass gunicorn, run Flask dev server directly.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5101)), threaded=True, debug=False)
