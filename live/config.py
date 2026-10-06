"""Live service tunable knobs — all configurable via environment variables.

Defaults match the original values baked into the module; override any of
these in the systemd unit (`Environment="PITVISOR_LIVE_STREAM_INTERVAL=0.5"`)
or on the command line (`PITVISOR_LIVE_STREAM_INTERVAL=0.5 python live_main.py`).

The `live_main.py` entrypoint prints the effective config on startup so you
can verify overrides took effect without reading logs in depth.
"""
import os


def _f(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _i(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _b(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# ── SSE pacing ────────────────────────────────────────────────────────

# Seconds between full snapshot pushes on /live/stream. 0.33 ≈ 3 Hz which
# matches the native Position.z rate so the track map dots glide smoothly
# without squashing multiple position updates into a single frame. At replay
# speeds above 1× this still compresses movement (replay_speed × 0.33 of
# session time per frame) — bump it lower during high-speed replay if you
# want silky motion.
STREAM_INTERVAL = _f("PITVISOR_LIVE_STREAM_INTERVAL", 0.33)

# Seconds between telemetry pushes on /live/telemetry/stream.
# 0.25 = 4 Hz — matches the native ~3 Hz rate of CarData with a small buffer.
TEL_INTERVAL = _f("PITVISOR_LIVE_TEL_INTERVAL", 0.25)

# SSE keepalive comment ping interval. Keeps idle proxies (nginx, Cloudflare)
# from closing the connection as idle. Shorten if you see disconnects.
KEEPALIVE_INTERVAL = _f("PITVISOR_LIVE_KEEPALIVE_INTERVAL", 15.0)


# ── State buffers ─────────────────────────────────────────────────────

# Max telemetry samples kept in the rolling per-driver buffer. Native rate
# is ~3 Hz so 180 = ~60 s of history.
TEL_BUFFER_LEN = _i("PITVISOR_LIVE_TEL_BUFFER_LEN", 180)

# How many race control messages to retain in memory.
RACE_CONTROL_KEEP = _i("PITVISOR_LIVE_RACE_CONTROL_KEEP", 50)


# ── Directories ───────────────────────────────────────────────────────

# Where recordings are stored (downloaded from F1's archive).
RECORDING_DIR = os.environ.get(
    "PITVISOR_LIVE_RECORDING_DIR",
    "/home/disinteg/pitvisor/backend/recordings",
)

# FastF1 cache directory used for track-outline extraction.
CACHE_DIR = os.environ.get(
    "PITVISOR_CACHE_DIR",
    "/home/disinteg/pitvisor/doc_cache",
)


# ── Replay playback ────────────────────────────────────────────────────

# Seconds to skip into the recording before playback starts. F1 recordings
# include ~20-30 min of pre-session activity (installation laps, formation,
# grid line-up) before the race actually starts — at 1x replay speed that
# looks like the page is frozen.
REPLAY_SEEK_SEC = _f("PITVISOR_LIVE_REPLAY_SEEK_SEC", 0.0)

# If true, automatically skip ahead to the first record where SessionStatus
# becomes "Started" (green flag). Overrides REPLAY_SEEK_SEC if that's 0.
REPLAY_SKIP_TO_START = _b("PITVISOR_LIVE_REPLAY_SKIP_TO_START", True)


def describe() -> dict:
    """Return a dict summary of the current effective config. Used by
    live_main.py at startup and by the /live/config endpoint."""
    return {
        "stream_interval": STREAM_INTERVAL,
        "tel_interval": TEL_INTERVAL,
        "keepalive_interval": KEEPALIVE_INTERVAL,
        "tel_buffer_len": TEL_BUFFER_LEN,
        "race_control_keep": RACE_CONTROL_KEEP,
        "recording_dir": RECORDING_DIR,
        "cache_dir": CACHE_DIR,
        "replay_seek_sec": REPLAY_SEEK_SEC,
        "replay_skip_to_start": REPLAY_SKIP_TO_START,
    }
