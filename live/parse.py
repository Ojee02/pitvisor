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
import json
import time
import zlib
from typing import Any

from .state import STATE


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
        STATE.set_session_info(info)


def on_session_status(payload: Any):
    d = _merge_dict_stream(payload)
    status = d.get("Status")
    if status:
        STATE.set_session_status(status)


def on_track_status(payload: Any):
    d = _merge_dict_stream(payload)
    status = str(d.get("Status", "1"))
    message = d.get("Message", "")
    STATE.set_track_status(status, message)


def on_lap_count(payload: Any):
    d = _merge_dict_stream(payload)
    STATE.set_lap_count(d.get("CurrentLap"), d.get("TotalLaps"))


def on_extrapolated_clock(payload: Any):
    d = _merge_dict_stream(payload)
    STATE.set_clock(d.get("Remaining"))


def on_weather(payload: Any):
    d = _merge_dict_stream(payload)
    if not d:
        return
    STATE.set_weather({
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
    STATE.append_race_control(msgs)


def on_driver_list(payload: Any):
    d = _merge_dict_stream(payload)
    # dict keyed by driver number. Can be either a full snapshot or a partial
    # update with just a few drivers changing. Merge driver-by-driver.
    for num, info in d.items():
        if num == "_kf" or not isinstance(info, dict):
            continue
        team_colour = info.get("TeamColour")
        color = f"#{team_colour}" if team_colour else None
        STATE.upsert_driver(num, {
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

        STATE.update_driver_timing(num, patch)


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
                STATE.update_driver_timing(num, {
                    "tire_compound": last.get("Compound"),
                    "tire_age": last.get("TotalLaps") or last.get("TyreAge"),
                    "tire_new": last.get("New"),
                    "stints": stints_list,
                })
        if "GridPos" in info:
            STATE.update_driver_timing(num, {"grid_pos": info["GridPos"]})


def on_timing_stats(payload: Any):
    d = _merge_dict_stream(payload)
    lines = d.get("Lines") or {}
    for num, info in lines.items():
        if num == "_kf" or not isinstance(info, dict):
            continue
        pb = info.get("PersonalBestLapTime")
        if isinstance(pb, dict) and pb.get("Value"):
            STATE.update_driver_timing(num, {"pb_lap": pb["Value"]})


def on_position_z(payload: Any):
    """Position.z: zlib-wrapped positions for every car over a short window.
    Format after decode:
        {Position: [{Timestamp, Entries: {<num>: {X, Y, Z, Status}}}]}
    We only keep the latest sample per driver (intermediate samples are
    resent rapidly anyway)."""
    data = _decompress_z(payload) if isinstance(payload, str) else _merge_dict_stream(payload)
    if not data:
        return
    positions = data.get("Position") or []
    if not positions:
        return
    latest = positions[-1]
    entries = latest.get("Entries") or {}
    for num, p in entries.items():
        if not isinstance(p, dict):
            continue
        x = p.get("X")
        y = p.get("Y")
        if x is None or y is None:
            continue
        STATE.update_driver_position(str(num), float(x), float(y), p.get("Status"))


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
                STATE.append_driver_telemetry(str(num), sample)


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
