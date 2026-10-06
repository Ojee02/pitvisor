import numpy as np
import datetime as dt
from collections import OrderedDict
from time import time
from time import ctime
import fastf1 as ff1
import pathlib
import platform
import threading
from pymongo import MongoClient
import os
from dotenv import load_dotenv
platform.system()

load_dotenv()

connection_string = os.getenv("connection_string")
db_name = os.getenv("db_name")

client = MongoClient(connection_string)
db = client[db_name]

### UTIL FUNCTIONS ###

# get_datetime helper
def get_time():
    return ctime(time())

# get current time
def get_datetime():
    datetime = get_time()
    datetime = datetime.replace(" ", "-")
    datetime = datetime.replace(":", ".")
    return datetime

# get parth of file
dir_path = r"" + str(pathlib.Path(__file__).parent.resolve())

# get path separator for os
def get_path():
    return os.sep


# ─── FastF1 session loading ────────────────────────────────────────────────
#
# session.load() is the single most expensive thing this app does: even with
# a warm doc_cache a full load is ~90 s, of which ~45 s is telemetry nobody
# asked for. Two things fix that:
#
#   1. Profile. Callers say what they need. Laps-only is the default shape
#      for every standings/timing chart; only the telemetry charts ask for
#      car data, and only positions asks for race control messages.
#   2. A process-wide LRU keyed by session. The first analysis of a session
#      pays for it; the next six are dict lookups. A cached session that was
#      loaded laps-only is upgraded in place (load(laps=False, ...)) rather
#      than rebuilt, so upgrading never repeats the 40 s timing pass.
#
# Loads are serialised per session so two threads can't build the same
# session twice, and bounded by a semaphore so a burst of jobs can't open
# six telemetry sessions at once and blow the memory ceiling.

_SESSION_CACHE: "OrderedDict[tuple, tuple]" = OrderedDict()
_SESSION_CACHE_LOCK = threading.Lock()
_SESSION_KEY_LOCKS: dict[tuple, threading.Lock] = {}
_SESSION_LOAD_SEMAPHORE = threading.Semaphore(int(os.environ.get("PITVISOR_SESSION_LOAD_CONCURRENCY", "2")))
SESSION_CACHE_SIZE = int(os.environ.get("PITVISOR_SESSION_CACHE_SIZE", "4"))


def _cache_key(yr, rc, sn):
    try:
        return (int(yr), str(rc), str(sn))
    except (TypeError, ValueError):
        return (str(yr), str(rc), str(sn))


def _key_lock(key):
    with _SESSION_CACHE_LOCK:
        lock = _SESSION_KEY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SESSION_KEY_LOCKS[key] = lock
        return lock


def _session_object(yr, rc, sn, session_type, sn_resolved):
    """Build (but do not load) the FastF1 Session for these inputs."""
    if session_type == "1":
        return ff1.get_testing_session(yr, 1, sn_resolved)
    if session_type == "2":
        return ff1.get_testing_session(yr, 2, sn_resolved)
    return ff1.get_session(yr, rc, sn_resolved)


# load the session
def get_sess(yr, rc, sn, *, telemetry=True, weather=True, messages=True):
    """Return a loaded FastF1 Session.

    Defaults load everything, which is what the matplotlib functions in
    funcs.py expect. The JSON data functions pass telemetry/weather/messages
    explicitly to skip the ~45 s of telemetry work they don't use.
    """
    # Callers pass form values straight through, so year can arrive as a
    # string. Everything below compares it against int literals
    # (`elif yr >= 2023`), which raises TypeError on a str.
    try:
        yr = int(yr)
    except (TypeError, ValueError):
        pass
    try:
        rc = int(rc)
    except:
        pass

    session_type = "normal"

    if yr == 2020:
        if rc == "Pre-Season Test 1":
            session_type = "1"
        elif rc == "Pre-Season Test 2":
            session_type = "2"
    elif yr == 2021:
        if rc == "Pre-Season Test":
            session_type = "1"
    elif yr == 2022:
        if rc == "Pre-Season Track Session":
            session_type = "1"
        elif rc == "Pre-Season Test":
            session_type = "2"
    elif yr >= 2023:
        if "Pre-Season" in str(rc):
            session_type = "1"

    sn_resolved = sn
    if session_type == "1" or session_type == "2":
        if sn == "Day 1":
            sn_resolved = 1
        elif sn == "Day 2":
            sn_resolved = 2
        elif sn == "Day 3":
            sn_resolved = 3

    want = (bool(telemetry), bool(weather), bool(messages))
    key = _cache_key(yr, rc, sn)

    # Fast path: already loaded with a superset of what's being asked for.
    with _SESSION_CACHE_LOCK:
        entry = _SESSION_CACHE.get(key)
        if entry is not None:
            _SESSION_CACHE.move_to_end(key)
    if entry is not None and all(have or not w for w, have in zip(want, entry[1])):
        return entry[0]

    with _key_lock(key):
        # Re-check inside the per-session lock — someone may have loaded it
        # while we waited.
        with _SESSION_CACHE_LOCK:
            entry = _SESSION_CACHE.get(key)
            if entry is not None:
                _SESSION_CACHE.move_to_end(key)
        if entry is None:
            session = _session_object(yr, rc, sn, session_type, sn_resolved)
            with _SESSION_LOAD_SEMAPHORE:
                session.load(laps=True, telemetry=want[0], weather=want[1], messages=want[2])
            have = want
        else:
            session, have = entry
            missing = tuple(w and not h for w, h in zip(want, have))
            if any(missing):
                with _SESSION_LOAD_SEMAPHORE:
                    # laps=False: the timing pass already ran, so this only
                    # pays for the pieces we skipped last time.
                    session.load(laps=False, telemetry=missing[0],
                                 weather=missing[1], messages=missing[2])
                have = tuple(a or b for a, b in zip(have, want))

        with _SESSION_CACHE_LOCK:
            _SESSION_CACHE[key] = (session, have)
            _SESSION_CACHE.move_to_end(key)
            if have[0]:
                # A telemetry session holds car_data + position_data for the
                # whole race — hundreds of MB. Keep at most one of those
                # resident, or a handful of charts puts us over the memory
                # ceiling. Laps-only sessions are a few MB each and stack.
                for other in list(_SESSION_CACHE):
                    if other != key and _SESSION_CACHE[other][1][0]:
                        _SESSION_CACHE.pop(other, None)
            while len(_SESSION_CACHE) > SESSION_CACHE_SIZE:
                _SESSION_CACHE.popitem(last=False)

    try:
        fix = session.laps.pick_fastest()
    except:
        pass
    return session


def invalidate_session_cache():
    """Drop every cached FastF1 session (used after cache_dir changes)."""
    with _SESSION_CACHE_LOCK:
        _SESSION_CACHE.clear()

# enable cache
if os.path.exists(dir_path + get_path() + "doc_cache"):
    ff1.Cache.enable_cache(dir_path + get_path() + "doc_cache")

###

### gets the years for which data is available for each function ###
def get_years(func):
    years = []
    func = func.lower()
    if func == "results" or func == "schedule" or func == "drivers" or func == "points":
        for i in range(1950, dt.datetime.now().year+1):
            years.append(i)
    elif func == "constructors":
        for i in range(1958, dt.datetime.now().year+1):
            years.append(i)
    else:
        for i in range(2018, dt.datetime.now().year+1):
            years.append(i)
    return years[::-1]

### get races of a given year ###
def get_races(yr):
    collection_name = "races"
    collection = db[collection_name]
    doc = collection.find_one({"year": int(yr)})
    return doc["races"]

### gets sessions of a grand prix weekend ###
def get_sessions(yr, rc):
    
    if "Pre-Season" in rc:
        return ['Day 1', 'Day 2', 'Day 3']
    else:
        yr = int(yr)
        sessions = []
        i=1
        while True:
            sess = 'Session' + str(i)
            fastf1_obj = ff1.get_event(yr, rc)
            try: 
                sessions.append((getattr(fastf1_obj, sess)))
            except:
                break
            i+=1
        return sessions
 
### gets drivers of a session ###
def get_drivers(yr, rc, sn):
    session = get_sess(yr, rc, sn, telemetry=False, weather=False, messages=False)
    laps = session.laps
    ls = set(tuple(x) for x in laps[['Driver']].values.tolist())
    lis = [x[0] for x in ls]
    return lis

### gets laps of a session ###
def get_laps(yr, rc, sn):
    session = get_sess(yr, rc, sn, telemetry=False, weather=False, messages=False)
    laps = session.laps
    ls = set(tuple(x) for x in laps[['LapNumber']].values.tolist())
    lis = sorted([int(x[0]) for x in ls])
    max = int(np.max(lis))
    res = []
    for i in range(1, max+1):
        res.append(i)
    return res

### gets distance of a session ###
def get_distance(yr, rc, sn):
    session = get_sess(yr, rc, sn, telemetry=True, weather=False, messages=False)
    laps = session.laps
    car_data = laps.pick_fastest().get_car_data().add_distance()
    maxdist = int(np.max(car_data['Distance']))
    res = []
    for i in range(0, maxdist+1, 100):
        res.append(i)
    res.append(maxdist)
    return res

### db ###

# A year's race list is a cache of FastF1's schedule, and calendars move
# mid-season. Reconciling on read keeps the dropdown honest without ever
# putting a network call on the request path: the first time this process
# asks about a year it kicks a background merge, and the next request
# picks up whatever FastF1 has that Mongo did not.
_races_reconciled = set()
_races_reconcile_lock = threading.Lock()


def kick_races_reconcile(yr):
    try:
        key = int(yr)
    except (TypeError, ValueError):
        return
    with _races_reconcile_lock:
        if key in _races_reconciled:
            return
        _races_reconciled.add(key)

    def _run():
        try:
            from update import update_races   # deferred: update.py imports this module
            update_races(key)
        except Exception as exc:
            print("races reconcile failed for %s: %s" % (key, exc))

    threading.Thread(target=_run, daemon=True, name="races-reconcile").start()


def get_races_from_db(func, yr):
    kick_races_reconcile(yr)
    collection = db["races"]
    doc = collection.find_one({"year": int(yr)})
    if doc is None:
        return []
    races = doc["races"]
    res = []
    for race in races:
        if race not in res:
            res.append(race)
    return res


def get_race_options(yr):
    """Race dropdown options: the FastF1 event name plus where it is held.

    The name is what every analysis passes back to fastf1.get_event(), so
    it must stay exactly as FastF1 spells it. The venue exists because a
    relocated event is otherwise invisible — in 2026 the Bahrain Grand
    Prix ran at Sepang and there was no way to find it by looking for
    Malaysia.
    """
    kick_races_reconcile(yr)
    names = get_races_from_db(None, yr)
    venues = {}
    try:
        doc = db["races"].find_one({"year": int(yr)}) or {}
        venues = doc.get("venues") or {}
    except Exception:
        pass
    return [{"name": n, "location": venues.get(n)} for n in names]

def get_sessions_from_db(yr, rc):
    # Always return full session list from FastF1
    return get_sessions(yr, rc)

def get_drivers_from_db(yr, rc, sn):
    if rc is None and sn is None:
        # All drivers for the year — try DB first
        collection = db["data"]
        docs = collection.find({"year": int(yr)})
        drivers = []
        for doc in docs:
            for driver in doc.get("drivers", []):
                if driver not in drivers:
                    drivers.append(driver)
        if drivers:
            return drivers
        # Fallback: fetch from Ergast API
        try:
            import requests
            url = f'https://api.jolpi.ca/ergast/f1/{yr}/drivers.json?limit=100'
            resp = requests.get(url, timeout=15).json()
            driver_list = resp['MRData']['DriverTable']['Drivers']
            return [d.get('code', d['driverId'][:3].upper()) for d in driver_list]
        except Exception:
            return []
    # Try DB first
    collection = db["data"]
    sn_cap = sn.capitalize() if sn else sn
    doc = collection.find_one({"year": int(yr), "race": rc, "session": sn_cap})
    if doc and "drivers" in doc:
        return doc["drivers"]
    # Fallback to live FastF1, then cache
    drivers = get_drivers(yr, rc, sn)
    if drivers:
        _cache_session_data(yr, rc, sn_cap, drivers=drivers)
    return drivers

def get_laps_from_db(yr, rc, sn):
    if rc is None and sn is None:
        return []
    collection = db["data"]
    sn_cap = sn.capitalize() if sn else sn
    doc = collection.find_one({"year": int(yr), "race": rc, "session": sn_cap})
    if doc and "laps" in doc:
        return doc["laps"]
    laps = get_laps(yr, rc, sn)
    if laps:
        _cache_session_data(yr, rc, sn_cap, laps=laps)
    return laps

def get_distance_from_db(yr, rc, sn):
    if rc is None and sn is None:
        return []
    collection = db["data"]
    sn_cap = sn.capitalize() if sn else sn
    doc = collection.find_one({"year": int(yr), "race": rc, "session": sn_cap})
    if doc and "distance" in doc:
        return doc["distance"]
    dist = get_distance(yr, rc, sn)
    if dist:
        _cache_session_data(yr, rc, sn_cap, distance=dist)
    return dist

def _cache_session_data(yr, rc, sn, drivers=None, laps=None, distance=None):
    """Upsert session data into the DB so subsequent lookups are instant."""
    try:
        collection = db["data"]
        doc = collection.find_one({"year": int(yr), "race": rc, "session": sn})
        update = {}
        if drivers is not None: update["drivers"] = drivers
        if laps is not None: update["laps"] = laps
        if distance is not None: update["distance"] = distance
        if doc:
            collection.update_one({"year": int(yr), "race": rc, "session": sn}, {"$set": update})
        else:
            collection.insert_one({"year": int(yr), "race": rc, "session": sn, **update})
    except Exception:
        pass

def upload_drivers_standings(year, file):
    with open(file, 'rb') as f:
        file_data = f.read()
        collection_name = "drivers_standings"
        collection = db[collection_name]
        doc = collection.find_one({"year": year})
        if doc:
            collection.update_one({"year": year}, {"$set": {"file": file_data}})
            print(f"Updated {year} Drivers Standings")
        else:
            collection.insert_one({"year": year, "file": file_data})
            print(f"Inserted {year} Drivers Standings")
        f.close()
        os.remove(file)
    return
    
def upload_constructors_standings(year, file):
    with open(file, 'rb') as f:
        file_data = f.read()
        collection_name = "constructors_standings"
        collection = db[collection_name]
        doc = collection.find_one({"year": year})
        if doc:
            collection.update_one({"year": year}, {"$set": {"file": file_data}})
            print(f"Updated {year} Constructors Standings")
        else:
            collection.insert_one({"year": year, "file": file_data})
            print(f"Inserted {year} Constructors Standings")
        f.close()
        os.remove(file)
    return
    
def upload_points(year, file):
    with open(file, 'rb') as f:
        file_data = f.read()
        collection_name = "points"
        collection = db[collection_name]
        doc = collection.find_one({"year": year})
        if doc:
            collection.update_one({"year": year}, {"$set": {"file": file_data}})
            print(f"Updated {year} Points")
        else:
            collection.insert_one({"year": year, "file": file_data})
            print(f"Inserted {year} Points")
        f.close()
        os.remove(file)
    return
    
def get_d_standings(yr):
    collection_name = "drivers_standings"
    collection = db[collection_name]
    doc = collection.find_one({"year": int(yr)})
    if doc is None:
        raise Exception(f"No drivers standings data available for {yr}")
    file = doc["file"]
    with open(dir_path + get_path() + "res" + get_path() + "output" + get_path() + f"{yr}_DRIVERS_STANDINGS" + ".png", 'wb') as f:
        f.write(file)
        f.close()
    return f"{yr}_DRIVERS_STANDINGS.png"
    
def get_c_standings(yr):
    collection_name = "constructors_standings"
    collection = db[collection_name]
    doc = collection.find_one({"year": int(yr)})
    if doc is None:
        raise Exception(f"No constructors standings data available for {yr}")
    file = doc["file"]
    with open(dir_path + get_path() + "res" + get_path() + "output" + get_path() + f"{yr}_CONSTRUCTORS_STANDINGS" + ".png", 'wb') as f:
        f.write(file)
        f.close()
    return f"{yr}_CONSTRUCTORS_STANDINGS.png"
    
def get_p(yr):
    collection_name = "points"
    collection = db[collection_name]
    doc = collection.find_one({"year": int(yr)})
    if doc is None:
        raise Exception(f"No points data available for {yr}")
    file = doc["file"]
    with open(dir_path + get_path() + "res" + get_path() + "output" + get_path() + f"{yr}_POINTS" + ".png", 'wb') as f:
        f.write(file)
        f.close()
    return f"{yr}_POINTS.png"