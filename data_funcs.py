"""
data_funcs.py — Data extraction functions for the /data JSON API.
Each function returns a JSON-serializable dict.
Image rendering stays in funcs.py for the Discord bot.
"""
import fastf1
from fastf1 import plotting, utils
from fastf1.core import Laps
import pandas as pd
import numpy as np
import requests
from utils import get_sess

MAX_TEL_POINTS = 500

# Fallback team colors when no session is available (FastF1 3.8+ requires session)
TEAM_COLORS = {
    'Red Bull Racing': '#3671C6', 'Red Bull': '#3671C6',
    'Ferrari': '#E8002D', 'Scuderia Ferrari': '#E8002D',
    'Mercedes': '#27F4D2', 'Mercedes-AMG Petronas F1 Team': '#27F4D2',
    'McLaren': '#FF8000', 'McLaren F1 Team': '#FF8000',
    'Aston Martin': '#229971', 'Aston Martin Aramco F1 Team': '#229971',
    'Alpine F1 Team': '#FF87BC', 'Alpine': '#FF87BC',
    'Williams': '#64C4FF', 'Williams Racing': '#64C4FF',
    'RB F1 Team': '#6692FF', 'AlphaTauri': '#6692FF', 'Toro Rosso': '#6692FF',
    'Kick Sauber': '#52E252', 'Alfa Romeo': '#C92D4B', 'Sauber': '#52E252',
    'Haas F1 Team': '#B6BABD', 'Haas': '#B6BABD',
    'Racing Point': '#F596C8', 'Force India': '#F596C8',
    'Renault': '#FFF500',
}


def _driver_color(driver, session=None, default='#888888'):
    try:
        return fastf1.plotting.get_driver_color(driver, session=session)
    except Exception:
        pass
    try:
        return fastf1.plotting.driver_color(driver)
    except Exception:
        pass
    # Last resort: try to get team color from session results
    if session is not None:
        try:
            team = session.results.loc[session.results['Abbreviation'] == driver, 'TeamName'].iloc[0]
            return _team_color(team, session, default)
        except Exception:
            pass
    return default


def _team_color(team, session=None, default='#888888'):
    try:
        return fastf1.plotting.get_team_color(team, session=session)
    except Exception:
        pass
    try:
        return fastf1.plotting.get_team_color(team)
    except Exception:
        return TEAM_COLORS.get(team, default)


def _downsample(df, n=MAX_TEL_POINTS):
    if len(df) <= n:
        return df
    step = max(1, len(df) // n)
    return df.iloc[::step].reset_index(drop=True)


def _td_sec(td):
    if pd.isna(td):
        return None
    return td.total_seconds()


# ─── FASTEST ────────────────────────────────────────────────────────────────

def fastest_data(input_list):
    yr, rc, sn = input_list["year"], input_list["race"], input_list["session"]
    session = get_sess(yr, rc, sn)

    drivers = pd.unique(session.laps['Driver'])
    fl = []
    for drv in drivers:
        try:
            fl.append(session.laps.pick_driver(drv).pick_fastest())
        except Exception:
            continue
    fastest_laps = Laps(fl).sort_values(by='LapTime').reset_index(drop=True)
    pole = fastest_laps.pick_fastest()
    fastest_laps['Delta'] = fastest_laps['LapTime'] - pole['LapTime']

    from timple.timedelta import strftimedelta
    pole_str = strftimedelta(pole['LapTime'], '%m:%s.%ms')

    data = []
    for _, lap in fastest_laps.iterlaps():
        data.append({
            "driver": lap['Driver'],
            "team": lap['Team'],
            "delta": _td_sec(lap['Delta']),
            "color": _team_color(lap['Team'], session),
        })

    return {
        "type": "fastest",
        "title": f"{yr} {rc} {sn} — Fastest Laps",
        "pole_time": pole_str,
        "pole_driver": pole['Driver'],
        "data": data,
    }


# ─── RESULTS ────────────────────────────────────────────────────────────────

def results_data(input_list):
    yr, rc, sn = input_list["year"], input_list["race"], input_list["session"]
    session = get_sess(yr, rc, sn)
    res = session.results
    if res.empty:
        raise Exception("The data you are trying to access has not been loaded yet.")

    sn_name = session.event.get_session_name(sn).lower()
    if sn_name in ("qualifying", "sprint shootout"):
        cols = ['Position', 'BroadcastName', 'TeamName', 'Q1', 'Q2', 'Q3']
    elif sn_name in ("race", "sprint"):
        cols = ['Position', 'BroadcastName', 'TeamName', 'Points', 'Status']
    else:
        cols = ['BroadcastName', 'TeamName']

    rows = []
    for _, row in res[cols].iterrows():
        r = {}
        for c in cols:
            v = row[c]
            if isinstance(v, pd.Timedelta):
                r[c] = str(v)[-12:-3] if not pd.isna(v) else None
            elif pd.isna(v) if not isinstance(v, str) else False:
                r[c] = None
            elif isinstance(v, (np.integer, np.floating)):
                r[c] = float(v)
            else:
                r[c] = v
        rows.append(r)

    return {
        "type": "results",
        "title": f"{yr} {rc} {sn}",
        "columns": [c.replace('BroadcastName', 'Driver').replace('TeamName', 'Team') for c in cols],
        "column_keys": cols,
        "rows": rows,
    }


# ─── SCHEDULE ───────────────────────────────────────────────────────────────

def schedule_data(input_list):
    yr = input_list["year"]
    schedule = fastf1.get_event_schedule(yr)
    if schedule.empty:
        raise Exception("The data you are trying to access has not been loaded yet.")

    events = []
    for _, row in schedule.iterrows():
        events.append({
            "name": row['EventName'],
            "date": str(row['EventDate'])[:10],
            "format": str(row['EventFormat']).replace("_", " ").title(),
        })

    return {"type": "schedule", "title": f"{yr} Schedule", "events": events}


# ─── EVENT ──────────────────────────────────────────────────────────────────

def event_data(input_list):
    yr, rc = input_list["year"], input_list["race"]
    event = fastf1.get_event(yr, rc)
    if event.empty:
        raise Exception("The data you are trying to access has not been loaded yet.")

    fields = []
    for key in ['RoundNumber', 'EventName', 'Country', 'Location', 'EventDate',
                'EventFormat', 'Session1', 'Session1Date', 'Session2', 'Session2Date',
                'Session3', 'Session3Date', 'Session4', 'Session4Date',
                'Session5', 'Session5Date']:
        val = event.get(key, None)
        if val is not None and str(val) != 'nan':
            label = key.replace('EventName', 'Event Name').replace('EventDate', 'Event Date') \
                       .replace('EventFormat', 'Event Format').replace('RoundNumber', 'Round')
            for i in range(1, 6):
                label = label.replace(f'Session{i}Date', f'Session {i} Date') \
                             .replace(f'Session{i}', f'Session {i}')
            fields.append({"key": label, "value": str(val)})

    return {
        "type": "event",
        "title": f"{yr} {event.get('EventName', rc)}",
        "fields": fields,
    }


# ─── LAPS ───────────────────────────────────────────────────────────────────

def laps_data(input_list):
    yr, rc, sn = input_list["year"], input_list["race"], input_list["session"]
    drivers = input_list["drivers"]
    session = get_sess(yr, rc, sn)

    result = []
    for drv in drivers:
        temp = session.laps.pick_driver(drv)
        color = _driver_color(drv, session)
        pts = []
        for _, lap in temp.iterrows():
            lt = _td_sec(lap['LapTime'])
            if lt is not None:
                pts.append({"lap": int(lap['LapNumber']), "time": round(lt, 3)})
        result.append({"name": drv, "color": color, "data": pts})

    vs = " vs ".join(drivers)
    return {
        "type": "laps",
        "title": f"Laps — {yr} {rc} {sn} — {vs}",
        "drivers": result,
    }


# ─── SPEED vs TIME ──────────────────────────────────────────────────────────

def time_data(input_list):
    yr, rc, sn = input_list["year"], input_list["race"], input_list["session"]
    drivers = input_list["drivers"]
    lap = input_list.get("lap")
    session = get_sess(yr, rc, sn)

    result = []
    for drv in drivers:
        if not lap:
            fast = session.laps.pick_driver(drv).pick_fastest()
        else:
            dl = session.laps.pick_driver(drv)
            fast = dl[dl['LapNumber'] == int(lap)].iloc[0]
        car = _downsample(fast.get_car_data())
        pts = []
        for _, row in car.iterrows():
            t = _td_sec(row['Time'])
            if t is not None:
                pts.append({"time": round(t, 3), "speed": float(row['Speed'])})
        result.append({"name": drv, "color": _driver_color(drv, session), "data": pts})

    vs = " vs ".join(drivers)
    lap_str = "Fastest Lap" if not lap else f"Lap {lap}"
    return {
        "type": "time",
        "title": f"Speed/Time — {lap_str} — {yr} {rc} {sn} — {vs}",
        "drivers": result,
    }


# ─── SPEED vs DISTANCE ─────────────────────────────────────────────────────

def distance_data(input_list):
    yr, rc, sn = input_list["year"], input_list["race"], input_list["session"]
    drivers = input_list["drivers"]
    lap = input_list.get("lap")
    session = get_sess(yr, rc, sn)

    result = []
    for drv in drivers:
        if not lap:
            fast = session.laps.pick_driver(drv).pick_fastest()
        else:
            dl = session.laps.pick_driver(drv)
            fast = dl[dl['LapNumber'] == int(lap)].iloc[0]
        car = _downsample(fast.get_car_data().add_distance())
        pts = []
        for _, row in car.iterrows():
            pts.append({"distance": round(float(row['Distance']), 1), "speed": float(row['Speed'])})
        result.append({"name": drv, "color": _driver_color(drv, session), "data": pts})

    vs = " vs ".join(drivers)
    lap_str = "Fastest Lap" if not lap else f"Lap {lap}"
    return {
        "type": "distance",
        "title": f"Speed/Distance — {lap_str} — {yr} {rc} {sn} — {vs}",
        "drivers": result,
    }


# ─── DELTA ──────────────────────────────────────────────────────────────────

def delta_data(input_list):
    yr, rc, sn = input_list["year"], input_list["race"], input_list["session"]
    d1, d2 = input_list.get("driver1"), input_list.get("driver2")
    lap1, lap2 = input_list.get("lap1"), input_list.get("lap2")

    session = get_sess(yr, rc, sn)
    laps = session.laps

    if not d1: d1 = laps.pick_fastest()['Driver']
    if not d2: d2 = laps.pick_fastest()['Driver']

    dd1 = laps.pick_driver(d1).pick_fastest() if not lap1 else \
          laps.pick_driver(d1).loc[lambda x: x['LapNumber'] == int(lap1)].iloc[0]
    dd2 = laps.pick_driver(d2).pick_fastest() if not lap2 else \
          laps.pick_driver(d2).loc[lambda x: x['LapNumber'] == int(lap2)].iloc[0]

    delta_arr, ref_tel, cmp_tel = utils.delta_time(dd1, dd2)

    step = max(1, len(ref_tel) // MAX_TEL_POINTS)
    data = []
    for i in range(0, min(len(ref_tel), len(cmp_tel), len(delta_arr)), step):
        data.append({
            "distance": round(float(ref_tel['Distance'].iloc[i]), 1),
            "speed_d1": float(ref_tel['Speed'].iloc[i]),
            "speed_d2": float(cmp_tel['Speed'].iloc[i]),
            "delta": round(float(delta_arr[i]), 4),
        })

    l1s = "Fastest Lap" if not lap1 else f"Lap {lap1}"
    l2s = "Fastest Lap" if not lap2 else f"Lap {lap2}"
    c2 = _driver_color(d2, session) if d1 != d2 else '#777777'

    return {
        "type": "delta",
        "title": f"Delta — {yr} {rc} {sn} — {d1} ({l1s}) vs {d2} ({l2s})",
        "driver1": {"name": d1, "color": _driver_color(d1, session), "lap": l1s},
        "driver2": {"name": d2, "color": c2, "lap": l2s},
        "data": data,
    }


# ─── TELEMETRY ──────────────────────────────────────────────────────────────

def tel_data(input_list):
    yr, rc, sn = input_list["year"], input_list["race"], input_list["session"]
    d1, d2 = input_list.get("driver1"), input_list.get("driver2")
    lap1, lap2 = input_list.get("lap1"), input_list.get("lap2")

    session = get_sess(yr, rc, sn)
    laps = session.laps

    if not d1: d1 = laps.pick_fastest()['Driver']
    if not d2: d2 = laps.pick_fastest()['Driver']

    drv1_lap = laps.pick_driver(d1).pick_fastest() if not lap1 else \
               laps.pick_driver(d1).loc[lambda x: x['LapNumber'] == int(lap1)].iloc[0]
    drv2_lap = laps.pick_driver(d2).pick_fastest() if not lap2 else \
               laps.pick_driver(d2).loc[lambda x: x['LapNumber'] == int(lap2)].iloc[0]

    car1 = drv1_lap.get_car_data().add_distance()
    car2 = drv2_lap.get_car_data().add_distance()
    delta_arr, _, _ = utils.delta_time(drv1_lap, drv2_lap)

    def drs_binary(series):
        return [(1 if (v >= 10 and v % 2 == 0) else 0) for v in series]

    drs1 = drs_binary(car1['DRS'])
    drs2 = drs_binary(car2['DRS'])

    n = min(len(car1), len(car2), len(delta_arr))
    step = max(1, n // MAX_TEL_POINTS)

    data = []
    for i in range(0, n, step):
        data.append({
            "distance": round(float(car1['Distance'].iloc[i]), 1),
            "d1_speed": float(car1['Speed'].iloc[i]),
            "d2_speed": float(car2['Speed'].iloc[i]),
            "d1_rpm": float(car1['RPM'].iloc[i]),
            "d2_rpm": float(car2['RPM'].iloc[i]),
            "d1_gear": int(car1['nGear'].iloc[i]),
            "d2_gear": int(car2['nGear'].iloc[i]),
            "d1_throttle": float(car1['Throttle'].iloc[i]),
            "d2_throttle": float(car2['Throttle'].iloc[i]),
            "d1_brake": int(car1['Brake'].iloc[i]),
            "d2_brake": int(car2['Brake'].iloc[i]) * -1,
            "d1_drs": drs1[i],
            "d2_drs": drs2[i] * -1,
            "delta": round(float(delta_arr[i]) * -1, 4),
        })

    c2 = _driver_color(d2, session) if d1 != d2 else '#777777'
    l1s = "Fastest Lap" if not lap1 else f"Lap {lap1}"
    l2s = "Fastest Lap" if not lap2 else f"Lap {lap2}"

    return {
        "type": "telemetry",
        "title": f"Telemetry — {yr} {rc} {sn} — {d1} ({l1s}) vs {d2} ({l2s})",
        "driver1": {"name": d1, "color": _driver_color(d1, session), "lap": l1s},
        "driver2": {"name": d2, "color": c2, "lap": l2s},
        "data": data,
    }


# ─── CORNERING ──────────────────────────────────────────────────────────────

def cornering_data(input_list):
    yr, rc, sn = input_list["year"], input_list["race"], input_list["session"]
    d1, d2 = input_list.get("driver1"), input_list.get("driver2")
    dist1, dist2 = input_list.get("dist1"), input_list.get("dist2")
    lap1, lap2 = input_list.get("lap1"), input_list.get("lap2")

    session = get_sess(yr, rc, sn)
    laps = session.laps

    if not d1: d1 = laps.pick_fastest()['Driver']
    if not d2: d2 = laps.pick_fastest()['Driver']

    ref_car = laps.pick_driver(d1).pick_fastest().get_car_data().add_distance()
    maxdist = float(ref_car['Distance'].iloc[-1])
    if not dist1: dist1 = 0
    if not dist2: dist2 = maxdist
    if dist1 > dist2: dist1, dist2 = dist2, dist1

    def get_tel(driver, lap_num):
        dl = laps.pick_driver(driver)
        if not lap_num:
            return dl.pick_fastest().get_car_data().add_distance()
        return dl[dl['LapNumber'] == int(lap_num)].iloc[0].get_car_data().add_distance()

    tel1 = get_tel(d1, lap1)
    tel2 = get_tel(d2, lap2)

    # Classify actions
    for tel in [tel1, tel2]:
        tel.loc[tel['Brake'] > 0, 'Action'] = 'Brake'
        tel.loc[tel['Throttle'] == 100, 'Action'] = 'Full Throttle'
        tel.loc[(tel['Brake'] == 0) & (tel['Throttle'] < 100), 'Action'] = 'Cornering'

    def action_segments(tel):
        tel = tel.copy()
        tel['AID'] = (tel['Action'] != tel['Action'].shift(1)).cumsum()
        acts = tel[['AID', 'Action', 'Distance']].groupby(['AID', 'Action']).max('Distance').reset_index()
        acts['Delta'] = acts['Distance'] - acts['Distance'].shift(1)
        acts.iloc[0, acts.columns.get_loc('Delta')] = acts.iloc[0]['Distance']
        segs = []
        start = 0
        for _, r in acts.iterrows():
            segs.append({"action": r['Action'], "start": round(float(start), 1), "width": round(float(r['Delta']), 1)})
            start += r['Delta']
        return segs

    # Speed data in range
    t1f = tel1[(tel1['Distance'] >= dist1) & (tel1['Distance'] <= dist2)]
    t2f = tel2[(tel2['Distance'] >= dist1) & (tel2['Distance'] <= dist2)]
    t1d = _downsample(t1f)
    t2d = _downsample(t2f)

    speed = []
    for i in range(min(len(t1d), len(t2d))):
        speed.append({
            "distance": round(float(t1d['Distance'].iloc[i]), 1),
            "speed_d1": float(t1d['Speed'].iloc[i]),
            "speed_d2": float(t2d['Speed'].iloc[i]),
        })

    avg1 = float(np.mean(t1f['Speed']))
    avg2 = float(np.mean(t2f['Speed']))
    if avg1 > avg2:
        speed_text = f"{d1} {round(avg1 - avg2, 2)}km/h faster"
    else:
        speed_text = f"{d2} {round(avg2 - avg1, 2)}km/h faster"

    c2 = _driver_color(d2, session) if d1 != d2 else '#777777'
    l1s = "Fastest Lap" if not lap1 else f"Lap {lap1}"
    l2s = "Fastest Lap" if not lap2 else f"Lap {lap2}"

    return {
        "type": "cornering",
        "title": f"Cornering — {yr} {rc} {sn} — {d1} ({l1s}) vs {d2} ({l2s})",
        "driver1": {"name": d1, "color": _driver_color(d1, session), "actions": action_segments(tel1)},
        "driver2": {"name": d2, "color": c2, "actions": action_segments(tel2)},
        "speed_data": speed,
        "speed_text": speed_text,
        "distance_range": [float(dist1), float(dist2)],
        "action_colors": {"Full Throttle": "#22C55E", "Cornering": "#888888", "Brake": "#EF4444"},
    }


# ─── STRATEGY ───────────────────────────────────────────────────────────────

def strategy_data(input_list):
    yr, rc = input_list["year"], input_list["race"]
    race = fastf1.get_session(yr, rc, 'R')
    race.load()

    stints = race.laps[['Driver', 'Stint', 'Compound', 'LapNumber']].groupby(
        ['Driver', 'Stint', 'Compound']).count().reset_index()
    stints = stints.rename(columns={'LapNumber': 'StintLength'}).sort_values(by=['Stint'])

    if yr <= 2018:
        cc = {'HYPERSOFT': '#FFAACC', 'ULTRASOFT': '#772277', 'SUPERSOFT': '#FF3333',
              'SOFT': '#FFF200', 'MEDIUM': '#EBEBEB', 'HARD': '#07A6F5',
              'SUPERHARD': '#CC6600', 'INTERMEDIATE': '#39B54A', 'WET': '#0033EE'}
    else:
        cc = {'SOFT': '#FF3333', 'MEDIUM': '#FFF200', 'HARD': '#EBEBEB',
              'INTERMEDIATE': '#39B54A', 'WET': '#0033EE'}

    drivers_data = []
    for driver in race.results['Abbreviation']:
        ds = stints.loc[stints['Driver'] == driver]
        sl = []
        start = 0
        for _, s in ds.iterrows():
            comp = s['Compound']
            length = int(s['StintLength'])
            sl.append({"compound": comp, "color": cc.get(comp, '#888'), "start": start, "length": length})
            start += length
        drivers_data.append({"name": driver, "stints": sl})

    return {
        "type": "strategy",
        "title": f"Race Strategy — {race.event.year} {race.event['EventName']}",
        "drivers": drivers_data,
        "compound_colors": cc,
    }


# ─── POSITIONS ──────────────────────────────────────────────────────────────

def positions_data(input_list):
    yr, rc = input_list["year"], input_list["race"]
    safety_car = input_list.get("safety_car", True)

    session = get_sess(yr, rc, 'Race')

    drivers_data = []
    for drv in session.drivers:
        dl = session.laps.pick_driver(drv)
        if len(dl) == 0:
            continue
        abb = dl['Driver'].iloc[0]
        pts = []
        for _, lap in dl.iterrows():
            pos = lap['Position']
            if not pd.isna(pos):
                pts.append({"lap": int(lap['LapNumber']), "position": int(pos)})
        drivers_data.append({"name": abb, "color": _driver_color(abb, session), "data": pts})

    sc_periods = []
    if safety_car:
        try:
            rcm = session.race_control_messages
            sc_on = vsc_on = False
            sc_start = vsc_start = None
            for _, msg in rcm.iterrows():
                status = str(msg.get('Status', '')).upper() if 'Status' in rcm.columns else ''
                msg_time = msg.get('Time')
                if msg_time is None:
                    continue
                lt = session.laps[['LapNumber', 'Time']].dropna()
                if len(lt) == 0:
                    continue
                lap_num = int(lt.loc[(lt['Time'] - msg_time).abs().idxmin(), 'LapNumber'])

                if 'SAFETY CAR DEPLOYED' in status:
                    sc_on, sc_start = True, lap_num
                elif 'SAFETY CAR IN' in status or (sc_on and 'ENDING' in status):
                    if sc_on and sc_start is not None:
                        sc_periods.append({"type": "SC", "start": sc_start, "end": lap_num})
                    sc_on = False
                elif 'VIRTUAL SAFETY CAR DEPLOYED' in status or 'VSC DEPLOYED' in status:
                    vsc_on, vsc_start = True, lap_num
                elif 'VSC ENDING' in status or (vsc_on and 'ENDING' in status):
                    if vsc_on and vsc_start is not None:
                        sc_periods.append({"type": "VSC", "start": vsc_start, "end": lap_num})
                    vsc_on = False
        except Exception:
            pass

    return {
        "type": "positions",
        "title": f"{yr} {rc} — Position Changes",
        "drivers": drivers_data,
        "safety_car": sc_periods,
    }


# ─── RACE TRACE ────────────────────────────────────────────────────────────

def rt_data(input_list):
    yr, rc = input_list["year"], input_list["race"]
    drivers = input_list["drivers"]
    session = get_sess(yr, rc, 'Race')

    pd.options.mode.chained_assignment = None
    laps = session.laps
    laps['LapTimeSeconds'] = laps['LapTime'].dt.total_seconds()
    avg = laps.groupby(['DriverNumber', 'Driver'])['LapTimeSeconds'].mean()
    laps['Difference'] = laps['LapTimeSeconds'] - avg.min()
    laps['Cumulative'] = laps.groupby('Driver')['Difference'].cumsum()

    result = []
    for drv in drivers:
        temp = laps.loc[laps['Driver'] == drv][['LapNumber', 'Cumulative']]
        pts = []
        for _, r in temp.iterrows():
            if not pd.isna(r['Cumulative']):
                pts.append({"lap": int(r['LapNumber']), "cumulative": round(float(r['Cumulative']), 3)})
        result.append({"name": drv, "color": _driver_color(drv, session), "data": pts})

    vs = " vs ".join(drivers)
    return {
        "type": "racetrace",
        "title": f"Race Trace — {yr} {rc} — {vs}",
        "drivers": result,
    }


# ─── BATTLES ────────────────────────────────────────────────────────────────

def battles_data(input_list):
    yr = input_list["year"]
    drivers_filter = input_list["drivers"]

    all_quali = pd.DataFrame()
    team_drivers = {}
    rnd = 1

    while True:
        url = f'https://api.jolpi.ca/ergast/f1/{yr}/{rnd}/qualifying.json'
        resp = requests.get(url).json()
        races = resp['MRData']['RaceTable']['Races']
        if not races:
            break

        results = races[0]['QualifyingResults']
        qr = {'round': rnd}

        for r in results:
            try:
                code = r['Driver']['code']
            except Exception:
                code = r['Driver']['driverId'].upper()[:3]
            if code not in drivers_filter:
                continue
            pos = int(r['position'])
            team = r['Constructor']['name']

            if team not in team_drivers:
                team_drivers[team] = [code]
            elif code not in team_drivers[team]:
                team_drivers[team].append(code)
            qr[code] = pos

        all_quali = pd.concat([all_quali, pd.DataFrame([qr])], ignore_index=True)
        rnd += 1

    data = []
    for team, drvs in team_drivers.items():
        if len(drvs) < 2:
            continue
        qr = all_quali[drvs]
        fastest = qr.dropna().idxmin(axis=1)
        counts = fastest.value_counts().reset_index()
        color = _team_color(team)
        for _, row in counts.iterrows():
            data.append({"driver": row['index'], "team": team, "score": int(row['count']), "color": color})

    return {
        "type": "battles",
        "title": f"{yr} Teammate Qualifying Battle",
        "data": data,
    }


# ─── TRACK ──────────────────────────────────────────────────────────────────

def track_data(input_list):
    yr, rc = input_list["year"], input_list["race"]
    event = fastf1.get_event(yr, rc)

    sessions = []
    for i in range(1, 6):
        name = event.get(f'Session{i}', None)
        date = event.get(f'Session{i}Date', None)
        if name and str(name) != 'nan':
            sessions.append({"name": str(name), "date": str(date)[:10] if date else "TBD"})

    return {
        "type": "track",
        "title": f"{event['EventName']}",
        "data": {
            "circuit": event['EventName'],
            "country": event['Country'],
            "location": event['Location'],
            "round": int(event['RoundNumber']),
            "format": str(event.get('EventFormat', 'N/A')).replace('_', ' ').title(),
            "sessions": sessions,
        },
    }


# ─── DRIVER STATS ──────────────────────────────────────────────────────────

def driver_stats_data(input_list):
    yr = input_list["year"]
    drivers = input_list["drivers"]
    driver = drivers[0] if isinstance(drivers, list) else drivers

    url = f'https://api.jolpi.ca/ergast/f1/{yr}/results.json?limit=1000'
    resp = requests.get(url).json()
    races = resp['MRData']['RaceTable']['Races']

    wins = podiums = dnfs = points = fastest_laps = races_entered = 0
    grid_positions = []
    finish_positions = []

    for race in races:
        for r in race['Results']:
            if r['Driver'].get('code', '').upper() != driver.upper():
                continue
            races_entered += 1
            pos = int(r['position'])
            grid = int(r['grid'])
            pts = float(r['points'])
            status = r['status']

            points += pts
            grid_positions.append(grid)
            finish_positions.append(pos)
            if pos == 1: wins += 1
            if pos <= 3: podiums += 1
            if status != 'Finished' and not status.startswith('+'): dnfs += 1
            if r.get('FastestLap', {}).get('rank') == '1': fastest_laps += 1

    if races_entered == 0:
        return {"type": "driverstats", "title": f"{driver} — {yr}", "data": None}

    return {
        "type": "driverstats",
        "title": f"{driver} — {yr}",
        "data": {
            "driver": driver, "year": yr, "races": races_entered,
            "points": points, "wins": wins, "podiums": podiums,
            "fastest_laps": fastest_laps, "dnfs": dnfs,
            "avg_grid": round(sum(grid_positions) / len(grid_positions), 1),
            "avg_finish": round(sum(finish_positions) / len(finish_positions), 1),
        },
    }


# ─── DISPATCH TABLE ─────────────────────────────────────────────────────────

DATA_FUNCS = {
    "fastest": fastest_data,
    "results": results_data,
    "schedule": schedule_data,
    "event": event_data,
    "laps": laps_data,
    "time": time_data,
    "distance": distance_data,
    "delta": delta_data,
    "telemetry": tel_data,
    "cornering": cornering_data,
    "strategy": strategy_data,
    "positions": positions_data,
    "racetrace": rt_data,
    "battles": battles_data,
    "track": track_data,
    "driverstats": driver_stats_data,
}
