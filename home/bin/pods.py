#!/home/admin/.venvs/deck/bin/python
"""Deck podcasts: find shows (Apple's free directory), subscribe by RSS, play with resume and speed.

Enter/Right play (or open a show), s subscribe, d download, m mark played, i show notes,
Space pause, , back 15 s, . forward 30 s, [ ] speed, - + volume, x stop, / find,
1-4 or Tab switch views, I import OPML, r refresh, Left/Esc back, ? help.
"""
import hashlib
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

import deck_mpv
import deck_ui
deck_ui.install()

HOME = os.path.expanduser("~")
CONFIG = f"{HOME}/.config/deck/podcasts.json"
PROGRESS = f"{HOME}/.local/share/deck/podcasts-progress.json"
CACHE_DIR = f"{HOME}/.cache/deck/podcasts"
DL_DIR = f"{HOME}/Podcasts"
SOCK = deck_mpv.SOCKETS["pods"]
NOW_FILE = f"{deck_mpv.RUN}/deck-pods-now.json"
MPV_LOG = f"{deck_mpv.RUN}/deck-pods-mpv.log"
UA = {"User-Agent": "deck-podcasts/1.0"}
FEED_TTL = 1800          # refetch a feed after 30 minutes

PURPLE, PINK, CYAN, GREEN = "#bd93f9", "#ff79c6", "#8be9fd", "#50fa7b"
ORANGE, YELLOW, RED, GRAY, FG = "#ffb86c", "#f1fa8c", "#ff5555", "#6272a4", "#f8f8f2"
try:
    ACCENT = json.load(open(f"{HOME}/.config/deck/prefs.json")).get("accent", PURPLE)
except Exception:
    ACCENT = PURPLE

I_POD, I_PLAY, I_PAUSE, I_DL, I_CHECK = "", "", "", "", ""
TABS = [("latest", "Latest"), ("shows", "Shows"), ("find", "Find"), ("progress", "In progress")]
NS = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd", "content": "http://purl.org/rss/1.0/modules/content/"}


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


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def fmt_time(s):
    if s is None:
        return ""
    s = int(s)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_len(s):
    if not s:
        return ""
    m = int(s // 60)
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


def fmt_date(ts):
    if not ts:
        return ""
    d = time.time() - ts
    if d < 86400:
        return "today"
    if d < 2 * 86400:
        return "yesterday"
    if d < 7 * 86400:
        return f"{int(d // 86400)} days ago"
    return time.strftime("%b %-d, %Y" if d > 300 * 86400 else "%b %-d", time.localtime(ts))


def strip_html(s):
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.strip() for line in s.splitlines())).strip()


def safe_name(s):
    return re.sub(r"[^\w .,()&'-]+", "", s).strip()[:80] or "episode"


# ---------- directory and feeds ----------
def fetch(url, timeout=12):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def apple_search(q):
    data = json.loads(fetch("https://itunes.apple.com/search?media=podcast&limit=30&term=" + urllib.parse.quote(q)))
    return [show_row(r) for r in data.get("results", []) if r.get("feedUrl")]


def apple_top():
    top = json.loads(fetch("https://rss.applemarketingtools.com/api/v2/us/podcasts/top/30/podcasts.json"))
    ids = ",".join(r["id"] for r in top["feed"]["results"])
    data = json.loads(fetch(f"https://itunes.apple.com/lookup?id={ids}"))
    rank = {r["id"]: i for i, r in enumerate(top["feed"]["results"])}
    found = sorted(data.get("results", []), key=lambda r: rank.get(str(r.get("collectionId")), 999))
    return [show_row(r) for r in found if r.get("feedUrl")]


def show_row(r):
    return {"_show": True, "feed": r["feedUrl"], "title": r.get("collectionName") or "?",
            "author": r.get("artistName") or "", "genre": r.get("primaryGenreName") or "",
            "count": r.get("trackCount")}


def parse_duration(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        parts = [float(p) for p in s.split(":")]
    except ValueError:
        return None
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def parse_feed(url, raw):
    root = ET.fromstring(raw)
    ch = root.find("channel")
    if ch is None:
        raise ValueError("not an RSS podcast feed")
    title = (ch.findtext("title") or url).strip()
    author = (ch.findtext("itunes:author", "", NS) or "").strip()
    eps = []
    for it in ch.findall("item")[:300]:
        enc = it.find("enclosure")
        if enc is None or not enc.get("url"):
            continue
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate")).timestamp()
        except (TypeError, ValueError):
            ts = None
        guid = (it.findtext("guid") or enc.get("url")).strip()
        eps.append({"id": hashlib.sha1(f"{url}|{guid}".encode()).hexdigest()[:16],
                    "title": (it.findtext("title") or "?").strip(), "show": title, "feed": url,
                    "url": enc.get("url"), "date": ts,
                    "duration": parse_duration(it.findtext("itunes:duration", "", NS)),
                    "notes": strip_html(it.findtext("content:encoded", "", NS) or it.findtext("description")
                                        or it.findtext("itunes:summary", "", NS))[:4000]})
    return {"title": title, "author": author, "episodes": eps}


def cache_path(url):
    return f"{CACHE_DIR}/{hashlib.sha1(url.encode()).hexdigest()[:16]}.json"


def get_feed(url, refresh=False):
    """A parsed feed, from the cache when it is fresh enough."""
    cached = load_json(cache_path(url), None)
    if cached and not refresh and time.time() - cached.get("t", 0) < FEED_TTL:
        return cached
    try:
        feed = parse_feed(url, fetch(url))
    except Exception:
        if cached:
            return cached           # offline: keep showing what we had
        raise
    feed["t"] = time.time()
    save_json(cache_path(url), feed)
    return feed


def import_opml(path):
    root = ET.parse(path).getroot()
    return [{"url": o.get("xmlUrl"), "title": o.get("title") or o.get("text") or o.get("xmlUrl"), "author": ""}
            for o in root.iter("outline") if o.get("xmlUrl")]


def local_file(ep):
    ext = os.path.splitext(urllib.parse.urlparse(ep["url"]).path)[1][:5] or ".mp3"
    return f"{DL_DIR}/{safe_name(ep['show'])}/{safe_name(ep['title'])}{ext}"


# ---------- modal screens ----------
MODAL = """
    {name} {{ align: center middle; background: #282a36 60%; }}
    {name} > Vertical, {name} > VerticalScroll {{ width: 80; max-width: 95%; height: auto; max-height: 90%;
        background: #21222c; border: round {accent}; padding: 1 2; }}
    {name} Input {{ background: #282a36; border: tall #44475a; }}
    {name} Input:focus {{ border: tall {accent}; }}
"""


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


class Notes(ModalScreen):
    """Episode show notes; Up/Down scroll, Esc closes."""
    BINDINGS = [Binding("escape,q,i,left", "dismiss", show=False),
                Binding("down,j", "scroll(1)", show=False), Binding("up,k", "scroll(-1)", show=False),
                Binding("pagedown,space", "scroll(10)", show=False), Binding("pageup", "scroll(-10)", show=False)]
    DEFAULT_CSS = MODAL.format(name="Notes", accent=ACCENT)

    def __init__(self, ep):
        super().__init__()
        self.ep = ep

    def compose(self):
        ep = self.ep
        t = Text.assemble((ep["title"], f"bold {ACCENT}"), "\n", (ep["show"], CYAN),
                          ("   " + fmt_date(ep.get("date")) + ("   " + fmt_len(ep["duration"]) if ep.get("duration") else ""), GRAY),
                          "\n\n", (ep.get("notes") or "No show notes.", FG))
        with VerticalScroll():
            yield Static(t)

    def action_scroll(self, n):
        self.query_one(VerticalScroll).scroll_relative(y=n, animate=False)


HELP = [
    ("Episodes", [("Enter  Right", "play, from where you stopped"), ("d", "download for offline"),
                  ("m", "mark played or unplayed"), ("i", "show notes"), ("s", "subscribe or unsubscribe")]),
    ("Playing", [("Space", "pause or play"), (",  .", "back 15 / forward 30 seconds"), ("[  ]", "slower / faster"),
                 ("-  +", "volume"), ("x", "stop"), ("F7", "media keys work too")]),
    ("Views", [("1 to 4  Tab", "latest, shows, find, in progress"), ("/", "find a podcast"),
               ("Left  Esc", "back"), ("r", "refresh feeds"), ("I", "import an OPML file"),
               ("Delete", "unsubscribe, or clear progress"), ("q", "quit")]),
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


# ---------- app ----------
class Pods(App):
    TITLE = "Podcasts"
    CSS = """
    Screen { background: #282a36; }
    #title { height: 3; padding: 1 3 0 3; }
    #find { margin: 0 3; background: #21222c; border: tall #44475a; display: none; }
    #find:focus { border: tall #bd93f9; }
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
        Binding("slash", "find", show=False), Binding("escape", "back", show=False),
        Binding("1", "jump('latest')", show=False), Binding("2", "jump('shows')", show=False),
        Binding("3", "jump('find')", show=False), Binding("4", "jump('progress')", show=False),
        Binding("right", "play", show=False), Binding("left", "back", show=False),
        Binding("s", "subscribe", show=False), Binding("d", "download", show=False),
        Binding("m", "mark", show=False), Binding("i", "notes", show=False),
        Binding("r", "refresh", show=False), Binding("I", "import_opml", show=False),
        Binding("delete,backspace", "remove", show=False), Binding("x", "stop", show=False),
        Binding("space", "toggle", show=False), Binding("comma", "seek(-15)", show=False),
        Binding("full_stop", "seek(30)", show=False), Binding("left_square_bracket", "speed(-0.1)", show=False),
        Binding("right_square_bracket", "speed(0.1)", show=False), Binding("minus", "volume(-5)", show=False),
        Binding("plus,equals_sign", "volume(5)", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.cfg = {"feeds": [], "speed": 1.0}
        self.cfg.update(load_json(CONFIG, {}))
        self.progress = load_json(PROGRESS, {})
        self.now = load_json(NOW_FILE, None)        # the episode loaded in the player
        self.view = None
        self.stack = []
        self.rows = {}
        self.latest = None
        self.find_results = None
        self.find_query = ""
        self.last_save = 0
        self.downloading = set()

    @property
    def main(self):
        return self.screen_stack[0]

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        yield Input(placeholder="find a podcast by name or topic, then Enter", id="find")
        yield Static(id="status")
        yield OptionList(id="list")
        yield Static(id="now")
        yield Static(Text.assemble(("Enter", CYAN), (" play  ", GRAY), ("s", CYAN), (" subscribe  ", GRAY),
                                   ("d", CYAN), (" download  ", GRAY), ("i", CYAN), (" notes  ", GRAY),
                                   ("[ ]", CYAN), (" speed  ", GRAY), ("Tab", CYAN), (" views  ", GRAY),
                                   ("?", CYAN), (" help", GRAY)), id="hint")

    def on_mount(self):
        self.jump("latest" if self.cfg["feeds"] else "find")
        self.set_interval(1, self.tick)
        self.tick()

    def save_cfg(self):
        save_json(CONFIG, self.cfg)

    def status(self, msg):
        self.main.query_one("#status").update(msg)

    # ----- views -----
    def jump(self, kind):
        self.stack = []
        if kind == "latest":
            self.show_latest()
        elif kind == "shows":
            self.show_shows()
        elif kind == "progress":
            self.show_progress()
        else:
            self.show_find()

    def action_jump(self, kind):
        self.jump(kind)

    def action_cycle(self, d):
        if isinstance(self.screen, ModalScreen):
            return
        kinds = [k for k, _ in TABS]
        cur = self.view[0] if self.view else "latest"
        if cur not in kinds:
            cur = next((v[0] for v in reversed(self.stack) if v[0] in kinds), "shows")
        self.jump(kinds[(kinds.index(cur) + d) % len(kinds)])

    def show(self, kind, title, items, push=False):
        if push and self.view:
            self.stack.append(self.view)
        self.view = (kind, title, items)
        if kind in dict(TABS):
            head = Text.assemble((I_POD + "  ", PURPLE), ("Podcasts", f"bold {ACCENT}"), "  ")
            for i, (k, name) in enumerate(TABS, 1):
                head.append(f" {i} {name} ", f"bold {ACCENT}" if k == kind else GRAY)
        else:
            head = Text.assemble((I_POD + "  ", PURPLE), (title, f"bold {ACCENT}"), ("   Left goes back", GRAY))
        self.main.query_one("#title").update(head)
        self.main.query_one("#find").display = kind == "find"
        self.fill(items, keep=0)
        if kind != "find" or items:
            self.main.query_one("#list").focus()

    def fill(self, items, keep=None):
        ol = self.main.query_one("#list", OptionList)
        if keep is None:
            keep = ol.highlighted
        ol.clear_options()
        self.rows = {}
        subs = {f["url"] for f in self.cfg["feeds"]}
        opts = []
        for i, r in enumerate(items):
            key = f"{i}"
            self.rows[key] = r
            opts.append(Option(self.render_show(r, subs) if r.get("_show") else self.render_ep(r), id=key))
        ol.add_options(opts)
        if opts:
            ol.highlighted = min(keep or 0, ol.option_count - 1)
            if ol.get_option_at_index(ol.highlighted).disabled:
                ol.highlighted = max(0, ol.highlighted - 1)

    def render_show(self, r, subs):
        subbed = r["feed"] in subs
        t = Text.assemble((I_POD + "  ", PURPLE), (r["title"], f"bold {FG}"))
        line = Text("\n   ")
        line.append(r.get("author") or "", GRAY)
        for extra in (r.get("genre"), plural(r["count"], "episode") if r.get("count") else None):
            if extra:
                line.append("  ·  " + extra, GRAY)
        if subbed:
            line.append("  ·  subscribed", GREEN)
        return t + line

    def render_ep(self, ep):
        p = self.progress.get(ep["id"], {})
        playing = self.now and self.now.get("id") == ep["id"]
        done = p.get("done")
        icon = I_CHECK if done else I_PLAY
        color = GREEN if playing else (GRAY if done else PURPLE)
        t = Text.assemble((icon + "  ", color), (ep["title"], f"bold {GREEN}" if playing else (GRAY if done else f"bold {FG}")))
        line = Text("\n   ")
        line.append(ep["show"], CYAN)
        bits = [(fmt_date(ep.get("date")), GRAY)]
        dur = ep.get("duration") or p.get("dur")
        if done:
            bits.append(("played", GRAY))
        elif p.get("pos") and dur:
            bits.append((f"{fmt_len(max(60, dur - p['pos']))} left", YELLOW))
        elif dur:
            bits.append((fmt_len(dur), GRAY))
        if playing:
            bits.append(("now playing", f"bold {GREEN}"))
        if os.path.exists(local_file(ep)):
            bits.append((I_DL + " offline", CYAN))
        elif ep["id"] in self.downloading:
            bits.append(("downloading", YELLOW))
        for text, style in bits:
            if text:
                line.append("  ·  ", GRAY)
                line.append(text, style)
        return t + line

    def current(self):
        ol = self.main.query_one("#list", OptionList)
        if ol.highlighted is None or not self.rows:
            return None
        return self.rows.get(ol.get_option_at_index(ol.highlighted).id)

    def refill(self):
        """Redraw the current list in place (progress or downloads changed)."""
        if self.view:
            self.fill(self.view[2])

    # ----- latest -----
    def show_latest(self, refresh=False):
        n = len(self.cfg["feeds"])
        if not n:
            self.show("latest", "Latest", [])
            self.status("No subscriptions yet. Press 3 to find podcasts, or I to import an OPML file.")
            return
        self.show("latest", "Latest", self.latest or [])
        if self.latest is None or refresh:
            self.status(f"Loading {plural(n, 'show')}...")
            self.load_latest(refresh)
        else:
            self.status(f"Newest episodes from {plural(n, 'show')}")

    @work(thread=True, exclusive=True, group="latest")
    def load_latest(self, refresh):
        eps, failed = [], 0

        def one(f):
            try:
                return get_feed(f["url"], refresh)["episodes"]
            except Exception:
                return None
        with ThreadPoolExecutor(8) as ex:
            for res in ex.map(one, self.cfg["feeds"]):
                if res is None:
                    failed += 1
                else:
                    eps += res[:20]
        eps.sort(key=lambda e: e.get("date") or 0, reverse=True)
        self.call_from_thread(self._latest_done, eps[:150], failed)

    def _latest_done(self, eps, failed):
        self.latest = eps
        if self.view and self.view[0] == "latest":
            self.show("latest", "Latest", eps)
        msg = f"Newest episodes from {plural(len(self.cfg['feeds']), 'show')}"
        self.status(msg + (f", {failed} could not load" if failed else ""))

    # ----- shows and episodes -----
    def show_shows(self):
        rows = [{"_show": True, "feed": f["url"], "title": f["title"], "author": f.get("author", "")}
                for f in self.cfg["feeds"]]
        self.show("shows", "Shows", rows)
        self.status(plural(len(rows), "show") if rows else "No shows yet. Press 3 to find some.")

    @work(thread=True, exclusive=True, group="show")
    def open_show(self, row, refresh=False):
        try:
            feed = get_feed(row["feed"], refresh)
        except Exception as ex:
            self.call_from_thread(self.status, f"Could not load the feed: {str(ex)[:100]}")
            return
        self.call_from_thread(self._show_done, row, feed)

    def _show_done(self, row, feed):
        push = self.view is not None and self.view[0] != "show"
        self.show("show", feed["title"], feed["episodes"], push=push)
        self.show_row = row
        subbed = any(f["url"] == row["feed"] for f in self.cfg["feeds"])
        self.status(f"{plural(len(feed['episodes']), 'episode')}" + ("   subscribed" if subbed else "   s to subscribe"))

    # ----- find -----
    def show_find(self):
        self.show("find", "Find", self.find_results or [])
        box = self.main.query_one("#find", Input)
        box.focus()
        if self.find_results is None:
            self.status("Loading top podcasts...")
            self.load_top()
        elif self.find_query:
            self.status(f"{len(self.find_results)} results for {self.find_query}")

    def action_find(self):
        if not self.view or self.view[0] != "find":
            self.jump("find")
        box = self.main.query_one("#find", Input)
        box.focus()
        box.action_select_all()

    @work(thread=True, exclusive=True, group="find")
    def load_top(self):
        try:
            res = apple_top()
        except Exception as ex:
            self.call_from_thread(self.status, f"Could not load top podcasts: {str(ex)[:100]}")
            return
        self.call_from_thread(self._find_done, "", res)

    @work(thread=True, exclusive=True, group="find")
    def do_find(self, q):
        try:
            res = apple_search(q)
        except Exception as ex:
            self.call_from_thread(self.status, f"Search failed: {str(ex)[:100]}")
            return
        self.call_from_thread(self._find_done, q, res)

    def _find_done(self, q, res):
        self.find_results, self.find_query = res, q
        if self.view and self.view[0] == "find":
            self.show("find", "Find", res)
            if q:
                self.main.query_one("#list").focus()
        self.status((f"{len(res)} results for {q}" if res else "Nothing found.") if q
                    else "Top podcasts right now. Type to search.")

    def on_input_submitted(self, e):
        if e.input.id == "find" and e.value.strip():
            self.status(f"Searching for {e.value.strip()}...")
            self.do_find(e.value.strip())

    # ----- in progress -----
    def show_progress(self):
        eps = [p["ep"] for p in sorted(self.progress.values(), key=lambda p: -p.get("t", 0))
               if p.get("ep") and p.get("pos") and not p.get("done")]
        self.show("progress", "In progress", eps)
        self.status(plural(len(eps), "episode") + " started" if eps else "Nothing in progress.")

    # ----- keys that depend on focus -----
    def on_key(self, event):
        if isinstance(self.screen, ModalScreen):
            return
        box = self.main.query_one("#find", Input)
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
        if self.main.query_one("#find", Input).has_focus and action not in ("back", "cycle"):
            return False
        return True

    def action_back(self):
        if self.stack:
            kind, title, items = self.stack.pop()
            self.view = None
            if kind == "shows":
                self.show_shows()
            elif kind == "progress":
                self.show_progress()
            else:
                self.show(kind, title, items)
        elif self.view and self.view[0] == "find" and not self.main.query_one("#find").has_focus:
            self.main.query_one("#find").focus()

    def action_refresh(self):
        kind = self.view[0] if self.view else ""
        if kind == "latest":
            self.show_latest(refresh=True)
        elif kind == "show":
            self.open_show(self.show_row, refresh=True)

    # ----- subscriptions -----
    def action_subscribe(self):
        cur = self.current()
        if not cur:
            return
        url = cur["feed"]
        title = cur["title"] if cur.get("_show") else cur["show"]
        hit = [f for f in self.cfg["feeds"] if f["url"] == url]
        if hit:
            self.cfg["feeds"].remove(hit[0])
            self.notify(f"Unsubscribed from {title}", timeout=2)
        else:
            self.cfg["feeds"].append({"url": url, "title": title, "author": cur.get("author", "")})
            self.cfg["feeds"].sort(key=lambda f: f["title"].lower())
            self.notify(f"Subscribed to {title}", timeout=2)
        self.save_cfg()
        self.latest = None
        self.refill()

    def action_import_opml(self):
        def done(path):
            if not path:
                return
            try:
                feeds = import_opml(os.path.expanduser(path))
            except (OSError, ET.ParseError) as ex:
                self.notify(f"Could not read {path}: {ex}", severity="error", timeout=5)
                return
            have = {f["url"] for f in self.cfg["feeds"]}
            new = [f for f in feeds if f["url"] not in have]
            self.cfg["feeds"] += new
            self.cfg["feeds"].sort(key=lambda f: f["title"].lower())
            self.save_cfg()
            self.latest = None
            self.notify(f"Imported {plural(len(new), 'new show')}", timeout=3)
            self.jump("shows")
        import glob
        hits = sorted(glob.glob(f"{HOME}/Downloads/**/*.opml", recursive=True), key=os.path.getmtime, reverse=True)
        self.push_screen(Prompt("Import podcasts from an OPML file", "path to the .opml file",
                                hits[0] if hits else ""), done)

    def action_remove(self):
        kind = self.view[0] if self.view else ""
        cur = self.current()
        if not cur:
            return
        if kind == "shows":
            def done(yes):
                if yes:
                    self.cfg["feeds"] = [f for f in self.cfg["feeds"] if f["url"] != cur["feed"]]
                    self.save_cfg()
                    self.latest = None
                    self.show_shows()
            self.push_screen(Confirm(f"Unsubscribe from {cur['title']}?"), done)
        elif kind == "progress":
            self.progress.pop(cur["id"], None)
            save_json(PROGRESS, self.progress)
            self.show_progress()

    # ----- playback -----
    def on_option_list_option_selected(self, e):
        if e.option_list.id == "list":
            self.action_play()

    def action_play(self):
        cur = self.current()
        if not cur:
            return
        if cur.get("_show"):
            self.status(f"Loading {cur['title']}...")
            return self.open_show(cur)
        self.play(cur)

    @work(thread=True, exclusive=True, group="player")
    def play(self, ep):
        if not deck_mpv.start(SOCK, ["--ytdl=no"], MPV_LOG):
            self.call_from_thread(self.notify, f"The player did not start. Details: {MPV_LOG}", severity="error")
            return
        self.save_position()
        p = self.progress.get(ep["id"], {})
        start = 0 if p.get("done") else max(0, (p.get("pos") or 0) - 3)
        src = local_file(ep) if os.path.exists(local_file(ep)) else ep["url"]
        opts = f"force-media-title={deck_mpv.quote(ep['title'])},start={start:.0f}"
        deck_mpv.pause_others(SOCK)
        deck_mpv.ipc(SOCK, "loadfile", src, "replace", -1, opts)
        deck_mpv.ipc(SOCK, "set_property", "speed", self.cfg["speed"])
        deck_mpv.ipc(SOCK, "set_property", "pause", False)
        self.now = {"id": ep["id"], "ep": ep}
        save_json(NOW_FILE, self.now)
        if p.get("done"):
            p["done"] = False
        self.call_from_thread(self.refill)
        self.call_from_thread(self.notify, f"Playing {ep['title']}" + (f" from {fmt_time(start)}" if start else ""),
                              timeout=2)

    def save_position(self, st=None):
        """Store how far the loaded episode got; mark it played near the end."""
        if not self.now:
            return
        st = st or deck_mpv.status(SOCK)
        if not st:
            return
        ep = self.now["ep"]
        p = self.progress.setdefault(ep["id"], {})
        p.update(pos=st["pos"], dur=st["dur"] or p.get("dur"), t=time.time(), ep=ep)
        if st["dur"] and st["pos"] > st["dur"] - 30:
            p["done"] = True
        save_json(PROGRESS, self.progress)
        self.last_save = time.time()

    def action_toggle(self):
        if deck_mpv.ipc(SOCK, "cycle", "pause") is None:
            self.notify("Nothing is playing", timeout=1.5)
        elif deck_mpv.ipc(SOCK, "get_property", "pause") is False:
            deck_mpv.pause_others(SOCK)
        else:
            self.save_position()
        self.tick()

    def action_seek(self, secs):
        deck_mpv.ipc(SOCK, "seek", secs, "relative")
        self.tick()

    def action_speed(self, d):
        self.cfg["speed"] = round(min(3.0, max(0.5, self.cfg["speed"] + d)), 1)
        self.save_cfg()
        deck_mpv.ipc(SOCK, "set_property", "speed", self.cfg["speed"])
        self.notify(f"Speed {self.cfg['speed']:g}x", timeout=1)
        self.tick()

    def action_volume(self, d):
        deck_mpv.ipc(SOCK, "add", "volume", d)
        vol = deck_mpv.ipc(SOCK, "get_property", "volume")
        if vol is not None:
            self.notify(f"Volume {vol:.0f}%", timeout=1)

    def action_stop(self):
        self.save_position()
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
            if self.now:                 # the episode ended and the player quit
                p = self.progress.get(self.now["id"])
                if p and p.get("dur") and p.get("pos", 0) > p["dur"] - 60:
                    p["done"] = True
                    save_json(PROGRESS, self.progress)
                self.now = None
                save_json(NOW_FILE, None)
                if not isinstance(self.screen, ModalScreen):
                    self.refill()
            now.display = False
            return
        if not st["paused"] and time.time() - self.last_save > 5:
            self.save_position(st)
        w = max(10, self.size.width - 8)
        pos, dur = st["pos"], st["dur"]
        speed = f"   {st['speed']:g}x" if abs(st["speed"] - 1) > 0.01 else ""
        t = Text.assemble(((I_PAUSE if st["paused"] else I_PLAY) + "  ", GRAY if st["paused"] else PURPLE),
                          (st["title"][: w - 34], f"bold {FG}"), ("   " + fmt_time(pos), CYAN),
                          (" / " + fmt_time(dur) if dur else "", GRAY), (speed, YELLOW))
        bar = Text()
        if dur:
            filled = int(w * min(1, pos / dur))
            bar.append("━" * filled, ACCENT)
            bar.append("─" * (w - filled), "#44475a")
        now.update(Text.assemble(bar, "\n", t))
        now.display = True

    # ----- episode actions -----
    def action_mark(self):
        cur = self.current()
        if not cur or cur.get("_show"):
            return
        p = self.progress.setdefault(cur["id"], {"ep": cur})
        p["done"] = not p.get("done")
        p["t"] = time.time()
        if not p["done"]:
            p["pos"] = 0
        save_json(PROGRESS, self.progress)
        self.refill()

    def action_notes(self):
        cur = self.current()
        if cur and not cur.get("_show"):
            self.push_screen(Notes(cur))

    def action_download(self):
        cur = self.current()
        if not cur or cur.get("_show"):
            return
        if os.path.exists(local_file(cur)):
            self.notify("Already downloaded", timeout=1.5)
            return
        self.downloading.add(cur["id"])
        self.refill()
        self.download(cur)

    @work(thread=True, group="download")
    def download(self, ep):
        dest = local_file(ep)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            with urllib.request.urlopen(urllib.request.Request(ep["url"], headers=UA), timeout=60) as r, \
                    open(dest + ".part", "wb") as f:
                while chunk := r.read(1 << 16):
                    f.write(chunk)
            os.replace(dest + ".part", dest)
            self.call_from_thread(self.notify, f"Downloaded {ep['title']}", timeout=3)
        except Exception as ex:
            self.call_from_thread(self.notify, f"Download failed: {str(ex)[:100]}", severity="error", timeout=5)
        self.downloading.discard(ep["id"])
        self.call_from_thread(self.refill)

    def action_help(self):
        self.push_screen(Help())


if __name__ == "__main__":
    Pods().run()
