import os
import pathlib
import fastf1
from fastf1 import plotting
from fastf1 import utils
from fastf1.core import Laps
import matplotlib.pyplot as plt
import pandas as pd
import tabulate
from timple.timedelta import strftimedelta
from matplotlib.colors import ListedColormap
from matplotlib.collections import LineCollection
from matplotlib import cm
import numpy as np
import matplotlib as mpl
from matplotlib.ticker import MaxNLocator
import matplotlib.font_manager as fm
from time import time, ctime
import time as tm
from matplotlib import dates
from PIL import Image, ImageDraw, ImageFont
import warnings
import platform
import requests
from update import *

warnings.filterwarnings("ignore", category=FutureWarning)
platform.system()
mpl.use('Agg')
pd.set_option('display.max_rows', None)
pd.set_option('display.max_colwidth', None)

# get path of file
dir_path = r"" + str(pathlib.Path(__file__).parent.resolve())

# create folders if they don't exist
if not os.path.exists(dir_path + get_path() + "res"):
    os.mkdir(dir_path + get_path() + "res")
if not os.path.exists(dir_path + get_path() + "res" + get_path() + "output"):
    os.mkdir(dir_path + get_path() + "res" + get_path() + "output")

import threading
mpl_lock = threading.Lock()


### GENERAL FUNCTIONS ###

# get_datetime helper
def get_time():
    return ctime(time())

# get current time
def get_datetime():
    datetime = get_time()
    datetime = datetime.replace(" ", "-")
    datetime = datetime.replace(":", ".")
    return datetime

# set mpl font
def set_font():
    fe = fm.FontEntry(
        fname=dir_path + get_path() + "fonts" + get_path() +
        "Formula1-Regular_web.ttf",
        name='Formula1 Display Regular')
    fm.fontManager.ttflist.insert(0, fe)
    mpl.rcParams['font.family'] = fe.name

# dark mode for all plots
def setup_dark():
    mpl.rcParams.update({
        'figure.facecolor': '#1e1e1e',
        'axes.facecolor': '#1e1e1e',
        'axes.edgecolor': '#444444',
        'axes.labelcolor': 'white',
        'text.color': 'white',
        'xtick.color': 'white',
        'ytick.color': 'white',
        'grid.color': '#333333',
        'legend.facecolor': '#2a2a2a',
        'legend.edgecolor': '#444444',
        'legend.labelcolor': 'white',
        'savefig.facecolor': '#1e1e1e',
    })

# get path separator for os
def get_path():
    return os.sep

# get_sess is imported from utils.py via update.py

# Color helpers (FastF1 3.8+ requires session)
TEAM_COLORS = {
    'Red Bull Racing': '#3671C6', 'Ferrari': '#E8002D', 'Mercedes': '#27F4D2',
    'McLaren': '#FF8000', 'Aston Martin': '#229971', 'Alpine F1 Team': '#FF87BC',
    'Williams': '#64C4FF', 'RB F1 Team': '#6692FF', 'AlphaTauri': '#6692FF',
    'Kick Sauber': '#52E252', 'Haas F1 Team': '#B6BABD', 'Alfa Romeo': '#C92D4B',
    'Racing Point': '#F596C8', 'Renault': '#FFF500', 'Toro Rosso': '#6692FF',
    'Force India': '#F596C8', 'Sauber': '#52E252',
}

def _driver_col(driver, session=None):
    try:
        return fastf1.plotting.get_driver_color(driver, session=session)
    except:
        pass
    try:
        return _driver_col(driver, session)
    except:
        return 'grey'

def _team_col(team, session=None):
    try:
        return fastf1.plotting.get_team_color(team, session=session)
    except:
        return TEAM_COLORS.get(team, 'grey')

# reset mpl
def rstall(plt):
    plt.clf()
    plt.cla()
    plt.close()
    mpl.rcParams.update(mpl.rcParamsDefault)
    mpl.rcdefaults()
    set_font()
    setup_dark()

# turn text into image

# Full image generation function
def make_img(datetime, text):
    # Font paths (update these if needed)
    font_path = dir_path + get_path() + "fonts" + get_path() + "JetBrainsMono-Medium.ttf"
    
    # Load font
    font = ImageFont.truetype(font_path, 60)

    # Split the text into lines
    lines = text.split('\n')
    line_height = 60
    line_spacing = 10

    # Create a temporary draw object to measure text size
    temp_img = Image.new("RGB", (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)
    
    # Determine image dimensions
    width = max([temp_draw.textbbox((0, 0), line, font=font)[2] for line in lines]) + 25
    height = (len(lines) * line_height) + ((len(lines) - 1) * line_spacing) + 25

    # Create the final image
    img = Image.new('RGB', (width, height), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw each line
    y = 10
    for line in lines:
        draw.text((10, y), line, fill=(255, 255, 255), font=font)
        y += line_height + line_spacing

    # Save the image
    img.save(dir_path + get_path() + "res" + get_path() + "output" + get_path() + datetime + ".png", "PNG")

# set mpl font
set_font()
setup_dark()

def plot_corners(ax, session):
    """Annotate corner numbers on a track map axis."""
    try:
        ci = session.get_circuit_info()
        if ci is None or ci.corners is None or ci.corners.empty:
            return
        for _, corner in ci.corners.iterrows():
            x, y = corner['X'], corner['Y']
            num = str(int(corner['Number']))
            letter = corner.get('Letter', '')
            if letter and str(letter) != 'nan':
                num += str(letter)
            # offset label using corner angle
            angle_rad = np.radians(corner['Angle'])
            offset = 300
            tx = x + np.cos(angle_rad) * offset
            ty = y + np.sin(angle_rad) * offset
            ax.text(tx, ty, num, fontsize=7, color='white', ha='center', va='center',
                    fontweight='bold', alpha=0.7,
                    bbox=dict(boxstyle='round,pad=0.15', facecolor='black', edgecolor='none', alpha=0.5))
    except Exception:
        pass

### END OF GENERAL FUNCTIONS ###


### PLOTTING FUNCTIONS ###

def fastest_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]
    sn = input_list["session"]

    session = get_sess(yr, rc, sn)
    session.load()

    mpl_lock.acquire()

    plotting.setup_mpl()
    setup_dark()

    plt.rcParams["figure.figsize"] = [14, 10]
    plt.rcParams["figure.autolayout"] = True
    
    fig, ax = plt.subplots()

    drivers = pd.unique(session.laps['Driver'])

    list_fastest_laps = list()
    
    for drv in drivers:
        drvs_fastest_lap = session.laps.pick_driver(drv).pick_fastest()
        list_fastest_laps.append(drvs_fastest_lap)
    fastest_laps = Laps(list_fastest_laps).sort_values(
        by='LapTime').reset_index(drop=True)

    pole_lap = fastest_laps.pick_fastest()
    fastest_laps['LapTimeDelta'] = fastest_laps['LapTime'] - \
        pole_lap['LapTime']

    team_colors = list()
    for index, lap in fastest_laps.iterlaps():
        try:
            color = _team_col(lap['Team'], session)
        except:
            color = 'grey'
        team_colors.append(color)

    ax.barh(fastest_laps.index,
            fastest_laps['LapTimeDelta'], color=team_colors, edgecolor='grey')
    ax.set_yticks(fastest_laps.index)
    ax.set_yticklabels(fastest_laps['Driver'], fontsize=15)

    # show fastest at the top
    ax.invert_yaxis()

    # draw vertical lines behind the bars
    ax.set_axisbelow(True)
    ax.xaxis.grid(True, which='major', linestyle='--',
                  color='black', zorder=-1000)

    lap_time_string = strftimedelta(pole_lap['LapTime'], '%m:%s.%ms')

    # sn = session.event.get_session_name(sn)
    plt.suptitle(
        f"{yr} {rc} {sn}\nFastest Lap: " + lap_time_string + " (" + pole_lap['Driver'] + ")", fontsize=20)
    plt.setp(ax.get_xticklabels(), fontsize=12)

    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')
    
    rstall(plt)
    mpl_lock.release()
    return "success"

def results_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]
    sn = input_list["session"]

    session = get_sess(yr, rc, sn)
    session.load()

    msg = session.results
    
    if msg.empty:
        raise Exception("The data you are trying to access has not been loaded yet.")
    
    if session.event.get_session_name(sn).lower() == "qualifying" or session.event.get_session_name(sn).lower() == "sprint shootout":
        msg2 = msg[['Position', 'BroadcastName', 'TeamName', 'Q1', 'Q2', 'Q3']] 
    elif session.event.get_session_name(sn).lower() == "race" or session.event.get_session_name(sn).lower() == "sprint":
        msg2 = msg[['Position', 'BroadcastName', 'TeamName', 'Points', 'Status']] 
    else:
        msg2 = msg[['BroadcastName', 'TeamName']]
    # sn = session.event.get_session_name(sn)
    text = f"{yr} {rc} {sn}"

    text = tabulate.tabulate([[text]], tablefmt='fancy_grid')
    
    msg2 = tabulate.tabulate(msg2.values, headers=msg2.columns, tablefmt='fancy_grid')
    
    msg2 = msg2.replace("BroadcastName", "Driver       ").replace("TeamName", "Team    ")
    
    if session.event.get_session_name(sn).lower() == "qualifying" or session.event.get_session_name(sn).lower() == "sprint shootout":
        msg2 = msg2.replace(".0 0 days", "   0 days").replace(".0                    NaT", "                      NaT").replace("0 days 00:", "").replace("                    Q", "       Q").replace("                   NaT", "      NaT").replace("000 ", " ").replace("000\n", "\n").replace("NaT", "   ")
        msg2 = msg2.replace("Q1                    ", "Q1       ").replace("Q2                    ", "Q2       ").replace("Q3                    ", "Q3       ")
        msg2 = msg2.replace("═══════════════════════╤════════════════════════╤════════════════════════╕", "══════════╤═══════════╤═══════════╕")
        msg2 = msg2.replace("═══════════════════════╧════════════════════════╧════════════════════════╛", "══════════╧═══════════╧═══════════╛")
        msg2 = msg2.replace("───────────────────────┼────────────────────────┼────────────────────────┤", "──────────┼───────────┼───────────┤")
        msg2 = msg2.replace("═══════════════════════╪════════════════════════╪════════════════════════╡", "══════════╪═══════════╪═══════════╡")
        msg2 = msg2.replace("                        │", "           │")
    elif session.event.get_session_name(sn).lower() == "race" or session.event.get_session_name(sn).lower() == "sprint":
        msg2 = msg2.replace(".0", "  ")

    make_img(datetime, text + "\n" + msg2)
    return "success"

def schedule_func(input_list, datetime):
    
    yr = input_list["year"]

    schedule = fastf1.get_event_schedule(yr)

    if schedule.empty:
        raise Exception("The data you are trying to access has not been loaded yet.")

    msg = schedule[['EventName', 'EventDate', 'EventFormat']]
    msg = tabulate.tabulate(msg.values, headers=msg.columns, tablefmt='fancy_grid')
    msg = msg.replace("EventDate", "Date     ").replace("EventName", "Name     ").replace("EventFormat", "Format     ")
    msg = msg.replace("testing", "Testing").replace("conventional", "Conventional").replace("sprint_shootout", "Sprint Shootout").replace("sprint", "Sprint")
    text = tabulate.tabulate([[str(yr) + " Schedule"]], tablefmt='fancy_grid')

    make_img(datetime, text + "\n" + msg)
    return "success"

def event_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]

    msg = fastf1.get_event(yr, rc)
    
    if msg.empty:
        raise Exception("The data you are trying to access has not been loaded yet.")
    
    lines = str(msg).splitlines()
    l1 = []
    l2 = []
    rc = ""
    for line in lines:
        if "Name:" not in line:
            if line[:17].strip() == "OfficialEventName":
                l1.append("Official Event Name")
            else:
                l1.append(line[:17].strip())
            l2.append(line[17:].strip())
        if "EventName" in line:
            rc = line[17:].strip()
    list = [l1, l2]
    list = np.array(list).T.tolist() 
    msg = tabulate.tabulate(list, tablefmt='fancy_grid')
    msg = msg.replace("RoundNumber ", "Round Number").replace("EventDate ", "Event Date").replace("EventName ", "Event Name").replace("EventFormat ","Event Format").replace("Session1 ", "Session 1").replace("Session1Date  ", "Session 1 Date").replace("Session2 ", "Session 2").replace("Session2Date  ", "Session 2 Date").replace("Session3 ", "Session 3").replace("Session3Date  ", "Session 3 Date").replace("Session4 ", "Session 4").replace("Session4Date  ", "Session 4 Date").replace("Session5 ", "Session 5").replace("Session5Date  ", "Session 5 Date").replace("F1ApiSupport  ", "F1 Api Support")
    msg = msg.replace("conventional", "Conventional").replace("sprint_shootout", "Sprint Shootout").replace("sprint", "Sprint")
    text = tabulate.tabulate([[str(yr) + " " + rc + " Event Data"]], tablefmt='fancy_grid')

    make_img(datetime, text + "\n" + msg)
    return "success"

def laps_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]
    sn = input_list["session"]
    drivers = input_list["drivers"]

    session = get_sess(yr, rc, sn)
    session.load()

    
    mpl_lock.acquire()
    


    plotting.setup_mpl()
    setup_dark()
    fig, ax = plt.subplots()

    plt.rcParams["figure.figsize"] = [7, 5]
    plt.rcParams["figure.autolayout"] = True

    title = str(drivers[0])

    i = 0
    temp = 0
    title = ""
    title = title + str(drivers[0])
    while (i < len(drivers)):
        temp = session.laps.pick_driver(drivers[i])
        try:
            ax.plot(temp['LapNumber'], temp['LapTime'], color=fastf1.plotting.driver_color(
                drivers[i]), label=str(drivers[i]))
        except:
            ax.plot(temp['LapNumber'], temp['LapTime'],
                    color='grey', label=str(drivers[i]))
        if (i+1 < len(drivers)):
            title = title + " vs " + str(drivers[i+1])
        i += 1

    # sn = session.event.get_session_name(sn)
    plt.suptitle(
        f"Laps Comparison\n{yr} {rc} {sn}\n" + title)

    def yformat(x, pos): return dates.DateFormatter('%M:%S.%f')(x)[:-5]
    ax.yaxis.set_major_formatter(plt.FuncFormatter(yformat))

    ax.set_xlabel("Lap Number")
    ax.set_ylabel("Lap Time")
    ax.legend()
    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')
    
    rstall(plt)
    mpl_lock.release()
    return "success"

def time_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]
    sn = input_list["session"]
    drivers = input_list["drivers"]
    lap = input_list["lap"]

    session = get_sess(yr, rc, sn)
    session.load()

    
    mpl_lock.acquire()
    


    plotting.setup_mpl()
    setup_dark()
    fig, ax = plt.subplots()

    plt.rcParams["figure.figsize"] = [7, 5]
    plt.rcParams["figure.autolayout"] = True

    fast = 0
    t = 0
    vCar = 0
    car_data = 0

    i = 0
    while (i < len(drivers)):
        if (lap == None or lap == ''):
            fast = session.laps.pick_driver(drivers[i]).pick_fastest()
        else:
            driver_laps = session.laps.pick_driver(drivers[i])
            fast = driver_laps[driver_laps['LapNumber'] == int(lap)].iloc[0]
        car_data = fast.get_car_data()
        t = car_data['Time']
        vCar = car_data['Speed']
        try:
            ax.plot(t, vCar, color=fastf1.plotting.driver_color(
                drivers[i]), label=str(drivers[i]))
        except:
            ax.plot(t, vCar, color='grey', label=str(drivers[i]))
        i = i+1

    title = str(drivers[0])

    i = 0
    while (i < len(drivers)):
        if (i+1 < len(drivers)):
            title = title + " vs " + str(drivers[i+1])
        i += 1

    # sn = session.event.get_session_name(sn)

    if (lap == None or lap == ''):
        plt.suptitle("Fastest Lap Comparison\n" +
                     f"{yr} {rc} {sn}\n" + title)
    else:
        plt.suptitle("Lap " + str(lap) + " Comparison " +
                     f"{yr} {rc} {sn}\n" + title)

    plt.setp(ax.get_xticklabels(), fontsize=7)

    ax.set_xlabel('Time')
    ax.set_ylabel('Speed [Km/h]')
    ax.legend()
    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')
    
    rstall(plt)
    mpl_lock.release()
    return "success"

def distance_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]
    sn = input_list["session"]
    drivers = input_list["drivers"]
    lap = input_list["lap"]

    session = get_sess(yr, rc, sn)
    session.load()

    
    mpl_lock.acquire()
    


    plotting.setup_mpl()
    setup_dark()
    fig, ax = plt.subplots()

    plt.rcParams["figure.figsize"] = [7, 5]
    plt.rcParams["figure.autolayout"] = True

    fast = 0
    t = 0
    vCar = 0
    car_data = 0

    i = 0
    while (i < len(drivers)):
        if (lap == None or lap == ''):
            fast = session.laps.pick_driver(drivers[i]).pick_fastest()
        else:
            driver_laps = session.laps.pick_driver(drivers[i])
            fast = driver_laps[driver_laps['LapNumber'] == int(lap)].iloc[0]
        car_data = fast.get_car_data().add_distance()
        t = car_data['Distance']
        vCar = car_data['Speed']
        try:
            ax.plot(t, vCar, color=fastf1.plotting.driver_color(
                drivers[i]), label=str(drivers[i]))
        except:
            ax.plot(t, vCar, color='grey', label=str(drivers[i]))
        i = i+1

    title = str(drivers[0])

    i = 0
    while (i < len(drivers)):
        if (i+1 < len(drivers)):
            title = title + " vs " + str(drivers[i+1])
        i += 1

    # sn = session.event.get_session_name(sn)

    if (lap == None or lap == ''):
        plt.suptitle("Fastest Lap Comparison\n" +
                     f"{yr} {rc} {sn}\n" + title)
    else:
        plt.suptitle("Lap " + str(lap) + " Comparison " +
                     f"{yr} {rc} {sn}\n" + title)

    ax.set_xlabel('Distance in m')
    ax.set_ylabel('Speed km/h')
    ax.legend()
    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')
    
    rstall(plt)
    mpl_lock.release()
    return "success"

def delta_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]
    sn = input_list["session"]
    d1 = input_list["driver1"]
    d2 = input_list["driver2"]
    lap1 = input_list["lap1"]
    lap2 = input_list["lap2"]

    session = get_sess(yr, rc, sn)
    session.load()

    
    mpl_lock.acquire()
    

    
    if (d1 == None or d1 == ''):
        d1 = session.laps.pick_fastest()['Driver']
    
    if (d2 == None or d2 == ''):
        d2 = session.laps.pick_fastest()['Driver']

    if (lap1 == None or lap1 == ''):
        dd1 = session.laps.pick_driver(d1).pick_fastest()
    else:
        driver_laps = session.laps.pick_driver(d1)
        dd1 = driver_laps[driver_laps['LapNumber'] == int(lap1)].iloc[0]

    if (lap2 == None or lap2 == ''):
        dd2 = session.laps.pick_driver(d2).pick_fastest()
    else:
        driver_laps = session.laps.pick_driver(d2)
        dd2 = driver_laps[driver_laps['LapNumber'] == int(lap2)].iloc[0]

    delta_time, ref_tel, compare_tel = utils.delta_time(dd1, dd2)

    plotting.setup_mpl()
    setup_dark()
    fig, ax = plt.subplots()

    plt.rcParams["figure.figsize"] = [7, 5]
    plt.rcParams["figure.autolayout"] = True

    try:
        ax.plot(ref_tel['Distance'], ref_tel['Speed'],
                color=_driver_col(d1, session), label=d1)
    except:
        ax.plot(ref_tel['Distance'], ref_tel['Speed'], color='grey', label=d1)
    try:
        if (d1 != d2):
            ax.plot(compare_tel['Distance'], compare_tel['Speed'],
                    color=_driver_col(d2, session), label=d2)
        else:
            ax.plot(compare_tel['Distance'],
                    compare_tel['Speed'], color='#777777', label=d2)
    except:
        ax.plot(compare_tel['Distance'],
                compare_tel['Speed'], color='grey', label=d2)

    ax.legend()
    twin = ax.twinx()
    twin.plot(ref_tel['Distance'], delta_time, '--', color='white')
    twin.set_ylabel("<-- " + d2 + " ahead | " + d1 + " ahead -->")
    ticks = twin.get_yticks()
    # set labels to absolute values and with integer representation
    twin.set_yticklabels([round(abs(tick), 1) for tick in ticks])

    if (lap1 == None or lap1 == ''):
        lap1 = "Fastest Lap"
    else:
        lap1 = "Lap " + str(lap1)
    if (lap2 == None or lap2 == ''):
        lap2 = "Fastest Lap"
    else:
        lap2 = "Lap " + str(lap2)

    # sn = session.event.get_session_name(sn)

    plt.suptitle("Lap Comparison\n" +
                 f"{yr} {rc} {sn}\n" + d1 + " (" + lap1 + ") vs " + d2 + " (" + lap2 + ")")

    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')
    
    rstall(plt)
    mpl_lock.release()
    return "success"

def gear_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]
    sn = input_list["session"]
    driver = input_list["driver"]
    lap = input_list["lap"]

    session = get_sess(yr, rc, sn)
    session.load()

    
    mpl_lock.acquire()
    

    
    rstall(plt)

    plotting.setup_mpl()
    setup_dark()

    plt.rcParams["figure.figsize"] = [7, 5]
    plt.rcParams["figure.autolayout"] = True

    if (lap == None or lap == '') and (driver == None or driver ==''):
        # get fastest lap of the session
        d_lap = session.laps.pick_fastest()
    elif (lap == None or lap == '') and (driver != None and driver != ''):
        # get fastest lap of driver
        d_lap = session.laps.pick_driver(driver).pick_fastest()
    elif (lap != None and lap != '') and (driver != None and driver != ''):
        # get specific lap of driver
        driver_laps = session.laps.pick_driver(driver)
        d_lap = driver_laps[driver_laps['LapNumber'] == int(lap)].iloc[0]
    elif (lap != None and lap != '') and (driver == None or driver == ''):
        temp_lap = session.laps.pick_fastest()
        driver_laps = session.laps.pick_driver(str(f"{temp_lap['Driver']}"))
        d_lap = driver_laps[driver_laps['LapNumber'] == int(lap)].iloc[0]
    # get telemetry data
    tel = d_lap.get_telemetry()

    x = np.array(tel['X'].values)
    y = np.array(tel['Y'].values)

    points = np.array([x, y]).T.reshape(-1, 1, 2)
    segments = np.concatenate([points[:-1], points[1:]], axis=1)
    gear = tel['nGear'].to_numpy().astype(float)

    cmap = cm.get_cmap('Paired')
    lc_comp = LineCollection(
        segments, norm=plt.Normalize(1, cmap.N+1), cmap=cmap)
    lc_comp.set_array(gear)
    lc_comp.set_linewidth(4)

    fig = plt.gcf()
    ax = plt.gca()
    fig.patch.set_facecolor('#1e1e1e')
    ax.set_facecolor('#1e1e1e')

    ax.add_collection(lc_comp)
    plt.axis('equal')
    plt.tick_params(labelleft=False, left=False,
                    labelbottom=False, bottom=False)

    if (lap == None or lap == ''):
        plt.suptitle(
            f"Fastest Lap Gear Shift Visualization - " + f"{d_lap['Driver']}\n" + f"{yr} {rc} {sn}\n", color='white')
    else:
        plt.suptitle(f"Lap {lap} Gear Shift Visualization - " +
                     f"{d_lap['Driver']}\n" + f"{yr} {rc} {sn}\n", color='white')

    cbar = plt.colorbar(mappable=lc_comp, label="Gear",
                        boundaries=np.arange(1, 10))
    cbar.set_ticks(np.arange(1.5, 9.5))
    cbar.set_ticklabels(np.arange(1, 9))
    cbar.ax.yaxis.set_tick_params(color='white')
    cbar.set_label("Gear", color='white')
    plt.setp(plt.getp(cbar.ax, 'yticklabels'), color='white')

    plot_corners(plt.gca(), session)

    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')

    rstall(plt)
    mpl_lock.release()
    return "success"

def speed_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]
    sn = input_list["session"]
    driver = input_list["driver"]
    lap = input_list["lap"]

    colormap = mpl.cm.plasma

    session = get_sess(yr, rc, sn)
    session.load()

    
    mpl_lock.acquire()
    


    # weekend = session.event

    if (lap == None or lap == '') and (driver == None or driver == ''):
        # get fastest lap of the session
        d_lap = session.laps.pick_fastest()
    elif (lap == None or lap == '') and (driver != None and driver != ''):
        # get fastest lap of driver
        d_lap = session.laps.pick_driver(driver).pick_fastest()
    elif (lap != None and lap != '') and (driver != None and driver != ''):
        # get specific lap of driver
        driver_laps = session.laps.pick_driver(driver)
        d_lap = driver_laps[driver_laps['LapNumber'] == int(lap)].iloc[0]
    elif (lap != None and lap != '') and (driver == None or driver == ''):
        temp_lap = session.laps.pick_fastest()
        driver_laps = session.laps.pick_driver(str(f"{temp_lap['Driver']}"))
        d_lap = driver_laps[driver_laps['LapNumber'] == int(lap)].iloc[0]

    # Get telemetry data
    x = d_lap.telemetry['X']              # values for x-axis
    y = d_lap.telemetry['Y']              # values for y-axis
    color = d_lap.telemetry['Speed']      # value to base color gradient on

    points = np.array([x, y]).T.reshape(-1, 1, 2)
    segments = np.concatenate([points[:-1], points[1:]], axis=1)

    rstall(plt)

    plotting.setup_mpl()
    setup_dark()
    # We create a plot with title and adjust some setting to make it look good.
    fig, ax = plt.subplots(sharex=True, sharey=True, figsize=(12, 6.75))

    fig.patch.set_facecolor('#1e1e1e')
    ax.set_facecolor('#1e1e1e')

    plt.rcParams["figure.figsize"] = [7, 5]
    plt.rcParams["figure.autolayout"] = True

    if (lap == None or lap == ''):
        fig.suptitle("Fastest Lap Speed Visualization - " +
                     f"{d_lap['Driver']}" + "\n" + f"{yr} {rc} {sn}\n", size=20, y=0.97, color='white')
    else:
        fig.suptitle("Lap " + str(lap) + " Speed Visualization - " +
                     f"{d_lap['Driver']}" + "\n" + f"{yr} {rc} {sn}\n", size=20, y=0.97, color='white')

    plt.subplots_adjust(left=0.1, right=0.9, top=0.9, bottom=0.12)
    ax.axis('off')

    ax.plot(d_lap.telemetry['X'], d_lap.telemetry['Y'],
            color='#333333', linestyle='-', linewidth=16, zorder=0)

    # Create a continuous norm to map from data points to colors
    norm = plt.Normalize(color.min(), color.max())
    lc = LineCollection(segments, cmap=colormap, norm=norm,
                        linestyle='-', linewidth=5)

    # Set the values used for colormapping
    lc.set_array(color)

    # Merge all line segments together
    line = ax.add_collection(lc)

    # Finally, we create a color bar as a legend.
    cbaxes = fig.add_axes([0.25, 0.05, 0.5, 0.05])
    normlegend = mpl.colors.Normalize(vmin=color.min(), vmax=color.max())
    legend = mpl.colorbar.ColorbarBase(
        cbaxes, norm=normlegend, cmap=colormap, orientation="horizontal")

    plot_corners(ax, session)

    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')

    rstall(plt)
    mpl_lock.release()
    return "success"

def tel_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]
    sn = input_list["session"]
    d1 = input_list["driver1"]
    d2 = input_list["driver2"]
    lap1 = input_list["lap1"]
    lap2 = input_list["lap2"]

    session = get_sess(yr, rc, sn)
    session.load()

    
    mpl_lock.acquire()
    


    weekend = session.event
    laps = session.laps
    
    if (d1 == None or d1 == ''):
        d1 = laps.pick_fastest()['Driver']
    
    if (d2 == None or d2 == ''):
        d2 = laps.pick_fastest()['Driver']
    
    drv1 = d1
    drv2 = d2

    first_driver = laps.pick_driver(drv1)
    first_driver_info = session.get_driver(drv1)

    try:
        first_color = _driver_col(d1, session)
    except:
        first_color = 'grey'

    second_driver = laps.pick_driver(drv2)
    second_driver_info = session.get_driver(drv2)

    try:
        if (d1 != d2):
            second_color = _driver_col(d2, session)
        else:
            second_color = '#777777'
    except:
        second_color = 'grey'

    if (lap1 == None or lap1 == ''):
        first_driver = laps.pick_driver(drv1).pick_fastest()
    else:
        driver_laps = session.laps.pick_driver(drv1)
        first_driver = driver_laps[driver_laps['LapNumber'] == int(
            lap1)].iloc[0]

    if (lap2 == None or lap2 == ''):
        second_driver = laps.pick_driver(drv2).pick_fastest()
    else:
        driver_laps = session.laps.pick_driver(drv2)
        second_driver = driver_laps[driver_laps['LapNumber'] == int(
            lap2)].iloc[0]

    first_car = first_driver.get_car_data().add_distance()
    second_car = second_driver.get_car_data().add_distance()

    plotting.setup_mpl()
    setup_dark()
    fig, ax = plt.subplots(7, 1, figsize=(20, 20), gridspec_kw={
                           'height_ratios': [2, 2, 2, 2, 2, 2, 3]})

    if (lap1 == None or lap1 == ''):
        lap1 = "Fastest Lap"
    else:
        lap1 = "Lap " + str(lap1)
    if (lap2 == None or lap2 == ''):
        lap2 = "Fastest Lap"
    else:
        lap2 = "Lap " + str(lap2)

    # sn = session.event.get_session_name(sn)

    # sn = session.event.get_session_name(sn)

    fig.suptitle(f"{yr} {rc} {sn}\n" +
                 drv1 + " (" + lap1 + ") vs " + drv2 + " (" + lap2 + ")", size=30)

    drs_1 = first_car['DRS']
    drs_2 = second_car['DRS']

    brake_2 = second_car['Brake']

    drs1 = []
    drs2 = []

    d = 0
    while (d < len(drs_1)):
        if (drs_1[d] >= 10 and drs_1[d] % 2 == 0):
            drs1.extend([1])
        else:
            drs1.extend([0])
        d += 1
    d = 0
    while (d < len(drs_2)):
        if (drs_2[d] >= 10 and drs_2[d] % 2 == 0):
            drs2.extend([-1])
        else:
            drs2.extend([0])
        d += 1

    brake2 = []

    b = 0
    while (b < len(brake_2)):
        if (brake_2[b] == 1):
            brake2.extend([-1])
        else:
            brake2.extend([0])
        b += 1
    if (len(brake_2) < len(second_car['Distance'])):
        b = len(brake_2)
        while (b < len(second_car['Distance'])):
            brake_2.extend([0])
            b += 1
    delta_time, ref_tel, compare_tel = utils.delta_time(
        first_driver, second_driver)

    delta = []

    dt = 0
    while (dt < len(first_car['Distance'])):
        delta.extend([float(delta_time[dt])*(-1)])
        dt += 1

    ax[6].set_ylabel(drv1 + " ahead | " + drv2 + " ahead", fontsize=15)

    l2, = ax[0].plot(second_car['Distance'],
                     second_car['Speed'], color=second_color)
    l1, = ax[0].plot(first_car['Distance'],
                     first_car['Speed'], color=first_color)
    ax[1].plot(second_car['Distance'], second_car['RPM'], color=second_color)
    ax[1].plot(first_car['Distance'], first_car['RPM'], color=first_color)
    ax[2].plot(second_car['Distance'], second_car['nGear'], color=second_color)
    ax[2].plot(first_car['Distance'], first_car['nGear'], color=first_color)
    ax[3].plot(second_car['Distance'],
               second_car['Throttle'], color=second_color)
    ax[3].plot(first_car['Distance'], first_car['Throttle'], color=first_color)
    ax[6].plot(first_car['Distance'], delta, color='white')

    ax[0].set_ylabel("Speed [km/h]", fontsize=15)
    ax[1].set_ylabel("RPM [#]", fontsize=15)
    ax[2].set_ylabel("Gear [#]", fontsize=15)
    ax[3].set_ylabel("Throttle [%]", fontsize=15)
    ax[4].set_ylabel("Brake [%]", fontsize=15)
    ax[5].set_ylabel("DRS", fontsize=15)

    ax[0].get_xaxis().set_ticklabels([])
    ax[1].get_xaxis().set_ticklabels([])
    ax[2].get_xaxis().set_ticklabels([])
    ax[3].get_xaxis().set_ticklabels([])
    ax[4].get_xaxis().set_ticklabels([])

    fig.align_ylabels()
    fig.legend((l1, l2), (drv1, drv2), loc='upper right', fontsize=20)

    ax[5].fill_between(second_car['Distance'], drs2,
                       step="pre", color=second_color, alpha=1)
    ax[5].fill_between(first_car['Distance'], drs1,
                       step="pre", color=first_color, alpha=1)
    ax[4].fill_between(second_car['Distance'], brake2,
                       step="pre", color=second_color, alpha=1)
    ax[4].fill_between(first_car['Distance'], first_car['Brake'],
                       step="pre", color=first_color, alpha=1)

    plt.subplots_adjust(left=0.06, right=0.99, top=0.9, bottom=0.05)

    ax[2].get_yaxis().set_major_locator(MaxNLocator(integer=True))

    ticks = ax[6].get_yticks()
    # set labels to absolute values and with integer representation
    ax[6].set_yticklabels([round(abs(tick), 1) for tick in ticks])
    
    # Increase the font size of the ticks for all axes
    for axis in ax:
        axis.tick_params(axis='both', which='major', labelsize=15)

    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')
    
    rstall(plt)
    mpl_lock.release()
    return "success"

def cornering_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]
    sn = input_list["session"]
    d1 = input_list["driver1"]
    d2 = input_list["driver2"]
    dist1 = input_list["dist1"]
    dist2 = input_list["dist2"]
    lap1 = input_list["lap1"]
    lap2 = input_list["lap2"]

    session = get_sess(yr, rc, sn)
    session.load()

    
    mpl_lock.acquire()
    


    # Get the laps
    laps = session.laps
    
    if (d1 == None or d1 == ''):
        d1 = laps.pick_fastest()['Driver']
        
    if (d2 == None or d2 == ''):
        d2 = laps.pick_fastest()['Driver']

    # Setting parameters
    driver_1, driver_2 = d1, d2

    car_data = laps.pick_driver(
        driver_1).pick_fastest().get_car_data().add_distance()
    dist = car_data['Distance']
    maxdist = dist[len(dist)-1]
    
    if (dist1 == None or dist1 == ''):
        dist1 = 0
        
    if (dist2 == None or dist2 == ''):
        dist2 = maxdist

    if (dist1 > dist2):
        dist1, dist2 = dist2, dist1

    distance_min, distance_max = dist1, dist2

    # Extracting the laps
    laps_driver_1 = laps.pick_driver(driver_1)
    laps_driver_2 = laps.pick_driver(driver_2)

    if (lap1 == None or lap1 == ''):
        telemetry_driver_1 = laps_driver_1.pick_fastest().get_car_data().add_distance()
    else:
        temp_laps1 = laps_driver_1[laps_driver_1['LapNumber'] == int(
            lap1)].iloc[0]
        telemetry_driver_1 = temp_laps1.get_car_data().add_distance()

    if (lap2 == None or lap2 == ''):
        telemetry_driver_2 = laps_driver_2.pick_fastest().get_car_data().add_distance()
    else:
        temp_laps2 = laps_driver_2[laps_driver_2['LapNumber'] == int(
            lap2)].iloc[0]
        telemetry_driver_2 = temp_laps2.get_car_data().add_distance()

    # Identifying the team for coloring later on
    team_driver_1 = laps_driver_1.reset_index().loc[0, 'Team']
    team_driver_2 = laps_driver_2.reset_index().loc[0, 'Team']

    # Assigning labels to what the drivers are currently doing
    telemetry_driver_1.loc[telemetry_driver_1['Brake']
                           > 0, 'CurrentAction'] = 'Brake'
    telemetry_driver_1.loc[telemetry_driver_1['Throttle']
                           == 100, 'CurrentAction'] = 'Full Throttle'
    telemetry_driver_1.loc[(telemetry_driver_1['Brake'] == 0) & (
        telemetry_driver_1['Throttle'] < 100), 'CurrentAction'] = 'Cornering'

    telemetry_driver_2.loc[telemetry_driver_2['Brake']
                           > 0, 'CurrentAction'] = 'Brake'
    telemetry_driver_2.loc[telemetry_driver_2['Throttle']
                           == 100, 'CurrentAction'] = 'Full Throttle'
    telemetry_driver_2.loc[(telemetry_driver_2['Brake'] == 0) & (
        telemetry_driver_2['Throttle'] < 100), 'CurrentAction'] = 'Cornering'

    # Numbering each unique action to identify changes, so that we can group later on
    telemetry_driver_1['ActionID'] = (
        telemetry_driver_1['CurrentAction'] != telemetry_driver_1['CurrentAction'].shift(1)).cumsum()
    telemetry_driver_2['ActionID'] = (
        telemetry_driver_2['CurrentAction'] != telemetry_driver_2['CurrentAction'].shift(1)).cumsum()

    # Identifying all unique actions
    actions_driver_1 = telemetry_driver_1[['ActionID', 'CurrentAction', 'Distance']].groupby(
        ['ActionID', 'CurrentAction']).max('Distance').reset_index()
    actions_driver_2 = telemetry_driver_2[['ActionID', 'CurrentAction', 'Distance']].groupby(
        ['ActionID', 'CurrentAction']).max('Distance').reset_index()

    actions_driver_1['Driver'] = driver_1
    actions_driver_2['Driver'] = driver_2

    # Calculating the distance between each action, so that we know how long the bar should be
    actions_driver_1['DistanceDelta'] = actions_driver_1['Distance'] - \
        actions_driver_1['Distance'].shift(1)
    actions_driver_1.loc[0,
                         'DistanceDelta'] = actions_driver_1.loc[0, 'Distance']

    actions_driver_2['DistanceDelta'] = actions_driver_2['Distance'] - \
        actions_driver_2['Distance'].shift(1)
    actions_driver_2.loc[0,
                         'DistanceDelta'] = actions_driver_2.loc[0, 'Distance']

    # Merging together
    all_actions = pd.concat([actions_driver_1, actions_driver_2])

    # Calculating average speed
    avg_speed_driver_1 = np.mean(telemetry_driver_1['Speed'].loc[
        (telemetry_driver_1['Distance'] >= distance_min) &
        (telemetry_driver_1['Distance'] >= distance_max)
    ])

    avg_speed_driver_2 = np.mean(telemetry_driver_2['Speed'].loc[
        (telemetry_driver_2['Distance'] >= distance_min) &
        (telemetry_driver_2['Distance'] >= distance_max)
    ])

    if avg_speed_driver_1 > avg_speed_driver_2:
        speed_text = f"{driver_1} {round(avg_speed_driver_1 - avg_speed_driver_2,2)}km/h faster"
    else:
        speed_text = f"{driver_1} {round(avg_speed_driver_2 - avg_speed_driver_1,2)}km/h faster"

    ##############################
    #
    # Setting everything up
    #
    ##############################
    plt.rcParams["figure.figsize"] = [13, 4]
    plt.rcParams["figure.autolayout"] = True

    telemetry_colors = {
        'Full Throttle': 'green',
        'Cornering': 'grey',
        'Brake': 'red',
    }

    plotting.setup_mpl()
    setup_dark()
    fig, ax = plt.subplots(2)

    ##############################
    #
    # Lineplot for speed
    #
    ##############################

    try:
        ax[0].plot(telemetry_driver_1['Distance'], telemetry_driver_1['Speed'],
                   label=driver_1, color=_driver_col(d1, session))
    except:
        ax[0].plot(telemetry_driver_1['Distance'],
                   telemetry_driver_1['Speed'], label=driver_1, color='grey')

    try:
        if (d1 != d2):
            ax[0].plot(telemetry_driver_2['Distance'], telemetry_driver_2['Speed'],
                       label=driver_2, color=_driver_col(d2, session))
        else:
            ax[0].plot(telemetry_driver_2['Distance'],
                       telemetry_driver_2['Speed'], label=driver_2, color='#777777')
    except:
        ax[0].plot(telemetry_driver_2['Distance'],
                   telemetry_driver_2['Speed'], label=driver_2, color='grey')

    # Speed difference
    if distance_min == None:
        ax[0].text(0, 200, speed_text, fontsize=15)
    else:
        ax[0].text(distance_min + 15, 200, speed_text, fontsize=15)

    ax[0].set(ylabel='Speed')
    ax[0].legend(loc="lower right")

    ##############################
    #
    # Horizontal barplot for telemetry
    #
    ##############################
    for driver in [driver_1, driver_2]:
        driver_actions = all_actions.loc[all_actions['Driver'] == driver]

        previous_action_end = 0
        for _, action in driver_actions.iterrows():
            ax[1].barh(
                [driver],
                action['DistanceDelta'],
                left=previous_action_end,
                color=telemetry_colors[action['CurrentAction']]
            )

            previous_action_end = previous_action_end + action['DistanceDelta']

    ##############################
    #
    # Styling of the plot
    #
    ##############################
    # Set x-label
    plt.xlabel('Distance')

    # Invert y-axis
    plt.gca().invert_yaxis()

    # Remove frame from plot
    ax[1].spines['top'].set_visible(False)
    ax[1].spines['right'].set_visible(False)
    ax[1].spines['left'].set_visible(False)

    # Add legend
    labels = list(telemetry_colors.keys())
    handles = [plt.Rectangle(
        (0, 0), 1, 1, color=telemetry_colors[label]) for label in labels]
    ax[1].legend(handles, labels)

    # Zoom in on the specific part we want to see
    ax[0].set_xlim(distance_min, distance_max)
    ax[1].set_xlim(distance_min, distance_max)

    if (lap1 == None or lap1 == ''):
        lap1 = "Fastest Lap"
    else:
        lap1 = "Lap " + str(lap1)
    if (lap2 == None or lap2 == ''):
        lap2 = "Fastest Lap"
    else:
        lap2 = "Lap " + str(lap2)

    # sn = session.event.get_session_name(sn)

    plt.suptitle(f"{yr} {rc} {sn}\n" +
                 d1 + " (" + lap1 + ") vs " + d2 + " (" + lap2 + ")", size=20)

    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')
    
    rstall(plt)
    mpl_lock.release()
    return "success"

def tires_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]
    sn = input_list["session"]
    sl = input_list["lap"]

    session = get_sess(yr, rc, sn)
    session.load()

    mpl_lock.acquire()

    plotting.setup_mpl()
    setup_dark()

    plt.rcParams["figure.figsize"] = [7, 5]
    plt.rcParams["figure.autolayout"] = True

    # Get the laps
    laps = session.laps

    # Calculate RaceLapNumber
    laps['RaceLapNumber'] = laps['LapNumber'] - 1

    # Starting lap
    laps = laps.loc[laps['RaceLapNumber'] >= sl]

    # Get all drivers
    drivers = pd.unique(laps['Driver'])

    # OPTIMIZATION: instead of every lap for every driver, sample representative laps
    # Pick the first lap of each stint (compound change) + every 5th lap per driver
    telemetry_parts = []

    for driver in drivers:
        driver_laps = laps.pick_driver(driver)
        if len(driver_laps) == 0:
            continue

        # Get stint boundaries (where compound changes)
        prev_compound = None
        sampled_laps = []
        for idx, (_, lap_data) in enumerate(driver_laps.iterlaps()):
            compound = lap_data['Compound']
            lap_num = lap_data['RaceLapNumber']
            # Always include: first lap of stint, every 10th lap
            if compound != prev_compound or idx % 10 == 0:
                sampled_laps.append(lap_data)
            prev_compound = compound

        for lap_data in sampled_laps:
            try:
                driver_telemetry = lap_data.get_telemetry().add_distance()
                driver_telemetry['Driver'] = driver
                driver_telemetry['Lap'] = lap_data['RaceLapNumber']
                driver_telemetry['Compound'] = lap_data['Compound']
                telemetry_parts.append(driver_telemetry)
            except Exception:
                continue

    telemetry = pd.concat(telemetry_parts) if telemetry_parts else pd.DataFrame()

    # Only keep required columns
    telemetry = telemetry[['Lap', 'Distance', 'Compound', 'Speed', 'X', 'Y']]

    # Everything that's not intermediate or wet will be "slick"
    telemetry['Compound'].loc[(telemetry['Compound'] != 'INTERMEDIATE') & (
        telemetry['Compound'] != 'WET')] = 'SLICK'

    # We want 25 mini-sectors
    num_minisectors = 25

    # What is the total distance of a lap?
    total_distance = max(telemetry['Distance'])

    # Generate equally sized mini-sectors
    minisector_length = total_distance / num_minisectors

    minisectors = [0]

    for i in range(0, (num_minisectors - 1)):
        minisectors.append(minisector_length * (i + 1))

    # Assign minisector to every row in the telemetry data (vectorized for speed)
    minisector_arr = np.array(minisectors)
    telemetry['Minisector'] = np.searchsorted(minisector_arr, telemetry['Distance'].values, side='right')

    # Calculate fastest tyre per mini sector
    average_speed = telemetry.groupby(['Lap', 'Minisector', 'Compound'])[
        'Speed'].mean().reset_index()

    # Select the compound with the highest average speed
    fastest_compounds = average_speed.loc[average_speed.groupby(
        ['Lap', 'Minisector'])['Speed'].idxmax()]

    # Get rid of the speed column and rename the Compound column
    fastest_compounds = fastest_compounds[['Lap', 'Minisector', 'Compound']].rename(
        columns={'Compound': 'Fastest_compound'})

    # Join the fastest compound per minisector with the full telemetry
    telemetry = telemetry.merge(fastest_compounds, on=['Lap', 'Minisector'])

    # Order the data by distance to make matploblib does not get confused
    telemetry = telemetry.sort_values(by=['Distance'])

    # Assign integer value to the compound because that's what matplotlib wants
    telemetry.loc[telemetry['Fastest_compound'] ==
                  "INTERMEDIATE", 'Fastest_compound_int'] = 1
    telemetry.loc[telemetry['Fastest_compound']
                  == "SLICK", 'Fastest_compound_int'] = 3
    telemetry.loc[telemetry['Fastest_compound']
                  == "WET", 'Fastest_compound_int'] = 2

    def generate_minisector_plot(lap, sn):
        # Get clean telemetry from fastest lap for smooth X,Y path
        clean_tel = session.laps.pick_fastest().get_telemetry().add_distance()
        clean_tel['Minisector'] = np.searchsorted(
            np.array([0] + [total_distance / num_minisectors * (i + 1) for i in range(num_minisectors - 1)]),
            clean_tel['Distance'].values, side='right')

        # Map fastest compound per minisector from the analysis
        single_lap = telemetry.loc[telemetry['Lap'] == lap]
        if 'Driver' in single_lap.columns:
            single_lap = single_lap.loc[single_lap['Driver'] == single_lap['Driver'].iloc[0]]
        compound_map = single_lap.groupby('Minisector')['Fastest_compound_int'].first().to_dict()
        clean_tel['Fastest_compound_int'] = clean_tel['Minisector'].map(compound_map).fillna(3).astype(float)

        x = np.array(clean_tel['X'].values)
        y = np.array(clean_tel['Y'].values)

        points = np.array([x, y]).T.reshape(-1, 1, 2)
        segments = np.concatenate([points[:-1], points[1:]], axis=1)
        compound = clean_tel['Fastest_compound_int'].to_numpy().astype(float)

        cmap = cm.get_cmap('ocean', 3)
        lc_comp = LineCollection(
            segments, norm=plt.Normalize(1, cmap.N+1), cmap=cmap)
        lc_comp.set_array(compound)
        lc_comp.set_linewidth(2)

        plt.rcParams['figure.figsize'] = [12, 5]
        plt.rcParams["figure.autolayout"] = True

        fig = plt.gcf()
        ax = plt.gca()
        fig.patch.set_facecolor('#1e1e1e')
        ax.set_facecolor('#1e1e1e')

        ax.add_collection(lc_comp)
        plt.axis('equal')
        plt.tick_params(labelleft=False, left=False,
                        labelbottom=False, bottom=False)

        cbar = plt.colorbar(mappable=lc_comp, boundaries=np.arange(1, 5))
        cbar.set_ticks(np.arange(1.5, 4.5))
        cbar.set_ticklabels(['Inters', 'Wets', 'Slicks'])
        cbar.ax.yaxis.set_tick_params(color='white')
        plt.setp(plt.getp(cbar.ax, 'yticklabels'), color='white')

        plt.suptitle(
            f"{yr} {rc} {sn}\n Lap {sl} - Tire Comparison", color='white')

    generate_minisector_plot(sl, sn)
    plot_corners(plt.gca(), session)
    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')
    
    rstall(plt)
    mpl_lock.release()
    return "success"

def strategy_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]

    # Load the session data
    race = fastf1.get_session(yr, rc, 'R')
    race.load()
    laps = race.laps

    
    mpl_lock.acquire()

    driver_stints = laps[['Driver', 'Stint', 'Compound', 'LapNumber']].groupby(
        ['Driver', 'Stint', 'Compound']
    ).count().reset_index()

    driver_stints = driver_stints.rename(columns={'LapNumber': 'StintLength'})

    driver_stints = driver_stints.sort_values(by=['Stint'])

    if yr <= 2018:

        compound_colors = {
            'HYPERSOFT': '#FFAACC',
            'ULTRASOFT': '#772277',
            'SUPERSOFT': '#FF3333',
            'SOFT': '#FFF200',
            'MEDIUM': '#EBEBEB',
            'HARD': '#07A6F5',
            'SUPERHARD': '#CC6600',
            'INTERMEDIATE': '#39B54A',
            'WET': '#0033EE'
        }
        
    else:
        
        compound_colors = {
            'SOFT': '#FF3333',
            'MEDIUM': '#FFF200',
            'HARD': '#EBEBEB',
            'INTERMEDIATE': '#39B54A',
            'WET': '#0033EE'
        }

    plt.rcParams["figure.figsize"] = [15, 10]
    plt.rcParams["figure.autolayout"] = True

    plotting.setup_mpl()
    setup_dark()
    fig, ax = plt.subplots()

    for driver in race.results['Abbreviation']:
        stints = driver_stints.loc[driver_stints['Driver'] == driver]

        previous_stint_end = 0
        for _, stint in stints.iterrows():
            plt.barh(
                [driver],
                stint['StintLength'],
                left=previous_stint_end,
                color=compound_colors[stint['Compound']],
                edgecolor="black"
            )

            previous_stint_end = previous_stint_end + stint['StintLength']

    # Set title
    plt.title(f"Race strategy - {race.event.year} {race.event['EventName']}\n")

    # Set x-label
    plt.xlabel('Lap')

    # Invert y-axis
    plt.gca().invert_yaxis()

    # Add legend
    labels = list(compound_colors.keys())
    handles = [plt.Rectangle(
        (0, 0), 1, 1, color=compound_colors[label]) for label in labels]
    ax.legend(handles, labels)

    # Remove frame from plot
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)

    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')
    
    rstall(plt)
    mpl_lock.release()
    return "success"

def sectors_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]
    sn = input_list["session"]
    d1 = input_list["driver1"]
    d2 = input_list["driver2"]
    lap1 = input_list["lap1"]
    lap2 = input_list["lap2"]

    session = get_sess(yr, rc, sn)
    session.load()

    
    mpl_lock.acquire()
    


    rstall(plt)

    plotting.setup_mpl()
    setup_dark()

    # Explore the lap data
    session.laps
    
    if (d1 == None or d1 == ''):
        d1 = session.laps.pick_fastest()['Driver']
        
    if (d2 == None or d2 == ''):
        d2 = session.laps.pick_fastest()['Driver']

    driver_1 = d1
    driver_2 = d2

    try:
        color_1 = _driver_col(d1, session)
    except:
        color_1 = 'grey'

    try:
        if d1 != d2:
            color_2 = _driver_col(d2, session)
        else:
            color_2 = '#777777'
    except:
        color_2 = 'grey'

    # Find the laps
    laps_driver_1 = session.laps.pick_driver(driver_1)
    laps_driver_2 = session.laps.pick_driver(driver_2)

    if (lap1 == None or lap1 == ''):
        fastest_driver_1 = laps_driver_1.pick_fastest()
    else:
        fastest_driver_1 = laps_driver_1[laps_driver_1['LapNumber'] == int(
            lap1)].iloc[0]

    if (lap2 == None or lap2 == ''):
        fastest_driver_2 = laps_driver_2.pick_fastest()
    else:
        fastest_driver_2 = laps_driver_2[laps_driver_2['LapNumber'] == int(
            lap2)].iloc[0]

    telemetry_driver_1 = fastest_driver_1.get_telemetry()
    telemetry_driver_2 = fastest_driver_2.get_telemetry()

    # Get the gap (delta time) between driver 1 and driver 2
    delta_time, ref_tel, compare_tel = fastf1.utils.delta_time(
        fastest_driver_1, fastest_driver_2)

    # Identify team colors
    team_driver_1 = laps_driver_1['Team'].iloc[0]
    team_driver_2 = laps_driver_2['Team'].iloc[0]

    # Merge the telemetry from both drivers into one dataframe
    telemetry_driver_1['Driver'] = driver_1
    telemetry_driver_2['Driver'] = driver_2

    telemetry = pd.concat([telemetry_driver_1, telemetry_driver_2])

    # Calculate minisectors
    num_minisectors = 25
    total_distance = max(telemetry['Distance'])
    minisector_length = total_distance / num_minisectors

    minisectors = [0]

    for i in range(0, (num_minisectors - 1)):
        minisectors.append(minisector_length * (i + 1))

    # Assign a minisector number to every row in the telemetry dataframe
    telemetry['Minisector'] = telemetry['Distance'].apply(
        lambda dist: (
            int((dist // minisector_length) + 1)
        )
    )

    # Calculate minisector speeds per driver
    average_speed = telemetry.groupby(['Minisector', 'Driver'])[
        'Speed'].mean().reset_index()

    # Per minisector, find the fastest driver
    fastest_driver = average_speed.loc[average_speed.groupby(['Minisector'])[
        'Speed'].idxmax()]
    fastest_driver = fastest_driver[['Minisector', 'Driver']].rename(
        columns={'Driver': 'Fastest_driver'})

    # Merge the fastest_driver dataframe to the telemetry dataframe on minisector
    telemetry = telemetry.merge(fastest_driver, on=['Minisector'])
    telemetry = telemetry.sort_values(by=['Distance'])

    # Since our plot can only work with integers, we need to convert the driver abbreviations to integers (1 or 2)
    telemetry.loc[telemetry['Fastest_driver']
                  == driver_1, 'Fastest_driver_int'] = 1
    telemetry.loc[telemetry['Fastest_driver']
                  == driver_2, 'Fastest_driver_int'] = 2

    # Get the x and y coordinates
    x = np.array(telemetry['X'].values)
    y = np.array(telemetry['Y'].values)

    # Convert the coordinates to points, and then concat them into segments
    points = np.array([x, y]).T.reshape(-1, 1, 2)
    segments = np.concatenate([points[:-1], points[1:]], axis=1)
    fastest_driver_array = telemetry['Fastest_driver_int'].to_numpy().astype(
        float)

    # The segments we just created can now be colored according to the fastest driver in a minisector
    cmap = ListedColormap([color_1, color_2])
    lc_comp = LineCollection(
        segments, norm=plt.Normalize(1, cmap.N+1), cmap=cmap)
    lc_comp.set_array(fastest_driver_array)
    lc_comp.set_linewidth(5)

    plt.rcParams['figure.figsize'] = [18, 10]
    plt.rcParams["figure.autolayout"] = True

    fig = plt.gcf()
    ax = plt.gca()
    fig.patch.set_facecolor('#1e1e1e')
    ax.set_facecolor('#1e1e1e')

    ax.add_collection(lc_comp)
    plt.axis('equal')
    plt.box(False)
    plt.tick_params(labelleft=False, left=False,
                    labelbottom=False, bottom=False)

    # Add a colorbar for as legend
    cbar = plt.colorbar(mappable=lc_comp, boundaries=np.arange(1, 4))
    cbar.set_ticks(np.arange(1.5, 3.5))
    cbar.set_ticklabels([driver_1, driver_2])

    if (lap1 == None or lap1 == ''):
        lap1 = "Fastest Lap"
    else:
        lap1 = "Lap " + str(lap1)
    if (lap2 == None or lap2 == ''):
        lap2 = "Fastest Lap"
    else:
        lap2 = "Lap " + str(lap2)

    # sn = session.event.get_session_name(sn)

    plt.suptitle(f"{yr} {rc} {sn} - Fastest Sectors\n" +
                 d1 + " (" + lap1 + ") vs " + d2 + " (" + lap2 + ")", size=25, color='white')
    cbar.ax.yaxis.set_tick_params(color='white')
    plt.setp(plt.getp(cbar.ax, 'yticklabels'), color='white')

    plot_corners(plt.gca(), session)

    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')

    rstall(plt)
    mpl_lock.release()
    return "success"

def rt_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]
    drivers = input_list["drivers"]

    session = get_sess(yr, rc, 'Race')
    session.load()

    
    mpl_lock.acquire()
    


    plotting.setup_mpl()
    setup_dark()
    fig, ax = plt.subplots()

    plt.rcParams["figure.figsize"] = [10, 8]
    plt.rcParams["figure.autolayout"] = True

    title = str(drivers[0])

    i = 0
    temp = 0
    title = ""
    title = title + str(drivers[0])
    while (i < len(drivers)):
        temp = session.laps.pick_driver(drivers[i])
        if (i+1 < len(drivers)):
            title = title + " vs " + str(drivers[i+1])
        i += 1
    i = 0

    # suppress errors as dont really matter for this
    pd.options.mode.chained_assignment = None

    laps = session.laps
    # laps = laps.loc[laps['PitOutTime'].isna() & laps['PitInTime'].isna() & laps['LapTime'].notna()]
    laps['LapTimeSeconds'] = laps['LapTime'].dt.total_seconds()

    avg = laps.groupby(['DriverNumber', 'Driver'])['LapTimeSeconds'].mean()

    # calculate the diff vs the best average. You could average the average if you want?
    laps['Difference'] = laps['LapTimeSeconds'] - avg.min()

    laps['Cumulative'] = laps.groupby('Driver')['Difference'].cumsum()

    fig, ax = plt.subplots()
    fig.set_size_inches(15, 7)

    for driver in drivers:
        temp = laps.loc[laps['Driver'] == driver][[
            'Driver', 'LapNumber', 'Cumulative']]
        try:
            temp_color = _driver_col(temp.iloc[0]['Driver'], session)
        except:
            temp_color = 'grey'
        ax.plot(temp['LapNumber'], temp['Cumulative'],
                label=temp.iloc[0]['Driver'], color=temp_color)

    ax.set_xlabel('Lap Number')
    ax.set_ylabel('Race Trace (relative to imaginary driver)')
    ax.set_title("Race Trace - " +
                 f"{yr} {rc}\n" + title)

    ax.legend()

    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')
    
    rstall(plt)
    mpl_lock.release()
    return "success"

def positions_func(input_list, datetime):

    yr = input_list["year"]
    rc = input_list["race"]
    safety_car = input_list.get("safety_car", True)

    sn = "Race"

    session = get_sess(yr, rc, sn)
    session.load(telemetry=False, weather=False)

    mpl_lock.acquire()

    plotting.setup_mpl()
    setup_dark()
    fig, ax = plt.subplots(figsize=(12.0, 6))

    for drv in session.drivers:
        drv_laps = session.laps.pick_driver(drv)

        abb = drv_laps['Driver'].iloc[0]
        try:
            color = _driver_col(abb, session)
        except:
            color = 'grey'

        ax.plot(drv_laps['LapNumber'], drv_laps['Position'],
                label=abb, color=color)

    if safety_car:
        try:
            rcm = session.race_control_messages
            sc_on = False
            vsc_on = False
            sc_start = None
            vsc_start = None
            sc_labeled = False
            vsc_labeled = False

            for _, msg in rcm.iterrows():
                status = str(msg.get('Status', '')).upper() if 'Status' in rcm.columns else ''
                category = str(msg.get('Category', '')).upper() if 'Category' in rcm.columns else ''

                # Find the closest lap number for this message time
                msg_time = msg.get('Time')
                if msg_time is not None and hasattr(session, 'laps') and len(session.laps) > 0:
                    lap_times = session.laps[['LapNumber', 'Time']].dropna()
                    if len(lap_times) > 0:
                        closest_idx = (lap_times['Time'] - msg_time).abs().idxmin()
                        lap_num = lap_times.loc[closest_idx, 'LapNumber']
                    else:
                        continue
                else:
                    continue

                if 'SAFETY CAR DEPLOYED' in status or (category == 'SAFETYCAR' and 'DEPLOYED' in status):
                    sc_on = True
                    sc_start = lap_num
                elif 'SAFETY CAR IN' in status or (sc_on and 'ENDING' in status):
                    if sc_on and sc_start is not None:
                        ax.axvspan(sc_start, lap_num, alpha=0.15, color='yellow',
                                  label='SC' if not sc_labeled else None)
                        sc_labeled = True
                    sc_on = False
                    sc_start = None
                elif 'VIRTUAL SAFETY CAR DEPLOYED' in status or 'VSC DEPLOYED' in status:
                    vsc_on = True
                    vsc_start = lap_num
                elif 'VSC ENDING' in status or (vsc_on and 'ENDING' in status):
                    if vsc_on and vsc_start is not None:
                        ax.axvspan(vsc_start, lap_num, alpha=0.15, color='orange',
                                  label='VSC' if not vsc_labeled else None)
                        vsc_labeled = True
                    vsc_on = False
                    vsc_start = None
        except Exception:
            pass  # If race control messages aren't available, skip SC overlay

    ax.set_ylim([20.5, 0.5])
    ax.set_yticks([1, 5, 10, 15, 20])
    ax.set_xlabel('Lap')
    ax.set_ylabel('Position')
    
    ax.legend(bbox_to_anchor=(1.0, 1.02))

    # sn = session.event.get_session_name(sn)
    plt.suptitle(
        f"{yr} {rc} {sn}\nPositions Changes")
    plt.setp(ax.get_xticklabels(), fontsize=7)
    
    plt.tight_layout()

    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')
    
    rstall(plt)
    mpl_lock.release()
    return "success"

def battles_func(input_list, datetime):
    
    yr = input_list["year"]
    drivers = input_list["drivers"]

    def ergast_retrieve(api_endpoint: str):
        url = f'https://api.jolpi.ca/ergast/f1/{api_endpoint}.json'
        response = requests.get(url).json()
        
        return response['MRData']

    all_quali_results = pd.DataFrame()

    # We want this so that we know which driver belongs to which team, so we can color them later
    team_drivers = {}

    current_round = 1

    while True:
        race = ergast_retrieve(f'{yr}/{current_round}/qualifying')
        
        # If session doesn't exist, cancel loop
        if not race['RaceTable']['Races']:
            break

        results = race['RaceTable']['Races'][0]['QualifyingResults']

        quali_results = {'round': current_round}

        for j in range(len(results)):
            try:
                driver = results[j]['Driver']['code']
            except:
                driver = " ".join(word[0].upper()+word[1:] for word in(results[j]['Driver']['driverId'].replace("_", "\n")).split(" "))
            position = int(results[j]['position'])
            team = results[j]['Constructor']['name']
            
            if driver not in drivers:
                continue
            
            # Create mapping for driver - team
            if not team in team_drivers:
                team_drivers[team] = [driver]
            else:
                if not driver in team_drivers[team]:
                    team_drivers[team].append(driver)
                    
            quali_results[driver] = position
                
        all_quali_results = pd.concat([all_quali_results, pd.DataFrame([quali_results])], ignore_index=True)
        
        current_round += 1
        
    # Now we want to know, per round, per team, who qualified higher?
    all_quali_battle_results = []
    team_colors_palette = []

    for team in team_drivers:
        drivers = team_drivers[team]
        
        quali_results = all_quali_results[drivers]
        
        # We do dropna() to only include the sessions in which both drivers participated
        fastest_driver_per_round = quali_results.dropna().idxmin(axis=1)
        
        quali_battle_result = fastest_driver_per_round.value_counts().reset_index()
        
        for _, row in quali_battle_result.iterrows():
            all_quali_battle_results.append({
                'driver': row['index'],
                'team': team,
                'quali_score': int(row['count'])  # Correctly fetch the score
            })

        try:
            team_colors_palette.append(_team_col(team))
        except:
            team_colors_palette.append(None)
        # If none, replace None with grey
        team_colors_palette = ['#D3D3D3' if v is None else v for v in team_colors_palette]


    # Finally, convert to a DataFrame so we can plot
    all_quali_battle_results = pd.DataFrame.from_dict(all_quali_battle_results)

    # Increase the size of the plot 
    # sns.set(rc={'figure.figsize':(11.7,8.27)})
    sns.set_theme(rc={'figure.figsize':(11.7,8.27)})

    # Create custom color palette
    # custom_palette = sns.set_palette(sns.color_palette(team_colors_palette))
    custom_palette = sns.color_palette(team_colors_palette)
    mpl_lock.acquire()

    plotting.setup_mpl()
    setup_dark()
    
    # Load custom font
    font_path = "fonts/Formula1-Regular_web.ttf"  # adjust path if needed
    font_prop = fm.FontProperties(fname=font_path)

    fig, ax = plt.subplots()

    ax.set_title(f"{yr} Teammate Qualifying Battle", color = 'white', fontproperties=font_prop)
    fig.set_facecolor("black")
    ax.xaxis.label.set_color("white")
    ax.yaxis.label.set_color("white")
    # set colorbar tick color
    ax.yaxis.set_tick_params(color='white')
    ax.xaxis.set_tick_params(color='white')

    # set colorbar ticklabels
    plt.setp(plt.getp(ax.axes, 'yticklabels'), color='white', fontproperties=font_prop)
    plt.setp(plt.getp(ax.axes, 'xticklabels'), color='white', fontproperties=font_prop)
    ax.set_facecolor('black')

    ax.spines['bottom'].set_color('black')
    ax.spines['top'].set_color('black')
    ax.spines['left'].set_color('black')
    ax.spines['right'].set_color('black')
    for t in ax.xaxis.get_ticklines(): t.set_color('black')
    for t in ax.yaxis.get_ticklines(): t.set_color('black')
    ax.set_facecolor('black')

    g = sns.barplot(
        x='driver',
        y='quali_score', 
        hue='team',
        data=all_quali_battle_results, 
        dodge=False,
        palette=custom_palette,
    )
    
    plt.yticks(range(max(all_quali_battle_results['quali_score']) + 1))

    plt.legend([],[], frameon=False)

    g.set(xlabel=None)
    g.set(ylabel=None)
    
    plt.savefig(dir_path + get_path() + "res" + get_path() + "output" + get_path() + str(datetime) + '.png', bbox_inches='tight')
    
    rstall(plt)
    mpl_lock.release()
    return "success"

def track_func(input_list, datetime):
    yr = input_list["year"]
    rc = input_list["race"]

    event = fastf1.get_event(yr, rc)

    text = ""
    text += f"Circuit: {event['EventName']}\n"
    text += f"Country: {event['Country']}\n"
    text += f"Location: {event['Location']}\n"
    text += f"Round: {event['RoundNumber']}\n"
    text += f"Format: {event.get('EventFormat', 'N/A')}\n\n"

    # Add session dates
    for i in range(1, 6):
        session_name = event.get(f'Session{i}', None)
        session_date = event.get(f'Session{i}Date', None)
        if session_name and str(session_name) != 'nan':
            date_str = str(session_date)[:10] if session_date else 'TBD'
            text += f"{session_name}: {date_str}\n"

    make_img(datetime, text)
    return "success"

def driver_stats_func(input_list, datetime):
    yr = input_list["year"]
    drivers = input_list["drivers"]
    driver = drivers[0] if isinstance(drivers, list) else drivers

    # Get all race results for the season
    url = f'https://api.jolpi.ca/ergast/f1/{yr}/results.json?limit=1000'
    response = requests.get(url).json()
    races = response['MRData']['RaceTable']['Races']

    wins = 0
    podiums = 0
    dnfs = 0
    points = 0
    grid_positions = []
    finish_positions = []
    fastest_laps = 0
    races_entered = 0

    for race in races:
        for result in race['Results']:
            # Match driver by code or by constructing from driverId
            code = result['Driver'].get('code', '')
            if code.upper() != driver.upper():
                continue

            races_entered += 1
            pos = int(result['position'])
            grid = int(result['grid'])
            pts = float(result['points'])
            status = result['status']

            points += pts
            grid_positions.append(grid)
            finish_positions.append(pos)

            if pos == 1:
                wins += 1
            if pos <= 3:
                podiums += 1
            if status != 'Finished' and not status.startswith('+'):
                dnfs += 1
            if result.get('FastestLap', {}).get('rank') == '1':
                fastest_laps += 1

    if races_entered == 0:
        text = f"No data found for {driver} in {yr}"
    else:
        avg_grid = sum(grid_positions) / len(grid_positions) if grid_positions else 0
        avg_finish = sum(finish_positions) / len(finish_positions) if finish_positions else 0

        text = f"Driver Stats: {driver} - {yr}\n"
        text += f"{'='*35}\n"
        text += f"Races:         {races_entered}\n"
        text += f"Points:        {points:.0f}\n"
        text += f"Wins:          {wins}\n"
        text += f"Podiums:       {podiums}\n"
        text += f"Fastest Laps:  {fastest_laps}\n"
        text += f"DNFs:          {dnfs}\n"
        text += f"Avg Grid:      {avg_grid:.1f}\n"
        text += f"Avg Finish:    {avg_finish:.1f}\n"

    make_img(datetime, text)
    return "success"

### END OF PLOTTING FUNCTIONS ###


### If you want to run a function make sure to provide the correct input_list and datetime ###

# results_func({"year": 2021, "race": "Abu Dhabi Grand Prix", "session": "Race"}, get_datetime())