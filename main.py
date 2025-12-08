import asyncio
from flask import Flask
from flask_cors import CORS
from flask_sslify import SSLify
from threading import Thread
import os
from dotenv import load_dotenv
from flask import jsonify, request
import fastf1
import fastf1.plotting
import pandas as pd
import matplotlib as mpl
from datetime import datetime as dt
import datetime
from pymongo import MongoClient
from update import *
import warnings
import platform
import traceback
from utils import *

load_dotenv()

FUNCS = os.getenv("FUNCS")

from funcs import *

HTTPS = os.getenv("HTTPS")

IDS = os.getenv("IDS")
PY = os.getenv("PY")
connection_string = os.getenv("connection_string")
db_name = os.getenv("db_name")
    
warnings.filterwarnings("ignore", category=FutureWarning)
platform.system()
mpl.use('Agg')
pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)

# set mpl font
set_font()

# enable cache
if os.path.exists(dir_path + get_path() + "doc_cache"):
    fastf1.Cache.enable_cache(dir_path + get_path() + "doc_cache")

# delete all files
def delete_all():
    folder_path = dir_path + get_path() + "res" + get_path() + "output"
    # Get a list of all files in the folder
    file_list = os.listdir(folder_path)
    # Loop through each file and delete it
    for file_name in file_list:
        file_path = os.path.join(folder_path, file_name)
        os.remove(file_path)

# method that logs data from slash commands
def log(user_id, func, message, exc, flag, datetime):
    datetime = datetime.replace("-", " ").replace(".", ":")
    
    id_flg = False
    for i in IDS:
        if i in user_id:
            id_flg = True
            break
    if (not id_flg and not flag):
        collection_name = "logs"
    elif (not id_flg and flag):
        collection_name = "exc"
    elif (user_id in IDS and not flag):
        collection_name = "devlogs"
    elif (user_id in IDS and flag):
        collection_name = "devexc"
        
    client = MongoClient(connection_string)
    db = client[db_name]
    collection = db[collection_name]
    
    print(message)
        
    comm = func
    inputs = message
    
    if not exc:
        collection.insert_one({
            "name": user_id, 
            "command": comm,
            "inputs": inputs,
            "datetime": datetime})
    else:
        collection.insert_one({
            "name": user_id, 
            "command": comm,
            "inputs": inputs,
            "exception": exc,
            "datetime": datetime,
        })

# fix exception string
def fix_exc(exc, input_list, comm):
    
    yr = input_list["year"]
    
    try:
        rc = input_list["race"]
        if type(rc) == int:
            rc = int(rc)
    except:
        rc = None
        
    try:
        sn = input_list["session"]
    except:
        sn = None

    if exc.__contains__("The data you are trying to access has not been loaded yet.") and yr < 2018:
        exc = "The FastF1 API has data starting from 2018. Please make sure you provided all the inputs correctly and try again.\n"

    elif exc.__contains__("The data you are trying to access has not been loaded yet.") and (yr < datetime.datetime.now().year and yr >= 2018):
        if rc != None:
            if type(rc) == int:
                exc = "I can't find any F1 data for the requested grand prix (" + str(yr) + " Round " + str(rc) + ").\n"
            else:
                exc = "I can't find any F1 data for the requested grand prix (" + str(yr) + " " + str(rc) + ").\n"
        else:
            exc = "I can't find any F1 data for the requested Grand Prix. It may have not been loaded yet.\n"
        
    elif exc.__contains__("The data you are trying to access has not been loaded yet.") and yr >= datetime.datetime.now().year:
        if rc != None and sn != None:
            if type(rc) == int:
                exc = "I can't find any F1 data for the requested grand prix (" + str(yr) + " Round " + str(rc) + " " + sn + "). It may have not been loaded yet.\n"
            else:
                exc = "I can't find any F1 data for the requested grand prix (" + str(yr) + " " + str(rc) + " " + sn + "). It may have not been loaded yet.\n"
        else:
            exc = "I can't find any F1 data for the requested Grand Prix. It may have not been loaded yet.\n"
    
    elif exc.__contains__("'code'") or exc.__contains__("integer division or modulo by zero"): 
        exc = "There requested data is not available for the year " + str(yr) + ".\n"
        
    elif exc.__contains__("cannot find race"):
        exc = "I can't find the requested race (" + str(rc) + "). Please make sure you provided all the inputs correctly and try again.\n"

    elif exc.__contains__("Invalid session type"):
        exc = "I can't find the requested session (" + input_list["session"] + "). Please make sure you provided all the inputs correctly and try again.\n"

    elif exc.__contains__("Invalid driver identifier"):
        exc = "I can't find the requested driver(s). Make sure to provide a driver abbreviation, like 'VER' or 'LEC'.\n"
    
    elif exc.__contains__("None of [Index") and exc.__contains__("are in the [columns]") and comm == "tires":
        exc = "An error has occured. Try a different lap number\n"
        
    elif exc.__contains__("single positional indexer is out-of-bounds") or exc=="0" or exc.__contains__ ("'Lap' object has no attribute 'session'") or exc.__contains__("attempt to get argmin of an empty sequence") or exc == "":
        exc = "An unknown error occured. Please make sure you provided all the command inputs correctly.\n"
        
    elif exc.__contains__("Cannot connect to host") or exc.__contains__("Unauthorized") or exc.__contains__("HTTP"):
        exc = "A network error has occured. Please try again later.\n"
        
    elif exc.__contains__("Expecting value: line 1 column 1 (char 0)"):
        exc = "An error occured while fetching data from the API. Please try again later.\n"
    
    else:
        exc = "An unknown error occured.\n"
        
    return exc

# command
async def command(user_id, input_list, comm, datetime):
  
    try:

        inputs = ""
        message = ""
        for i in input_list:
            inputs += i + " " + str(input_list[i]) + " "
        message = comm + " " + inputs
        
        print("STARTED " + message + " " + datetime)
        
        if comm == "drivers":
            res = get_d_standings(input_list["year"])
            return res
        elif comm == "constructors":
            res = get_c_standings(input_list["year"])
            return res
        elif comm == "points":
            res = get_p(input_list["year"])
            return res
        else:
            if comm == "fastest":
                res = fastest_func(input_list, datetime)
            elif comm == "results":
                res = results_func(input_list, datetime)
            elif comm == "schedule":
                res = schedule_func(input_list, datetime)
            elif comm == "event":
                res = event_func(input_list, datetime)
            elif comm == "laps":
                res = laps_func(input_list, datetime)
            elif comm == "time":
                res = time_func(input_list, datetime)
            elif comm == "distance":
                res = distance_func(input_list, datetime)
            elif comm == "delta":
                res = delta_func(input_list, datetime)
            elif comm == "gear":
                res = gear_func(input_list, datetime)
            elif comm == "speed":
                res = speed_func(input_list, datetime)
            elif comm == "telemetry":
                res = tel_func(input_list, datetime)
            elif comm == "cornering":
                res = cornering_func(input_list, datetime)
            elif comm == "tires":
                res = tires_func(input_list, datetime)
            elif comm == "strategy":
                res = strategy_func(input_list, datetime)
            elif comm == "sectors":
                res = sectors_func(input_list, datetime)
            elif comm == "racetrace":
                res = rt_func(input_list, datetime)
            elif comm == "positions":
                res = positions_func(input_list, datetime)
            elif comm == "battles":
                res = battles_func(input_list, datetime)
            
        if res !="success" or res == None:
            raise Exception("Internal Server Error. Please try again.")
        else:
            print("FINISHED " + message + " " + datetime)
                
            exc = ""
            flag = False
    
    except Exception as exc:
        
        exc = str(exc)
        flag = True
        
        if datetime in queue:
            queue.remove(datetime)
        
        print("FAILED " + message + " " + datetime + " ")
        print(traceback.format_exc())
            
        exc = fix_exc(exc, input_list, comm)
        
        raise Exception(exc)

    try:
        log(user_id, message, exc, flag, datetime)
    except:
        pass
    return datetime

# flask server
app = Flask('', static_folder='res')
CORS(app)
if HTTPS == "true":
    sslify = SSLify(app)

async def update_helper():
    yr = dt.datetime.now().year
    stnd = ""
    races = ""
    data = ""
    try:
        # PY is string like "py" or"python" or "ptyhon3", etc.
        os.system(PY + " stnd.py")
        stnd = "stnd success"
    except:
        stnd = "stnd fail"
    try:
        update_races(yr)
        races = "race success"
    except:
        races = "races fail"
    return stnd + "<br />" + races + "<br />" + data

async def main_helper(request):
    try:
        data = request.get_json()
        func_name = data.get('func_name')
        input_list = data.get('input_list')
        user_id = data.get('user_id')
        datetime = get_datetime()
        output_path = dir_path + get_path() + "res" + get_path() + "output" + get_path()
        res = await command(user_id, input_list, func_name.lower(), datetime)
        with open(output_path + res + ".png", "rb") as image:
            f = image.read()
            b = bytearray(f)
        result = list(b)
        os.remove(output_path + res + ".png")
        try:
            log(user_id, func_name, input_list, "", False, datetime)
        except:
            pass
        return jsonify({'result': result, 'datetime': datetime}), 200
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400

async def inputs_helper(request):
    try:
        data = request.get_json()
        input_type = data.get('input')
        input_data = data.get('data')
        
        if input_type == "years":
            try:
                res = get_years(input_data["func"])
            except:
                res = []
        elif input_type == "races":
            try:
                res = get_races_from_db(input_data["func"], input_data["year"])
            except:
                res = []
        elif input_type == "sessions":
            try:
                res = get_sessions_from_db(input_data["year"], input_data["race"])
            except:
                res = []
        elif input_type == "all":
            try:
                res = [[], "", ""]
                res[0] = get_drivers_from_db(input_data["year"], input_data["race"], input_data["session"])
                res[1] = get_laps_from_db(input_data["year"], input_data["race"], input_data["session"])
                res[2] = get_distance_from_db(input_data["year"], input_data["race"], input_data["session"])
            except:
                print(traceback.format_exc())
                res = []
                
        return jsonify({'result': res}), 200
    
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify(status="UP"), 200

# update data
@app.route('/update', methods=['GET', 'POST'])
def update():
    return asyncio.run(update_helper())

# execute function when user submits form
@app.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST':
        try:
            return asyncio.run(main_helper(request))
        except Exception as exc:
            return jsonify({'error': str(exc)}), 400
    else:
        return "Backend is running."

# get inputs
@app.route('/inputs', methods=['GET', 'POST'])
def inputs():
    if request.method == 'POST':
        try:
            return asyncio.run(inputs_helper(request))
        except Exception as exc:
            return jsonify({'error': str(exc)}), 400

# run the server
def run():
    # ensure output directory exists
    output_dir = dir_path + get_path() + "res" + get_path() + "output"
    os.makedirs(output_dir, exist_ok=True)
    try:
        delete_all()
    except:
        pass
    if HTTPS == "true":
        app.run(ssl_context=('cert/fullchain.pem', 'cert/privkey.pem'), host='0.0.0.0',port=5000)
    else:
        app.run(host='0.0.0.0',port=5000)

# keep the server running
def keep_alive():
    t = Thread(target=run)
    t.start()

# keep the server running
keep_alive()