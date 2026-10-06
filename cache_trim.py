"""Keep the FastF1 doc_cache inside a size budget.

doc_cache holds FastF1's per-session timing payloads. Unattended it grows
without limit — a single season of races plus telemetry is multiple
gigabytes, and it once reached a point where it had to be deleted outright,
which then made every analysis cold again.

So the cache is capped instead of deleted: files are aged out oldest-first
until the tree fits `PITVISOR_DOC_CACHE_MAX_GB` (default 4 GB). Anything
written in the last few hours is spared, so an analysis that has just
finished is never immediately evicted, and a load in progress is never
yanked out from under itself.

Runs once at startup and then every six hours in a daemon thread.
"""
import logging
import os
import threading
import time

_log = logging.getLogger("pitvisor.cache")

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("PITVISOR_DOC_CACHE_DIR", os.path.join(BASE, "doc_cache"))
MAX_BYTES = int(float(os.environ.get("PITVISOR_DOC_CACHE_MAX_GB", "4")) * (1024 ** 3))
MIN_AGE_SEC = int(float(os.environ.get("PITVISOR_DOC_CACHE_MIN_AGE_HOURS", "6")) * 3600)
INTERVAL_SEC = int(float(os.environ.get("PITVISOR_DOC_CACHE_TRIM_HOURS", "6")) * 3600)

_started = False


def _walk(root):
    """(mtime, size, path) for every file under root, oldest access irrelevant."""
    out = []
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(dirpath, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            out.append((st.st_mtime, st.st_size, path))
    return out


def trim(max_bytes: int = MAX_BYTES, min_age_sec: int = MIN_AGE_SEC) -> int:
    """Delete oldest cache files until the tree fits. Returns files removed."""
    if not os.path.isdir(CACHE_DIR) or max_bytes <= 0:
        return 0

    files = _walk(CACHE_DIR)
    total = sum(size for _mtime, size, _path in files)
    if total <= max_bytes:
        return 0

    files.sort()  # oldest first
    now = time.time()
    removed = 0
    freed = 0
    for mtime, size, path in files:
        if total - freed <= max_bytes:
            break
        if now - mtime < min_age_sec:
            continue  # too fresh — don't evict what was just written
        try:
            os.remove(path)
        except OSError:
            continue
        freed += size
        removed += 1

    if removed:
        _log.info("doc_cache trimmed: removed %d files (%.1f MB), now %.1f MB / cap %.1f MB",
                  removed, freed / 1e6, (total - freed) / 1e6, max_bytes / 1e6)

    # Sweep directories the deletions emptied, oldest first.
    for dirpath, dirs, names in os.walk(CACHE_DIR, topdown=False):
        if dirpath == CACHE_DIR:
            continue
        if not dirs and not names:
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
    return removed


def _loop():
    while True:
        try:
            trim()
        except Exception as exc:   # never let the sweeper kill the app
            _log.warning("doc_cache trim failed: %s", exc)
        time.sleep(INTERVAL_SEC)


def start():
    """Kick off the sweeper once per process."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="pitvisor-cache-gc", daemon=True).start()
    # Trim immediately so a cache left over from before the cap existed
    # comes down without waiting six hours.
    try:
        trim()
    except Exception as exc:
        _log.warning("initial doc_cache trim failed: %s", exc)
