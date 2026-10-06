"""Gunicorn entry point for the pitvisor replay service.

Run with:
    gunicorn --workers 1 --threads 32 --worker-class gthread \
             --timeout 0 --bind 127.0.0.1:5101 live_main:app

Why these flags:
    --workers 1   -> all sessions share one process: one set of parsed
                     recordings, one track-outline cache, one session
                     registry. Multiple workers would multiply the memory
                     cost of every open replay for no throughput gain.
    --threads 32  -> each SSE client pins a thread; 32 is enough for our scale.
    --timeout 0   -> SSE responses are long-lived; we don't want gunicorn to
                     kill them on its idle timer.

NO --preload: with preload, app import runs in the gunicorn master. After
fork the workers don't inherit threads, so anything the app starts at import
time belongs to a process that can never serve a request.

These imports touch numpy/pandas/fastf1/idna on the MAIN thread before any
replay feeder thread exists. numpy 2.x's lazy __getattr__ deadlocks when
`import numpy.rec` is first triggered from a worker thread, and idna's
uts46data submodule fails with a circular-import error for the same reason.
Forcing them here resolves both once, up front.
"""
import logging
import os

import numpy  # noqa: F401,E402
import numpy.rec  # noqa: F401,E402 - the specific submodule that deadlocks
import numpy.core  # noqa: F401,E402
import pandas  # noqa: F401,E402
import fastf1  # noqa: F401,E402
import idna  # noqa: F401,E402
import idna.uts46data  # noqa: F401,E402

from live import config
from live.server import create_app

_log = logging.getLogger("pitvisor.live.main")


def _print_config():
    cfg = config.describe()
    _log.info("-- pitvisor-replay config --")
    for k, v in cfg.items():
        _log.info("  %-20s %s", k, v)


app = create_app(cache_dir=config.CACHE_DIR)
_print_config()


if __name__ == "__main__":
    # Dev run — bypass gunicorn, run Flask dev server directly.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5101)), threaded=True, debug=False)
