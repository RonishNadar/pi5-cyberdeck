#!/home/admin/.venvs/deck/bin/python
"""Deck weather: now, the next 24 hours and 7 days, with air quality. Open-Meteo (free, no key).

Left/Right or Tab switch place, a add a place, d remove it, u metric or US units, r refresh, q quit.
The first place is guessed from your internet address; add your own with a.
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

import deck_ui
deck_ui.install()

HOME = os.path.expanduser("~")
CONFIG = f"{HOME}/.config/deck/weather.json"
CACHE = f"{HOME}/.cache/deck/weather.json"
UA = {"User-Agent": "deck-weather/1.0"}
STALE = 900              # refetch after 15 minutes

PURPLE, PINK, CYAN, GREEN = "#bd93f9", "#ff79c6", "#8be9fd", "#50fa7b"
ORANGE, YELLOW, RED, GRAY, FG, DIM = "#ffb86c", "#f1fa8c", "#ff5555", "#6272a4", "#f8f8f2", "#44475a"
try:
    ACCENT = json.load(open(f"{HOME}/.config/deck/prefs.json")).get("accent", PURPLE)
except Exception:
    ACCENT = PURPLE

I = {"sunrise": "", "sunset": "", "humidity": "", "wind": "", "pressure": "",
     "rain": "", "feels": "", "uv": "", "umbrella": "", "pin": "", "air": ""}

# WMO weather codes: (text, day icon, night icon, color)
WMO = {
    0: ("Clear", "", "", YELLOW), 1: ("Mostly clear", "", "", YELLOW),
    2: ("Partly cloudy", "", "", FG), 3: ("Overcast", "", "", GRAY),
    45: ("Fog", "", "", GRAY), 48: ("Freezing fog", "", "", GRAY),
    51: ("Light drizzle", "", "", CYAN), 53: ("Drizzle", "", "", CYAN),
    55: ("Heavy drizzle", "", "", CYAN), 56: ("Freezing drizzle", "", "", CYAN),
    57: ("Freezing drizzle", "", "", CYAN), 61: ("Light rain", "", "", CYAN),
    63: ("Rain", "", "", CYAN), 65: ("Heavy rain", "", "", PURPLE),
    66: ("Freezing rain", "", "", CYAN), 67: ("Freezing rain", "", "", PURPLE),
    71: ("Light snow", "", "", FG), 73: ("Snow", "", "", FG), 75: ("Heavy snow", "", "", FG),
    77: ("Snow grains", "", "", FG), 80: ("Light showers", "", "", CYAN),
    81: ("Showers", "", "", CYAN), 82: ("Violent showers", "", "", PURPLE),
    85: ("Snow showers", "", "", FG), 86: ("Heavy snow showers", "", "", FG),
    95: ("Thunderstorm", "", "", ORANGE), 96: ("Thunderstorm, hail", "", "", RED),
    99: ("Thunderstorm, hail", "", "", RED),
}

# Big digits for the current temperature (3 wide, 5 high).
BIG = {
    "0": ["█▀█", "█ █", "█ █", "█ █", "█▄█"],
    "1": [" ▄█", "  █", "  █", "  █", "  █"],
    "2": ["▀▀█", "  █", "█▀▀", "█  ", "█▄▄"],
    "3": ["▀▀█", "  █", " ▀█", "  █", "▄▄█"],
    "4": ["█ █", "█ █", "▀▀█", "  █", "  █"],
    "5": ["█▀▀", "█  ", "▀▀█", "  █", "▄▄█"],
    "6": ["█▀▀", "█  ", "█▀█", "█ █", "█▄█"],
    "7": ["▀▀█", "  █", "  █", "  █", "  █"],
    "8": ["█▀█", "█ █", "█▀█", "█ █", "█▄█"],
    "9": ["█▀█", "█ █", "▀▀█", "  █", "▄▄█"],
    "-": ["   ", "   ", "▀▀▀", "   ", "   "],
    "°": ["█▀█", "▀▀▀", "   ", "   ", "   "],
}


def big(s):
    rows = ["", "", "", "", ""]
    for ch in s:
        g = BIG.get(ch, ["   "] * 5)
        for i in range(5):
            rows[i] += g[i] + " "
    return rows


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, indent=1)
    os.replace(path + ".tmp", path)


def get(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=12).read())


# ---------- data ----------
def guess_place():
    d = get("https://ipwho.is/")
    if not d.get("success"):
        raise ValueError("location lookup failed")
    name = ", ".join(x for x in (d.get("city"), d.get("region")) if x)
    return {"name": name or "Here", "lat": d["latitude"], "lon": d["longitude"], "country": d.get("country_code", "")}


def find_places(q):
    d = get("https://geocoding-api.open-meteo.com/v1/search?count=10&name=" + urllib.parse.quote(q))
    out = []
    for r in d.get("results") or []:
        where = ", ".join(x for x in (r.get("admin1"), r.get("country")) if x)
        out.append({"name": r["name"], "where": where, "lat": r["latitude"], "lon": r["longitude"],
                    "country": r.get("country_code", "")})
    return out


def forecast(place, imperial):
    q = {"latitude": place["lat"], "longitude": place["lon"], "timezone": "auto", "forecast_days": 7,
         "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,is_day,precipitation,"
                    "wind_speed_10m,wind_direction_10m,wind_gusts_10m,pressure_msl,cloud_cover",
         "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
         "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                  "precipitation_sum,sunrise,sunset,uv_index_max,wind_speed_10m_max"}
    if imperial:
        q.update(temperature_unit="fahrenheit", wind_speed_unit="mph", precipitation_unit="inch")
    data = get("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(q))
    try:
        data["air"] = get("https://air-quality-api.open-meteo.com/v1/air-quality?" + urllib.parse.urlencode(
            {"latitude": place["lat"], "longitude": place["lon"], "current": "us_aqi,european_aqi,pm2_5"}))["current"]
    except Exception:
        data["air"] = None
    data["fetched"] = time.time()
    return data


def compass(deg):
    arrows = ["↓", "↙", "←", "↖", "↑", "↗", "→", "↘"]  # where the wind blows to
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    i = int(((deg or 0) + 22.5) // 45) % 8
    return f"{arrows[i]} {names[i]}"


def aqi_label(us):
    for limit, text, color in ((50, "good", GREEN), (100, "moderate", YELLOW), (150, "unhealthy for some", ORANGE),
                               (200, "unhealthy", RED), (300, "very unhealthy", PURPLE)):
        if us <= limit:
            return text, color
    return "hazardous", RED


def temp_color(t, imperial):
    c = (t - 32) * 5 / 9 if imperial else t
    for limit, color in ((0, CYAN), (10, "#a4c8ff"), (20, GREEN), (28, YELLOW), (34, ORANGE)):
        if c < limit:
            return color
    return RED


def clock(iso):
    return datetime.fromisoformat(iso).strftime("%-I:%M %p").lower() if iso else ""


# ---------- screens ----------
class AddPlace(ModalScreen):
    """Search for a place and pick one; returns the place dict or None."""
    BINDINGS = [Binding("escape", "cancel", show=False)]
    DEFAULT_CSS = """
    AddPlace { align: center middle; background: #282a36 60%; }
    AddPlace > Vertical { width: 70; max-width: 95%; height: auto; max-height: 90%; background: #21222c;
                          border: round #bd93f9; padding: 1 2; }
    AddPlace Input { background: #282a36; border: tall #44475a; }
    AddPlace Input:focus { border: tall #bd93f9; }
    AddPlace OptionList { background: #21222c; border: none; height: auto; max-height: 16; }
    AddPlace OptionList > .option-list--option-highlighted,
    AddPlace OptionList:focus > .option-list--option-highlighted { background: #44475a; }
    """.replace("#bd93f9", ACCENT)

    def compose(self):
        with Vertical():
            yield Static(Text("Add a place\n", style=f"bold {ACCENT}"))
            yield Input(placeholder="city or town, then Enter")
            yield Static(id="msg")
            yield OptionList()

    def on_mount(self):
        self.places = []
        self.query_one(Input).focus()

    def on_input_submitted(self, e):
        e.stop()
        if e.value.strip():
            self.query_one("#msg").update(Text("Searching...", style=GRAY))
            self.search(e.value.strip())

    @work(thread=True, exclusive=True)
    def search(self, q):
        try:
            res = find_places(q)
        except Exception as ex:
            self.app.call_from_thread(self.query_one("#msg").update, Text(f"Search failed: {ex}", style=RED))
            return
        self.app.call_from_thread(self.show, res)

    def show(self, res):
        self.places = res
        ol = self.query_one(OptionList)
        ol.clear_options()
        ol.add_options([Option(Text.assemble((p["name"], f"bold {FG}"), ("  " + p["where"], GRAY)), id=str(i))
                        for i, p in enumerate(res)])
        self.query_one("#msg").update(Text("" if res else "Nothing found.", style=GRAY))
        if res:
            ol.focus()
            ol.highlighted = 0

    def on_option_list_option_selected(self, e):
        e.stop()
        self.dismiss(self.places[int(e.option.id)])

    def on_key(self, e):
        if e.key == "up" and self.query_one(OptionList).has_focus and self.query_one(OptionList).highlighted == 0:
            self.query_one(Input).focus()
            e.stop()

    def action_cancel(self):
        self.dismiss(None)


class Weather(App):
    TITLE = "Weather"
    CSS = """
    Screen { background: #282a36; }
    #title { height: 3; padding: 1 3 0 3; }
    VerticalScroll { padding: 0 3; scrollbar-color: #bd93f9; }
    #now { height: auto; }
    #hours { height: auto; margin-top: 1; }
    #days { height: auto; margin-top: 1; }
    #hint { height: 1; padding: 0 3; }
    """.replace("#bd93f9", ACCENT)
    BINDINGS = [
        Binding("q", "quit", show=False), Binding("r", "refresh", show=False),
        Binding("right,tab,l", "place(1)", show=False), Binding("left,shift+tab,h", "place(-1)", show=False),
        Binding("a", "add", show=False), Binding("d,delete", "remove", show=False),
        Binding("u", "units", show=False),
        Binding("down,j", "scroll(3)", show=False), Binding("up,k", "scroll(-3)", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.cfg = {"places": [], "current": 0, "imperial": None}
        self.cfg.update(load_json(CONFIG, {}))
        self.cache = load_json(CACHE, {})

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with VerticalScroll():
            yield Static(id="now")
            yield Static(id="hours")
            yield Static(id="days")
        yield Static(Text.assemble(("Left Right", CYAN), (" place  ", GRAY), ("a", CYAN), (" add  ", GRAY),
                                   ("d", CYAN), (" remove  ", GRAY), ("u", CYAN), (" units  ", GRAY),
                                   ("r", CYAN), (" refresh  ", GRAY), ("q", CYAN), (" quit", GRAY)), id="hint")

    def on_mount(self):
        self.main.query_one(VerticalScroll).can_focus = False
        if not self.cfg["places"]:
            self.main.query_one("#now").update(Text("Finding where you are...", style=GRAY))
            self.first_place()
        else:
            self.render_all()
            self.load()
        self.set_interval(STALE, self.load)

    @property
    def main(self):
        """The app's own screen, even while the add-place box is open."""
        return self.screen_stack[0]

    def check_action(self, action, parameters):
        return not isinstance(self.screen, ModalScreen)

    def save_cfg(self):
        save_json(CONFIG, self.cfg)

    @property
    def place(self):
        p = self.cfg["places"]
        return p[self.cfg["current"] % len(p)] if p else None

    def key(self):
        p = self.place
        return f"{p['lat']:.3f},{p['lon']:.3f},{'us' if self.cfg['imperial'] else 'si'}"

    @work(thread=True, exclusive=True, group="place")
    def first_place(self):
        try:
            p = guess_place()
        except Exception:
            self.call_from_thread(self.main.query_one("#now").update,
                                  Text("Could not tell where you are. Press a to add a place.", style=GRAY))
            return
        self.call_from_thread(self.added, p)

    def added(self, p):
        if not p:
            return
        if self.cfg["imperial"] is None:
            self.cfg["imperial"] = p.get("country") in ("US", "LR", "MM")
        self.cfg["places"].append({k: p[k] for k in ("name", "lat", "lon", "country")})
        self.cfg["current"] = len(self.cfg["places"]) - 1
        self.save_cfg()
        self.render_all()
        self.load(force=True)

    # ----- data -----
    @work(thread=True, exclusive=True, group="load")
    def load(self, force=False):
        if not self.place:
            return
        key = self.key()
        old = self.cache.get(key)
        if old and not force and time.time() - old.get("fetched", 0) < STALE:
            self.call_from_thread(self.render_all)
            return
        self.call_from_thread(self.render_title, "updating...")
        try:
            data = forecast(self.place, self.cfg["imperial"])
        except Exception as ex:
            self.call_from_thread(self.render_title, f"offline: {str(ex)[:40]}" if old else "could not load")
            return
        self.cache[key] = data
        save_json(CACHE, self.cache)
        if self.key() == key:
            self.call_from_thread(self.render_all)

    # ----- drawing -----
    def render_title(self, note=None):
        t = Text.assemble((I["pin"] + "  ", RED))
        places = self.cfg["places"]
        for i, p in enumerate(places):
            cur = i == self.cfg["current"] % max(1, len(places))
            t.append(f" {p['name'].split(',')[0]} ", f"bold {ACCENT}" if cur else GRAY)
        data = self.cache.get(self.key()) if self.place else None
        if note:
            t.append(f"   {note}", YELLOW)
        elif data:
            mins = int((time.time() - data["fetched"]) // 60)
            t.append(f"   updated {'just now' if mins < 1 else f'{mins} min ago'}", GRAY)
        self.main.query_one("#title").update(t)

    def render_all(self):
        self.render_title()
        if not self.place:
            return
        data = self.cache.get(self.key())
        if not data:
            self.main.query_one("#now").update(Text("Loading...", style=GRAY))
            self.main.query_one("#hours").update("")
            self.main.query_one("#days").update("")
            return
        imp = self.cfg["imperial"]
        deg, speed, rain_u = ("°F", "mph", "in") if imp else ("°C", "km/h", "mm")
        c, d = data["current"], data["daily"]
        text, iday, inight, col = WMO.get(c["weather_code"], ("?", "", "", FG))
        icon = iday if c.get("is_day") else inight
        temp = round(c["temperature_2m"])
        rows = big(f"{temp}°")
        tcol = temp_color(c["temperature_2m"], imp)

        # current conditions: big temperature on the left, details on the right
        details = [
            (I["feels"], "Feels like", f"{round(c['apparent_temperature'])}{deg}"),
            (I["wind"], "Wind", f"{round(c['wind_speed_10m'])} {speed} {compass(c['wind_direction_10m'])}"
                                 f", gusts {round(c['wind_gusts_10m'])}"),
            (I["humidity"], "Humidity", f"{c['relative_humidity_2m']}%"),
            (I["pressure"], "Pressure", f"{round(c['pressure_msl'])} hPa"),
            (I["sunrise"], "Sun", f"{clock(d['sunrise'][0])}  {I['sunset']}  {clock(d['sunset'][0])}"),
            (I["uv"], "UV today", f"{d['uv_index_max'][0]:.0f}"),
        ]
        air = data.get("air")
        if air and air.get("us_aqi") is not None:
            label, acol = aqi_label(air["us_aqi"])
            details.append((I["air"], "Air", f"AQI {air['us_aqi']:.0f} {label}"))
        now = Text()
        left_w = 26
        lines = [
            Text.assemble((icon + "  ", col), (text, f"bold {col}")),
            *[Text(r, style=tcol) for r in rows],
            Text.assemble((f"High {round(d['temperature_2m_max'][0])}{deg}   Low {round(d['temperature_2m_min'][0])}{deg}", GRAY)),
        ]
        for i in range(max(len(lines), len(details))):
            left = lines[i].copy() if i < len(lines) else Text("")
            left.truncate(left_w, pad=True)
            now.append_text(left)
            if i < len(details):
                ic, label, val = details[i]
                now.append(f"   {ic}  ", CYAN)
                now.append(f"{label:<11}", GRAY)
                now.append(val, FG if label != "Air" else aqi_label(air["us_aqi"])[1])
            now.append("\n")
        now.rstrip()
        self.main.query_one("#now").update(now)

        # next 24 hours, every 2 hours
        h = data["hourly"]
        times = h["time"]
        start = next((i for i, t in enumerate(times) if t >= c["time"][:13]), 0)
        idx = list(range(start, min(start + 24, len(times)), 2))
        col_w = max(7, min(9, (self.size.width - 8) // max(1, len(idx))))
        hrs = Text("Next 24 hours\n", style=f"bold {ACCENT}")
        r_time, r_icon, r_temp, r_rain = Text(), Text(), Text(), Text()
        temps = [h["temperature_2m"][i] for i in idx]
        lo, hi = min(temps), max(temps)
        for k, i in enumerate(idx):
            t = datetime.fromisoformat(times[i])
            label = "Now" if k == 0 else t.strftime("%-I%p").lower()
            w = WMO.get(h["weather_code"][i], ("?", "", "", FG))
            r_time.append(f"{label:^{col_w}}", GRAY)
            r_icon.append(f"{(w[1] if h['is_day'][i] else w[2]):^{col_w}}", w[3])
            tv = h["temperature_2m"][i]
            r_temp.append(f"{round(tv)}°".center(col_w), f"bold {temp_color(tv, imp)}")
            p = h["precipitation_probability"][i] or 0
            r_rain.append(f"{p}%".center(col_w) if p >= 10 else " " * col_w, CYAN if p >= 40 else GRAY)
        hrs.append_text(r_time); hrs.append("\n")
        hrs.append_text(r_icon); hrs.append("\n")
        hrs.append_text(r_temp); hrs.append("\n")
        hrs.append_text(r_rain)
        self.main.query_one("#hours").update(hrs)

        # 7 days, with a bar showing each day's range against the week's range
        days = Text("7 days\n", style=f"bold {ACCENT}")
        wlo, whi = min(d["temperature_2m_min"]), max(d["temperature_2m_max"])
        bar_w = 24
        for i, day in enumerate(d["time"]):
            name = "Today" if i == 0 else datetime.fromisoformat(day).strftime("%a %-d")
            w = WMO.get(d["weather_code"][i], ("?", "", "", FG))
            lo_t, hi_t = d["temperature_2m_min"][i], d["temperature_2m_max"][i]
            a = int((lo_t - wlo) / max(1, whi - wlo) * bar_w)
            b = max(a + 1, int((hi_t - wlo) / max(1, whi - wlo) * bar_w))
            days.append(f"{name:<8}", f"bold {FG}" if i == 0 else FG)
            days.append(f"{w[1]}  ", w[3])
            days.append(f"{w[0]:<19}", GRAY)
            days.append(f"{round(lo_t):>4}° ", temp_color(lo_t, imp))
            days.append("─" * a, DIM)
            days.append("━" * (b - a), temp_color((lo_t + hi_t) / 2, imp))
            days.append("─" * (bar_w - b), DIM)
            days.append(f" {round(hi_t)}°", temp_color(hi_t, imp))
            p = d["precipitation_probability_max"][i] or 0
            amt = d["precipitation_sum"][i] or 0
            if p >= 20:
                days.append(f"   {I['umbrella']} {p}%", CYAN if p >= 50 else GRAY)
                if amt >= (0.02 if imp else 0.5):
                    days.append(f" {amt:.2f} {rain_u}" if imp else f" {amt:.0f} {rain_u}", GRAY)
            if i < len(d["time"]) - 1:
                days.append("\n")
        self.main.query_one("#days").update(days)

    def on_resize(self, _):
        if self.place and self.cache.get(self.key()):
            self.render_all()

    # ----- actions -----
    def action_place(self, step):
        if len(self.cfg["places"]) > 1:
            self.cfg["current"] = (self.cfg["current"] + step) % len(self.cfg["places"])
            self.save_cfg()
            self.render_all()
            self.load()

    def action_add(self):
        self.push_screen(AddPlace(), self.added)

    def action_remove(self):
        if len(self.cfg["places"]) <= 1:
            self.notify("Add another place before removing this one", timeout=2)
            return
        gone = self.cfg["places"].pop(self.cfg["current"] % len(self.cfg["places"]))
        self.cfg["current"] = 0
        self.save_cfg()
        self.notify(f"Removed {gone['name']}", timeout=2)
        self.render_all()
        self.load()

    def action_units(self):
        self.cfg["imperial"] = not self.cfg["imperial"]
        self.save_cfg()
        self.notify("US units (°F, mph)" if self.cfg["imperial"] else "Metric units (°C, km/h)", timeout=1.5)
        self.render_all()
        self.load()

    def action_refresh(self):
        self.load(force=True)

    def action_scroll(self, n):
        self.main.query_one(VerticalScroll).scroll_relative(y=n, animate=False)


if __name__ == "__main__":
    Weather().run()
