#!/home/admin/.venvs/deck/bin/python
"""spot: a keyboard-first Spotify client for the deck.

Audio comes from spotifyd (a Spotify Connect speaker running on the Pi).
This app browses your library through the Spotify Web API and tells
spotifyd what to play.

  spot.py           start the app
  spot.py --login   connect your Spotify account (run once, over SSH is fine)
"""
import base64
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse

import requests
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, OptionList, Static
from textual.widgets.option_list import Option

# ---------------------------------------------------------------- settings
HOME = os.path.expanduser("~")
CFG_DIR = f"{HOME}/.config/deck"
CONF_FILE = f"{CFG_DIR}/spotify.json"         # {"client_id": "...", "device": "cyberdeck"}
TOKEN_FILE = f"{CFG_DIR}/spotify-token.json"  # written by --login, private
REDIRECT = "http://127.0.0.1:8888/callback"
SCOPES = ("user-read-playback-state user-modify-playback-state user-read-currently-playing "
          "user-library-read user-library-modify playlist-read-private "
          "playlist-read-collaborative user-read-recently-played")
API = "https://api.spotify.com/v1"
ACCOUNTS = "https://accounts.spotify.com"

POLL_SECONDS = 2.0      # how often the player state is fetched
MAX_ROWS = 1000         # cap for very long lists (liked songs, big playlists)

PURPLE, PINK, CYAN, GREEN = "#bd93f9", "#ff79c6", "#8be9fd", "#50fa7b"
ORANGE, YELLOW, RED, GRAY, FG = "#ffb86c", "#f1fa8c", "#ff5555", "#6272a4", "#f8f8f2"
BG, CUR, DARK = "#282a36", "#44475a", "#21222c"
try:
    ACCENT = json.load(open(f"{CFG_DIR}/prefs.json")).get("accent", PURPLE)
except Exception:
    ACCENT = PURPLE

I_PLAY, I_PAUSE, I_SHUF, I_REP, I_REP1 = "", "", "", "\U000f0456", "\U000f0458"
I_VOL, I_DEV, I_HEART, I_MUSIC = "", "\U000f04c3", "", ""
I_HOME, I_SEARCH, I_ALBUM, I_LIST, I_QUEUE, I_USER = "", "", "\U000f0025", "\U000f0cb8", "\U000f05f2", ""


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_private(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)


def fmt_ms(ms):
    s = max(0, int(ms or 0) // 1000)
    return f"{s // 60}:{s % 60:02d}"


def cut(s, n):
    s = s or ""
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


# ---------------------------------------------------------------- login (PKCE, no client secret)
def login():
    conf = load_json(CONF_FILE, {})
    cid = conf.get("client_id", "")
    if not cid:
        cid = input("Spotify app Client ID: ").strip()
        conf["client_id"] = cid
        conf.setdefault("device", "cyberdeck")
        save_private(CONF_FILE, conf)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(12)
    url = f"{ACCOUNTS}/authorize?" + urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state})
    print("\n1. Open this address in a browser on your phone or computer and log in:\n")
    print(url)
    print("\n2. The browser then goes to a page that does not load (127.0.0.1...). That is expected.")
    print("   Copy the whole address from the address bar and paste it here.\n")
    back = input("Address: ").strip()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(back).query)
    if q.get("state", [""])[0] != state:
        sys.exit("That address does not belong to this login attempt. Run --login again.")
    if "code" not in q:
        sys.exit(f"Spotify said: {q.get('error', ['no code in the address'])[0]}")
    r = requests.post(f"{ACCOUNTS}/api/token", timeout=15, data={
        "grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": REDIRECT,
        "client_id": cid, "code_verifier": verifier})
    if r.status_code != 200:
        sys.exit(f"Login failed: {r.status_code} {r.text}")
    tok = r.json()
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    save_private(TOKEN_FILE, tok)
    print("\nLogged in. Start the app with: spot.py")


# ---------------------------------------------------------------- local control (MPRIS)
# When the deck itself is the speaker, play/pause, skip, seek and volume go straight to
# spotifyd over D-Bus (instant) instead of a round trip through Spotify's servers.
PLAYERCTL = shutil.which("playerctl")


def mpris(*args):
    if not PLAYERCTL:
        return False
    env = dict(os.environ)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
    try:
        r = subprocess.run([PLAYERCTL, "-p", "spotifyd", *args], env=env,
                           capture_output=True, timeout=1.5)
        return r.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------- Web API client
class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


class Spotify:
    def __init__(self):
        self.conf = load_json(CONF_FILE, {})
        self.tok = load_json(TOKEN_FILE, None)
        self.device_name = self.conf.get("device", "cyberdeck")
        self.lock = threading.Lock()
        self.http = requests.Session()
        self.user_id = None
        self.active_id = None     # device id of the current speaker, kept fresh by the player poll

    def ready(self):
        return bool(self.conf.get("client_id") and self.tok and self.tok.get("refresh_token"))

    def _refresh(self):
        r = self.http.post(f"{ACCOUNTS}/api/token", timeout=15, data={
            "grant_type": "refresh_token", "refresh_token": self.tok["refresh_token"],
            "client_id": self.conf["client_id"]})
        if r.status_code != 200:
            raise ApiError(r.status_code, "Login expired. Run: spot.py --login")
        new = r.json()
        new.setdefault("refresh_token", self.tok["refresh_token"])
        new["expires_at"] = time.time() + new.get("expires_in", 3600) - 60
        self.tok = new
        save_private(TOKEN_FILE, new)

    def token(self, force=False):
        with self.lock:
            if force or time.time() > self.tok.get("expires_at", 0):
                self._refresh()
            return self.tok["access_token"]

    def req(self, method, path, params=None, body=None, _retry=True):
        url = path if path.startswith("http") else API + path
        try:
            r = self.http.request(method, url, params=params, json=body, timeout=12,
                                  headers={"Authorization": f"Bearer {self.token()}"})
        except requests.RequestException:
            raise ApiError(0, "No connection to Spotify")
        if r.status_code == 401 and _retry:
            self.token(force=True)
            return self.req(method, path, params, body, False)
        if r.status_code == 429 and _retry:
            time.sleep(min(int(r.headers.get("Retry-After", "2")), 8))
            return self.req(method, path, params, body, False)
        if r.status_code >= 400:
            try:
                err = r.json().get("error", {})
                msg = err.get("message") if isinstance(err, dict) else str(err)
            except Exception:
                msg = r.text[:120]
            raise ApiError(r.status_code, msg or f"HTTP {r.status_code}")
        if r.status_code == 204 or not r.content:
            return None
        try:
            return r.json()
        except ValueError:      # player commands may answer 200 with a plain-text id
            return None

    def get(self, path, **params):
        return self.req("GET", path, params=params or None)

    def pages(self, path, limit=50, cap=MAX_ROWS, **params):
        """Collect items from a paged endpoint, following 'next' links."""
        out = []
        for batch in self.page_batches(path, limit, cap, **params):
            out.extend(batch)
        return out

    def page_batches(self, path, limit=50, cap=MAX_ROWS, **params):
        """Yield one page of items at a time, so the first page can be shown right away."""
        count, data = 0, self.get(path, limit=limit, **params)
        while data:
            batch = [i for i in data.get("items", []) if i][: cap - count]
            count += len(batch)
            yield batch
            if count >= cap or not data.get("next"):
                break
            data = self.get(data["next"])

    def me(self):
        if not self.user_id:
            self.user_id = (self.get("/me") or {}).get("id")
        return self.user_id

    # --- devices
    def devices(self):
        return (self.get("/me/player/devices") or {}).get("devices", [])

    def target_device(self, fresh=False):
        # The active device id comes from the player poll, so most plays need no extra request.
        if self.active_id and not fresh:
            return self.active_id
        devs = self.devices()
        for d in devs:
            if d.get("is_active"):
                return d["id"]
        for d in devs:
            if d.get("name", "").lower() == self.device_name.lower():
                return d["id"]
        raise ApiError(404, f"Speaker '{self.device_name}' not found. Is spotifyd running?")

    def play(self, body=None):
        dev = self.target_device()
        try:
            self.req("PUT", "/me/player/play", params={"device_id": dev}, body=body)
        except ApiError as e:
            if e.status != 404:
                raise
            dev = self.target_device(fresh=True)
            self.req("PUT", "/me/player", body={"device_ids": [dev], "play": True})
            time.sleep(0.6)
            self.req("PUT", "/me/player/play", params={"device_id": dev}, body=body)


# ---------------------------------------------------------------- rows shown in the main list
def track_row(t, album=None):
    if not t or not t.get("uri"):
        return None
    alb = t.get("album") or album or {}
    artists = t.get("artists") or []
    return {"kind": "track", "uri": t["uri"], "name": t.get("name", ""),
            "sub": ", ".join(a.get("name", "") for a in artists),
            "album": alb.get("name", ""), "album_id": alb.get("id"),
            "artist_id": artists[0].get("id") if artists else None,
            "ms": t.get("duration_ms", 0)}


def album_row(a):
    return {"kind": "album", "uri": a["uri"], "id": a["id"], "name": a.get("name", ""),
            "sub": ", ".join(x.get("name", "") for x in a.get("artists", [])),
            "album": (a.get("album_type") or "album").title(), "ms": 0,
            "artist_id": (a.get("artists") or [{}])[0].get("id")}


def playlist_row(p):
    return {"kind": "playlist", "uri": p["uri"], "id": p["id"], "name": p.get("name", ""),
            "sub": (p.get("owner") or {}).get("display_name", ""), "album": "Playlist", "ms": 0}


def artist_row(a):
    return {"kind": "artist", "uri": a["uri"], "id": a["id"], "name": a.get("name", ""),
            "sub": "", "album": "Artist", "ms": 0}


# ---------------------------------------------------------------- widgets
class Sidebar(OptionList):
    BINDINGS = [Binding("j", "cursor_down", show=False), Binding("k", "cursor_up", show=False),
                Binding("right", "select", show=False), Binding("l", "select", show=False)]


class Tracks(DataTable):
    BINDINGS = [Binding("j", "cursor_down", show=False), Binding("k", "cursor_up", show=False),
                Binding("g", "scroll_top", show=False), Binding("G", "scroll_bottom", show=False),
                Binding("left", "app.back", show=False), Binding("h", "app.back", show=False),
                Binding("right", "select_cursor", show=False)]


HELP = [
    ("Playback", [("space", "play / pause"), ("n  p", "next / previous track"),
                  (",  .", "seek back / forward 10 s"), ("-  +", "volume down / up"),
                  ("s", "shuffle on / off"), ("r", "repeat: off, all, one"),
                  ("d", "choose speaker")]),
    ("Moving around", [("Tab", "switch between menu and list"), ("j k  arrows", "move"),
                       ("Enter  Right", "open / play"), ("Left  h", "back"),
                       ("g  G", "top / bottom of list"), ("1-5", "Home, Search, Liked, Albums, Queue"),
                       ("/", "search")]),
    ("On a song", [("a", "add to queue"), ("f", "like / unlike"),
                   ("o", "open its album"), ("i", "open its artist")]),
    ("App", [("R", "reload"), ("?", "this help"), ("q", "quit")]),
]


class HelpScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", ""), ("q", "dismiss", ""), ("question_mark", "dismiss", "")]
    CSS = f"""
    HelpScreen {{ align: center middle; background: {BG} 70%; }}
    #help {{ width: 64; height: auto; padding: 1 3; background: {DARK}; border: round {ACCENT}; }}
    """

    def compose(self):
        t = Text()
        for i, (title, keys) in enumerate(HELP):
            t.append(("\n" if i else "") + title + "\n", style=f"bold {ACCENT}")
            for k, d in keys:
                t.append(f"  {k:<14}", style=f"bold {CYAN}")
                t.append(d + "\n", style=FG)
        t.append("\nEsc to close", style=GRAY)
        yield Static(t, id="help")


class DeviceScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", ""), ("q", "dismiss", ""), ("left", "dismiss", "")]
    CSS = f"""
    DeviceScreen {{ align: center middle; background: {BG} 70%; }}
    #box {{ width: 56; height: auto; max-height: 20; padding: 1 2; background: {DARK}; border: round {ACCENT}; }}
    #box OptionList {{ background: {DARK}; border: none; height: auto; max-height: 14; }}
    #box OptionList > .option-list--option-highlighted {{ background: {CUR}; }}
    """

    def __init__(self, devices):
        super().__init__()
        self.devices = devices

    def compose(self):
        with Vertical(id="box"):
            yield Static(Text("Play on", style=f"bold {ACCENT}"))
            if not self.devices:
                yield Static(Text("\nNo speakers found. Is spotifyd running?", style=GRAY))
            opts = []
            for d in self.devices:
                mark = (" ● active", GREEN) if d.get("is_active") else ("", GRAY)
                opts.append(Option(Text.assemble((I_DEV + "  ", CYAN), (d.get("name", "?"), FG),
                                                 (f"  {d.get('type', '').lower()}", GRAY), mark), id=d["id"]))
            yield Sidebar(*opts)

    def on_mount(self):
        self.query_one(Sidebar).focus()

    def on_option_list_option_selected(self, ev):
        self.dismiss(ev.option.id)


# ---------------------------------------------------------------- the app
class Spot(App):
    TITLE = "spot"
    CSS = f"""
    Screen {{ background: {BG}; layout: vertical; }}
    #body {{ height: 1fr; }}
    #side {{ width: 30; background: {DARK}; padding: 1 0 0 0; }}
    #side .label {{ padding: 0 2; color: {GRAY}; }}
    Sidebar {{ background: {DARK}; border: none; height: 1fr; padding: 0 1;
              scrollbar-color: {ACCENT}; scrollbar-background: {DARK}; }}
    Sidebar:focus {{ border: none; }}
    Sidebar > .option-list--option {{ padding: 0 1; }}
    Sidebar > .option-list--option-highlighted {{ background: {CUR}; }}
    Sidebar:blur > .option-list--option-highlighted {{ background: #343746; }}
    #main {{ padding: 1 2 0 2; }}
    #title {{ height: 2; }}
    #search {{ display: none; margin: 0 0 1 0; border: round {ACCENT}; background: {BG}; }}
    #search.on {{ display: block; }}
    Tracks {{ background: {BG}; height: 1fr; overflow-x: hidden; scrollbar-color: {ACCENT}; scrollbar-background: {DARK}; }}
    Tracks > .datatable--header {{ background: {BG}; color: {GRAY}; text-style: bold; }}
    Tracks > .datatable--cursor {{ background: {CUR}; }}
    Tracks:blur > .datatable--cursor {{ background: #343746; }}
    Tracks > .datatable--hover {{ background: #343746; }}
    #np {{ height: 4; background: {DARK}; padding: 0 2; border-top: solid {CUR}; }}
    #keys {{ height: 1; background: {DARK}; padding: 0 2; }}
    """
    BINDINGS = [
        Binding("q", "quit", show=False), Binding("question_mark", "help", show=False),
        Binding("space", "toggle", show=False, priority=False),
        Binding("n", "next", show=False), Binding("p", "prev", show=False),
        Binding("comma", "seek(-10)", show=False), Binding("full_stop", "seek(10)", show=False),
        Binding("minus", "volume(-5)", show=False), Binding("plus", "volume(5)", show=False),
        Binding("equals_sign", "volume(5)", show=False),
        Binding("s", "shuffle", show=False), Binding("r", "repeat", show=False),
        Binding("d", "devices", show=False), Binding("slash", "search", show=False),
        Binding("a", "queue_add", show=False), Binding("f", "like", show=False),
        Binding("o", "open_album", show=False), Binding("i", "open_artist", show=False),
        Binding("R", "reload", show=False), Binding("escape", "escape", show=False),
        Binding("1", "jump('home')", show=False), Binding("2", "jump('search')", show=False),
        Binding("3", "jump('liked')", show=False), Binding("4", "jump('albums')", show=False),
        Binding("5", "jump('queue')", show=False),
    ]

    def __init__(self, sp):
        super().__init__()
        self.sp = sp
        self.view = None          # {"title", "sub", "rows", "context", "key"}
        self.history = []         # previous views, for Left / back
        self.state = None         # last /me/player answer
        self.state_at = 0.0       # time.monotonic() when state was fetched
        self.playing_uri = None
        self.liked = {}           # uri -> bool cache for the heart in the player bar
        self.load_token = 0       # ignores results of loads that were replaced
        self.pl_names = {}
        self.vol_target = None    # volume the user asked for, shown until Spotify catches up
        self.vol_until = 0.0
        self.vol_timer = None
        self.holds = {}           # state key -> (value, until): keeps a change on screen until Spotify catches up
        self.seek_hold = None     # (position_ms, set_at, until)
        self.cache = {}           # view key -> loaded view, so going back to a list is instant
        self.col_w = (30, 20, 20)

    # ---------- layout
    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with Vertical(id="side"):
                yield Sidebar(id="menu")
            with Vertical(id="main"):
                yield Static(id="title")
                yield Input(placeholder="Search songs, artists, albums, playlists", id="search")
                yield Tracks(cursor_type="row", zebra_stripes=False, id="list")
        yield Static(id="np")
        yield Static(self.keys_text(), id="keys")

    def keys_text(self):
        t = Text()
        for k, d in [("space", "play"), ("n/p", "next/prev"), (",/.", "seek"), ("-/+", "vol"),
                     ("/", "search"), ("a", "queue"), ("f", "like"), ("d", "speaker"), ("?", "help")]:
            t.append(k, style=f"bold {ACCENT}")
            t.append(f" {d}   ", style=GRAY)
        return t

    def on_mount(self):
        self.build_menu([])
        self.query_one("#list", Tracks).focus()
        self.render_player()
        self.set_interval(0.5, self.render_player)
        self.set_interval(POLL_SECONDS, self.poll_once)
        self.poll_once()
        self.load_playlists()
        self.open_section("home")
        self.set_timer(3, self.prefetch)

    @work(thread=True, group="prefetch", exclusive=True)
    def prefetch(self):
        """Load Liked Songs and Albums quietly in the background so 3 and 4 open instantly."""
        try:
            self.sp.me()
        except Exception:
            pass
        for key, path, conv in (("liked", "/me/tracks", lambda i: track_row(i.get("track"))),
                                ("albums", "/me/albums", lambda i: album_row(i["album"]) if i.get("album") else None)):
            if key in self.cache:
                continue
            try:
                rows = [r for r in (conv(i) for i in self.sp.pages(path)) if r]
            except Exception:
                continue
            view = self.make_view(key, rows)
            self.call_from_thread(self.cache.setdefault, key, view)

    def make_view(self, key, rows):
        if key == "liked":
            uid = self.sp.user_id
            return {"title": "Liked Songs", "sub": f"{len(rows)} songs", "rows": rows, "key": "liked",
                    "context": None}
        return {"title": "Albums", "sub": f"{len(rows)} saved", "rows": rows, "context": None, "key": "albums"}

    # ---------- sidebar
    def build_menu(self, playlists):
        menu = self.query_one("#menu", Sidebar)
        keep = menu.highlighted
        menu.clear_options()

        def it(oid, icon, label, color):
            return Option(Text.assemble((icon, color), "  ", (label, f"bold {FG}")), id=oid)

        menu.add_options([
            it("home", I_HOME, "Home", ACCENT), it("search", I_SEARCH, "Search", CYAN),
            it("liked", I_HEART, "Liked Songs", PINK), it("albums", I_ALBUM, "Albums", ORANGE),
            it("queue", I_QUEUE, "Queue", GREEN),
            Option(Text("\nPLAYLISTS", style=f"bold {GRAY}"), id="hdr", disabled=True),
        ])
        seen = set()
        playlists = [p for p in playlists if p.get("id") and not (p["id"] in seen or seen.add(p["id"]))]
        self.pl_names = {p["id"]: p.get("name", "") for p in playlists}
        menu.add_options([Option(Text.assemble((I_LIST + "  ", GRAY), cut(p.get("name", ""), 22)), id=f"pl:{p['id']}")
                          for p in playlists])
        menu.highlighted = keep if keep is not None else 0

    @work(thread=True, group="playlists", exclusive=True)
    def load_playlists(self):
        try:
            pls = self.sp.pages("/me/playlists", cap=300)
            self.call_from_thread(self.build_menu, pls)
        except ApiError as e:
            self.call_from_thread(self.toast, str(e), "error")

    def on_option_list_option_selected(self, ev):
        if ev.option_list.id != "menu":
            return
        oid = ev.option.id
        self.history.clear()
        if oid.startswith("pl:"):
            self.open_playlist(oid[3:], self.pl_names.get(oid[3:], "Playlist"))
        else:
            self.open_section(oid)
        if oid != "search":
            self.query_one("#list", Tracks).focus()

    def action_jump(self, section):
        menu = self.query_one("#menu", Sidebar)
        idx = ["home", "search", "liked", "albums", "queue"].index(section)
        menu.highlighted = idx
        self.history.clear()
        self.open_section(section)
        if section != "search":
            self.query_one("#list", Tracks).focus()

    # ---------- views
    def show(self, view, push=False):
        if push and self.view and self.view is not view:
            self.history.append(self.view)
        self.view = view
        self.render_rows(keep_cursor=False)

    def set_loading(self, title):
        self.load_token += 1
        self.query_one("#title", Static).update(Text.assemble((title, f"bold {ACCENT}"), ("   loading\u2026", GRAY)))
        return self.load_token

    def from_cache(self, key, push=False):
        view = self.cache.get(key)
        if view is None:
            return False
        self.load_token += 1
        self.show(view, push)
        return True

    def open_section(self, sec):
        search = self.query_one("#search", Input)
        search.set_class(sec == "search", "on")
        if sec == "search":
            search.focus()
            self.show({"title": "Search", "sub": "type and press Enter", "rows": [], "context": None, "key": "search"})
            return
        if sec in ("liked", "albums") and self.from_cache(sec):
            return
        tok = self.set_loading({"home": "Home", "liked": "Liked Songs", "albums": "Albums", "queue": "Queue"}[sec])
        self.fetch_section(sec, tok)

    def stream(self, tok, view, batches, convert, unit, push=False):
        """Show the first page as soon as it arrives, then add the rest as it loads (worker thread)."""
        shown = False
        total = 0
        for batch in batches:
            if tok != self.load_token:
                return
            rows = [r for r in (convert(i) for i in batch) if r]
            total += len(rows)
            if not shown:
                view["rows"] = rows
                view["sub"] = f"{total} {unit}, loading\u2026"
                self.call_from_thread(self.show, view, push)
                shown = True
            else:
                self.call_from_thread(self.append_rows, tok, view, rows, f"{total} {unit}, loading\u2026")
        if not shown:
            view["rows"] = []
            view["sub"] = f"0 {unit}"
            self.call_from_thread(self.show, view, push)
        self.call_from_thread(self.finish_load, tok, view, f"{total} {unit}")

    def finish_load(self, tok, view, sub):
        view["sub"] = sub
        self.cache[view["key"]] = view
        if tok == self.load_token and self.view is view:
            self.render_title()

    @work(thread=True, group="load", exclusive=True)
    def fetch_section(self, sec, tok, push=False):
        sp = self.sp
        try:
            if sec == "home":
                items = sp.get("/me/player/recently-played", limit=50) or {}
                rows, seen = [], set()
                for it in items.get("items", []):
                    r = track_row(it.get("track"))
                    if r and r["uri"] not in seen:
                        seen.add(r["uri"])
                        rows.append(r)
                view = {"title": "Home", "sub": "recently played", "rows": rows, "context": None}
            elif sec == "liked":
                sp.me()
                view = self.make_view("liked", [])
                self.stream(tok, view, sp.page_batches("/me/tracks"),
                            lambda i: track_row(i.get("track")), "songs", push)
                return
            elif sec == "albums":
                view = self.make_view("albums", [])
                self.stream(tok, view, sp.page_batches("/me/albums", cap=500),
                            lambda i: album_row(i["album"]) if i.get("album") else None, "saved", push)
                return
            else:
                q = sp.get("/me/player/queue") or {}
                rows = []
                cur = track_row(q.get("currently_playing"))
                if cur:
                    rows.append(cur)
                rows += [r for r in (track_row(t) for t in q.get("queue", [])) if r]
                view = {"title": "Queue", "sub": "now playing and up next", "rows": rows,
                        "context": None, "noplay": True}
            view["key"] = sec
        except ApiError as e:
            view = {"title": sec.title(), "sub": "", "rows": [], "context": None, "key": sec, "error": str(e)}
        if tok == self.load_token:
            self.call_from_thread(self.show, view, push)

    def open_playlist(self, pid, name, push=False):
        if self.from_cache(f"pl:{pid}", push):
            return
        tok = self.set_loading(name)
        self.fetch_playlist(pid, name, tok, push)

    @work(thread=True, group="load", exclusive=True)
    def fetch_playlist(self, pid, name, tok, push):
        uri = f"spotify:playlist:{pid}"
        view = {"title": name, "sub": "", "rows": [], "context": uri, "key": f"pl:{pid}"}
        try:
            self.stream(tok, view, self.sp.page_batches(f"/playlists/{pid}/items"),
                        lambda i: track_row(i.get("item") or i.get("track")), "songs", push)
            return
        except ApiError as e:
            if e.status not in (403, 404):
                view["error"] = str(e)
            # Spotify only lists the songs of playlists you own or collaborate on.
            view.update(sub="songs hidden by Spotify (not your playlist)", rows=[{
                "kind": "play_context", "uri": uri, "name": "Play this playlist", "sub": "",
                "album": "", "ms": 0}])
            self.cache[view["key"]] = view
        if tok == self.load_token:
            self.call_from_thread(self.show, view, push)

    def open_album(self, aid, name, push=True):
        if self.from_cache(f"al:{aid}", push):
            return
        tok = self.set_loading(name)
        self.fetch_album(aid, name, tok, push)

    @work(thread=True, group="load", exclusive=True)
    def fetch_album(self, aid, name, tok, push):
        alb = {"id": aid, "name": name}
        try:
            items = self.sp.pages(f"/albums/{aid}/tracks")
            rows = [r for r in (track_row(t, alb) for t in items) if r]
            view = {"title": name, "sub": f"album, {len(rows)} songs", "rows": rows,
                    "context": f"spotify:album:{aid}", "key": f"al:{aid}"}
            self.cache[view["key"]] = view
        except ApiError as e:
            view = {"title": name, "sub": "", "rows": [], "context": None, "key": f"al:{aid}", "error": str(e)}
        if tok == self.load_token:
            self.call_from_thread(self.show, view, push)

    def open_artist(self, aid, name, push=True):
        if self.from_cache(f"ar:{aid}", push):
            return
        tok = self.set_loading(name or "Artist")
        self.fetch_artist(aid, name, tok, push)

    @work(thread=True, group="load", exclusive=True)
    def fetch_artist(self, aid, name, tok, push):
        try:
            if not name:
                name = (self.sp.get(f"/artists/{aid}") or {}).get("name", "Artist")
            items = self.sp.pages(f"/artists/{aid}/albums", cap=200, include_groups="album,single")
            rows = [album_row(a) for a in items]
            view = {"title": name, "sub": f"artist, {len(rows)} releases", "rows": rows,
                    "context": None, "key": f"ar:{aid}"}
            self.cache[view["key"]] = view
        except ApiError as e:
            view = {"title": name or "Artist", "sub": "", "rows": [], "context": None, "key": f"ar:{aid}", "error": str(e)}
        if tok == self.load_token:
            self.call_from_thread(self.show, view, push)

    def on_input_submitted(self, ev):
        q = ev.value.strip()
        if not q:
            return
        tok = self.set_loading(f"Search: {q}")
        self.fetch_search(q, tok)
        self.query_one("#list", Tracks).focus()

    @work(thread=True, group="load", exclusive=True)
    def fetch_search(self, q, tok):
        try:
            # Spotify now returns at most 10 results per type per request.
            res = self.sp.get("/search", q=q, type="track,artist,album,playlist", limit=10) or {}
            tracks = (res.get("tracks") or {}).get("items", [])
            rows = [r for r in (track_row(t) for t in tracks if t) if r]
            rows += [artist_row(a) for a in (res.get("artists") or {}).get("items", []) if a]
            rows += [album_row(a) for a in (res.get("albums") or {}).get("items", []) if a]
            rows += [playlist_row(p) for p in (res.get("playlists") or {}).get("items", []) if p]
            view = {"title": f"Search: {q}", "sub": f"{len(rows)} results", "rows": rows,
                    "context": None, "key": "search"}
        except ApiError as e:
            view = {"title": f"Search: {q}", "sub": "", "rows": [], "context": None, "key": "search", "error": str(e)}
            if tok == self.load_token:
                self.call_from_thread(self.show, view, False)
            return
        if tok != self.load_token:
            return
        self.call_from_thread(self.show, view, False)
        # second page of songs arrives a moment later and is slotted in after the first ten
        try:
            more = self.sp.get("/search", q=q, type="track", limit=10, offset=10) or {}
            extra = [r for r in (track_row(t) for t in (more.get("tracks") or {}).get("items", []) if t) if r]
        except ApiError:
            return
        if extra:
            self.call_from_thread(self.insert_search_rows, tok, view, extra)

    def insert_search_rows(self, tok, view, extra):
        if tok != self.load_token or self.view is not view:
            return
        n = sum(1 for r in view["rows"] if r["kind"] == "track")
        view["rows"][n:n] = extra
        view["sub"] = f"{len(view['rows'])} results"
        self.render_rows()

    # ---------- main list drawing
    def render_title(self):
        v = self.view
        t = Text.assemble((v["title"], f"bold {ACCENT}"), ("   " + v.get("sub", ""), GRAY))
        if v.get("error"):
            t.append(f"   {v['error']}", style=RED)
        self.query_one("#title", Static).update(t)

    ICONS = {"album": (I_ALBUM, ORANGE), "playlist": (I_LIST, CYAN), "artist": (I_USER, PINK),
             "play_context": (I_PLAY, GREEN)}

    def cells(self, i, r):
        tw, aw, lw = self.col_w
        now = r["kind"] == "track" and r["uri"] == self.playing_uri
        if now:
            num = Text(I_PLAY, style=f"bold {GREEN}")
        elif r["kind"] in self.ICONS:
            num = Text(self.ICONS[r["kind"]][0], style=self.ICONS[r["kind"]][1])
        else:
            num = Text(str(i + 1), style=GRAY)
        return (num, Text(cut(r["name"], tw), style=f"bold {GREEN}" if now else FG),
                Text(cut(r["sub"], aw), style=GREEN if now else CYAN),
                Text(cut(r["album"], lw), style=GRAY),
                Text(fmt_ms(r["ms"]) if r["ms"] else "", style=GRAY))

    def render_rows(self, keep_cursor=True):
        v = self.view
        if not v:
            return
        self.render_title()
        table = self.query_one("#list", Tracks)
        row_before = table.cursor_row if keep_cursor else 0
        w = max(60, table.size.width or self.size.width - 34)
        free = w - 4 - 5 - 10 - 2      # number and time columns, cell padding, scrollbar
        tw, aw = int(free * 0.42), int(free * 0.28)
        self.col_w = (tw, aw, free - tw - aw)
        table.clear(columns=True)
        table.add_column("#", width=4, key="n")
        table.add_column("Title", width=tw, key="t")
        table.add_column("Artist", width=aw, key="a")
        table.add_column("Album", width=self.col_w[2], key="l")
        table.add_column("Time", width=5, key="d")
        for i, r in enumerate(v["rows"]):
            table.add_row(*self.cells(i, r), key=str(i))
        if v["rows"]:
            table.move_cursor(row=min(row_before, len(v["rows"]) - 1), scroll=False)
            if not keep_cursor:
                table.scroll_home(animate=False)
        elif not v.get("error") and v.get("key") != "search":
            table.add_row("", Text("Nothing here yet", style=GRAY), "", "", "")

    def append_rows(self, tok, view, rows, sub):
        view["rows"].extend(rows)
        view["sub"] = sub
        if tok != self.load_token or self.view is not view:
            return
        table = self.query_one("#list", Tracks)
        start = table.row_count
        for i, r in enumerate(view["rows"][start:], start):
            table.add_row(*self.cells(i, r), key=str(i))
        self.render_title()

    def mark_playing(self, old, new):
        """Restyle only the rows of the old and new song instead of redrawing the whole list."""
        v = self.view
        if not v:
            return
        table = self.query_one("#list", Tracks)
        for i, r in enumerate(v["rows"]):
            if r["kind"] == "track" and r["uri"] in (old, new) and i < table.row_count:
                for col, cell in zip(("n", "t", "a"), self.cells(i, r)[:3]):
                    try:
                        table.update_cell(str(i), col, cell)
                    except Exception:
                        pass

    def on_resize(self, ev):
        self.call_after_refresh(self.render_rows)

    def selected(self):
        v = self.view
        table = self.query_one("#list", Tracks)
        if not v or not v["rows"] or table.cursor_row is None:
            return None
        i = table.cursor_row
        return (i, v["rows"][i]) if 0 <= i < len(v["rows"]) else None

    def on_data_table_row_selected(self, ev):
        sel = self.selected()
        if not sel:
            return
        i, r = sel
        if r["kind"] == "album":
            self.open_album(r["id"], r["name"])
        elif r["kind"] == "playlist":
            self.open_playlist(r["id"], r["name"], push=True)
        elif r["kind"] == "artist":
            self.open_artist(r["id"], r["name"])
        elif r["kind"] == "play_context":
            self.do(lambda: self.sp.play({"context_uri": r["uri"]}), "Playing playlist")
        elif r["kind"] == "track":
            self.play_from(i)

    def play_from(self, i):
        v = self.view
        r = v["rows"][i]
        if v.get("noplay"):
            self.do(lambda: self.sp.play({"uris": [r["uri"]]}))
            return
        ctx = v.get("context")
        uris = [x["uri"] for x in v["rows"][i:] if x["kind"] == "track"][:100]

        def go():
            if ctx:
                try:
                    return self.sp.play({"context_uri": ctx, "offset": {"uri": r["uri"]}})
                except ApiError as e:
                    if e.status in (0, 401):
                        raise
            self.sp.play({"uris": uris})
        self.do(go)

    def action_back(self):
        if self.history:
            self.load_token += 1
            self.view = self.history.pop()
            self.render_rows(keep_cursor=False)
        else:
            self.query_one("#menu", Sidebar).focus()

    def action_escape(self):
        search = self.query_one("#search", Input)
        if search.has_focus:
            self.query_one("#list", Tracks).focus()
        elif self.history:
            self.action_back()

    def action_search(self):
        menu = self.query_one("#menu", Sidebar)
        menu.highlighted = 1
        self.history.clear()
        search = self.query_one("#search", Input)
        search.add_class("on")
        search.focus()
        if not self.view or self.view.get("key") != "search":
            self.show({"title": "Search", "sub": "type and press Enter", "rows": [], "context": None, "key": "search"})

    def action_reload(self):
        if self.view:
            self.cache.pop(self.view.get("key"), None)
        self.load_playlists()
        if self.view and self.view.get("key", "").startswith("pl:"):
            pid = self.view["key"][3:]
            self.open_playlist(pid, self.view["title"])
        elif self.view and self.view.get("key") in ("home", "liked", "albums", "queue"):
            self.open_section(self.view["key"])
        self.poll_once()

    def action_open_album(self):
        sel = self.selected()
        if sel and sel[1].get("album_id"):
            self.open_album(sel[1]["album_id"], sel[1]["album"])

    def action_open_artist(self):
        sel = self.selected()
        if sel and sel[1].get("artist_id"):
            r = sel[1]
            self.open_artist(r["artist_id"], r["sub"].split(", ")[0] if r["kind"] != "artist" else r["name"])
        elif sel and sel[1]["kind"] == "artist":
            self.open_artist(sel[1]["id"], sel[1]["name"])

    # ---------- background calls
    def toast(self, msg, severity="information"):
        self.notify(msg, severity=severity, timeout=3)

    @work(thread=True, group="cmd")
    def do(self, fn, ok_msg=None, local=None):
        """Run a command off the UI thread. local=(playerctl args) tries spotifyd directly first."""
        try:
            if not (local and mpris(*local)):
                fn()
            if ok_msg:
                self.call_from_thread(self.toast, ok_msg)
            self.call_from_thread(self.poll_soon)
        except ApiError as e:
            self.call_from_thread(self.toast, str(e), "error")
            self.call_from_thread(self.poll_soon)
        except Exception as e:  # never let one failed command close the app
            self.call_from_thread(self.toast, f"{type(e).__name__}: {e}", "error")

    def poll_soon(self):
        self.set_timer(0.3, self.poll_once)
        self.set_timer(1.3, self.poll_once)

    # ---------- playback keys
    def st(self, key, default=None):
        return (self.state or {}).get(key, default)

    def is_local(self):
        """True when this deck (spotifyd) is the speaker that is playing."""
        dev = self.st("device") or {}
        return bool(PLAYERCTL) and dev.get("name", "").lower() == self.sp.device_name.lower()

    def hold(self, key, value, seconds=2.5):
        """Show a change right away and ignore older values from Spotify for a moment."""
        if self.state is not None:
            self.state[key] = value
        self.holds[key] = (value, time.monotonic() + seconds)
        self.render_player()

    def action_toggle(self):
        playing = bool(self.st("is_playing"))
        local = self.is_local() and bool(self.st("item"))
        if self.state and self.state.get("item"):
            self.state["progress_ms"], self.state_at = self.progress(), time.monotonic()
            self.hold("is_playing", not playing)
        if playing:
            self.do(lambda: self.sp.req("PUT", "/me/player/pause"), local=("pause",) if local else None)
        else:
            self.do(lambda: self.sp.play(), local=("play",) if local else None)

    def action_next(self):
        self.do(lambda: self.sp.req("POST", "/me/player/next"), local=("next",) if self.is_local() else None)

    def action_prev(self):
        self.do(lambda: self.sp.req("POST", "/me/player/previous"),
                local=("previous",) if self.is_local() else None)

    def action_seek(self, delta):
        if not self.state or not self.state.get("item"):
            return
        pos = max(0, self.progress() + delta * 1000)
        now = time.monotonic()
        self.state["progress_ms"], self.state_at = pos, now
        self.seek_hold = (pos, now, now + 2.5)
        self.render_player()
        self.do(lambda: self.sp.req("PUT", "/me/player/seek", params={"position_ms": int(pos)}),
                local=("position", f"{pos / 1000:.1f}") if self.is_local() else None)

    def action_volume(self, delta):
        if self.vol_target is not None and time.monotonic() < self.vol_until:
            base = self.vol_target
        else:
            base = (self.st("device") or {}).get("volume_percent")
            base = 50 if base is None else base
        self.vol_target = min(100, max(0, base + delta))
        self.vol_until = time.monotonic() + 5
        if self.state and self.state.get("device"):
            self.state["device"]["volume_percent"] = self.vol_target
        self.render_player()
        # wait until the key presses stop, then send only the final value
        if self.vol_timer:
            self.vol_timer.stop()
        self.vol_timer = self.set_timer(0.12 if self.is_local() else 0.35, self.send_volume)

    def send_volume(self):
        vol = self.vol_target
        self.vol_until = time.monotonic() + 5
        self.do(lambda: self.sp.req("PUT", "/me/player/volume", params={"volume_percent": vol}),
                local=("volume", f"{vol / 100:.2f}") if self.is_local() else None)

    def action_shuffle(self):
        new = not self.st("shuffle_state", False)
        self.hold("shuffle_state", new)
        self.do(lambda: self.sp.req("PUT", "/me/player/shuffle", params={"state": str(new).lower()}),
                "Shuffle " + ("on" if new else "off"))

    def action_repeat(self):
        nxt = {"off": "context", "context": "track", "track": "off"}[self.st("repeat_state", "off")]
        self.hold("repeat_state", nxt)
        self.do(lambda: self.sp.req("PUT", "/me/player/repeat", params={"state": nxt}),
                {"off": "Repeat off", "context": "Repeat all", "track": "Repeat this song"}[nxt])

    def action_queue_add(self):
        sel = self.selected()
        if sel and sel[1]["kind"] == "track":
            r = sel[1]
            self.do(lambda: self.sp.req("POST", "/me/player/queue", params={"uri": r["uri"]}),
                    f"Queued: {cut(r['name'], 40)}")

    def action_like(self):
        sel = self.selected()
        r = sel[1] if sel and sel[1]["kind"] in ("track", "album") else None
        if r is None and self.st("item"):
            r = {"uri": self.state["item"]["uri"], "name": self.state["item"].get("name", "")}
        if not r:
            return

        def go():
            has = (self.sp.get("/me/library/contains", uris=r["uri"]) or [False])[0]
            self.sp.req("DELETE" if has else "PUT", "/me/library", params={"uris": r["uri"]})
            self.liked[r["uri"]] = not has
            self.cache.pop("liked", None)
            self.call_from_thread(self.toast, ("Removed from" if has else "Added to") + f" Liked: {cut(r['name'], 36)}")
        self.do(go)

    def action_devices(self):
        self.fetch_devices()

    @work(thread=True, group="cmd")
    def fetch_devices(self):
        try:
            devs = self.sp.devices()
        except ApiError as e:
            self.call_from_thread(self.toast, str(e), "error")
            return
        self.call_from_thread(lambda: self.push_screen(DeviceScreen(devs), self.pick_device))

    def pick_device(self, dev_id):
        if dev_id:
            self.do(lambda: self.sp.req("PUT", "/me/player", body={"device_ids": [dev_id], "play": True}),
                    "Switched speaker")

    def action_help(self):
        self.push_screen(HelpScreen())

    # ---------- player state
    @work(thread=True, group="poll", exclusive=True)
    def poll_once(self):
        self._poll()

    def _poll(self):
        try:
            st = self.sp.get("/me/player")
        except ApiError as e:
            if e.status in (0, 401):
                self.call_from_thread(self.set_offline, str(e))
            return
        self.call_from_thread(self.set_state, st)

    def set_offline(self, msg):
        self.state = {"offline": msg}
        self.render_player()

    def set_state(self, st):
        now = time.monotonic()
        if st:
            if st.get("device") and self.vol_target is not None and now < self.vol_until:
                st["device"]["volume_percent"] = self.vol_target
            for key, (value, until) in list(self.holds.items()):
                if now < until:
                    st[key] = value
                else:
                    del self.holds[key]
            if self.seek_hold and now < self.seek_hold[2]:
                pos, at, _ = self.seek_hold
                st["progress_ms"] = pos + ((now - at) * 1000 if st.get("is_playing") else 0)
            dev = st.get("device") or {}
            if dev.get("id"):
                self.sp.active_id = dev["id"]
        self.state, self.state_at = st, now
        uri = ((st or {}).get("item") or {}).get("uri")
        if uri != self.playing_uri:
            old, self.playing_uri = self.playing_uri, uri
            self.mark_playing(old, uri)
        self.render_player()

    def progress(self):
        st = self.state or {}
        p = st.get("progress_ms") or 0
        if st.get("is_playing"):
            p += (time.monotonic() - self.state_at) * 1000
        dur = (st.get("item") or {}).get("duration_ms") or 0
        return min(p, dur) if dur else p

    def render_player(self):
        np = self.query_one("#np", Static)
        w = max(40, np.size.width - 4 if np.size.width else 100)
        st = self.state
        if st and st.get("offline"):
            np.update(Text.assemble(("\n" + st["offline"], RED)))
            return
        item = (st or {}).get("item")
        if not item:
            dev = self.sp.device_name
            np.update(Text.assemble(("\n" + I_MUSIC + "  Nothing playing", f"bold {FG}"),
                                    (f"   pick a song and press Enter (speaker: {dev})", GRAY)))
            return
        artists = ", ".join(a.get("name", "") for a in item.get("artists", [])) or \
            (item.get("show") or {}).get("name", "")
        playing = st.get("is_playing")
        heart = self.liked.get(item.get("uri"))
        line1 = Text.assemble(((I_PAUSE if playing else I_PLAY) + "  ", f"bold {GREEN if playing else YELLOW}"),
                              (cut(item.get("name", ""), w // 2), f"bold {FG}"),
                              ("   " + cut(artists, w // 3), CYAN),
                              (("   " + I_HEART) if heart else "", PINK))
        dur = item.get("duration_ms") or 1
        pos = self.progress()
        bar_w = max(10, w - 14)
        filled = int(bar_w * pos / dur)
        bar = Text.assemble((fmt_ms(pos) + " ", GRAY), ("━" * filled, ACCENT),
                            ("●", f"bold {FG}"), ("─" * max(0, bar_w - filled - 1), CUR),
                            (" " + fmt_ms(dur), GRAY))
        dev = st.get("device") or {}
        rep = st.get("repeat_state", "off")
        line3 = Text.assemble(
            (I_SHUF + " shuffle", GREEN if st.get("shuffle_state") else GRAY), "    ",
            ((I_REP1 if rep == "track" else I_REP) + (" repeat one" if rep == "track" else " repeat"),
             GREEN if rep != "off" else GRAY), "    ",
            (f"{I_VOL} {dev.get('volume_percent', '?')}%", FG), "    ",
            (f"{I_DEV} {dev.get('name', '?')}", GRAY),
            ("   " + cut((item.get("album") or {}).get("name", ""), w // 3), GRAY))
        np.update(Text("\n").join([line1, bar, line3]))


def main():
    if "--login" in sys.argv:
        login()
        return
    sp = Spotify()
    if not sp.ready():
        sys.exit("Not logged in yet. Run: spot.py --login")
    Spot(sp).run()


if __name__ == "__main__":
    main()
