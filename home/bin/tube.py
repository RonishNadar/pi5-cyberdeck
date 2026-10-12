#!/home/admin/.venvs/deck/bin/python
"""Deck YouTube: search, subscriptions feed and history, played with mpv. No Google account needed.

Enter/Right watch (mpv full screen, q there returns here), a listen in the background,
d download video, D download audio, s subscribe to the channel, c open the channel,
Space pause, , . seek, - + volume, x stop, / search, 1 feed, 2 search, 3 history, Left/Esc back.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

import deck_ui
deck_ui.install()

HOME = os.path.expanduser("~")
VENV_BIN = os.path.dirname(os.path.abspath(__import__("sys").executable))
RUN = f"/run/user/{os.getuid()}"
CONFIG = f"{HOME}/.config/deck/youtube.json"
HISTORY = f"{HOME}/.local/share/deck/youtube-history.json"
SOCK = f"{RUN}/deck-tube.sock"
MPV_LOG = f"{RUN}/deck-tube-mpv.log"
VIDEO_DIR = f"{HOME}/Videos/YouTube"
AUDIO_DIR = f"{HOME}/Music/YouTube"

PURPLE, PINK, CYAN, GREEN = "#bd93f9", "#ff79c6", "#8be9fd", "#50fa7b"
ORANGE, YELLOW, RED, GRAY, FG = "#ffb86c", "#f1fa8c", "#ff5555", "#6272a4", "#f8f8f2"
try:
    ACCENT = json.load(open(f"{HOME}/.config/deck/prefs.json")).get("accent", PURPLE)
except Exception:
    ACCENT = PURPLE

I_YT, I_PLAY, I_PAUSE, I_MUSIC, I_FEED, I_SEARCH, I_HIST, I_USER, I_DL = (
    "", "", "", "", "", "", "", "", "")


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
            v = n / div
            return f"{v:.1f}".rstrip("0").rstrip(".") + f"{suf} views"
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
def search(q, n=30):
    from yt_dlp import YoutubeDL     # slow import, so only when needed
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True}
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
        pub = e.findtext("a:published", "", NS)
        try:
            ts = datetime.fromisoformat(pub).timestamp()
        except ValueError:
            ts = None
        out.append({"id": e.findtext("yt:videoId", "", NS), "title": e.findtext("a:title", "?", NS),
                    "channel": e.findtext("a:author/a:name", name, NS), "channel_id": cid,
                    "duration": None, "views": int(stats.get("views")) if stats is not None and stats.get("views") else None,
                    "published": ts, "short": link is not None and "/shorts/" in link.get("href", ""), "live": False})
    return name, out


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


def mpv_cmd(video, quality, audio_only=False):
    cmd = ["mpv", f"--ytdl-format={ytdl_format(quality, audio_only)}",
           f"--script-opts=ytdl_hook-ytdl_path={VENV_BIN}/yt-dlp",
           f"--force-media-title={video['title']}", "--hwdec=auto-safe", f"--log-file={MPV_LOG}"]
    if audio_only:
        cmd += ["--no-video", "--idle=no", f"--input-ipc-server={SOCK}", "--no-terminal"]
    else:
        cmd += ["--fs", "--keep-open=no", "--osd-level=1"]
    return cmd + [f"https://www.youtube.com/watch?v={video['id']}"]


def ipc(*command):
    """Send one command to the background mpv. Returns its data (True if it has none),
    or None when mpv is not running or the command failed."""
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(1)
        s.connect(SOCK)
        s.sendall((json.dumps({"command": list(command), "request_id": 1}) + "\n").encode())
        buf = b""
        while True:
            chunk = s.recv(4096)
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


# ---------- UI ----------
HELP = [
    ("Videos", [("Enter  Right", "watch full screen (q in the player comes back)"), ("a", "listen in the background"),
                ("d  D", "download video / audio only"), ("s", "subscribe or unsubscribe the channel"),
                ("c", "open the channel")]),
    ("Listening", [("Space", "pause or play"), (",  .", "back / forward 10 seconds"), ("-  +", "volume"),
                   ("x", "stop"), ("F6 F7 F8", "media keys work too")]),
    ("Views", [("1  2  3", "subscriptions, search, history"), ("/", "search"), ("Left  Esc", "back"),
               ("r", "refresh"), ("q", "quit")]),
]


class Help(ModalScreen):
    BINDINGS = [Binding("escape,q,question_mark,left", "dismiss", show=False)]
    DEFAULT_CSS = """
    Help { align: center middle; background: #282a36 60%; }
    Help > Static { width: 72; height: auto; background: #21222c; border: round #bd93f9; padding: 1 2; }
    """.replace("#bd93f9", ACCENT)

    def compose(self):
        t = Text()
        for i, (title, keys) in enumerate(HELP):
            t.append(("\n" if i else "") + title + "\n", style=f"bold {ACCENT}")
            for k, d in keys:
                t.append(f"  {k:<14}", style=f"bold {CYAN}")
                t.append(d + "\n", style=FG)
        t.append("\nEsc to close", style=GRAY)
        yield Static(t)

    def on_click(self, _):
        self.dismiss()


class Tube(App):
    TITLE = "YouTube"
    CSS = """
    Screen { background: #282a36; }
    #title { height: 3; padding: 1 3 0 3; }
    Input { margin: 0 3; background: #21222c; border: tall #44475a; display: none; }
    Input:focus { border: tall #bd93f9; }
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
        Binding("q", "quit", show=False), Binding("question_mark", "help", show=False),
        Binding("slash", "search", show=False), Binding("escape", "back", show=False),
        Binding("1", "jump('feed')", show=False), Binding("2", "jump('search')", show=False),
        Binding("3", "jump('history')", show=False), Binding("r", "refresh", show=False),
        Binding("right", "watch", show=False), Binding("left", "back", show=False),
        Binding("a", "listen", show=False), Binding("d", "download(False)", show=False),
        Binding("D", "download(True)", show=False), Binding("s", "subscribe", show=False),
        Binding("c", "channel", show=False), Binding("x", "stop", show=False),
        Binding("space", "toggle", show=False), Binding("comma", "seek(-10)", show=False),
        Binding("full_stop", "seek(10)", show=False), Binding("minus", "volume(-5)", show=False),
        Binding("plus,equals_sign", "volume(5)", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.cfg = {"subs": [], "quality": 720, "hide_shorts": False}
        self.cfg.update(load_json(CONFIG, {}))
        self.history = load_json(HISTORY, [])
        self.view = None            # (kind, title, videos)
        self.stack = []             # earlier views for Left/Esc
        self.search_results = []
        self.last_query = ""
        self.videos = {}
        self.feed_cache = None
        self.video_proc = None

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        yield Input(placeholder="search YouTube, then Enter")
        yield Static(id="status")
        yield OptionList(id="list")
        yield Static(id="now")
        yield Static(Text.assemble(("Enter", CYAN), (" watch  ", GRAY), ("a", CYAN), (" listen  ", GRAY),
                                   ("d", CYAN), (" download  ", GRAY), ("s", CYAN), (" subscribe  ", GRAY),
                                   ("c", CYAN), (" channel  ", GRAY), ("/", CYAN), (" search  ", GRAY),
                                   ("?", CYAN), (" help", GRAY)), id="hint")

    def on_mount(self):
        self.jump("feed" if self.cfg["subs"] else ("history" if self.history else "search"))
        self.set_interval(1, self.update_now)
        self.update_now()

    # ----- views -----
    def jump(self, kind):
        self.stack = []
        if kind == "feed":
            self.show_feed()
        elif kind == "history":
            self.show("history", "History", self.history)
            if not self.history:
                self.status("Nothing watched yet.")
        else:
            self.show("search", f"Search: {self.last_query}" if self.last_query else "Search", self.search_results)
            inp = self.query_one(Input)
            inp.focus()
            if not self.search_results:
                self.status("Type what you want to find, then Enter.")

    def action_jump(self, kind):
        self.jump(kind)

    def show(self, kind, title, videos, push=False):
        if push and self.view:
            self.stack.append(self.view)
        self.view = (kind, title, videos)
        tabs = Text("  ")
        for key, name, k in (("1", "Subscriptions", "feed"), ("2", "Search", "search"), ("3", "History", "history")):
            active = kind == k
            tabs.append(f" {key} {name} ", f"bold {ACCENT}" if active else GRAY)
        if kind in ("feed", "search", "history"):
            head = Text.assemble((I_YT + "  ", RED), ("YouTube", f"bold {ACCENT}"), tabs)
        else:
            head = Text.assemble((I_YT + "  ", RED), (title, f"bold {ACCENT}"), ("   Left goes back", GRAY))
        self.query_one("#title").update(head)
        self.query_one(Input).display = kind == "search"
        self.fill(videos)
        if kind != "search" or videos:
            self.query_one("#list").focus()

    def fill(self, videos):
        if self.cfg.get("hide_shorts"):
            videos = [v for v in videos if not v.get("short")]
        ol = self.query_one("#list", OptionList)
        ol.clear_options()
        self.videos = {}
        subs = {s["id"] for s in self.cfg["subs"]}
        opts = []
        for i, v in enumerate(videos):
            key = f"{i}:{v['id']}"
            self.videos[key] = v
            t = Text.assemble((I_PLAY + "  ", RED), (v["title"], f"bold {FG}"))
            meta = []
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
            if v.get("watched"):
                meta.append((f"watched {fmt_age(v['watched'])}", GRAY))
            line = Text("\n   ")
            line.append(v.get("channel") or "", CYAN if v.get("channel_id") in subs else GRAY)
            for text, style in meta:
                line.append("  ·  ", GRAY)
                line.append(text, style)
            t.append_text(line)
            opts.append(Option(t, id=key))
        ol.add_options(opts)
        if opts:
            ol.highlighted = 0

    def status(self, msg):
        self.query_one("#status").update(msg)

    def current(self):
        ol = self.query_one("#list", OptionList)
        if ol.highlighted is None or not self.videos:
            return None
        return self.videos.get(ol.get_option_at_index(ol.highlighted).id)

    # ----- feed -----
    def show_feed(self, refresh=False):
        if not self.cfg["subs"]:
            self.show("feed", "Subscriptions", [])
            self.status("No subscriptions yet. Highlight a video and press s to follow its channel.")
            return
        if self.feed_cache is not None and not refresh:
            self.show("feed", "Subscriptions", self.feed_cache)
            self.status(f"{len(self.feed_cache)} videos from {plural(len(self.cfg['subs']), 'channel')}")
            return
        self.show("feed", "Subscriptions", self.feed_cache or [])
        self.status("Loading subscriptions...")
        self.load_feed()

    @work(thread=True, exclusive=True, group="feed")
    def load_feed(self):
        videos, failed = [], 0
        with ThreadPoolExecutor(8) as ex:
            for res in ex.map(lambda s: self._safe_feed(s["id"]), self.cfg["subs"]):
                if res is None:
                    failed += 1
                else:
                    videos += res
        videos.sort(key=lambda v: v.get("published") or 0, reverse=True)
        self.call_from_thread(self._feed_done, videos, failed)

    def _safe_feed(self, cid):
        try:
            return channel_feed(cid)[1]
        except Exception:
            return None

    def _feed_done(self, videos, failed):
        self.feed_cache = videos
        if self.view and self.view[0] == "feed":
            self.show("feed", "Subscriptions", videos)
        msg = f"{len(videos)} videos from {plural(len(self.cfg['subs']), 'channel')}"
        self.status(msg + (f", {failed} could not load" if failed else ""))

    # ----- search -----
    def action_search(self):
        if not self.view or self.view[0] != "search":
            self.jump("search")
        inp = self.query_one(Input)
        inp.focus()
        inp.action_select_all()

    def on_input_submitted(self, e):
        q = e.value.strip()
        if not q:
            return
        self.last_query = q
        self.status(f"Searching for {q}...")
        self.do_search(q)

    @work(thread=True, exclusive=True, group="search")
    def do_search(self, q):
        try:
            res = search(q)
        except Exception as ex:
            self.call_from_thread(self.status, f"Search failed: {str(ex)[:120]}")
            return
        self.call_from_thread(self._search_done, q, res)

    def _search_done(self, q, res):
        self.search_results = res
        if self.view and self.view[0] == "search":
            self.show("search", f"Search: {q}", res)
            self.query_one("#list").focus()
        self.status(f"{len(res)} results" if res else "Nothing found.")

    # ----- keys that depend on focus -----
    def on_key(self, event):
        inp = self.query_one(Input)
        ol = self.query_one("#list", OptionList)
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
        # While typing a search, letters belong to the input box.
        if self.query_one(Input).has_focus and action not in ("back", "jump"):
            return False
        return True

    def action_back(self):
        if self.stack:
            kind, title, videos = self.stack.pop()
            self.view = None
            self.show(kind, title, videos)
        elif self.view and self.view[0] == "search" and not self.query_one(Input).has_focus:
            self.query_one(Input).focus()

    def action_refresh(self):
        if self.view and self.view[0] == "feed":
            self.show_feed(refresh=True)
        elif self.view and self.view[0] == "channel" and self.view[2]:
            self.open_channel(self.view[2][0]["channel_id"])
        elif self.view and self.view[0] == "search" and self.last_query:
            self.do_search(self.last_query)

    # ----- channel and subscriptions -----
    def action_channel(self):
        v = self.current()
        if not v or not v.get("channel_id"):
            return
        self.status(f"Loading {v.get('channel') or 'channel'}...")
        self.open_channel(v["channel_id"])

    @work(thread=True, exclusive=True, group="channel")
    def open_channel(self, cid):
        if not cid:
            return
        try:
            name, videos = channel_feed(cid)
        except Exception as ex:
            self.call_from_thread(self.status, f"Could not load the channel: {str(ex)[:100]}")
            return
        self.call_from_thread(self._channel_done, name, videos)

    def _channel_done(self, name, videos):
        subbed = any(s["id"] == (videos[0]["channel_id"] if videos else "") for s in self.cfg["subs"])
        self.show("channel", name, videos, push=self.view is not None and self.view[0] != "channel")
        self.status(f"Latest {len(videos)} uploads" + ("   subscribed" if subbed else "   s to subscribe"))

    def action_subscribe(self):
        v = self.current()
        if not v or not v.get("channel_id"):
            return
        subs = self.cfg["subs"]
        hit = [s for s in subs if s["id"] == v["channel_id"]]
        if hit:
            subs.remove(hit[0])
            self.notify(f"Unsubscribed from {hit[0]['name']}", timeout=2)
        else:
            subs.append({"id": v["channel_id"], "name": v.get("channel") or v["channel_id"]})
            self.notify(f"Subscribed to {v.get('channel')}", timeout=2)
        subs.sort(key=lambda s: s["name"].lower())
        save_json(CONFIG, self.cfg)
        self.feed_cache = None
        ol = self.query_one("#list", OptionList)
        keep = ol.highlighted
        self.fill(self.view[2])
        if keep is not None and keep < ol.option_count:
            ol.highlighted = keep

    # ----- playback -----
    def remember(self, v):
        entry = dict(v, watched=time.time())
        self.history = [entry] + [h for h in self.history if h["id"] != v["id"]][:199]
        save_json(HISTORY, self.history)

    def on_option_list_option_selected(self, e):
        self.action_watch()

    def action_watch(self):
        v = self.current()
        if not v:
            return
        if not shutil.which("mpv"):
            self.notify("mpv is not installed: sudo apt install mpv mpv-mpris", severity="error", timeout=6)
            return
        if self.video_proc and self.video_proc.poll() is None:
            self.notify("A video is already playing", timeout=2)
            return
        if ipc("get_property", "pause") is False:
            ipc("set_property", "pause", True)
        self.remember(v)
        self.status(f"Opening {v['title']}...")
        self.video_proc = subprocess.Popen(mpv_cmd(v, self.cfg["quality"]), env=player_env(),
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
        started = time.time()
        proc = self.video_proc

        def wait():
            code = proc.wait()
            msg = ""
            if code not in (0, 4) and time.time() - started < 20:    # 4 = quit by the user
                msg = "Could not play this video. Details: " + MPV_LOG
            self.call_from_thread(self.status, msg)
        threading.Thread(target=wait, daemon=True).start()

    def action_listen(self):
        v = self.current()
        if not v:
            return
        if not shutil.which("mpv"):
            self.notify("mpv is not installed: sudo apt install mpv mpv-mpris", severity="error", timeout=6)
            return
        ipc("quit")
        time.sleep(0.1)
        self.remember(v)
        subprocess.Popen(mpv_cmd(v, self.cfg["quality"], audio_only=True), env=player_env(),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)       # keeps playing if this app is closed
        self.notify(f"Listening to {v['title']}", timeout=2)

    def action_toggle(self):
        if ipc("cycle", "pause") is None:
            self.notify("Nothing is playing in the background", timeout=1.5)
        self.update_now()

    def action_seek(self, secs):
        ipc("seek", secs, "relative")
        self.update_now()

    def action_volume(self, d):
        ipc("add", "volume", d)
        vol = ipc("get_property", "volume")
        if vol is not None:
            self.notify(f"Volume {vol:.0f}%", timeout=1)

    def action_stop(self):
        if ipc("quit"):
            self.notify("Stopped", timeout=1)
        self.update_now()

    def update_now(self):
        now = self.query_one("#now")
        title = ipc("get_property", "media-title")
        if title is None:
            now.display = False
            return
        pos = ipc("get_property", "time-pos") or 0
        dur = ipc("get_property", "duration")
        paused = ipc("get_property", "pause")
        w = max(10, self.size.width - 8)
        t = Text.assemble(((I_PAUSE if paused else I_PLAY) + "  ", RED if not paused else GRAY),
                          (title[: w - 20], f"bold {FG}"), ("   " + fmt_time(pos), CYAN),
                          (" / " + fmt_time(dur) if dur else "", GRAY))
        bar = Text()
        if dur:
            filled = int(w * min(1, pos / dur))
            bar.append("━" * filled, ACCENT)
            bar.append("─" * (w - filled), "#44475a")
        now.update(Text.assemble(bar, "\n", t))
        now.display = True

    # ----- downloads -----
    def action_download(self, audio):
        v = self.current()
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
        r = subprocess.run(cmd + [f"https://www.youtube.com/watch?v={v['id']}"], env=player_env(),
                           capture_output=True, text=True)
        where = "~/Music/YouTube" if audio else "~/Videos/YouTube"
        if r.returncode == 0:
            self.call_from_thread(self.notify, f"Saved {v['title']} to {where}", timeout=4)
        else:
            err = (r.stderr.strip().splitlines() or ["unknown error"])[-1]
            self.call_from_thread(self.notify, f"Download failed: {err[:120]}", severity="error", timeout=6)

    def action_help(self):
        self.push_screen(Help())


if __name__ == "__main__":
    Tube().run()
