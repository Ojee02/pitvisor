"""Async job store + on-disk result cache for the pitvisor backend.

Why this exists
---------------
A cold analysis costs 40-90 s in FastF1 even with a warm doc_cache, and
Cloudflare cuts any proxied request at 100 s with a 524. So the frontend
never waits on one long request: it POSTs /job, gets an id back in
milliseconds, and polls /job/<id> until the result is ready.

The same store doubles as the result cache. A job id is the SHA-1 of its
kind + inputs, so two people asking for the same chart share one entry, and
an entry that finished recently is returned without recomputing anything.
Historic seasons are immutable so they cache for 30 days; the current
season changes during a weekend so it caches for 6 hours.

Everything is a JSON file under JOB_DIR, which keeps it working across
threads and across a future multi-worker deployment.
"""
import hashlib
import json
import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor

_log = logging.getLogger("pitvisor.jobs")

BASE = os.path.dirname(os.path.abspath(__file__))
JOB_DIR = os.environ.get("PITVISOR_JOB_DIR", os.path.join(BASE, "job_cache"))
IMAGE_DIR = os.environ.get("PITVISOR_JOB_IMAGE_DIR", os.path.join(BASE, "job_images"))
MAX_WORKERS = int(os.environ.get("PITVISOR_JOB_WORKERS", "4"))

# Historic sessions never change; the current one does during a weekend.
TTL_PAST = int(os.environ.get("PITVISOR_JOB_TTL_PAST", str(30 * 86400)))
TTL_CURRENT = int(os.environ.get("PITVISOR_JOB_TTL_CURRENT", str(6 * 3600)))

# A job left in queued/running longer than this is assumed to have died
# with its worker (gunicorn restarts, OOM kill) rather than still be
# working. Cold analyses take 90-120 s, so there is a lot of headroom.
STALE_AFTER = int(os.environ.get("PITVISOR_JOB_STALE_AFTER", "900"))

TERMINAL = ("done", "error")

_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="pitvisor-job")
_lock = threading.Lock()
_sweeper_started = False


def ttl_for(input_list) -> int:
    try:
        year = int((input_list or {}).get("year"))
    except (TypeError, ValueError):
        return TTL_CURRENT
    now = time.localtime().tm_year
    return TTL_PAST if year < now else TTL_CURRENT


def make_id(kind: str, payload: dict) -> str:
    blob = json.dumps({"k": kind, "p": payload}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _path(jid: str) -> str:
    return os.path.join(JOB_DIR, jid + ".json")


def _read(jid: str):
    try:
        with open(_path(jid), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write(jid: str, state: dict) -> None:
    os.makedirs(JOB_DIR, exist_ok=True)
    tmp = _path(jid) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, _path(jid))


def get(jid: str):
    if not (isinstance(jid, str) and len(jid) == 40 and all(c in "0123456789abcdef" for c in jid)):
        return None
    return _read(jid)


def _fresh(state, ttl: int) -> bool:
    if not state or state.get("status") not in TERMINAL:
        return False
    return (time.time() - float(state.get("finished_at") or 0)) < ttl


def in_flight(state) -> bool:
    """True if another thread is genuinely still working on this job."""
    if not state or state.get("status") not in ("queued", "running"):
        return False
    ref = state.get("started_at") or state.get("created_at") or 0
    return (time.time() - float(ref)) < STALE_AFTER


def _run(jid: str, runner, meta: dict):
    state = dict(meta)
    state.update({"status": "running", "started_at": time.time()})
    try:
        _write(jid, state)
    except Exception:
        _log.exception("could not persist job start for %s", jid)
    result = None
    image_src = None
    try:
        out = runner()
        if isinstance(out, tuple):
            result, image_src = out
        else:
            result = out
        if image_src:
            os.makedirs(IMAGE_DIR, exist_ok=True)
            dest = os.path.join(IMAGE_DIR, jid + ".png")
            shutil.move(image_src, dest)
            # Merge rather than replace: renderers hand back metadata
            # (a timestamp for the download filename) alongside the file.
            result = {**(result if isinstance(result, dict) else {}),
                      "image": "/image/" + jid}
        state.update({"status": "done", "result": result,
                      "finished_at": time.time()})
    except Exception as exc:
        _log.warning("job %s failed: %s", jid, exc)
        state.update({"status": "error", "error": str(exc),
                      "finished_at": time.time()})
    try:
        _write(jid, state)
    except Exception:
        _log.exception("could not persist job end for %s", jid)


def submit(kind: str, payload: dict, runner, ttl: int = None):
    """Queue `runner` for the given (kind, payload) and return (jid, state).

    Returns immediately. If a fresh finished job already exists for this
    payload the runner is never called and the cached state — result
    included — comes straight back.
    """
    ttl = TTL_CURRENT if ttl is None else ttl
    jid = make_id(kind, payload)
    _start_sweeper()

    with _lock:
        state = _read(jid)
        if _fresh(state, ttl):
            return jid, state
        if in_flight(state):
            # Someone else is already on it.
            return jid, state
        state = {"status": "queued", "created_at": time.time(),
                 "kind": kind, "payload": payload}
        _write(jid, state)

    _executor.submit(_run, jid, runner, state)
    return jid, state


def compute(kind: str, payload: dict, runner, ttl: int = None, timeout: float = 900.0):
    """Blocking variant of submit(): queue (or join) the job and wait for
    it. Used by the synchronous /data and / endpoints that the Discord bot
    and any legacy client still call."""
    ttl = TTL_CURRENT if ttl is None else ttl
    jid = make_id(kind, payload)
    _start_sweeper()

    with _lock:
        state = _read(jid)
        if _fresh(state, ttl):
            return jid, state
        if in_flight(state):
            pass  # join the in-flight run below
        else:
            state = {"status": "queued", "created_at": time.time(),
                     "kind": kind, "payload": payload}
            _write(jid, state)
            _executor.submit(_run, jid, runner, state)

    deadline = time.time() + timeout
    while time.time() < deadline:
        state = _read(jid)
        if state and state.get("status") in TERMINAL:
            return jid, state
        time.sleep(0.4)
    return jid, {"status": "error", "error": "timed out waiting for the analysis to finish",
                 "finished_at": time.time()}


def image_path(jid: str):
    if not (isinstance(jid, str) and len(jid) == 40):
        return None
    path = os.path.join(IMAGE_DIR, jid + ".png")
    return path if os.path.isfile(path) else None


# ── housekeeping ───────────────────────────────────────────────────────────

def _sweep():
    now = time.time()
    for directory, ttl in ((JOB_DIR, TTL_PAST), (IMAGE_DIR, TTL_PAST)):
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            path = os.path.join(directory, name)
            try:
                if now - os.path.getmtime(path) > ttl:
                    os.remove(path)
            except OSError:
                pass


def _sweep_loop():
    while True:
        time.sleep(600)
        try:
            _sweep()
        except Exception:
            _log.exception("job sweep failed")


def _start_sweeper():
    global _sweeper_started
    if _sweeper_started:
        return
    with _lock:
        if _sweeper_started:
            return
        _sweeper_started = True
        threading.Thread(target=_sweep_loop, name="pitvisor-job-gc", daemon=True).start()
