"""Gunicorn entry point for the pitvisor live service.

Run with:
    gunicorn --workers 1 --threads 32 --worker-class gthread \
             --timeout 0 --bind 127.0.0.1:5101 live_main:app

Why these flags:
    --workers 1   → exactly one SignalR connection (multiple workers = multiple
                    duplicate connections to F1's feed, which is bad).
    --threads 32  → each SSE client pins a thread; 32 is enough for our scale.
    --timeout 0   → SSE responses are long-lived; we don't want gunicorn to
                    kill them on its idle timer.

NO --preload: with preload, app import (and therefore WORKER.start()'s
background scheduler thread) runs in the gunicorn master. After fork
the workers don't inherit threads, so the master's scheduler updates a
LiveState that the HTTP-serving worker can never see. Active flag stays
false, no live data ever reaches /status. Letting each worker import
the app itself keeps the scheduler thread + the HTTP handlers in the
same process so STATE is actually shared.

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

# Pre-warm numpy + pandas + fastf1 on the main thread BEFORE any replay
# feeder thread can run. Without this, numpy's lazy __getattr__ hits an
# infinite-recursion bug when `import numpy.rec as rec` is triggered
# from a worker thread (the classic numpy 2.x threading race). Touching
# numpy.rec here forces numpy's deferred loader to resolve it once, on
# the main thread, so every subsequent access is a plain attribute read.
import numpy  # noqa: F401,E402
import numpy.rec  # noqa: F401,E402 — this is the specific submodule that deadlocks
import numpy.core  # noqa: F401,E402
import pandas  # noqa: F401,E402
import fastf1  # noqa: F401,E402

# Same lazy-import-from-worker-thread footgun as numpy.rec: idna's
# uts46data submodule is loaded on first IDN host normalization. When
# the FIRST caller is a worker thread (fastf1 schedule fetch on a
# scheduler tick, or a SignalR HTTPS connection), we hit
#   "ImportError: cannot import name 'uts46data' from partially
#    initialized module 'idna.uts46data' (most likely due to a circular
#    import)"
# and every subsequent IDNA encode in that worker raises. Forcing the
# load on the main thread here resolves the module before any worker
# can race on it.
import idna  # noqa: F401,E402
import idna.uts46data  # noqa: F401,E402

# Crank up signalrcore + websocket logging so when Position.z stops
# flowing we can see whether frames are arriving on the wire and our
# dispatch dropped them, or whether F1 stopped sending. This is noisy
# but invaluable while diagnosing the new authenticated endpoint.
import logging as _logging  # noqa: E402
_logging.getLogger("SignalRCoreClient").setLevel(_logging.DEBUG)
_logging.getLogger("websocket").setLevel(_logging.WARNING)  # too noisy at DEBUG

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
