#!/home/admin/.venvs/deck/bin/python
"""Deck YouTube: home, subscriptions, search, history, library and a listening queue, played with mpv.
Works without a Google account. Signed in (YouTube Data API, with a code entered on another device),
Subscriptions and Library come from your YouTube account and Home shows what is popular in your region.

Enter/Right watch (mpv full screen, q there returns here), a listen now, e add to the queue,
l add to a playlist, d/D download video/audio, s subscribe, c channel, I import subscriptions,
L sign in or out, Space pause, , . seek, - + volume, n p next/previous, x stop, / search,
1-6 or Tab/Shift+Tab switch views, Left/Esc back.

  tube.py --import subscriptions.csv     import a Google Takeout subscriptions file
  tube.py --login                        sign in to YouTube (needs a Google Cloud OAuth client, see README)
  tube.py --logout                       forget the login
"""
import csv
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

import deck_mpv
import deck_ui
deck_ui.install()

HOME = os.path.expanduser("~")
VENV_BIN = os.path.dirname(os.path.abspath(sys.executable))
RUN = f"/run/user/{os.getuid()}"
CONFIG = f"{HOME}/.config/deck/youtube.json"
API_CFG = f"{HOME}/.config/deck/youtube-api.json"         # OAuth client: never collected
TOKEN = f"{HOME}/.config/deck/youtube-token.json"         # Google login: secret, never collected
HISTORY = f"{HOME}/.local/share/deck/youtube-history.json"
SOCK = f"{RUN}/deck-tube.sock"
QUEUE_META = f"{RUN}/deck-tube-queue.json"     # titles and channels of queued videos
MPV_LOG = f"{RUN}/deck-tube-mpv.log"
VIDEO_DIR = f"{HOME}/Videos/YouTube"
AUDIO_DIR = f"{HOME}/Music/YouTube"

PURPLE, PINK, CYAN, GREEN = "#bd93f9", "#ff79c6", "#8be9fd", "#50fa7b"
ORANGE, YELLOW, RED, GRAY, FG = "#ffb86c", "#f1fa8c", "#ff5555", "#6272a4", "#f8f8f2"
try:
    ACCENT = json.load(open(f"{HOME}/.config/deck/prefs.json")).get("accent", PURPLE)
except Exception:
    ACCENT = PURPLE

I_YT, I_PLAY, I_PAUSE, I_LIST, I_DL = "", "", "", "", ""
TABS = [("home", "Home"), ("feed", "Subscriptions"), ("search", "Search"), ("history", "History"),
        ("playlists", "Library"), ("queue", "Queue")]


# ---------- storage ----------
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


def load_config():
    cfg = {"subs": [], "quality": 720, "hide_shorts": False, "playlists": {}, "region": ""}
    cfg.update(load_json(CONFIG, {}))
    return cfg


# ---------- formatting ----------
def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def fmt_time(s):
    if s is None:
        return ""
    s = int(s)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_views(n):
    if not n:
        return ""
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + f"{suf} views"
    return f"{n} views"


def fmt_age(ts):
    if not ts:
        return ""
    d = time.time() - ts
    for secs, unit in ((31536000, "year"), (2592000, "month"), (604800, "week"), (86400, "day"),
                       (3600, "hour"), (60, "minute")):
        if d >= secs:
            n = int(d // secs)
            return f"{n} {unit}{'s' if n > 1 else ''} ago"
    return "just now"


# ---------- YouTube data ----------
class QuietLog:
    """yt-dlp prints errors to stderr even when quiet, which garbles the screen. They come back as exceptions."""
    def debug(self, msg): pass
    info = warning = error = debug


def err_text(ex, n=120):
    """An error without yt-dlp's colors and its 'ERROR: [youtube:tab] id:' prefix."""
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(ex))
    msg = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?([\w-]+:\s)?", "", msg)
    return msg[:n]


def search(q, n=30):
    from yt_dlp import YoutubeDL     # slow import, so only when needed
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True,
            "logger": QuietLog()}
    with YoutubeDL(opts) as y:
        info = y.extract_info(f"ytsearch{n}:{q}", download=False)
    out = []
    for e in info.get("entries") or []:
        if not e.get("id") or e.get("ie_key", "Youtube") != "Youtube":
            continue
        url = e.get("url") or ""
        out.append({"id": e["id"], "title": e.get("title") or "?", "channel": e.get("channel") or e.get("uploader") or "",
                    "channel_id": e.get("channel_id") or "", "duration": e.get("duration"),
                    "views": e.get("view_count"), "short": "/shorts/" in url,
                    "live": e.get("live_status") == "is_live"})
    return out


# ---------- YouTube account (Data API v3, signed in with a code like a TV app) ----------
# Google's API has no home recommendations, watch history or Watch later, so those stay out.
SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
TOKEN_LOCK = threading.Lock()


class ApiError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


def http_json(url, form=None, token=None):
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=data, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as ex:
        try:
            body = json.load(ex)
        except ValueError:
            body = {}
        err = body.get("error")
        if isinstance(err, dict):        # API errors are an object, OAuth errors a plain code
            raise ApiError(re.sub(r"<[^>]+>", "", err.get("message") or str(ex)), err.get("status")) from None
        raise ApiError(body.get("error_description") or err or str(ex), err) from None


def save_secret(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(path + ".tmp", path)


def signed_in():
    return os.path.exists(TOKEN)


def device_start(client):
    """Ask Google for a sign-in code. Returns user_code, verification_url, device_code, interval, expires_in."""
    return http_json("https://oauth2.googleapis.com/device/code", {"client_id": client["client_id"], "scope": SCOPE})


def device_wait(client, start, cancelled):
    """Wait until the code is entered on another device and save the token. Returns an error message or None."""
    interval = start.get("interval", 5)
    deadline = time.time() + start.get("expires_in", 1800)
    while time.time() < deadline:
        for _ in range(interval * 4):
            if cancelled():
                return None
            time.sleep(0.25)
        try:
            tok = http_json("https://oauth2.googleapis.com/token", {
                "client_id": client["client_id"], "client_secret": client["client_secret"],
                "device_code": start["device_code"], "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        except ApiError as ex:
            if ex.code == "authorization_pending":
                continue
            if ex.code == "slow_down":
                interval += 5
                continue
            return "Sign-in was declined." if ex.code == "access_denied" else str(ex)
        except OSError as ex:
            return f"No connection: {ex}"
        tok["expires_at"] = time.time() + tok.get("expires_in", 3600)
        save_secret(TOKEN, tok)
        return None
    return "The code expired. Press L for a new one."


def access_token():
    with TOKEN_LOCK:
        tok = load_json(TOKEN, None)
        if not tok:
            raise ApiError("Not signed in. Press L.")
        if tok.get("expires_at", 0) - 60 > time.time():
            return tok["access_token"]
        client = load_json(API_CFG, {})
        try:
            new = http_json("https://oauth2.googleapis.com/token", {
                "client_id": client.get("client_id", ""), "client_secret": client.get("client_secret", ""),
                "refresh_token": tok.get("refresh_token", ""), "grant_type": "refresh_token"})
        except ApiError as ex:
            raise ApiError(f"The login expired or was removed ({ex}). Press L to sign in again.") from None
        new.setdefault("refresh_token", tok.get("refresh_token"))
        new["expires_at"] = time.time() + new.get("expires_in", 3600)
        save_secret(TOKEN, new)
        return new["access_token"]


def api_pages(path, limit, **params):
    """GET a YouTube Data API list, following pages until limit items."""
    items, page = [], None
    while len(items) < limit:
        q = dict(params, maxResults=50, **({"pageToken": page} if page else {}))
        try:
            r = http_json(f"https://www.googleapis.com/youtube/v3/{path}?" + urllib.parse.urlencode(q),
                          token=access_token())
        except ApiError as ex:
            if ex.code == "UNAUTHENTICATED":
                raise ApiError("YouTube rejected the login. Press L to sign in again.") from None
            raise
        items += r.get("items", [])
        page = r.get("nextPageToken")
        if not page:
            break
    return items[:limit]


def iso_duration(s):
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", s or "")
    if not m:
        return None
    d, h, mi, sec = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + sec


def iso_time(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def api_video(v):
    sn, st = v.get("snippet", {}), v.get("statistics", {})
    return {"id": v["id"], "title": sn.get("title") or "?", "channel": sn.get("channelTitle") or "",
            "channel_id": sn.get("channelId") or "", "duration": iso_duration(v.get("contentDetails", {}).get("duration")),
            "views": int(st["viewCount"]) if st.get("viewCount") else None, "published": iso_time(sn.get("publishedAt")),
            "short": False, "live": sn.get("liveBroadcastContent") == "live"}


VIDEO_PARTS = "snippet,contentDetails,statistics"


def api_videos(ids):
    """Full details (duration, views) for video ids, in the same order. Private and deleted ones drop out."""
    found = {}
    for i in range(0, len(ids), 50):
        for v in api_pages("videos", 50, part=VIDEO_PARTS, id=",".join(ids[i:i + 50])):
            found[v["id"]] = api_video(v)
    return [found[i] for i in ids if i in found]


def my_subscriptions():
    items = api_pages("subscriptions", 2000, part="snippet", mine="true", order="alphabetical")
    return [{"id": s["snippet"]["resourceId"]["channelId"], "name": s["snippet"]["title"]} for s in items]


def my_playlists():
    items = api_pages("playlists", 500, part="snippet,contentDetails", mine="true")
    return [{"_pl": p["snippet"]["title"], "_api": p["id"], "count": p.get("contentDetails", {}).get("itemCount")}
            for p in items]


def playlist_videos(pid, limit=500):
    if pid == "liked":
        return [api_video(v) for v in api_pages("videos", limit, part=VIDEO_PARTS, myRating="like")]
    items = api_pages("playlistItems", limit, part="contentDetails", playlistId=pid)
    return api_videos([i["contentDetails"]["videoId"] for i in items])


def region(cfg):
    m = re.match(r"[a-z]{2}_([A-Z]{2})", os.environ.get("LANG", ""))
    return (cfg.get("region") or (m.group(1) if m else "US")).upper()


def popular(cfg):
    return [api_video(v) for v in api_pages("videos", 50, part=VIDEO_PARTS, chart="mostPopular", regionCode=region(cfg))]


NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "m": "http://search.yahoo.com/mrss/"}


def channel_feed(cid):
    """Latest 15 uploads of a channel from its public RSS feed."""
    req = urllib.request.Request(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}",
                                 headers={"User-Agent": "Mozilla/5.0"})
    root = ET.fromstring(urllib.request.urlopen(req, timeout=15).read())
    name = root.findtext("a:title", "", NS)
    out = []
    for e in root.findall("a:entry", NS):
        link = e.find("a:link", NS)
        stats = e.find("m:group/m:community/m:statistics", NS)
        try:
            ts = datetime.fromisoformat(e.findtext("a:published", "", NS)).timestamp()
        except ValueError:
            ts = None
        out.append({"id": e.findtext("yt:videoId", "", NS), "title": e.findtext("a:title", "?", NS),
                    "channel": e.findtext("a:author/a:name", name, NS), "channel_id": cid, "duration": None,
                    "views": int(stats.get("views")) if stats is not None and stats.get("views") else None,
                    "published": ts, "short": link is not None and "/shorts/" in link.get("href", ""),
                    "live": False})
    return name, out


def import_takeout(path, cfg):
    """Add channels from a Google Takeout subscriptions.csv. Returns how many were new."""
    have = {s["id"] for s in cfg["subs"]}
    added = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            cid = next((x.strip() for x in row if re.fullmatch(r"UC[\w-]{22}", x.strip())), None)
            if cid and cid not in have:
                cfg["subs"].append({"id": cid, "name": row[-1].strip() or cid})
                have.add(cid)
                added += 1
    cfg["subs"].sort(key=lambda s: s["name"].lower())
    return added


def takeout_candidates():
    pats = [f"{HOME}/Downloads/**/subscriptions*.csv", f"{HOME}/subscriptions*.csv", f"{HOME}/Downloads/**/*.csv"]
    for p in pats:
        hits = sorted(glob.glob(p, recursive=True), key=os.path.getmtime, reverse=True)
        if hits:
            return hits[0]
    return ""


# ---------- mpv ----------
def player_env():
    env = os.environ.copy()
    env["PATH"] = f"{VENV_BIN}:{HOME}/.local/bin:" + env.get("PATH", "")   # yt-dlp and deno
    if not env.get("WAYLAND_DISPLAY"):           # tmux started over SSH has no display set
        socks = sorted((f for f in os.listdir(RUN) if re.fullmatch(r"wayland-\d+", f)), reverse=True)
        if socks:
            env["WAYLAND_DISPLAY"] = socks[0]
    env.setdefault("XDG_RUNTIME_DIR", RUN)
    return env


def ytdl_format(quality, audio_only=False):
    if audio_only:
        return "bestaudio[acodec^=opus]/bestaudio/best"
    # H.264 is the cheapest to decode in software, and the Pi 5 has no H.264 hardware decoder.
    return (f"bestvideo[height<=?{quality}][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
            f"bestvideo[height<=?{quality}]+bestaudio/best[height<=?{quality}]/best")


def url_of(v):
    return f"https://www.youtube.com/watch?v={v['id']}"


def sign_out():
    """Forget the login, and tell Google to cancel it (best effort)."""
    tok = load_json(TOKEN, {})
    if tok.get("refresh_token"):
        try:
            http_json("https://oauth2.googleapis.com/revoke", {"token": tok["refresh_token"]})
        except Exception:
            pass
    if os.path.exists(TOKEN):
        os.remove(TOKEN)


def video_cmd(v, quality):
    return ["mpv", f"--ytdl-format={ytdl_format(quality)}", f"--script-opts=ytdl_hook-ytdl_path={VENV_BIN}/yt-dlp",
            f"--force-media-title={v['title']}", "--hwdec=auto-safe", f"--log-file={MPV_LOG}",
            "--fs", "--keep-open=no", "--osd-level=1", url_of(v)]


def audio_cmd():
    """Background player: starts empty, gets its playlist over IPC, quits when the playlist ends."""
    return ["mpv", f"--ytdl-format={ytdl_format(0, True)}", f"--script-opts=ytdl_hook-ytdl_path={VENV_BIN}/yt-dlp",
            "--no-video", "--idle=once", f"--input-ipc-server={SOCK}", "--no-terminal", f"--log-file={MPV_LOG}"]


def mpv_quote(s):
    """mpv key=value lists split on commas; %len% quoting keeps any title intact."""
    return f"%{len(s.encode())}%{s}"


def ipc(*command):
    """Send one command to the background mpv. Returns its data (True if it has none),
    or None when mpv is not running or the command failed."""
    s = socket.socket(socket.AF_UNIX)
    try:
        s.settimeout(1)
        s.connect(SOCK)
        s.sendall((json.dumps({"command": list(command), "request_id": 1}) + "\n").encode())
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                return None
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                msg = json.loads(line)
                if msg.get("request_id") == 1:
                    if msg.get("error") != "success":
                        return None
                    return True if msg.get("data") is None else msg.get("data")
    except (OSError, ValueError):
        return None
    finally:
        s.close()


def vid_of(filename):
    m = re.search(r"[?&]v=([\w-]{11})", filename or "")
    return m.group(1) if m else None


# ---------- small modal screens ----------
MODAL = """
    {name} {{ align: center middle; background: #282a36 60%; }}
    {name} > Vertical {{ width: 70; max-width: 95%; height: auto; max-height: 90%; background: #21222c;
                       border: round {accent}; padding: 1 2; }}
    {name} OptionList {{ background: #21222c; border: none; height: auto; max-height: 20; }}
    {name} Input {{ background: #282a36; border: tall #44475a; }}
    {name} Input:focus {{ border: tall {accent}; }}
"""


class Picker(ModalScreen):
    """A list in a box. Returns the chosen option id, or None."""
    BINDINGS = [Binding("escape,left", "cancel", show=False), Binding("right", "pick", show=False)]
    DEFAULT_CSS = MODAL.format(name="Picker", accent=ACCENT)

    def __init__(self, title, options):
        super().__init__()
        self.title_, self.options = title, options

    def compose(self):
        with Vertical():
            yield Static(Text(self.title_ + "\n", style=f"bold {ACCENT}"))
            yield OptionList(*[Option(Text(label, style=FG), id=oid) for oid, label in self.options])

    def on_mount(self):
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, e):
        e.stop()
        self.dismiss(e.option.id)

    def action_pick(self):
        self.query_one(OptionList).action_select()

    def action_cancel(self):
        self.dismiss(None)


class Prompt(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", show=False)]
    DEFAULT_CSS = MODAL.format(name="Prompt", accent=ACCENT)

    def __init__(self, title, placeholder="", value=""):
        super().__init__()
        self.title_, self.placeholder, self.value = title, placeholder, value

    def compose(self):
        with Vertical():
            yield Static(Text(self.title_ + "\n", style=f"bold {ACCENT}"))
            yield Input(value=self.value, placeholder=self.placeholder)

    def on_mount(self):
        self.query_one(Input).focus()

    def on_input_submitted(self, e):
        e.stop()
        self.dismiss(e.value.strip())

    def action_cancel(self):
        self.dismiss(None)


class Confirm(ModalScreen):
    BINDINGS = [Binding("y", "yes", show=False), Binding("escape,n,left", "no", show=False)]
    DEFAULT_CSS = MODAL.format(name="Confirm", accent=RED)

    def __init__(self, msg):
        super().__init__()
        self.msg = msg

    def compose(self):
        with Vertical():
            yield Static(Text.assemble((self.msg, FG), "\n\n", ("y", f"bold {RED}"), (" yes   ", GRAY),
                                       ("n", f"bold {GREEN}"), (" no", GRAY)))

    def action_yes(self):
        self.dismiss(True)

    def action_no(self):
        self.dismiss(False)


class DeviceLogin(ModalScreen):
    """Shows the sign-in code and waits for it to be entered. Returns None (cancelled), "ok" or an error."""
    BINDINGS = [Binding("escape,left", "cancel", show=False)]
    DEFAULT_CSS = MODAL.format(name="DeviceLogin", accent=ACCENT)

    def __init__(self, client, start):
        super().__init__()
        self.client, self.start, self.cancelled = client, start, False

    def compose(self):
        with Vertical():
            yield Static(Text.assemble(
                ("Sign in to YouTube\n\n", f"bold {ACCENT}"), ("On your phone or computer, open\n\n   ", FG),
                (self.start.get("verification_url", "https://www.google.com/device"), f"bold {CYAN}"),
                ("\n\nand enter this code:\n\n   ", FG), (self.start["user_code"], f"bold {YELLOW}"),
                ("\n\nWaiting for you there. Esc cancels.", GRAY)))

    def on_mount(self):
        self.wait()

    @work(thread=True)
    def wait(self):
        err = device_wait(self.client, self.start, lambda: self.cancelled)
        if not self.cancelled:
            self.app.call_from_thread(self.dismiss, err or "ok")

    def action_cancel(self):
        self.cancelled = True
        self.dismiss(None)


HELP = [
    ("Videos", [("Enter  Right", "watch full screen (q in the player comes back)"), ("a", "listen now, in the background"),
                ("e", "add to the listening queue"), ("l", "add to a playlist"),
                ("d  D", "download video / audio only"), ("s", "subscribe or unsubscribe the channel"),
                ("c", "open the channel")]),
    ("Listening", [("Space", "pause or play"), (",  .", "back / forward 10 seconds"), ("-  +", "volume"),
                   ("n  p", "next / previous in the queue"), ("x", "stop"), ("F6 F7 F8", "media keys work too")]),
    ("Library and queue", [("Enter", "open a playlist, or jump to a queued video"), ("A", "listen to the whole playlist"),
                           ("Delete", "remove the video, or delete the playlist (deck playlists only)")]),
    ("Views", [("1 to 6  Tab", "home, subscriptions, search, history, library, queue"), ("/", "search"),
               ("Left  Esc", "back"), ("r", "refresh"), ("L", "sign in to YouTube, or sign out"),
               ("I", "import subscriptions from Google Takeout"), ("q", "quit")]),
]


class Help(ModalScreen):
    BINDINGS = [Binding("escape,q,question_mark,left", "dismiss", show=False)]
    DEFAULT_CSS = """
    Help { align: center middle; background: #282a36 60%; }
    Help > Static { width: 76; height: auto; max-height: 100%; background: #21222c; border: round #bd93f9; padding: 1 2; }
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

    def on_click(self, _):
        self.dismiss()


# ---------- app ----------
class Tube(App):
    TITLE = "YouTube"
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
        Binding("1", "jump('home')", show=False), Binding("2", "jump('feed')", show=False),
        Binding("3", "jump('search')", show=False), Binding("4", "jump('history')", show=False),
        Binding("5", "jump('playlists')", show=False), Binding("6", "jump('queue')", show=False),
        Binding("r", "refresh", show=False), Binding("L", "account", show=False),
        Binding("right", "watch", show=False), Binding("left", "back", show=False),
        Binding("a", "listen", show=False), Binding("e", "enqueue", show=False),
        Binding("A", "listen_all", show=False), Binding("l", "add_to_playlist", show=False),
        Binding("delete,backspace", "remove", show=False),
        Binding("d", "download(False)", show=False), Binding("D", "download(True)", show=False),
        Binding("s", "subscribe", show=False), Binding("c", "channel", show=False),
        Binding("I", "import_subs", show=False), Binding("x", "stop", show=False),
        Binding("n", "skip('next')", show=False), Binding("p", "skip('prev')", show=False),
        Binding("space", "toggle", show=False), Binding("comma", "seek(-10)", show=False),
        Binding("full_stop", "seek(10)", show=False), Binding("minus", "volume(-5)", show=False),
        Binding("plus,equals_sign", "volume(5)", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.history = load_json(HISTORY, [])
        self.queue_meta = load_json(QUEUE_META, {})
        self.view = None            # (kind, title, items)
        self.stack = []             # earlier views for Left/Esc
        self.search_results = []
        self.last_query = ""
        self.rows = {}
        self.feed_cache = None
        self.account = {}           # "home", "subs", "playlists" from the YouTube account
        self.loading = set()
        self.video_proc = None
        self.queue_sig = None

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        yield Input(placeholder="search YouTube, then Enter", id="search")
        yield Static(id="status")
        yield OptionList(id="list")
        yield Static(id="now")
        yield Static(Text.assemble(("Enter", CYAN), (" watch  ", GRAY), ("a", CYAN), (" listen  ", GRAY),
                                   ("e", CYAN), (" queue  ", GRAY), ("l", CYAN), (" playlist  ", GRAY),
                                   ("s", CYAN), (" subscribe  ", GRAY), ("Tab", CYAN), (" views  ", GRAY),
                                   ("?", CYAN), (" help", GRAY)), id="hint")

    def on_mount(self):
        self.jump("feed" if signed_in() or self.cfg["subs"] else ("history" if self.history else "search"))
        self.set_interval(1, self.tick)
        self.tick()

    @property
    def main(self):
        """The app's own screen, even while a modal is on top."""
        return self.screen_stack[0]

    def save_cfg(self):
        save_json(CONFIG, self.cfg)

    # ----- views -----
    def jump(self, kind):
        self.stack = []
        if kind == "home":
            self.show_home()
        elif kind == "feed":
            self.show_feed()
        elif kind == "history":
            self.show("history", "History", self.history)
            self.status(plural(len(self.history), "video") if self.history else "Nothing watched yet.")
        elif kind == "playlists":
            self.show_playlists()
        elif kind == "queue":
            self.show_queue()
        else:
            self.show("search", "Search", self.search_results)
            self.main.query_one("#search").focus()
            self.status(f"{len(self.search_results)} results for {self.last_query}" if self.search_results
                        else "Type what you want to find, then Enter.")

    def action_jump(self, kind):
        self.jump(kind)

    def action_cycle(self, d):
        if isinstance(self.screen, ModalScreen):
            return
        kinds = [k for k, _ in TABS]
        cur = self.view[0] if self.view else "home"
        if cur not in kinds:                         # inside a channel or playlist: use its tab
            cur = next((v[0] for v in reversed(self.stack) if v[0] in kinds), "home")
        self.jump(kinds[(kinds.index(cur) + d) % len(kinds)])

    def show(self, kind, title, items, push=False):
        if push and self.view:
            self.stack.append(self.view)
        self.view = (kind, title, items)
        if kind in dict(TABS):
            head = Text.assemble((I_YT + "  ", RED), ("YouTube", f"bold {ACCENT}"), "  ")
            for i, (k, name) in enumerate(TABS, 1):
                head.append(f" {i} {name} ", f"bold {ACCENT}" if k == kind else GRAY)
        else:
            head = Text.assemble((I_YT + "  ", RED), (title, f"bold {ACCENT}"), ("   Left goes back", GRAY))
        self.main.query_one("#title").update(head)
        self.main.query_one("#search").display = kind == "search"
        self.fill(items, keep=0)
        if kind != "search" or items:
            self.main.query_one("#list").focus()

    def fill(self, items, keep=None):
        if self.cfg.get("hide_shorts"):
            items = [v for v in items if not v.get("short")]
        ol = self.main.query_one("#list", OptionList)
        if keep is None:
            keep = ol.highlighted
        ol.clear_options()
        self.rows = {}
        subs = {s["id"] for s in self.all_subs()}
        opts = []
        for i, v in enumerate(items):
            key = f"{i}:{v.get('id') or v.get('_pl')}"
            self.rows[key] = v
            opts.append(Option(self.render_playlist(v) if "_pl" in v else self.render_video(v, subs), id=key))
        ol.add_options(opts)
        if opts:
            ol.highlighted = min(keep or 0, ol.option_count - 1)
            if ol.get_option_at_index(ol.highlighted).disabled:
                ol.highlighted = max(0, ol.highlighted - 1)

    def render_video(self, v, subs):
        playing = v.get("_current")
        t = Text.assemble((I_PLAY + "  ", GREEN if playing else RED), (v["title"], f"bold {GREEN if playing else FG}"))
        meta = []
        if playing:
            meta.append(("now playing", f"bold {GREEN}"))
        if v.get("live"):
            meta.append(("LIVE", f"bold {RED}"))
        elif v.get("short"):
            meta.append(("Short", PINK))
        elif v.get("duration"):
            meta.append((fmt_time(v["duration"]), YELLOW))
        if v.get("views"):
            meta.append((fmt_views(v["views"]), GRAY))
        if v.get("published"):
            meta.append((fmt_age(v["published"]), GRAY))
        if v.get("watched") and self.view and self.view[0] == "history":
            meta.append((f"watched {fmt_age(v['watched'])}", GRAY))
        line = Text("\n   ")
        line.append(v.get("channel") or "", CYAN if v.get("channel_id") in subs else GRAY)
        for text, style in meta:
            line.append("  ·  ", GRAY)
            line.append(text, style)
        return t + line

    def render_playlist(self, p):
        if "_api" in p:
            sub = "on YouTube" + (f"  ·  {plural(p['count'], 'video')}" if p.get("count") is not None else "")
            return Text.assemble((I_LIST + "  ", RED), (p["_pl"], f"bold {FG}"), ("\n   " + sub, GRAY))
        return Text.assemble((I_LIST + "  ", PURPLE), (p["_pl"], f"bold {FG}"),
                             ("\n   on the deck  ·  " + plural(p["count"], "video"), GRAY))

    def status(self, msg):
        self.main.query_one("#status").update(msg)

    def current(self):
        ol = self.main.query_one("#list", OptionList)
        if ol.highlighted is None or not self.rows:
            return None
        return self.rows.get(ol.get_option_at_index(ol.highlighted).id)

    def current_video(self):
        v = self.current()
        return v if v and "_pl" not in v else None

    def index_of_current(self):
        ol = self.main.query_one("#list", OptionList)
        if ol.highlighted is None:
            return None
        return int(ol.get_option_at_index(ol.highlighted).id.split(":", 1)[0])

    # ----- the YouTube account -----
    def all_subs(self):
        """Subscriptions on the YouTube account plus channels followed only on the deck."""
        subs, seen = [], set()
        for s in (self.account.get("subs") or []) + self.cfg["subs"]:
            if s["id"] not in seen:
                seen.add(s["id"])
                subs.append(s)
        return subs

    def load_account(self, kind, fn):
        if kind in self.loading:
            return
        self.loading.add(kind)

        def run():
            try:
                items, err = fn(), None
            except Exception as ex:
                items, err = None, err_text(ex)
            self.call_from_thread(self._account_done, kind, items, err)
        self.run_worker(run, thread=True, group=f"account-{kind}")

    def _account_done(self, kind, items, err):
        self.loading.discard(kind)
        here = self.view and self.view[0] == {"subs": "feed"}.get(kind, kind)
        if err:
            if here:
                self.status(f"Could not load from YouTube: {err}")
            return
        self.account[kind] = items
        if kind == "subs":
            self.feed_cache = None
            if here:
                self.show_feed()
        elif kind == "playlists":
            if here:
                self.show_playlists()
        elif here and not self.stack:
            self.view = (kind, self.view[1], items)
            self.fill(items, keep=0)
            self.home_status()

    def show_home(self, refresh=False):
        if not signed_in():
            self.show("home", "Home", [])
            self.status("Press L to sign in to YouTube. The README explains the one-time setup.")
            return
        if refresh:
            self.account.pop("home", None)
        self.show("home", "Home", self.account.get("home") or [])
        if "home" in self.account:
            self.home_status()
        else:
            self.status("Loading...")
            self.load_account("home", lambda: popular(self.cfg))

    def home_status(self):
        self.status(f"Popular in {region(self.cfg)}   (Google's API has no personal recommendations)")

    def action_account(self):
        if signed_in():
            def picked(oid):
                if oid == "out":
                    self.push_screen(Confirm("Sign out of YouTube on the deck?"), signed_out)

            def signed_out(yes):
                if yes:
                    sign_out()
                    self.account = {}
                    self.feed_cache = None
                    self.notify("Signed out", timeout=2)
                    self.jump("feed")
            self.push_screen(Picker("YouTube account", [("out", "Sign out")]), picked)
            return
        client = load_json(API_CFG, {})
        if client.get("client_id") and client.get("client_secret"):
            return self.start_login(client)

        def got_id(cid):
            if cid:
                self.push_screen(Prompt("OAuth client secret", "GOCSPX-..."), lambda sec: got_secret(cid, sec))

        def got_secret(cid, sec):
            if sec:
                client = {"client_id": cid, "client_secret": sec}
                save_secret(API_CFG, client)
                self.start_login(client)
        self.push_screen(Prompt("OAuth client ID (Google Cloud, type TVs and Limited Input devices)",
                                "...apps.googleusercontent.com"), got_id)

    @work(thread=True, exclusive=True, group="signin")
    def start_login(self, client):
        self.call_from_thread(self.status, "Asking Google for a sign-in code...")
        try:
            start = device_start(client)
        except (ApiError, OSError) as ex:
            self.call_from_thread(self.status, f"Could not start the sign-in: {err_text(ex)}")
            return
        self.call_from_thread(self.push_screen, DeviceLogin(client, start), self._login_done)

    def _login_done(self, res):
        if res is None:
            self.status("Sign-in cancelled.")
        elif res != "ok":
            self.status(res)
            self.notify(res, severity="error", timeout=6)
        else:
            self.notify("Signed in to YouTube", timeout=3)
            self.account = {}
            self.feed_cache = None
            self.jump("feed")

    # ----- subscriptions feed -----
    def show_feed(self, refresh=False):
        if signed_in() and ("subs" not in self.account or refresh):
            self.account.pop("subs", None)
            self.show("feed", "Subscriptions", self.feed_cache or [])
            self.status("Loading your subscriptions...")
            self.load_account("subs", my_subscriptions)
            return
        subs = self.all_subs()
        n = len(subs)
        if not n:
            self.show("feed", "Subscriptions", [])
            self.status("No subscriptions yet. Press s on a video, I to import from Google Takeout, or L to sign in.")
            return
        if self.feed_cache is not None and not refresh:
            self.show("feed", "Subscriptions", self.feed_cache)
            self.status(f"{len(self.feed_cache)} videos from {plural(n, 'channel')}")
            return
        self.show("feed", "Subscriptions", self.feed_cache or [])
        self.status(f"Loading {plural(n, 'channel')}...")
        self.load_feed(subs)

    @work(thread=True, exclusive=True, group="feed")
    def load_feed(self, subs):
        """New uploads from each channel's RSS feed, which costs no API quota."""
        videos, failed = [], 0
        with ThreadPoolExecutor(8) as ex:
            for res in ex.map(lambda s: self._safe_feed(s["id"]), subs):
                if res is None:
                    failed += 1
                else:
                    videos += res
        videos.sort(key=lambda v: v.get("published") or 0, reverse=True)
        self.call_from_thread(self._feed_done, videos[:300], failed, len(subs))

    def _safe_feed(self, cid):
        try:
            return channel_feed(cid)[1]
        except Exception:
            return None

    def _feed_done(self, videos, failed, n):
        self.feed_cache = videos
        if self.view and self.view[0] == "feed":
            self.show("feed", "Subscriptions", videos)
        msg = f"{len(videos)} videos from {plural(n, 'channel')}"
        self.status(msg + (f", {failed} could not load" if failed else ""))

    # ----- search -----
    def action_search(self):
        if not self.view or self.view[0] != "search":
            self.jump("search")
        inp = self.main.query_one("#search", Input)
        inp.focus()
        inp.action_select_all()

    def on_input_submitted(self, e):
        if e.input.id != "search" or not e.value.strip():
            return
        self.last_query = e.value.strip()
        self.status(f"Searching for {self.last_query}...")
        self.do_search(self.last_query)

    @work(thread=True, exclusive=True, group="search")
    def do_search(self, q):
        try:
            res = search(q)
        except Exception as ex:
            self.call_from_thread(self.status, f"Search failed: {err_text(ex)}")
            return
        self.call_from_thread(self._search_done, q, res)

    def _search_done(self, q, res):
        self.search_results = res
        if self.view and self.view[0] == "search":
            self.show("search", "Search", res)
            self.main.query_one("#list").focus()
        self.status(f"{len(res)} results for {q}" if res else "Nothing found.")

    # ----- keys that depend on focus -----
    def on_key(self, event):
        inp = self.main.query_one("#search", Input)
        ol = self.main.query_one("#list", OptionList)
        if isinstance(self.screen, ModalScreen):
            return
        if inp.has_focus:
            if event.key == "down" and ol.option_count:
                ol.focus()
                event.stop()
            elif event.key == "escape":
                if ol.option_count:
                    ol.focus()
                event.stop()
        elif ol.has_focus and event.key == "up" and ol.highlighted == 0 and inp.display:
            inp.focus()
            event.stop()

    def check_action(self, action, parameters):
        # While typing a search, letters and numbers belong to the input box.
        if self.main.query_one("#search", Input).has_focus and action not in ("back", "cycle"):
            return False
        return True

    def action_back(self):
        if self.stack:
            prev = self.stack.pop()
            self.view = None
            if prev[0] == "playlists":
                self.show_playlists()
            else:
                self.show(*prev[:3])
                self.view = prev             # an open YouTube playlist keeps its source as a 4th item
        elif self.view and self.view[0] == "search" and not self.main.query_one("#search").has_focus:
            self.main.query_one("#search").focus()

    def action_refresh(self):
        kind = self.view[0] if self.view else ""
        if kind == "home":
            self.show_home(refresh=True)
        elif kind == "playlists" and signed_in():
            self.account.pop("playlists", None)
            self.show_playlists()
        elif kind == "rplaylist":
            self.open_playlist(self.view[3], refresh=True)
        elif kind == "feed":
            self.show_feed(refresh=True)
        elif kind == "channel" and self.view[2]:
            self.open_channel(self.view[2][0]["channel_id"])
        elif kind == "search" and self.last_query:
            self.do_search(self.last_query)
        elif kind == "queue":
            self.show_queue()

    # ----- channels and subscriptions -----
    def action_channel(self):
        v = self.current_video()
        if not v or not v.get("channel_id"):
            return
        self.status(f"Loading {v.get('channel') or 'channel'}...")
        self.open_channel(v["channel_id"])

    @work(thread=True, exclusive=True, group="channel")
    def open_channel(self, cid):
        try:
            name, videos = channel_feed(cid)
        except Exception as ex:
            self.call_from_thread(self.status, f"Could not load the channel: {err_text(ex, 100)}")
            return
        self.call_from_thread(self._channel_done, cid, name, videos)

    def _channel_done(self, cid, name, videos):
        subbed = any(s["id"] == cid for s in self.cfg["subs"])
        self.show("channel", name, videos, push=self.view is not None and self.view[0] != "channel")
        self.status(f"Latest {len(videos)} uploads" + ("   subscribed" if subbed else "   s to subscribe"))

    def action_subscribe(self):
        v = self.current_video()
        if not v or not v.get("channel_id"):
            return
        if any(s["id"] == v["channel_id"] for s in self.account.get("subs") or []):
            self.notify("You are subscribed on YouTube. Unsubscribe on youtube.com.", timeout=3)
            return
        subs = self.cfg["subs"]
        hit = [s for s in subs if s["id"] == v["channel_id"]]
        if hit:
            subs.remove(hit[0])
            self.notify(f"Unsubscribed from {hit[0]['name']}", timeout=2)
        else:
            subs.append({"id": v["channel_id"], "name": v.get("channel") or v["channel_id"]})
            subs.sort(key=lambda s: s["name"].lower())
            self.notify(f"Subscribed to {v.get('channel')}"
                        + (" on the deck only. Subscribe on youtube.com to add it to your account." if signed_in() else ""),
                        timeout=4 if signed_in() else 2)
        self.save_cfg()
        self.feed_cache = None
        self.fill(self.view[2])

    def action_import_subs(self):
        def done(path):
            if not path:
                return
            path = os.path.expanduser(path)
            try:
                n = import_takeout(path, self.cfg)
            except OSError as ex:
                self.notify(f"Could not read {path}: {ex.strerror}", severity="error", timeout=5)
                return
            self.save_cfg()
            self.feed_cache = None
            self.notify(f"Imported {plural(n, 'new channel')}", timeout=3)
            self.jump("feed")
        self.push_screen(Prompt("Import subscriptions (subscriptions.csv from Google Takeout)",
                                "path to the file", takeout_candidates()), done)

    # ----- playlists -----
    def show_playlists(self):
        pls = self.cfg["playlists"]
        items = []
        if signed_in():
            items = [{"_pl": "Liked videos", "_api": "liked"}] + (self.account.get("playlists") or [])
        items += [{"_pl": name, "count": len(v)} for name, v in pls.items()]
        self.show("playlists", "Library", items)
        if signed_in() and "playlists" not in self.account:
            self.status("Loading your YouTube playlists...")
            self.load_account("playlists", my_playlists)
        else:
            self.status(plural(len(items), "playlist") if items else "No playlists yet. Press l on a video to start one.")

    def open_playlist(self, p, refresh=False):
        if "_api" not in p:
            videos = self.cfg["playlists"].get(p["_pl"], [])
            self.show("playlist", p["_pl"], videos, push=True)
            self.status(f"{plural(len(videos), 'video')}   A listens to all, Delete removes one")
            return
        self.status(f"Loading {p['_pl']}...")
        self.load_playlist(p, refresh)

    @work(thread=True, exclusive=True, group="playlist")
    def load_playlist(self, p, refresh):
        try:
            videos = playlist_videos(p["_api"])
        except Exception as ex:
            self.call_from_thread(self.status, f"Could not load {p['_pl']}: {err_text(ex, 100)}")
            return
        self.call_from_thread(self._playlist_done, p, videos, refresh)

    def _playlist_done(self, p, videos, refresh):
        if refresh and self.view and self.view[0] == "rplaylist":
            self.view = ("rplaylist", p["_pl"], videos, p)
            self.fill(videos)
        else:
            self.show("rplaylist", p["_pl"], videos, push=True)
            self.view = ("rplaylist", p["_pl"], videos, p)
        self.status(f"{plural(len(videos), 'video')} on YouTube   A listens to all")

    def action_add_to_playlist(self):
        v = self.current_video()
        if not v:
            return
        clean = {k: val for k, val in v.items() if not k.startswith("_") and k != "watched"}

        def add(name):
            if not name:
                return
            lst = self.cfg["playlists"].setdefault(name, [])
            if any(x["id"] == clean["id"] for x in lst):
                self.notify(f"Already in {name}", timeout=2)
                return
            lst.append(clean)
            self.save_cfg()
            self.notify(f"Added to {name}", timeout=2)

        def picked(oid):
            if oid == "__new__":
                self.push_screen(Prompt("New playlist", "name"), add)
            elif oid:
                add(oid[3:])
        opts = [(f"pl:{name}", f"{name}  ({len(lst)})") for name, lst in self.cfg["playlists"].items()]
        self.push_screen(Picker("Add to playlist", opts + [("__new__", "+ New playlist")]), picked)

    def action_remove(self):
        kind = self.view[0] if self.view else ""
        cur = self.current()
        if not cur:
            return
        if kind == "rplaylist" or (kind == "playlists" and "_api" in cur):
            self.notify("That is on your YouTube account. Change it on youtube.com.", timeout=3)
        elif kind == "playlists":
            name = cur["_pl"]

            def done(yes):
                if yes:
                    self.cfg["playlists"].pop(name, None)
                    self.save_cfg()
                    self.show_playlists()
            self.push_screen(Confirm(f"Delete the playlist {name}?"), done)
        elif kind == "playlist":
            lst = self.cfg["playlists"].get(self.view[1], [])
            i = self.index_of_current()
            if i is not None and i < len(lst):
                lst.pop(i)
                self.save_cfg()
                self.fill(lst)
                self.status(plural(len(lst), "video"))
        elif kind == "queue" and self.index_of_current() is not None:
            ipc("playlist-remove", self.index_of_current())
            self.show_queue()
        elif kind == "history":
            self.history = [h for h in self.history if h["id"] != cur["id"]]
            save_json(HISTORY, self.history)
            self.fill(self.history)

    # ----- queue (the background player's playlist) -----
    def queue_items(self):
        pl = ipc("get_property", "playlist")
        if not isinstance(pl, list):
            return []
        items = []
        for e in pl:
            vid = vid_of(e.get("filename"))
            v = dict(self.queue_meta.get(vid) or {"id": vid or "?", "title": e.get("title") or e.get("filename")})
            v["_current"] = bool(e.get("current"))
            items.append(v)
        return items

    def show_queue(self):
        items = self.queue_items()
        self.queue_sig = [(v["id"], v["_current"]) for v in items]
        if self.view and self.view[0] == "queue":
            self.view = ("queue", "Queue", items)
            self.fill(items)
        else:
            self.show("queue", "Queue", items)
        self.status(f"{plural(len(items), 'video')}   Enter plays one, Delete removes it" if items
                    else "The queue is empty. Press e on a video to add it, or a to listen now.")

    def start_player(self):
        """Start the background mpv if needed and wait for it to answer."""
        if ipc("get_property", "idle-active") is not None:
            return True
        subprocess.Popen(audio_cmd(), env=player_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)   # keeps playing if this app closes
        for _ in range(50):
            time.sleep(0.1)
            if ipc("get_property", "idle-active") is not None:
                return True
        return False

    @work(thread=True, exclusive=True, group="player")
    def queue_videos(self, videos, replace):
        if not shutil.which("mpv"):
            self.call_from_thread(self.notify, "mpv is not installed: sudo apt install mpv mpv-mpris",
                                  severity="error", timeout=6)
            return
        if not self.start_player():
            self.call_from_thread(self.notify, f"The player did not start. Details: {MPV_LOG}", severity="error")
            return
        for i, v in enumerate(videos):
            clean = {k: val for k, val in v.items() if not k.startswith("_") and k != "watched"}
            self.queue_meta[v["id"]] = clean
            mode = "replace" if (replace and i == 0) else "append-play"
            ipc("loadfile", url_of(v), mode, -1, f"force-media-title={mpv_quote(v['title'])}")
        save_json(QUEUE_META, self.queue_meta)
        if replace:
            deck_mpv.pause_others(SOCK)
            ipc("set_property", "pause", False)
        self.call_from_thread(self.tick)

    def action_listen(self):
        v = self.current_video()
        if v:
            self.remember(v)
            self.queue_videos([v], True)
            self.notify(f"Listening to {v['title']}", timeout=2)

    @work(thread=True, group="player-fetch")
    def queue_remote(self, p, replace):
        try:
            videos = playlist_videos(p["_api"])
        except Exception as ex:
            self.call_from_thread(self.notify, f"Could not load {p['_pl']}: {err_text(ex, 100)}", severity="error")
            return
        if videos:
            self.call_from_thread(self.notify, f"{'Listening to' if replace else 'Queued'} {plural(len(videos), 'video')}",
                                  timeout=2)
            self.queue_videos(videos, replace)

    def action_enqueue(self):
        cur = self.current()
        if not cur:
            return
        if "_api" in cur:
            return self.queue_remote(cur, False)
        videos = self.cfg["playlists"].get(cur["_pl"], []) if "_pl" in cur else [cur]
        if videos:
            self.queue_videos(videos, False)
            self.notify(f"Queued {videos[0]['title'] if len(videos) == 1 else plural(len(videos), 'video')}", timeout=2)

    def action_listen_all(self):
        kind = self.view[0] if self.view else ""
        cur = self.current()
        if kind == "playlist":
            videos = self.view[2]
        elif cur and "_api" in cur:
            return self.queue_remote(cur, True)
        elif cur and "_pl" in cur:
            videos = self.cfg["playlists"].get(cur["_pl"], [])
        else:
            videos = [v for v in (self.view[2] if self.view else []) if "_pl" not in v]
        if videos:
            self.queue_videos(videos, True)
            self.notify(f"Listening to {plural(len(videos), 'video')}", timeout=2)

    # ----- playback -----
    def remember(self, v):
        clean = {k: val for k, val in v.items() if not k.startswith("_")}
        entry = dict(clean, watched=time.time())
        self.history = [entry] + [h for h in self.history if h["id"] != v["id"]][:199]
        save_json(HISTORY, self.history)

    def on_option_list_option_selected(self, e):
        if e.option_list.id == "list":
            self.action_watch()

    def action_watch(self):
        cur = self.current()
        kind = self.view[0] if self.view else ""
        if not cur:
            return
        if "_pl" in cur:
            return self.open_playlist(cur)
        if kind == "queue":
            deck_mpv.pause_others(SOCK)
            ipc("playlist-play-index", self.index_of_current())
            ipc("set_property", "pause", False)
            return self.show_queue()
        if not shutil.which("mpv"):
            self.notify("mpv is not installed: sudo apt install mpv mpv-mpris", severity="error", timeout=6)
            return
        if self.video_proc and self.video_proc.poll() is None:
            self.notify("A video is already playing", timeout=2)
            return
        deck_mpv.pause_others()          # every background player, this one included, and Spotify
        self.remember(cur)
        self.status(f"Opening {cur['title']}...")
        proc = self.video_proc = subprocess.Popen(video_cmd(cur, self.cfg["quality"]), env=player_env(),
                                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                  stderr=subprocess.DEVNULL)
        started = time.time()

        def wait():
            code = proc.wait()
            msg = ""
            if code not in (0, 4) and time.time() - started < 20:    # 4 = quit by the user
                msg = "Could not play this video. Details: " + MPV_LOG
            self.call_from_thread(self.status, msg)
        threading.Thread(target=wait, daemon=True).start()

    def action_toggle(self):
        if ipc("cycle", "pause") is None:
            self.notify("Nothing is playing in the background", timeout=1.5)
        elif ipc("get_property", "pause") is False:
            deck_mpv.pause_others(SOCK)
        self.tick()

    def action_seek(self, secs):
        ipc("seek", secs, "relative")
        self.tick()

    def action_skip(self, which):
        if ipc(f"playlist-{which}") is None:
            self.notify("No more videos in the queue" if which == "next" else "This is the first video", timeout=1.5)
        self.tick()

    def action_volume(self, d):
        ipc("add", "volume", d)
        vol = ipc("get_property", "volume")
        if vol is not None:
            self.notify(f"Volume {vol:.0f}%", timeout=1)

    def action_stop(self):
        if ipc("quit"):
            self.notify("Stopped", timeout=1)
        self.tick()

    def tick(self):
        """Every second: the now-playing bar, and the queue view if the playlist moved on."""
        now = self.main.query_one("#now")
        title = ipc("get_property", "media-title")
        if self.view and self.view[0] == "queue" and not isinstance(self.screen, ModalScreen):
            items = self.queue_items()
            if [(v["id"], v["_current"]) for v in items] != self.queue_sig:
                self.show_queue()
        if title is None or ipc("get_property", "idle-active") is True:
            now.display = False
            return
        pos = ipc("get_property", "time-pos") or 0
        dur = ipc("get_property", "duration")
        paused = ipc("get_property", "pause")
        count = ipc("get_property", "playlist-count") or 1
        idx = ipc("get_property", "playlist-pos")
        w = max(10, self.size.width - 8)
        t = Text.assemble(((I_PAUSE if paused is True else I_PLAY) + "  ", GRAY if paused is True else RED),
                          (title[: w - 30], f"bold {FG}"), ("   " + fmt_time(pos), CYAN),
                          (" / " + fmt_time(dur) if dur else "", GRAY))
        if isinstance(count, int) and count > 1 and isinstance(idx, int):
            t.append(f"   {idx + 1} of {count}", GRAY)
        bar = Text()
        if dur:
            filled = int(w * min(1, pos / dur))
            bar.append("━" * filled, ACCENT)
            bar.append("─" * (w - filled), "#44475a")
        now.update(Text.assemble(bar, "\n", t))
        now.display = True

    # ----- downloads -----
    def action_download(self, audio):
        v = self.current_video()
        if not v:
            return
        self.notify(f"Downloading {'audio of ' if audio else ''}{v['title']}", timeout=2)
        self.download(v, audio)

    @work(thread=True, group="download")
    def download(self, v, audio):
        out = AUDIO_DIR if audio else VIDEO_DIR
        os.makedirs(out, exist_ok=True)
        cmd = [f"{VENV_BIN}/yt-dlp", "-q", "--no-progress", "-o", f"{out}/%(title)s [%(id)s].%(ext)s"]
        if audio:
            cmd += ["-f", "bestaudio[acodec^=mp4a]/bestaudio", "-x", "--audio-format", "m4a", "--embed-metadata"]
        else:
            cmd += ["-f", ytdl_format(self.cfg["quality"]), "--merge-output-format", "mkv", "--embed-metadata"]
        r = subprocess.run(cmd + [url_of(v)], env=player_env(), capture_output=True, text=True)
        where = "~/Music/YouTube" if audio else "~/Videos/YouTube"
        if r.returncode == 0:
            self.call_from_thread(self.notify, f"Saved {v['title']} to {where}", timeout=4)
        else:
            err = (r.stderr.strip().splitlines() or ["unknown error"])[-1]
            self.call_from_thread(self.notify, f"Download failed: {err[:120]}", severity="error", timeout=6)

    def action_help(self):
        self.push_screen(Help())


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--import":
        cfg = load_config()
        n = import_takeout(sys.argv[2], cfg)
        save_json(CONFIG, cfg)
        print(f"Imported {plural(n, 'new channel')}, {len(cfg['subs'])} in total.")
        return
    if len(sys.argv) == 2 and sys.argv[1] == "--login":
        client = load_json(API_CFG, {})
        if not (client.get("client_id") and client.get("client_secret")):
            client = {"client_id": input("OAuth client ID: ").strip(), "client_secret": input("OAuth client secret: ").strip()}
            save_secret(API_CFG, client)
        start = device_start(client)
        print(f"On your phone or computer, open {start.get('verification_url')} and enter {start['user_code']}")
        err = device_wait(client, start, lambda: False)
        print(err or "Signed in.")
        sys.exit(1 if err else 0)
    if len(sys.argv) == 2 and sys.argv[1] == "--logout":
        sign_out()
        print("Signed out.")
        return
    Tube().run()


if __name__ == "__main__":
    main()
