from datetime import datetime, date
import json
from kivy.logger import Logger, LOG_LEVELS
from kivy.network.urlrequest import UrlRequest
import os
import sqlite3
from string import Formatter
from time import ctime, sleep, time
from threading import Thread, Event

try:
    from urllib.request import urlopen
    from queue import Queue, PriorityQueue
except ImportError:
    from urllib import urlopen
    from Queue import Queue, PriorityQueue

try:
    from api_keys import GOOGLEKEY, OPENWEATHERKEY
except ImportError:
    print('Error api_key.py file required with API KEYS inside')
    
weekdays = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
weather_icon_lookup = {
    2: 'a',
    3: 'n',
    5: 'b',
    6: 'd',
    7: 'm',
    800: 'f',
    8: 'g',
    900: 'a',
    901: 'a',
    902: 'a',
    903: 'k',
    904: 'f',
    905: 'e',
    906: 'i'
}
URLS = {
    'find_location': 'http://api.openweathermap.org/data/2.5/find?q={location}&type=like&APPID={appid}',
    'get_forecasts': 'http://api.openweathermap.org/data/2.5/forecast?id={id}&APPID={appid}',
    'timezone': 'https://maps.googleapis.com/maps/api/timezone/json?location={lat:.2f},{lon:.2f}&timestamp={time}&key={APPID}'
}


class MultiThreadedWeatherDatabase(Thread):
    def __init__(self, file):
        super(MultiThreadedWeatherDatabase, self).__init__()
        self.file = file
        self.queue = PriorityQueue()
        self.event = Event()
        self.create_tables = False
        if not os.path.isfile(file):
            self.create_tables = True
        self.start()  # Threading module start

    def run(self):
        super(MultiThreadedWeatherDatabase, self).run()
        db = sqlite3.connect(self.file)
        cursor = db.cursor()
        if self.create_tables:
            self.create_all_tables()
        while True:
            if self.queue.empty():
                sleep(0.1)  # So the thread doesnt use all of the processor
                continue
            job, sql, arg, result = self.queue.get_nowait()
            if sql == '__close__':
                break
            if arg is None:
                arg = ''
            cursor.execute(sql, arg)
            db.commit()
            if result:
                for rec in cursor:
                    result.put(rec)
                result.put('__last__')
        db.close()
        self.event.set()

    def execute(self, sql, args=None, res=None, priority=2):
        self.queue.put_nowait((priority, sql, args, res))

    def select(self, sql, args=None, priority=2):
        res = Queue()
        self.execute(sql, args, res, priority)
        while True:
            rec = res.get()
            if rec == '__last__':
                break
            yield rec

    def close(self):
        self.execute('__close__')

    def create_all_tables(self):
        command1 = '''CREATE TABLE location (location_id INTEGER PRIMARY KEY , town TEXT, country TEXT, lat REAL, lon REAL, dateadded INTEGER, timezone INTEGER)'''
        self.execute(command1)
        command2 = '''CREATE TABLE "forecast" (forecast_id INTEGER PRIMARY KEY, location_id INTEGER, time INTEGER, temp REAL, pressure INTEGER, humidity INTEGER, clouds INTEGER, windspeed REAL, winddirection INTEGER, symbol INTEGER, FOREIGN KEY (location_id) REFERENCES location (location_id) DEFERRABLE INITIALLY DEFERRED)'''
        self.execute(command2)


    def remove_old_forecasts(self):
        command = '''DELETE FROM forecast WHERE forecast.time < STRFTIME('%s', 'now')'''
        self.execute(command)


_db = MultiThreadedWeatherDatabase('weather.db')


def get_symbol_from_number(num, forecast=None):
    # http://openweathermap.org/weather-conditions
    if isinstance(num, int):
        num = str(num)
    if forecast is not None:
        localtime = ((datetime.fromtimestamp(forecast.time).hour + forecast.location.timezone) % 24)
        if not 6 < localtime < 20:
            #print('location is nighttime')  # Something is wrong with this line
            return 'o'
    try:
        return weather_icon_lookup[int(num)]
    except KeyError:
        try:
            return weather_icon_lookup[int(num[0])]
        except KeyError:
            text = Formatter().format('The weather symbol could not be found. Symbol: {num}', num=num)
            Logger.warn(text)
            return 'l'  # N/A


def get_timezone(lat, lon):
    url = Formatter().format(URLS['timezone'], lat=lat, lon=lon, time=int(time()), APPID=GOOGLEKEY)
    data = json.loads(urlopen(url).read().decode('utf-8'))
    if data['status'] != 'OK':
        raise Exception
    timezone = data['rawOffset'] / 3600
    # Get rawOffset from google in seconds so divide by 3600 to get in hours
    return timezone


def get_forecasts(location_id, on_success):
    url = URLS['get_forecasts'].format(id=location_id, appid=OPENWEATHERKEY).replace(' ', '%20')
    UrlRequest(url, on_success=on_success)


def refresh_forecasts():
    for location in Location.all_locations():
        get_forecasts(location.id, add_forecast)


def location_from_json(raw_json, count):
    data = raw_json['list'][count]

    if data['name'] != '':
        town = data['name']
    else:
        town = data['sys']['country']
        # Sometimes name field is blank so use the Country field instead
    country = data['sys']['country']
    location_id = data['id']
    lat = data['coord']['lat']
    lon = data['coord']['lon']
    # We assuming the timezone is 0, we add it probably if they add the location
    return Location(location_id, town, country, lat, lon, time(), 0)


def add_forecast(req, data):
    if data['cod'] != 404:
        Forecast.save_all_to_db(data)


class Forecast:
    def __init__(self, forecast_id, location_id, time, temp, pressure, humidity, clouds, windspeed, winddirection,
                 symbol):
        self.id = forecast_id or None
        self.location_id = location_id
        self.time = time
        self.temp = temp
        self.pressure = pressure
        self.humidity = humidity
        self.clouds = clouds
        self.wind_speed = windspeed
        self.wind_direction = winddirection
        self.symbol_number = symbol

    def __repr__(self):
        return Formatter().format('<Forecast {town}, {time}>', town=self.location.town, time=ctime(self.time))

    @classmethod
    def get_current_forecast(cls, location_id):
        command = '''SELECT * FROM forecast WHERE location_id = ? AND ''' \
                  '''ABS(forecast.time - STRFTIME('%s', 'now')) < (10799) AND forecast.time > STRFTIME('%s','NOW');'''
        try:
            return Forecast(*next(_db.select(command, (location_id,))))
        except StopIteration:
            Logger.error('Couldn\'t find any forecast for {}'.format(location_id))
            return Forecast.not_available(location_id)

    @classmethod
    def not_available(cls, location_id):
        return cls(0, location_id, 0, 0, 0, 0, 0, 0, 0, '000')

    @staticmethod
    def save_all_to_db(data):
        Logger.debug(str(data))
        try:

            location = Location.from_id(data['city']['id'])
        except KeyError:
            Logger.log(LOG_LEVELS['critical'], 'We seem to have some massive issue with something')
            return
        command = "INSERT OR REPLACE INTO forecast(forecast_id, location_id, time, temp, pressure, humidity, clouds, " \
                  "windspeed, winddirection, symbol) VALUES "
        for forecast in data['list']:
            forecast_time = forecast['dt']
            temp = forecast['main']['temp']
            pressure = forecast['main']['pressure']
            humidity = forecast['main']['humidity']
            clouds = forecast['clouds']['all']
            windspeed = forecast['wind']['speed']
            winddirection = forecast['wind']['deg']
            symbol = forecast['weather'][0]['id']
            command += Formatter().format(
                "((SELECT forecast_id FROM forecast WHERE location_id={location_id} AND time={time}),{location_id},{time},{temp},{presssure},{humidity},{clouds},{windspeed},{winddirection},{symbol}),",
                location_id = location_id, time=forecast_time, temp=temp, pressure=pressure, humidity=humidity, clouds=clouds, windspeed=windspeed, winddirection=winddirection, symbol=symbol)
        _db.execute(command[:-1])

    @property
    def location(self):
        return Location.from_id(self.location_id)

    @property
    def symbol(self):
        return get_symbol_from_number(self.symbol_number, self)


class Location:
    def __init__(self, location_id, town, country, lat, lon, date_added, timezone):
        self.id = location_id
        self.town = town
        self.country = country
        self.lat = lat
        self.lon = lon
        self.date_added = date_added
        self.timezone = timezone

    def __repr__(self):
        return Formatter().format('<Location: {town}>', town=self.town)  # For debugging properties

    @classmethod
    def from_id(cls, location_id):
        command = '''SELECT * FROM location WHERE location_id = ?'''
        try:
            return cls(*next(_db.select(command, (location_id,))))
        except StopIteration:
            raise IndexError(Formatter().format('location_id {location_id} is not in database', location_id=location_id))

    @classmethod
    def all_locations(cls):
        command = '''SELECT * FROM location ORDER BY dateadded ASC'''
        return [cls(*location) for location in _db.select(command)]

    @property
    def get_current_weather(self):
        return Forecast.get_current_forecast(self.id)

    def save_to_db(self):
        command = '''INSERT INTO location VALUES (?,?,?,?,?,?,?)'''
        timezone = get_timezone(self.lat, self.lon)
        _db.execute(command, (self.id, self.town, self.country, self.lat, self.lon, int(time()), timezone))

    def remove_from_db(self):
        command1 = '''DELETE FROM forecast WHERE location_id==?'''
        _db.execute(command1, (self.id,), priority=1)
        # Next time: location has an attribute of status only display if status is true then I can keep them in database
        # if someone deletes by mistake
        command2 = '''DELETE FROM location WHERE location_id = ?'''
        _db.execute(command2, (self.id,), priority=1)

    @property
    def forecasts(self):
        command = '''SELECT * FROM forecast WHERE location_id=? ORDER BY time ASC'''
        return [Forecast(*f) for f in _db.select(command, (self.id,))]
