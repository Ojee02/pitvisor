import numpy as np
import datetime as dt
from time import time
from time import ctime
import fastf1 as ff1
import pathlib
import platform
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

# get path for os
def get_path():
    if platform.system().__contains__("Win"):
        path = "\\"
    elif platform.system().__contains__("Lin"):
        path = "/"
    return path

# load the session
def get_sess(yr, rc, sn):
    
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
    elif yr == 2023:
        if rc == "Pre-Season Testing":
            session_type = "1"
    elif yr == 2024:
        if rc == "Pre-Season Testing":
            session_type = "1"
            
    if session_type == "1" or session_type == "2":
        if sn == "Day 1":
            sn = 1
        elif sn == "Day 2":
            sn = 2
        elif sn == "Day 3":
            sn = 3
            
    if session_type == "normal":
        session = ff1.get_session(yr, rc, sn)
    elif session_type == "1":
        session = ff1.get_testing_session(yr, 1, sn)
    elif session_type == "2":
        session = ff1.get_testing_session(yr, 2, sn)
        
    session.load()
    try:
        fix = session.laps.pick_fastest()
    except:
        pass
    return session

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
    session = get_sess(yr, rc, sn)
    session.load()
    laps = session.laps
    ls = set(tuple(x) for x in laps[['Driver']].values.tolist())
    lis = [x[0] for x in ls]
    return lis

### gets laps of a session ###
def get_laps(yr, rc, sn):
    session = get_sess(yr, rc, sn)
    session.load()
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
    session = get_sess(yr, rc, sn)
    session.load()
    laps = session.laps
    car_data = laps.pick_fastest().get_car_data().add_distance()
    maxdist = int(np.max(car_data['Distance']))
    res = []
    for i in range(0, maxdist+1, 100):
        res.append(i)
    res.append(maxdist)
    return res

### db ###

def get_races_from_db(func, yr):
    if func.lower() == "event":
        collection_name = "races"
        collection = db[collection_name]
        
        doc = collection.find_one({"year": int(yr)})
        races = doc["races"]
        
        res = []
        for race in races:
            if race not in res:
                res.append(race)    
    else:
        collection_name = "data"
        collection = db[collection_name]
        docs = collection.find({"year": int(yr)})
        
        records = []
        for doc in docs:
            records.append(doc["race"])
        
        res = []
        all_races = get_races(yr)
        for race in all_races:
            if race in records and race not in res:
                res.append(race)
    return res

def get_sessions_from_db(yr, rc):
    collection_name = "data"
    collection = db[collection_name]
    
    docs = collection.find({"year": int(yr), "race": rc})
    res = []
    for doc in docs:
        res.append(doc["session"])

    if int(yr) <= 2018:
        res = ['Practice 1', 'Practice 2', 'Practice 3', 'Qualifying', 'Race']
    return res

def get_drivers_from_db(yr, rc, sn):
    if rc == None and sn == None:
        collection_name = "data"
        collection = db[collection_name]
        docs = collection.find({"year": int(yr)})
        drivers = []
        for doc in docs:
            temp = doc["drivers"]
            for driver in temp:
                if driver not in drivers:
                    drivers.append(driver)
        return drivers
    else:
        collection_name = "data"
        collection = db[collection_name]
        sn = sn.capitalize()
        doc = collection.find_one({"year": int(yr), "race": rc, "session": sn})
        drivers = doc["drivers"]
        return drivers

def get_laps_from_db(yr, rc, sn):
    if rc == None and sn == None:
        return []
    else:
        collection_name = "data"
        collection = db[collection_name]
        sn = sn.capitalize()
        doc = collection.find_one({"year": int(yr), "race": rc, "session": sn})
        laps = doc["laps"]
        return laps

def get_distance_from_db(yr, rc, sn):
    if rc == None and sn == None:
        return []
    else:
        collection_name = "data"
        collection = db[collection_name]
        sn = sn.capitalize()
        doc = collection.find_one({"year": int(yr), "race": rc, "session": sn})
        dist = doc["distance"]
        return dist
    
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