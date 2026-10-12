#!/home/admin/.venvs/deck/bin/python
"""Deck internet radio: about 50,000 stations from radio-browser.info (free, no account), played with mpv.

Enter/Right play, f favorite, n p next/previous station (F6 F8 too), Space pause, - + volume, x stop,
/ search, 1 favorites, 2 top, 3 search, 4 genres, 5 songs heard, Tab switch views, Left/Esc back.
"""
import json
import os
import time
import urllib.parse
import urllib.request

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

import deck_mpv
import deck_ui
deck_ui.install()

HOME = os.path.expanduser("~")
CONFIG = f"{HOME}/.config/deck/radio.json"
HEARD = f"{HOME}/.local/share/deck/radio-heard.json"
SOCK = deck_mpv.SOCKETS["radio"]
NOW_FILE = f"{deck_mpv.RUN}/deck-radio-now.json"
MPV_LOG = f"{deck_mpv.RUN}/deck-radio-mpv.log"
SERVERS = ["all", "de1"]
UA = {"User-Agent": "deck-radio/1.0"}

PURPLE, PINK, CYAN, GREEN = "#bd93f9", "#ff79c6", "#8be9fd", "#50fa7b"
ORANGE, YELLOW, RED, GRAY, FG = "#ffb86c", "#f1fa8c", "#ff5555", "#6272a4", "#f8f8f2"
try:
    ACCENT = json.load(open(f"{HOME}/.config/deck/prefs.json")).get("accent", PURPLE)
except Exception:
    ACCENT = PURPLE

I_RADIO, I_PLAY, I_PAUSE, I_STAR, I_TAG, I_NOTE = "", "", "", "", "", ""
TABS = [("favs", "Favorites"), ("top", "Top"), ("search", "Search"), ("genres", "Genres"), ("heard", "Heard")]


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


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---------- radio-browser.info ----------
def api(path, **params):
    params.setdefault("hidebroken", "true")
    q = urllib.parse.urlencode(params)
    err = None
    for host in SERVERS:
        try:
            req = urllib.request.Request(f"https://{host}.api.radio-browser.info/json/{path}?{q}", headers=UA)
            return json.loads(urllib.request.urlopen(req, timeout=10).read())
        except Exception as ex:
            err = ex
    raise err


def station(s):
    return {"id": s["stationuuid"], "name": (s.get("name") or "?").strip(), "url": s.get("url_resolved") or s.get("url"),
            "country": s.get("countrycode") or "", "tags": ", ".join(t for t in (s.get("tags") or "").split(",")[:4] if t),
            "codec": "" if (s.get("codec") or "").upper() == "UNKNOWN" else (s.get("codec") or ""), "bitrate": s.get("bitrate") or 0, "votes": s.get("votes") or 0}


def stations(path, **params):
    return [station(s) for s in api(path, **params) if s.get("url_resolved") or s.get("url")]


def count_click(uuid):
    """radio-browser asks clients to report plays; it keeps the popularity lists useful."""
    try:
        api(f"url/{uuid}")
    except Exception:
        pass


# ---------- screens ----------
HELP = [
    ("Stations", [("Enter  Right", "play"), ("f", "add to or remove from favorites"),
                  ("n  p", "next / previous station in the list (F6 F8 too)")]),
    ("Playing", [("Space", "pause or play"), ("-  +", "volume"), ("x", "stop")]),
    ("Views", [("1 to 5  Tab", "favorites, top, search, genres, songs heard"), ("/", "search by name"),
               ("Left  Esc", "back"), ("r", "refresh"), ("Delete", "remove a favorite, or a heard song"),
               ("q", "quit")]),
]


class Help(ModalScreen):
    BINDINGS = [Binding("escape,q,question_mark,left", "dismiss", show=False)]
    DEFAULT_CSS = """
    Help { align: center middle; background: #282a36 60%; }
    Help > Static { width: 70; height: auto; background: #21222c; border: round #bd93f9; padding: 1 2; }
    """.replace("#bd93f9", ACCENT)

    def compose(self):
        t = Text()
        for i, (title, keys) in enumerate(HELP):
            t.append(("\n" if i else "") + title + "\n", style=f"bold {ACCENT}")
            for k, d in keys:
                t.append(f"  {k:<14}", style=f"bold {CYAN}")
                t.append(d + "\n", style=FG)
        t.rstrip()
        yield Static(t)


class Radio(App):
    TITLE = "Radio"
    CSS = """
    Screen { background: #282a36; }
    #title { height: 3; padding: 1 3 0 3; }
    #search { margin: 0 3; background: #21222c; border: tall #44475a; display: none; }
    #search:focus { border: tall #bd93f9; }
    #status { height: 1; padding: 0 4; color: #6272a4; }
    #list, #list:focus { background: #282a36; border: none; padding: 0 2; height: 1fr;
                         scrollbar-color: #bd93f9; scrollbar-background: #21222c; background-tint: #282a36 0%; }
    OptionList > .option-list--option { padding: 0 1; }
    OptionList > .option-list--option-highlighted,
    OptionList:focus > .option-list--option-highlighted { background: #44475a; color: #f8f8f2; }
    OptionList > .option-list--option-hover { background: #343746; }
    #now { height: 2; padding: 0 3; display: none; }
    #hint { height: 1; padding: 0 3; }
    """.replace("#bd93f9", ACCENT)
    BINDINGS = [
        Binding("tab", "cycle(1)", show=False, priority=True),
        Binding("shift+tab", "cycle(-1)", show=False, priority=True),
        Binding("q", "quit", show=False), Binding("question_mark", "help", show=False),
        Binding("slash", "search", show=False), Binding("escape", "back", show=False),
        Binding("1", "jump('favs')", show=False), Binding("2", "jump('top')", show=False),
        Binding("3", "jump('search')", show=False), Binding("4", "jump('genres')", show=False),
        Binding("5", "jump('heard')", show=False), Binding("r", "refresh", show=False),
        Binding("right", "play", show=False), Binding("left", "back", show=False),
        Binding("f", "favorite", show=False), Binding("delete,backspace", "remove", show=False),
        Binding("n", "skip('next')", show=False), Binding("p", "skip('prev')", show=False),
        Binding("space", "toggle", show=False), Binding("x", "stop", show=False),
        Binding("minus", "volume(-5)", show=False), Binding("plus,equals_sign", "volume(5)", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.cfg = {"favorites": []}
        self.cfg.update(load_json(CONFIG, {}))
        self.heard = load_json(HEARD, [])
        self.now = load_json(NOW_FILE, None)     # {"list": [stations], "id": playing station id}
        self.view = None
        self.stack = []
        self.rows = {}
        self.cache = {}
        self.last_song = None

    @property
    def main(self):
        return self.screen_stack[0]

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        yield Input(placeholder="station name, then Enter", id="search")
        yield Static(id="status")
        yield OptionList(id="list")
        yield Static(id="now")
        yield Static(Text.assemble(("Enter", CYAN), (" play  ", GRAY), ("f", CYAN), (" favorite  ", GRAY),
                                   ("n p", CYAN), (" next/prev  ", GRAY), ("Space", CYAN), (" pause  ", GRAY),
                                   ("Tab", CYAN), (" views  ", GRAY), ("?", CYAN), (" help", GRAY)), id="hint")

    def on_mount(self):
        self.jump("favs" if self.cfg["favorites"] else "top")
        self.set_interval(1, self.tick)
        self.tick()

    def status(self, msg):
        self.main.query_one("#status").update(msg)

    def save_cfg(self):
        save_json(CONFIG, self.cfg)

    # ----- views -----
    def jump(self, kind):
        self.stack = []
        if kind == "favs":
            favs = self.cfg["favorites"]
            self.show("favs", "Favorites", favs)
            self.status(plural(len(favs), "favorite") if favs else "No favorites yet. Press f on a station.")
        elif kind == "top":
            self.load("top", "Top", "stations/topclick", limit=100)
        elif kind == "genres":
            self.load("genres", "Genres", "tags", order="stationcount", reverse="true", limit=150)
        elif kind == "heard":
            self.show("heard", "Heard", self.heard)
            self.status(plural(len(self.heard), "song") + " heard" if self.heard
                        else "Song titles that stations send show up here.")
        else:
            self.show("search", "Search", self.cache.get("search", []))
            self.main.query_one("#search").focus()
            self.status("Type a station name, then Enter. Press 4 to browse by genre.")

    def action_jump(self, kind):
        self.jump(kind)

    def action_cycle(self, d):
        if isinstance(self.screen, ModalScreen):
            return
        kinds = [k for k, _ in TABS]
        cur = self.view[0] if self.view else "favs"
        if cur not in kinds:
            cur = next((v[0] for v in reversed(self.stack) if v[0] in kinds), "genres")
        self.jump(kinds[(kinds.index(cur) + d) % len(kinds)])

    def load(self, kind, title, path, push=False, refresh=False, **params):
        key = (path, tuple(sorted(params.items())))
        if key in self.cache and not refresh:
            self._loaded(kind, title, self.cache[key], push)
            return
        self.show(kind, title, [], push=push)
        self.status("Loading...")
        self.fetch(kind, title, path, push, key, params)

    @work(thread=True, exclusive=True, group="load")
    def fetch(self, kind, title, path, push, key, params):
        try:
            if path == "tags":
                rows = [{"_tag": t["name"], "count": t["stationcount"]} for t in api(path, **params)
                        if t.get("name") and t.get("stationcount", 0) >= 20]
            else:
                rows = stations(path, **params)
        except Exception as ex:
            self.call_from_thread(self.status, f"Could not reach radio-browser.info: {str(ex)[:90]}")
            return
        self.cache[key] = rows
        if kind == "search":
            self.cache["search"] = rows
        self.call_from_thread(self._loaded, kind, title, rows, False)

    def _loaded(self, kind, title, rows, push):
        if push or not self.view or self.view[0] == kind:
            self.show(kind, title, rows, push=push)
        word = "genre" if kind == "genres" else "station"
        self.status(plural(len(rows), word) if rows else "Nothing found.")

    def show(self, kind, title, items, push=False):
        if push and self.view:
            self.stack.append(self.view)
        self.view = (kind, title, items)
        if kind in dict(TABS):
            head = Text.assemble((I_RADIO + "  ", ORANGE), ("Radio", f"bold {ACCENT}"), "  ")
            for i, (k, name) in enumerate(TABS, 1):
                head.append(f" {i} {name} ", f"bold {ACCENT}" if k == kind else GRAY)
        else:
            head = Text.assemble((I_RADIO + "  ", ORANGE), (title, f"bold {ACCENT}"), ("   Left goes back", GRAY))
        self.main.query_one("#title").update(head)
        self.main.query_one("#search").display = kind == "search"
        self.fill(items, keep=0)
        if kind != "search" or items:
            self.main.query_one("#list").focus()

    def fill(self, items, keep=None):
        ol = self.main.query_one("#list", OptionList)
        if keep is None:
            keep = ol.highlighted
        ol.clear_options()
        self.rows = {}
        favs = {f["id"] for f in self.cfg["favorites"]}
        playing = self.now.get("id") if self.now else None
        opts = []
        for i, r in enumerate(items):
            key = str(i)
            self.rows[key] = r
            if "_tag" in r:
                t = Text.assemble((I_TAG + "  ", CYAN), (r["_tag"], f"bold {FG}"), (f"   {r['count']} stations", GRAY))
            elif "song" in r:
                t = Text.assemble((I_NOTE + "  ", PINK), (r["song"], f"bold {FG}"),
                                  ("\n   " + r["station"], CYAN), ("  ·  " + time.strftime("%b %-d %H:%M", time.localtime(r["t"])), GRAY))
            else:
                on = r["id"] == playing
                t = Text.assemble(((I_PLAY if on else I_RADIO) + "  ", GREEN if on else ORANGE),
                                  (r["name"], f"bold {GREEN if on else FG}"))
                if r["id"] in favs:
                    t.append("  " + I_STAR, YELLOW)
                line = Text("\n   ")
                bits = [b for b in (r["country"], r["tags"],
                                    f"{r['codec']} {r['bitrate']}k".strip() if r["bitrate"] else r["codec"]) if b]
                line.append("  ·  ".join(bits), GRAY)
                if on:
                    line.append("  ·  playing", f"bold {GREEN}")
                t.append_text(line)
            opts.append(Option(t, id=key))
        ol.add_options(opts)
        if opts:
            ol.highlighted = min(keep or 0, ol.option_count - 1)
            if ol.get_option_at_index(ol.highlighted).disabled:
                ol.highlighted = max(0, ol.highlighted - 1)

    def refill(self):
        if self.view:
            self.fill(self.view[2])

    def current(self):
        ol = self.main.query_one("#list", OptionList)
        if ol.highlighted is None or not self.rows:
            return None
        return self.rows.get(ol.get_option_at_index(ol.highlighted).id)

    def index_of_current(self):
        ol = self.main.query_one("#list", OptionList)
        return None if ol.highlighted is None else int(ol.get_option_at_index(ol.highlighted).id)

    # ----- search and focus -----
    def action_search(self):
        if not self.view or self.view[0] != "search":
            self.jump("search")
        box = self.main.query_one("#search", Input)
        box.focus()
        box.action_select_all()

    def on_input_submitted(self, e):
        if e.input.id == "search" and e.value.strip():
            self.load("search", "Search", "stations/search", name=e.value.strip(), limit=100,
                      order="clickcount", reverse="true")

    def on_key(self, event):
        if isinstance(self.screen, ModalScreen):
            return
        box = self.main.query_one("#search", Input)
        ol = self.main.query_one("#list", OptionList)
        if box.has_focus:
            if event.key == "down" and ol.option_count:
                ol.focus()
                event.stop()
            elif event.key == "escape":
                if ol.option_count:
                    ol.focus()
                event.stop()
        elif ol.has_focus and event.key == "up" and ol.highlighted == 0 and box.display:
            box.focus()
            event.stop()

    def check_action(self, action, parameters):
        if self.main.query_one("#search", Input).has_focus and action not in ("back", "cycle"):
            return False
        return True

    def action_back(self):
        if self.stack:
            kind, title, items = self.stack.pop()
            self.view = None
            self.show(kind, title, items)
        elif self.view and self.view[0] == "search" and not self.main.query_one("#search").has_focus:
            self.main.query_one("#search").focus()

    def action_refresh(self):
        kind = self.view[0] if self.view else ""
        if kind == "top":
            self.load("top", "Top", "stations/topclick", refresh=True, limit=100)
        elif kind == "genres":
            self.load("genres", "Genres", "tags", refresh=True, order="stationcount", reverse="true", limit=150)

    # ----- favorites and heard -----
    def action_favorite(self):
        cur = self.current()
        if not cur or "id" not in cur:
            return
        favs = self.cfg["favorites"]
        hit = [f for f in favs if f["id"] == cur["id"]]
        if hit:
            favs.remove(hit[0])
            self.notify(f"Removed {cur['name']} from favorites", timeout=2)
        else:
            favs.append(cur)
            self.notify(f"Added {cur['name']} to favorites", timeout=2)
        self.save_cfg()
        if self.view[0] == "favs":
            self.show("favs", "Favorites", favs)
        else:
            self.refill()

    def action_remove(self):
        kind = self.view[0] if self.view else ""
        i = self.index_of_current()
        if i is None:
            return
        if kind == "favs":
            self.cfg["favorites"].pop(i)
            self.save_cfg()
            self.fill(self.cfg["favorites"])
        elif kind == "heard":
            self.heard.pop(i)
            save_json(HEARD, self.heard)
            self.fill(self.heard)

    # ----- playback -----
    def on_option_list_option_selected(self, e):
        if e.option_list.id == "list":
            self.action_play()

    def action_play(self):
        cur = self.current()
        if not cur:
            return
        if "_tag" in cur:
            return self.load("tag", cur["_tag"].title(), "stations/search", push=True, tag=cur["_tag"], tagExact="true",
                             limit=100, order="clickcount", reverse="true")
        if "song" in cur:
            return
        rows = [r for r in self.view[2] if "id" in r]
        self.play(rows[:100], cur)

    @work(thread=True, exclusive=True, group="player")
    def play(self, lst, st):
        if not deck_mpv.start(SOCK, ["--ytdl=no", "--cache=yes", "--demuxer-max-bytes=4MiB"], MPV_LOG):
            self.call_from_thread(self.notify, f"The player did not start. Details: {MPV_LOG}", severity="error")
            return
        # The whole list becomes mpv's playlist, so next/previous (and the media keys) change station.
        # Load the chosen station first (it starts right away), then put the others around it in order.
        def title(x):
            return f"force-media-title={deck_mpv.quote(x['name'])}"
        k = lst.index(st)
        deck_mpv.pause_others(SOCK)
        deck_mpv.ipc(SOCK, "loadfile", st["url"], "replace", -1, title(st))
        deck_mpv.ipc(SOCK, "set_property", "pause", False)
        for j, x in enumerate(lst):
            if j < k:
                deck_mpv.ipc(SOCK, "loadfile", x["url"], "insert-at", j, title(x))
            elif j > k:
                deck_mpv.ipc(SOCK, "loadfile", x["url"], "append", -1, title(x))
        self.now = {"list": lst, "id": st["id"]}
        save_json(NOW_FILE, self.now)
        count_click(st["id"])
        self.call_from_thread(self.refill)
        self.call_from_thread(self.notify, f"Tuning in to {st['name']}", timeout=2)

    def playing_station(self):
        if not self.now:
            return None
        idx = deck_mpv.ipc(SOCK, "get_property", "playlist-pos")
        lst = self.now.get("list") or []
        if isinstance(idx, int) and 0 <= idx < len(lst) and not isinstance(idx, bool):
            return lst[idx]
        return None

    def action_skip(self, which):
        if deck_mpv.ipc(SOCK, f"playlist-{which}") is None:
            self.notify("No more stations in this list", timeout=1.5)
        self.tick()

    def action_toggle(self):
        if deck_mpv.ipc(SOCK, "cycle", "pause") is None:
            self.notify("Nothing is playing", timeout=1.5)
        elif deck_mpv.ipc(SOCK, "get_property", "pause") is False:
            deck_mpv.pause_others(SOCK)
        self.tick()

    def action_volume(self, d):
        deck_mpv.ipc(SOCK, "add", "volume", d)
        vol = deck_mpv.ipc(SOCK, "get_property", "volume")
        if vol is not None:
            self.notify(f"Volume {vol:.0f}%", timeout=1)

    def action_stop(self):
        if deck_mpv.ipc(SOCK, "quit"):
            self.notify("Stopped", timeout=1)
        self.now = None
        save_json(NOW_FILE, None)
        self.refill()
        self.tick()

    def tick(self):
        now = self.main.query_one("#now")
        st = deck_mpv.status(SOCK)
        if not st:
            if self.now:
                self.now = None
                save_json(NOW_FILE, None)
                if not isinstance(self.screen, ModalScreen):
                    self.refill()
            now.display = False
            return
        sta = self.playing_station()
        if sta and self.now.get("id") != sta["id"]:          # moved to another station with n/p or media keys
            self.now["id"] = sta["id"]
            save_json(NOW_FILE, self.now)
            if not isinstance(self.screen, ModalScreen):
                self.refill()
        song = (st.get("icy") or "").strip() if isinstance(st.get("icy"), str) else ""
        # Songs come as "Artist - Title"; other text is usually the station's slogan, so it is not logged.
        if song and sta and song != self.last_song and " - " in song:
            self.last_song = song
            if not self.heard or self.heard[0]["song"] != song:
                self.heard.insert(0, {"song": song, "station": sta["name"], "t": time.time()})
                del self.heard[300:]
                save_json(HEARD, self.heard)
        w = max(10, self.size.width - 8)
        name = sta["name"] if sta else st["title"]
        buffering = deck_mpv.ipc(SOCK, "get_property", "paused-for-cache") is True
        t = Text.assemble(((I_PAUSE if st["paused"] else I_PLAY) + "  ", GRAY if st["paused"] else ORANGE),
                          (name[:40], f"bold {FG}"))
        if song:
            t.append(f"   {I_NOTE} {song}"[: max(0, w - len(name) - 6)], PINK)
        t.append("   buffering..." if buffering else ("   paused" if st["paused"] else "   live"),
                 YELLOW if buffering else (GRAY if st["paused"] else RED))
        count = len(self.now.get("list") or []) if self.now else 0
        idx = deck_mpv.ipc(SOCK, "get_property", "playlist-pos")
        pos = Text(f"station {idx + 1} of {count}   n/p to switch" if count > 1 and isinstance(idx, int) else "", GRAY)
        now.update(Text.assemble(pos, "\n", t))
        now.display = True

    def action_help(self):
        self.push_screen(Help())


if __name__ == "__main__":
    Radio().run()
