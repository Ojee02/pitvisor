"""Topic decoders for F1 live-timing SignalR messages.

Each public function takes the raw payload for a topic and mutates LiveState
accordingly. The payloads follow F1's undocumented but stable format; fields
marked "delta" only arrive on change so we merge rather than overwrite.

`CarData.z` / `Position.z` are base64(zlib(json)) — we decode inline here.

Channel IDs for CarData:
    0  → RPM
    2  → Speed (km/h)
    3  → Gear
    4  → Throttle (%)
    5  → Brake (0/1)
    45 → DRS code (>=10 means on)
"""
import base64
import datetime as dt
import json
import threading
import time
import zlib
from typing import Any

from .state import STATE, LiveState


# Thread-local state context. Live-mode SignalR dispatches run on the
# live client thread and write to the module-level STATE singleton (the
# default). Per-client replay feeder threads set this to their own
# LiveState instance so each replay session is isolated. current_state()
# reads the active context; parsers call it instead of referencing STATE
# directly so they can be reused across live and per-client replay modes.
_ctx = threading.local()


def current_state() -> LiveState:
    return getattr(_ctx, "state", None) or STATE


def bind_state(state: LiveState):
    """Bind the current thread to write into `state`. Call this at the top
    of a replay feeder thread before dispatching records."""
    _ctx.state = state


# ─── helpers ─────────────────────────────────────────────────────────────

def _decompress_z(payload: str) -> dict | None:
    """Decode base64+zlib(-raw)+json payload used by CarData.z / Position.z."""
    if not isinstance(payload, str) or not payload:
        return None
    try:
        raw = zlib.decompress(base64.b64decode(payload), -zlib.MAX_WBITS)
        return json.loads(raw.decode("utf-8-sig"))
    except Exception:
        return None


def _merge_dict_stream(payload: Any) -> dict:
    """F1 sometimes sends `{_kf: true, ...}` full snapshots and sometimes
    sparse deltas. Either way it's a dict — this is a passthrough guard."""
    return payload if isinstance(payload, dict) else {}


def _iso_to_ms(ts: Any) -> int | None:
    """Convert an ISO 8601 timestamp string (with or without trailing 'Z',
    with or without fractional seconds) to milliseconds since the Unix
    epoch. Returns None if the input is malformed. F1 uses these on every
    Position.z / CarData.z sample and they're the only per-sample clock
    we have that's consistent across messages."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        s = ts
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # Python's fromisoformat only accepts up to 6-digit fractional
        # seconds; F1 timestamps have 7. Truncate to 6.
        if "." in s:
            head, tail = s.split(".", 1)
            # tail looks like "2531331+00:00" — split frac from tz suffix
            if "+" in tail:
                frac, tz = tail.split("+", 1)
                tz = "+" + tz
            elif "-" in tail:
                frac, tz = tail.split("-", 1)
                tz = "-" + tz
            else:
                frac, tz = tail, ""
            frac = frac[:6]
            s = f"{head}.{frac}{tz}"
        d = dt.datetime.fromisoformat(s)
        return int(d.timestamp() * 1000)
    except Exception:
        return None


def _time_to_seconds(s: str | None) -> float | None:
    """Parse a '1:23.456' or '23.456' lap/sector time into seconds."""
    if not s or not isinstance(s, str):
        return None
    try:
        if ":" in s:
            m, rest = s.split(":", 1)
            return int(m) * 60 + float(rest)
        return float(s)
    except (ValueError, TypeError):
        return None


# ─── topic handlers ──────────────────────────────────────────────────────

def on_session_info(payload: Any):
    info = _merge_dict_stream(payload)
    if info:
        current_state().set_session_info(info)


def on_session_status(payload: Any):
    d = _merge_dict_stream(payload)
    status = d.get("Status")
    if status:
        current_state().set_session_status(status)


def on_track_status(payload: Any):
    d = _merge_dict_stream(payload)
    status = str(d.get("Status", "1"))
    message = d.get("Message", "")
    current_state().set_track_status(status, message)


def on_lap_count(payload: Any):
    d = _merge_dict_stream(payload)
    current_state().set_lap_count(d.get("CurrentLap"), d.get("TotalLaps"))


def on_extrapolated_clock(payload: Any):
    d = _merge_dict_stream(payload)
    current_state().set_clock(d.get("Remaining"))


def on_weather(payload: Any):
    d = _merge_dict_stream(payload)
    if not d:
        return
    current_state().set_weather({
        "air_temp": _float(d.get("AirTemp")),
        "track_temp": _float(d.get("TrackTemp")),
        "humidity": _float(d.get("Humidity")),
        "pressure": _float(d.get("Pressure")),
        "rainfall": _float(d.get("Rainfall")),
        "wind_speed": _float(d.get("WindSpeed")),
        "wind_direction": _float(d.get("WindDirection")),
    })


def on_race_control(payload: Any):
    d = _merge_dict_stream(payload)
    msgs = d.get("Messages") or []
    # Messages arrives as either a list or a dict-of-index. Normalize.
    if isinstance(msgs, dict):
        msgs = list(msgs.values())
    if not isinstance(msgs, list):
        return
    current_state().append_race_control(msgs)


def on_driver_list(payload: Any):
    d = _merge_dict_stream(payload)
    # dict keyed by driver number. Can be either a full snapshot or a partial
    # update with just a few drivers changing. Merge driver-by-driver.
    for num, info in d.items():
        if num == "_kf" or not isinstance(info, dict):
            continue
        team_colour = info.get("TeamColour")
        color = f"#{team_colour}" if team_colour else None
        current_state().upsert_driver(num, {
            "number": str(info.get("RacingNumber") or num),
            "tla": info.get("Tla"),
            "full_name": info.get("FullName") or (
                f"{info.get('FirstName','')} {info.get('LastName','')}".strip() or None
            ),
            "team": info.get("TeamName"),
            "color": color,
            "headshot": info.get("HeadshotUrl"),
            "line": info.get("Line"),
        })


def on_timing_data(payload: Any):
    """TimingData is the main per-driver live timing feed. Format:
        {Lines: {<driverNumber>: {Position, GapToLeader, IntervalToPositionAhead,
                                  LastLapTime: {Value}, BestLapTime: {Value},
                                  Sectors: {0..2: {Value, ...}}, NumberOfLaps,
                                  InPit, PitOut, Status, Retired, Stopped, ...}}}
    """
    d = _merge_dict_stream(payload)
    lines = d.get("Lines") or d
    if not isinstance(lines, dict):
        return
    for num, info in lines.items():
        if num == "_kf" or not isinstance(info, dict):
            continue

        patch: dict = {}

        if "Position" in info:
            try:
                patch["position"] = int(info["Position"])
            except (ValueError, TypeError):
                pass

        if "NumberOfLaps" in info:
            patch["lap"] = info["NumberOfLaps"]

        if "GapToLeader" in info:
            patch["gap_leader"] = info["GapToLeader"]
        if "IntervalToPositionAhead" in info:
            iv = info["IntervalToPositionAhead"]
            patch["interval"] = iv.get("Value") if isinstance(iv, dict) else iv

        last = info.get("LastLapTime")
        if isinstance(last, dict) and last.get("Value"):
            patch["last_lap"] = last["Value"]
            patch["last_lap_personal_best"] = bool(last.get("PersonalFastest"))
            patch["last_lap_overall_best"] = bool(last.get("OverallFastest"))
        best = info.get("BestLapTime")
        if isinstance(best, dict) and best.get("Value"):
            patch["best_lap"] = best["Value"]

        sectors = info.get("Sectors")
        if isinstance(sectors, dict):
            s_out: list[dict] = []
            for i in ("0", "1", "2"):
                sec = sectors.get(i)
                if isinstance(sec, dict):
                    s_out.append({
                        "time": sec.get("Value"),
                        "pb": bool(sec.get("PersonalFastest")),
                        "ob": bool(sec.get("OverallFastest")),
                    })
            if s_out:
                patch["sectors"] = s_out

        # status flags
        for k, out_k in (
            ("InPit", "in_pit"), ("PitOut", "pit_out"),
            ("Retired", "retired"), ("Stopped", "stopped"),
            ("KnockedOut", "knocked_out"),
        ):
            if k in info:
                patch[out_k] = bool(info[k])

        if "Status" in info:
            patch["timing_status"] = info["Status"]

        current_state().update_driver_timing(num, patch)


def on_timing_app_data(payload: Any):
    """TimingAppData carries tire/stint info:
        {Lines: {<num>: {Stints: {<idx>: {Compound, TotalLaps, TyreAge, New, ...}}, GridPos, ...}}}
    """
    d = _merge_dict_stream(payload)
    lines = d.get("Lines") or {}
    for num, info in lines.items():
        if num == "_kf" or not isinstance(info, dict):
            continue
        stints = info.get("Stints")
        if stints:
            if isinstance(stints, dict):
                stints_list = [stints[k] for k in sorted(stints.keys()) if k != "_kf"]
            else:
                stints_list = list(stints)
            if stints_list:
                last = stints_list[-1] if isinstance(stints_list[-1], dict) else {}
                current_state().update_driver_timing(num, {
                    "tire_compound": last.get("Compound"),
                    "tire_age": last.get("TotalLaps") or last.get("TyreAge"),
                    "tire_new": last.get("New"),
                    "stints": stints_list,
                })
        if "GridPos" in info:
            current_state().update_driver_timing(num, {"grid_pos": info["GridPos"]})


def on_timing_stats(payload: Any):
    d = _merge_dict_stream(payload)
    lines = d.get("Lines") or {}
    for num, info in lines.items():
        if num == "_kf" or not isinstance(info, dict):
            continue
        pb = info.get("PersonalBestLapTime")
        if isinstance(pb, dict) and pb.get("Value"):
            current_state().update_driver_timing(num, {"pb_lap": pb["Value"]})


def on_position_z(payload: Any):
    """Position.z: zlib-wrapped positions for every car over a short window.
    Format after decode:
        {Position: [{Timestamp, Entries: {<num>: {X, Y, Z, Status}}}]}

    Each Position.z message contains MULTIPLE timestamped samples, not a
    single snapshot. We push every sample into each driver's rolling
    position buffer so the frontend has enough data to interpolate smooth
    continuous motion — keeping only the latest would make the cars
    appear to stop and jump between Position.z message arrivals."""
    data = _decompress_z(payload) if isinstance(payload, str) else _merge_dict_stream(payload)
    if not data:
        return
    positions = data.get("Position") or []
    if not positions:
        return
    for sample in positions:
        if not isinstance(sample, dict):
            continue
        t_ms = _iso_to_ms(sample.get("Timestamp"))
        if t_ms is None:
            continue
        entries = sample.get("Entries") or {}
        for num, p in entries.items():
            if not isinstance(p, dict):
                continue
            x = p.get("X")
            y = p.get("Y")
            if x is None or y is None:
                continue
            current_state().append_driver_position_sample(
                str(num), t_ms, float(x), float(y), p.get("Status"),
            )


def on_car_data_z(payload: Any):
    """CarData.z decodes to:
        {Entries: [{Utc, Cars: {<num>: {Channels: {"0": rpm, "2": speed, "3": gear, "4": throttle, "5": brake, "45": drs}}}}]}
    We append one sample per entry into the per-driver rolling buffer."""
    data = _decompress_z(payload) if isinstance(payload, str) else _merge_dict_stream(payload)
    if not data:
        return
    entries = data.get("Entries") or []
    if not isinstance(entries, list):
        return
    for e in entries:
        if not isinstance(e, dict):
            continue
        utc = e.get("Utc")
        cars = e.get("Cars") or {}
        for num, car in cars.items():
            ch = (car or {}).get("Channels") or {}
            sample = {
                "t": utc,
                "rpm": _int(ch.get("0")),
                "speed": _int(ch.get("2")),
                "gear": _int(ch.get("3")),
                "throttle": _int(ch.get("4")),
                "brake": _int(ch.get("5")),
                "drs": _int(ch.get("45")),
            }
            # drop fields that are None so we don't thrash the buffer
            sample = {k: v for k, v in sample.items() if v is not None}
            if sample:
                sample.setdefault("t", utc or time.time())
                current_state().append_driver_telemetry(str(num), sample)


# ─── type coercion ──────────────────────────────────────────────────────

def _float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _int(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


# ─── dispatch table ──────────────────────────────────────────────────────

DISPATCH = {
    "SessionInfo": on_session_info,
    "SessionStatus": on_session_status,
    "TrackStatus": on_track_status,
    "LapCount": on_lap_count,
    "ExtrapolatedClock": on_extrapolated_clock,
    "WeatherData": on_weather,
    "RaceControlMessages": on_race_control,
    "DriverList": on_driver_list,
    "TimingData": on_timing_data,
    "TimingAppData": on_timing_app_data,
    "TimingStats": on_timing_stats,
    "Position.z": on_position_z,
    "CarData.z": on_car_data_z,
}


def dispatch(topic: str, payload: Any):
    fn = DISPATCH.get(topic)
    if fn is None:
        return
    try:
        fn(payload)
    except Exception as e:
        # do not crash the SignalR thread on a single malformed message
        import logging
        logging.getLogger("pitvisor.live").warning(
            "parse error in topic=%s: %s", topic, e
        )
