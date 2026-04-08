"""
Cache all session data (drivers, laps, distance) into local MongoDB.
Starts from 2026 descending to 2018. Skips already-cached sessions.
Handles rate limits with backoff. Logs progress to /tmp/pitvisor-cache.log
"""
import os
import sys
import time
import traceback
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

import fastf1 as ff1
from pymongo import MongoClient

connection_string = os.getenv("connection_string")
db_name = os.getenv("db_name")
client = MongoClient(connection_string)
db = client[db_name]

if os.path.exists("doc_cache"):
    ff1.Cache.enable_cache("doc_cache")

LOG = "/tmp/pitvisor-cache.log"

def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def is_cached(yr, rc, sn):
    doc = db["data"].find_one({"year": yr, "race": rc, "session": sn})
    return doc and "drivers" in doc and "laps" in doc and "distance" in doc

def cache_session(yr, rc, sn):
    try:
        # Use get_sess from utils which handles pre-season testing
        from utils import get_sess
        session = get_sess(yr, rc, sn)

        drivers = list(set(
            x[0] for x in session.laps[['Driver']].values.tolist()
        ))

        laps_set = set(
            int(x[0]) for x in session.laps[['LapNumber']].values.tolist()
        )
        laps = sorted(laps_set)

        try:
            import numpy as np
            car_data = session.laps.pick_fastest().get_car_data().add_distance()
            maxdist = int(np.max(car_data['Distance']))
            distance = list(range(0, maxdist + 1, 100)) + [maxdist]
        except Exception:
            distance = 0

        db["data"].update_one(
            {"year": yr, "race": rc, "session": sn},
            {"$set": {"drivers": drivers, "laps": laps, "distance": distance}},
            upsert=True
        )
        return True, ""
    except Exception as e:
        log(f"  FAIL: {e}")
        return False, str(e)

def get_sessions(yr, rc):
    if "Pre-Season" in str(rc):
        return ['Day 1', 'Day 2', 'Day 3']
    try:
        event = ff1.get_event(yr, rc)
        sessions = []
        for i in range(1, 6):
            s = getattr(event, f'Session{i}', None)
            if s and str(s) != 'nan':
                sessions.append(s)
        return sessions
    except Exception:
        return []

def main():
    # Clear log
    with open(LOG, "w") as f:
        f.write(f"=== Cache started at {datetime.now()} ===\n")

    years = list(range(2026, 2017, -1))
    total_cached = 0
    total_skipped = 0
    total_failed = 0

    for yr in years:
        log(f"\n{'='*50}")
        log(f"YEAR {yr}")
        log(f"{'='*50}")

        try:
            schedule = ff1.get_event_schedule(yr)
            races = [row['EventName'] for _, row in schedule.iterrows()]
        except Exception as e:
            log(f"  Could not get schedule: {e}")
            continue

        for rc in races:
            sessions = get_sessions(yr, rc)
            for sn in sessions:
                if is_cached(yr, rc, sn):
                    log(f"  SKIP {yr} {rc} {sn} (cached)")
                    total_skipped += 1
                    continue

                log(f"  LOAD {yr} {rc} {sn}...")
                ok, err = cache_session(yr, rc, sn)
                if ok:
                    log(f"  OK   {yr} {rc} {sn}")
                    total_cached += 1
                else:
                    total_failed += 1
                    if 'has not been loaded yet' in err:
                        log(f"  No data yet — skipping rest of {yr}")
                        break
                    log(f"  Waiting 60s...")
                    time.sleep(60)

                # Delay between sessions (avoid rate limit)
                time.sleep(10)

            else:
                # Only continue to next race if inner loop didn't break
                time.sleep(5)
                continue
            break  # Break out of races loop too (skip rest of year)

    log(f"\n{'='*50}")
    log(f"DONE: {total_cached} cached, {total_skipped} skipped, {total_failed} failed")
    log(f"{'='*50}")

if __name__ == "__main__":
    main()
