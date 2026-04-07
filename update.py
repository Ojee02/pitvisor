import os
import pandas as pd
import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning
import datetime as dt
import fastf1 as ff1
from fastf1 import plotting
import plotly.express as px
from plotly.io import show
import seaborn as sns
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from pymongo import MongoClient
from dotenv import load_dotenv
import traceback
from utils import *
if os.name == 'nt':
    from fastf1.ergast import Ergast

load_dotenv()

connection_string = os.getenv("connection_string")
db_name = os.getenv("db_name")

client = MongoClient(connection_string)
db = client[db_name]



# enable cache
if os.path.exists(dir_path + get_path() + "doc_cache"):
    ff1.Cache.enable_cache(dir_path + get_path() + "doc_cache")

# set yr to current year
yr = dt.datetime.now().year

# get driver standings
def get_drivers_standings():
    url = "https://api.jolpi.ca/ergast/f1/" + str(yr) + "/driverStandings.json"
    response = requests.get(url)
    data = response.json()
    drivers_standings = data['MRData']['StandingsTable']['StandingsLists'][0]['DriverStandings']  # noqa: E501
    return drivers_standings

# gets the driver standings in text format
def drvr(driver_standings):
    st = ""
    for _, driver in enumerate(driver_standings):

        st = st + \
            f"{driver['position']}: {driver['Driver']['code']}, Points: {driver['points']}" + "\n"
    return st

# gets the driver standings and plots them
def driver_func(yr):

    def ergast_retrieve(api_endpoint: str):
        url = f'https://api.jolpi.ca/ergast/f1/{api_endpoint}.json'
        
        try:
            # Disable SSL verification
            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
            
            response = requests.get(url, verify=False, timeout=300)  # Set the timeout duration to 10 seconds
            response.raise_for_status()  # Raise an exception for any HTTP errors
            return response.json()['MRData']
        except requests.exceptions.RequestException as e:
            print(f"An error occurred: {e}")
            return None

    colors = ["#333333", "#444444", "#555555", "#666666", "#777777", "#888888", "#999999", "#AAAAAA", "#BBBBBB", "#CCCCCC"]
    color_counter = 0

    # Specify the number of rounds we want in our plot (in other words, specify the current round)
    rounds = 365

    # Initiate an empty dataframe to store our data
    all_championship_standings = pd.DataFrame()

    # We also want to store which driver drives for which team, which will help us later
    driver_team_mapping = {}
    driver_point_mapping = {}

    # Initate a loop through all the rounds
    for i in range(1, rounds + 1):
        try:
            # Make request to driverStandings endpoint for the current round
            race = ergast_retrieve(f'{yr}/{i}/driverStandings')
            
            # Get the standings from the result
            standings = race['StandingsTable']['StandingsLists'][0]['DriverStandings']
            
            # Initiate a dictionary to store the current rounds' standings in
            current_round = {'round': i}
            
            temp_pos = 1
            
            # Loop through all the drivers to collect their information
            for i in range(len(standings)):
                try:
                    driver = standings[i]['Driver']['code']
                except:
                    driver = " ".join(word[0].upper()+word[1:] for word in(standings[i]['Driver']['driverId'].replace("_", " ")).split(" "))
                
                if 'position' not in standings[i]:
                    position = temp_pos
                    temp_pos += 1
                else:
                    position = standings[i]['position']
                    
                points = standings[i]['points']
                
                # Store the drivers' position
                current_round[driver] = int(position)
                
                # Create mapping for driver-team to be used for the coloring of the lines
                driver_team_mapping[driver] = standings[i]['Constructors'][0]['name']

                driver_point_mapping[driver] = points

            # Append the current round to our fial dataframe
            all_championship_standings = all_championship_standings._append(current_round, ignore_index=True)
        except Exception as exc:
            print(traceback.format_exc())
            break
        
    rounds = i
        
    # Set the round as the index of the dataframe
    all_championship_standings = all_championship_standings.set_index('round')

    # Melt data so it can be used as input for plot
    all_championship_standings_melted = pd.melt(all_championship_standings.reset_index(), ['round'])

    # Increase the size of the plot 
    # sns.set(rc={'figure.figsize':(11.7,8.27)})
    sns.set_theme(rc={'figure.figsize':(11,8.27)})
    if yr<2005:
        # sns.set(rc={'figure.figsize':(13,8.27)})
        sns.set_theme(rc={'figure.figsize':(13,8.27)})
        if yr<1996:
            # sns.set(rc={'figure.figsize':(14,10)})
            sns.set_theme(rc={'figure.figsize':(14,10)})

    # Load custom font
    font_path = "fonts/Formula1-Regular_web.ttf"  # adjust path if needed
    font_prop = fm.FontProperties(fname=font_path)

    # Initiate the plot
    fig, ax = plt.subplots()

    # Set the title of the plot
    ax.set_title(str(yr) + " Championship Standing", color = 'white', fontproperties=font_prop)
    fig.set_facecolor("black")
    ax.xaxis.label.set_color("white")
    ax.yaxis.label.set_color("white")

    # Draw a line for every driver in the data by looping through all the standings
    # The reason we do it this way is so that we can specify the team color per driver
    for driver in pd.unique(all_championship_standings_melted['variable']):
        try:
            color=ff1.plotting.get_team_color(driver_team_mapping[driver])
        except:
            color=colors[color_counter]
        sns.lineplot(
            x='round', 
            y='value', 
            data=all_championship_standings_melted.loc[all_championship_standings_melted['variable']==driver], 
            color=color
        )
        try:
            color=ff1.plotting.get_team_color(driver_team_mapping[driver])
        except:
            color_counter += 1
            if color_counter >= len(colors):
                color_counter = 0

    # Invert Y-axis to have championship leader (#1) on top
    ax.invert_yaxis()

    # Set the values that appear on the x- and y-axes
    ax.set_xticks(range(1, rounds))
    if yr>1995:
        ax.set_yticks(range(1, len(driver_team_mapping)+1))
    else:
        ax.set_yticks(range(1, 31))

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

    # Set the labels of the axes
    ax.set_xlabel("Round", color = 'white', fontproperties=font_prop)
    ax.set_ylabel("Championship position", color = 'white', fontproperties=font_prop)

    # Disable the gridlines 
    ax.grid(False)
    
    # Add the driver name to the lines
    for line, name , points in zip(ax.lines, all_championship_standings.columns.tolist(), driver_point_mapping.values()):
        y = line.get_ydata()[-1]
        x = line.get_xdata()[-1]
            
        text = ax.annotate(
            name + ": " + str(points),
            xy=(x + 0.1, y),
            xytext=(0, 0),
            color=line.get_color(),
            xycoords=(
                ax.get_xaxis_transform(),
                ax.get_yaxis_transform()
            ),
            textcoords="offset points",
            fontproperties=font_prop
        )

    # Save the plot
    # plt.show()
    file = str(yr) + "_DRIVERS_STANDINGS" + '.png'
    plt.savefig("data_dump/" + file)
    upload_drivers_standings(yr, "data_dump/" + file)

# get constructorss standings
def get_constructors_standings():
    url = "https://api.jolpi.ca/ergast/f1/" + \
        str(yr) + "/constructorStandings.json"
    response = requests.get(url)
    data = response.json()
    constructors_standings = data['MRData']['StandingsTable']['StandingsLists'][0]['ConstructorStandings']  # noqa: E501
    return constructors_standings

# get the constructors standings in text format
def constr(constructor_standings):
    st = ""
    for _, constructor in enumerate(constructor_standings):

        st = st + \
            f"{constructor['position']}: {constructor['Constructor']['name']}, Points: {constructor['points']}" + "\n"
    return st

# get the constructors standings and plots them
def const_func(yr):

    def ergast_retrieve(api_endpoint: str):
        url = f'https://api.jolpi.ca/ergast/f1/{api_endpoint}.json'
        
        try:
            # Disable SSL verification
            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
            
            response = requests.get(url, verify=False, timeout=300)  # Set the timeout duration to 10 seconds
            response.raise_for_status()  # Raise an exception for any HTTP errors
            return response.json()['MRData']
        except requests.exceptions.RequestException as e:
            print(f"An error occurred: {e}")
            return None

    colors = ["#333333", "#444444", "#555555", "#666666", "#777777", "#888888", "#999999", "#AAAAAA", "#BBBBBB", "#CCCCCC"]
    color_counter = 0

    # Specify the number of rounds we want in our plot (in other words, specify the current round)
    rounds = 365

    # Initiate an empty dataframe to store our data
    all_championship_standings = pd.DataFrame()

    # We also want to store which driver drives for which team, which will help us later
    constructor_team_mapping = {}
    constructor_point_mapping = {}

    # Initate a loop through all the rounds
    for i in range(1, rounds + 1):
        try:
            # Make request to driverStandings endpoint for the current round
            race = ergast_retrieve(f'{yr}/{i}/constructorStandings')
            
            # Get the standings from the result
            standings = race['StandingsTable']['StandingsLists'][0]['ConstructorStandings']
            
            # Initiate a dictionary to store the current rounds' standings in
            current_round = {'round': i}
            
            # Loop through all the drivers to collect their information
            for i in range(len(standings)):
                constructor = standings[i]['Constructor']['name']
                position = standings[i]['position']
                points = standings[i]['points']
                
                # Store the drivers' position
                current_round[constructor] = int(position)
                
                # Create mapping for driver-team to be used for the coloring of the lines
                constructor_team_mapping[constructor] = standings[i]['Constructor']['name']
                
                constructor_point_mapping[constructor] = points


            # Append the current round to our fial dataframe
            all_championship_standings = all_championship_standings._append(current_round, ignore_index=True)
        except:
            break
        
    rounds = i
        
    # Set the round as the index of the dataframe
    all_championship_standings = all_championship_standings.set_index('round')

    # Melt data so it can be used as input for plot
    all_championship_standings_melted = pd.melt(all_championship_standings.reset_index(), ['round'])

    # Increase the size of the plot 
    # sns.set(rc={'figure.figsize':(15,8.27)})
    sns.set_theme(rc={'figure.figsize':(15,8.27)})
    if yr == 1961 or yr == 1962 or yr == 1971:
        # sns.set(rc={'figure.figsize':(16,8.27)})
        sns.set_theme(rc={'figure.figsize':(16,8.27)})

    # Load custom font
    font_path = "fonts/Formula1-Regular_web.ttf"  # adjust path if needed
    font_prop = fm.FontProperties(fname=font_path)

    # Initiate the plot
    fig, ax = plt.subplots()

    # Set the title of the plot
    ax.set_title(str(yr) + " Championship Standing", color = 'white', fontproperties=font_prop)
    fig.set_facecolor("black")
    ax.xaxis.label.set_color("white")
    ax.yaxis.label.set_color("white")

    # Draw a line for every driver in the data by looping through all the standings
    # The reason we do it this way is so that we can specify the team color per driver
    for constructor in pd.unique(all_championship_standings_melted['variable']):
        try:
            color=ff1.plotting.get_team_color(constructor_team_mapping[constructor])
        except:
            color=colors[color_counter]
        sns.lineplot(
            x='round', 
            y='value', 
            data=all_championship_standings_melted.loc[all_championship_standings_melted['variable']==constructor], 
            color=color
        )
        try:
            color=ff1.plotting.get_team_color(constructor_team_mapping[constructor])
        except:
            color_counter += 1
            if color_counter >= len(colors):
                color_counter = 0

    # Invert Y-axis to have championship leader (#1) on top
    ax.invert_yaxis()

    # Set the values that appear on the x- and y-axes
    ax.set_xticks(range(1, rounds))
    ax.set_yticks(range(1, len(constructor_team_mapping)+1))

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

    # Set the labels of the axes
    ax.set_xlabel("Round", color = 'white', fontproperties=font_prop)
    ax.set_ylabel("Championship position", color = 'white', fontproperties=font_prop)

    # Disable the gridlines 
    ax.grid(False)

    # Add the driver name to the lines
    for line, name, points in zip(ax.lines, all_championship_standings.columns.tolist(), constructor_point_mapping.values()):
        y = line.get_ydata()[-1]
        x = line.get_xdata()[-1]
            
        text = ax.annotate(
            name.replace("amp;", "") + ": " + str(points),
            xy=(x + 0.1, y),
            xytext=(0, 0),
            color=line.get_color(),
            xycoords=(
                ax.get_xaxis_transform(),
                ax.get_yaxis_transform()
            ),
            textcoords="offset points",
            fontproperties=font_prop
        )

    # Save the plot
    #plt.show()
    file = str(yr) + "_CONSTRUCTORS_STANDINGS" + '.png'
    plt.savefig("data_dump/" + file)
    upload_constructors_standings(yr, "data_dump/" + file)

# get the heatmap of the drivers standings
def points_func(yr):
    
    if os.name != 'nt':
        raise Exception("Function is not available")
    
    ergast = Ergast()
    races = ergast.get_race_schedule(yr)
    results = []

    # For each race in the season
    for rnd, race in races['raceName'].items():

        try: 
            # Get results. Note that we use the round no. + 1, because the round no.
            # starts from one (1) instead of zero (0)
            temp = ergast.get_race_results(season=yr, round=rnd + 1)
            temp = temp.content[0]

            # If there is a sprint, get the results as well
            sprint = ergast.get_sprint_results(season=yr, round=rnd + 1)
            if sprint.content and sprint.description['round'][0] == rnd + 1:
                temp = pd.merge(temp, sprint.content[0], on='driverCode', how='left')
                # Add sprint points and race points to get the total
                temp['points'] = temp['points_x'] + temp['points_y']
                temp.drop(columns=['points_x', 'points_y'], inplace=True)

            # Add round no. and grand prix name
            temp['round'] = rnd + 1
            temp['race'] = race.removesuffix(' Grand Prix')
            if yr > 1990:
                temp = temp[['round', 'race', 'driverCode', 'points']]  # Keep useful cols.
            else:
                temp['driverCode'] = temp['givenName'] + ' ' + temp['familyName']
                temp = temp[['round', 'race', 'driverCode', 'points']]  # Keep useful cols.
            results.append(temp)
        except:
            pass

    # Append all races into a single dataframe
    results = pd.concat(results)
    races = results['race'].drop_duplicates()

    # Group by 'driverCode' and 'round' and aggregate 'points'
    results = results.groupby(['driverCode', 'round'])['points'].sum().reset_index()


    # Then we “reshape” the results to a wide table, where each row represents a
    # driver and each column refers to a race, and the cell value is the points.
    results = results.pivot(index='driverCode', columns='round', values='points')

    # Rank the drivers by their total points
    results['total_points'] = results.sum(axis=1)
    results = results.sort_values(by='total_points', ascending=False)
    results.drop(columns='total_points', inplace=True)

    # Use race name, instead of round no., as column names
    results.columns = races

    # The final step is to plot a heatmap using plotly
    fig = px.imshow(
        results,
        text_auto=True,
        aspect='auto',  # Automatically adjust the aspect ratio
        color_continuous_scale=[[0, 'rgb(255, 255, 255)'],  # White
                                [0.25, 'rgb(255, 165, 0)'], # Orange
                                [0.5, 'rgb(255, 140, 0)'],  # Dark Orange
                                [0.75, 'rgb(255, 69, 0)'],  # Red Orange
                                [1, 'rgb(255, 0, 0)']],     # Red
        labels={'x': 'Race',
                'y': 'Driver',
                'color': 'Points'}       # Change hover texts
    )
    fig.update_xaxes(title_text='')      # Remove axis titles
    fig.update_yaxes(title_text='')
    fig.update_yaxes(tickmode='linear')  # Show all ticks, i.e. driver names
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor='LightGrey',
                    showline=False,
                    tickson='boundaries')               # Show horizontal grid only
    fig.update_xaxes(showgrid=False, showline=False)    # And remove vertical grid
    # fig.update_layout(plot_bgcolor='rgba(0,0,0,0)')   # White background
    fig.update_layout(plot_bgcolor='rgba(0,0,0,1)',     # Black background
                      paper_bgcolor='rgba(0,0,0,1)',    # Black background
                      title=f"{yr} Driver Standings Heatmap")# Add Title
    fig.update_layout(coloraxis_showscale=False)        # Remove legend
    fig.update_layout(xaxis=dict(side='top'))           # x-axis on top
    # fig.update_layout(margin=dict(l=0, r=0, b=0, t=0))  # Remove border margins
    fig.update_layout(margin=dict(l=0, r=0, b=0, t=300))  # Increase top margin
    fig.update_layout(font=dict(size=24, color='white'))
    fig.update_layout(font_family="FORMULA1 DISPLAY REGULAR")
    
    # show(fig)
    file = str(yr) + "_POINTS" + '.png'
    fig.write_image("data_dump/" + file, width=2000, height=2000)
    upload_points(yr, "data_dump/" + file)

### updates standings for both drivers and constructors ###
def update(yr):
    
    stnd = False
    
    try:
        if yr >= 1950:
            try:
                driver_func(yr)              
                print("FINISHED UPDATE D")
            except:
                print("FAILED UPDATE D")
        if yr >= 1958:
            try:
                const_func(yr)
                print("FINISHED UPDATE C")
            except:
                print("FAILED UPDATE C")
        if yr >= 1950:
            try:
                points_func(yr)
                print("FINISHED UPDATE P")
            except:
                print("FAILED UPDATE P")
        stnd = True

    except Exception as exc:
        
        print(traceback.format_exc() + "\nFAILED UPDATE " + str(yr))
        stnd = False
        
    return stnd

### updates standings for both drivers and constructors from a given year onwards ###
def update_from(i):
    while i >= 1950:
        try:
            update(i)
            print("FINISHED UPDATE " + str(i))
        except Exception as exc:
            print(str(exc) + "\nFAILED UPDATE " + str(i))
        i -= 1

### updates standings for both drivers and constructors from a given year to a given year ###
def update_to(i):
    while yr <= i:
        try:
            update(yr)
            print("FINISHED UPDATE " + str(yr))
        except Exception as exc:
            print(str(exc) + "\nFAILED UPDATE " + str(yr))
        yr += 1

### updates mongo with races of a given year ###
def update_races(yr):
    collection_name = "races"
    collection = db[collection_name]
    # if doc exists with yr not exists, create doc
    if collection.count_documents({"year": int(yr)}) == 0:
        schedule = ff1.get_event_schedule(yr)
        df = schedule[['EventName']]
        df = df.values
        df = df.tolist()
        races = []
        for i in df:
            races.append(i[0])
        collection.insert_one({"year": yr, 'races': races})

### updates mongo with data of all sessions of a given year and returns a list of all updated sessions ###
def update_data(yr):
    res = []
    msg = "Err"
    races_list = []
    collection_name = "races"
    collection = db[collection_name]
    doc = collection.find_one({"year": int(yr)})
    races_list = doc["races"]
    for rc in races_list:
        rc = rc.strip()
        sessions = get_sessions(yr, rc)
        for sn in sessions:
            collection_name = "data"
            collection = db[collection_name]
            if collection.count_documents({"year": int(yr), "race": rc, "session": sn}) > 0:
                pass
            else:
                try:
                    drivers = []
                    laps = []
                    distance = 0
                    print(yr, rc, sn)
                    try:
                        drivers = get_drivers(yr, rc, sn)
                    except Exception as exc:
                        print(traceback.format_exc())
                        print(str(exc) + "\nNO DRIVERS DATA")
                    try:
                        laps = get_laps(yr, rc, sn)
                    except Exception as exc:
                        print(str(exc) + "\nNO LAPS DATA")
                    try:
                        distance = get_distance(yr, rc, sn)
                    except Exception as exc:
                        print(str(exc) + "\nNO DISTANCE DATA")
                        distance = 0
                        if rc.lower().__contains__("austria"):
                            distance = 4300
                    if drivers != [] and laps != [] and distance != []:
                        collection.insert_one({
                            "year": int(yr),
                            "race": rc,
                            "session": sn,
                            "drivers": drivers,
                            "laps": laps,
                            "distance": distance
                        })
                        res.append([yr, rc, sn])
                except Exception as exc:
                    print(str(exc))
    if res == []:
        msg = "No sessions updated."
    else:
        msg = "Sessions updated:"
    return msg + "\n" + str(res)
   
# update(yr)

# update_from(yr)

# update_to(yr)

# update_races(yr)

# print(update_data(yr))