from datetime import datetime
from functools import partial
from kivy.app import App
from kivy.base import Clock, Config
from kivy.core.text import LabelBase
from kivy.logger import Logger
from kivy.metrics import sp
from kivy.network.urlrequest import UrlRequest
from kivy.properties import ObjectProperty
from kivy.uix.button import Button
from kivy.uix.listview import ListItemButton
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen, ScreenManager, RiseInTransition
from kivy.utils import get_color_from_hex as c, Platform
from string import Formatter
import database

LabelBase.register('symbols', fn_regular='assets/weathersymbols.ttf')
COLOURS = ['FF3B30', '2ECC71', '3498DB', '1ABC9C', '27AE60', 'E74C3C']
mobile_platform = Platform() in ('ios', 'android')
if not mobile_platform:
    # This must be here because a mobile screen is high density
    Config.set('graphics', 'height', int(1280 * 0.5))  # 50% of screen size
    Config.set('graphics', 'width', int(720 * 0.5))  # 50% of screen size
Config.set('kivy', 'log_level', 'warning')

def find_location(location, on_success):
    url = Formatter().format(database.URLS['find_location'], location=location, appid=database.OPENWEATHERKEY)
    UrlRequest(url, on_success=on_success)


class LargeButton(Button):
    pass


class LocationButton(LargeButton):
    def __init__(self, location, **kwargs):
        self.location = location  # This is here because object is linked to .kv in super and we need location attribute
        self.current_weather = self.location.get_current_weather
        super(LocationButton, self).__init__(**kwargs)
        self.long_press_clock = None

    def on_location_button_press(self):
        if not root.has_screen(self.location.town):
            root.add_widget(
                SimpleWeatherScreen(self.location)
            )
        root.current = self.location.town

    def create_clock(self, touch):
        function = partial(self.menu, touch)
        self.long_press_clock = Clock.schedule_once(function, 2)
        touch.ud['event'] = function  # So we can unschedule it if the button is pressed early

    def delete_clock(self, touch):
        try:
            Clock.unschedule(touch.ud['event'])
        except KeyError:
            pass
        self.long_press_clock = None

    def menu(self, touch, time):
        menu = DeleteDialog(self.location)
        menu.open()

    def close_menu(self, widget):
        self.root.remove_widget(widget)

    def on_touch_down(self, touch):
        super(LocationButton, self).on_touch_down(touch)
        if self.collide_point(*touch.pos):
            self.create_clock(touch)

    def on_touch_up(self, touch):
        if self.collide_point(*touch.pos):
            if self.long_press_clock is not None:
                if self.long_press_clock.is_triggered:  # Has 2 seconds gone past when we lifted the finger?
                    # If not we display simple location screen instead
                    # Seems to work in opposite way to expected?
                    self.on_location_button_press()
                else:
                    return
            self.delete_clock(touch)
        super(LocationButton, self).on_touch_up(touch)


class LargeGrid:
    pass


class RectangleButton(ListItemButton):
    iter_number = 0

    def __init__(self, forecast=None):  # I dont think this should have forecast in it
        super(RectangleButton, self).__init__()
        self.background_color = c(COLOURS[RectangleButton.iter_number % len(COLOURS)])
        RectangleButton.iter_number += 1
        self.forecast = forecast


class SearchRectangleButton(RectangleButton):

    def __init__(self, location, **kwargs):
        self.location = location
        super(SearchRectangleButton, self).__init__(**kwargs)

    def on_result_press(self, button):
        location = button.location
        location.save_to_db()
        root.get_screen('menu').populate()
        root.current = 'menu'
        root.get_screen('addform').reset()


class RectangleGrid:  # Used as a references
    pass


class DeleteDialog(Popup):
    location = ObjectProperty()

    def __init__(self, location, **kwargs):
        super(DeleteDialog, self).__init__(**kwargs)
        self.title = 'Delete Location'
        self.location = location
        self.ids.get('dialog_base_title').text = Formatter().format('Are you sure you\nwant to delete\n{}?',
                                                                    self.location.town)
        self.separator_color = (0, 0, 0, 0)
        self.title_align = 'center'
        self.title_size = 60 if mobile_platform else 30

    def on_yes_press(self):
        self.location.remove_from_db()
        root.get_screen('menu').populate()
        self.dismiss()

    def on_no_press(self):
        self.dismiss()


# Screens
class AddLocationForm(Screen):

    def __init__(self):
        super(AddLocationForm, self).__init__()
        self.results = self.ids.get('results')
        self.results.bind(minimum_height=self.results.setter('height'))
        self.input = self.ids.get('input')

    def on_search(self):
        location = self.input.text
        if len(location) < 3:
            popup_widget = AddLocErrorForm()
            popup_widget.open()
        else:
            find_location(location, self.on_found_search)

    def on_found_search(self, _, data):
        self.results.clear_widgets()
        if data['count'] > 0:
            for count in range(data['count']):
                location = database.location_from_json(data, count)
                button = SearchRectangleButton(
                    location,
                    font_size=60 if mobile_platform else 30,
                )
                button.bind(on_release=self.reset)
                self.results.add_widget(button)
        else:  # else nothing found
            button = RectangleButton(
                font_size=60 if mobile_platform else 30,
                text='Nothing Found',
            )
            button.bind(on_release=self.on_remove_nothing_found_button)
            self.results.add_widget(button)
            Logger.warn('We didn\'t find anything when searching')

    def on_remove_nothing_found_button(self, button):
        self.results.remove_widget(button)

    def reset(self, widget=None):
        self.input.text = ''
        self.results.clear_widgets()
        if widget is not None:
            widget.on_result_press(widget)

    def reset_and_back(self):
        self.reset()
        root.current = 'menu'


class AddLocErrorForm(Popup):
    def __init__(self):
        super(AddLocErrorForm, self).__init__()
        self.separator_color = (0, 0, 0, 0)
        self.title_align = 'center'
        self.title_size = 60 if mobile_platform else 30
        self.title = 'Error'


class SimpleWeatherScreen(Screen):
    location = ObjectProperty()

    def __init__(self, location):
        super(SimpleWeatherScreen, self).__init__()
        self.location = location
        self.name = self.location.town
        self.simple_weather_scroll = self.ids.get('SimpleWeatherScroll')
        self.simple_weather_scroll.bind(minimum_height=self.simple_weather_scroll.setter('height'))
        self.forecasts = self.location.forecasts
        self.ids.get('simple_menu_title').text = self.location.town
        for count, forecast in enumerate(self.forecasts):
            day_time = datetime.fromtimestamp(forecast.time)
            day = database.weekdays[day_time.weekday()]
            if day_time.day == datetime.now().day:
                day = 'Today'
            elif day_time.day == datetime.now().day + 1:  # Not sure this is gonna work at the end of the month
                day = 'Tomorrow'
            button_text = Formatter().format('[font=symbols]{symbol}[/font] {weekday} {time}:00',
                                             symbol=forecast.symbol,
                                             weekday=day,
                                             time=(day_time.hour + location.timezone) % 24)
            widget = RectangleButton(forecast=forecast,
                                     text=button_text,
                                     background_color=c(COLOURS[count % len(COLOURS)]),
                                     font_size=80 if mobile_platform else 40)
            widget.bind(on_release=self.on_weather_time_press)
            self.simple_weather_scroll.add_widget(widget)

    def on_weather_time_press(self, widget):
        new_screen = DetailedWeatherScreen(widget.forecast)
        self.manager.add_widget(new_screen)
        self.manager.current = new_screen.name

    def refresh_and_change_screen(self):
        self.manager.current = 'menu'


class DetailedWeatherScreen(Screen):
    def __init__(self, forecast):
        location = forecast.location
        name = location.town + str(forecast.time)
        super(DetailedWeatherScreen, self).__init__(name=name)
        self.location = location
        self.forecast = forecast
        day_time = datetime.fromtimestamp(forecast.time)
        detailed_menu_title = self.ids.get('detailed_menu_title')
        detailed_menu_title.text = Formatter().format('{} {}:00', self.location.town, (day_time.hour + self.location.timezone) % 24)
        self.ids.get('detailed_return_button').bind(on_release=self.return_to_simple_screen)
        detailed_menu_symbol = self.ids.get('detailed_menu_symbol')
        detailed_menu_symbol.text = Formatter().format('[font=symbols]{}[/font]', forecast.symbol)
        text = Formatter().format('[font=symbols]k[/font]Temperature: {}[sup]o[/sup]C\n'
                                  'Pressure: {} Pa\nHumidity: {} %\n'
                                  '\nClouds Cover: {} %\nWind Speed: {} m/s\n'
                                  'Wind Direction: {}[sup]o[/sup]',
                                  int(self.forecast.temp - 273),
                                  int(self.forecast.pressure) * 100,  # To convert hPa to Pa
                                  self.forecast.humidity,
                                  self.forecast.clouds,
                                  self.forecast.wind_speed,
                                  int(self.forecast.wind_direction))
        self.ids.get('detailed_menu_text').text = text

    def return_to_simple_screen(self, widget):
        self.manager.current = self.location.town


class MenuScreen(Screen):
    def __init__(self):
        super(MenuScreen, self).__init__()
        self.location_grid = self.ids.get('location_grid')
        self.location_grid.bind(minimum_height=self.location_grid.setter('height'))
        self.populate()

    def populate(self):
        self.location_grid.clear_widgets()
        locations = database.Location.all_locations()
        for iteration, location in enumerate(locations):
            button = LocationButton(
                location,
                background_color=c(COLOURS[iteration % len(COLOURS)]),
            )
            self.location_grid.add_widget(button)
        button = LargeButton(
            text='+',
            font_size=sp(150),
            background_color=c('#16a085')
        )
        button.bind(on_release=self.on_add_button_press)
        self.location_grid.add_widget(button)

    @staticmethod
    def on_add_button_press(widget):
        root.current = 'addform'


class WeatherApp(App):  # Will import weather.kv automatically
    def __init__(self):
        super(WeatherApp, self).__init__()
        self.title = 'Weather Application'
        self.icon = 'assets\\icon.png'
        database._db.remove_old_forecasts()
        database.refresh_forecasts()

    def build(self):
        root.add_widget(MenuScreen())
        root.add_widget(AddLocationForm())
        return root

    def on_pause(self):  # For mobile devices
        return True

    def on_stop(self):
        database._db.close()


root = ScreenManager(transition=RiseInTransition())

if __name__ in ('__main__', '__android__'):
    app = WeatherApp()
    app.run()
