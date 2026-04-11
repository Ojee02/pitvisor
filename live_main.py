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
"""
import os

from live.server import create_app

CACHE_DIR = os.environ.get("PITVISOR_CACHE_DIR", "/home/disinteg/pitvisor/doc_cache")

app = create_app(cache_dir=CACHE_DIR)


if __name__ == "__main__":
    # Dev run — bypass gunicorn, run the Flask dev server directly.
    app.run(host="0.0.0.0", port=5101, threaded=True, debug=False)
