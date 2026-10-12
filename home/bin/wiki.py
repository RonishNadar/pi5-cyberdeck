#!/home/admin/.venvs/deck/bin/python
"""Deck offline Wikipedia: reads Kiwix ZIM files from ~/Wiki and downloads them from the Kiwix library.

Search: type a title, Enter or Down to pick, Ctrl+R random article, Ctrl+G get more ZIM files.
Article: Up/Down scroll, Space/PgDn page, t contents, l links, Left/Backspace back, / new search,
Esc back to search, q quit.
"""
import json
import os
import posixpath
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from libzim.reader import Archive
from libzim.search import Query, Searcher
from libzim.suggestion import SuggestionSearcher
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

import deck_ui
import reader                      # the Books app: its HTML parser and page layout are reused here
deck_ui.install()

HOME = os.path.expanduser("~")
WIKI = f"{HOME}/Wiki"
RUN = f"/run/user/{os.getuid()}"
CATALOG = "https://opds.library.kiwix.org/catalog/v2/entries"

PURPLE, PINK, CYAN, GREEN = "#bd93f9", "#ff79c6", "#8be9fd", "#50fa7b"
ORANGE, YELLOW, RED, GRAY, FG = "#ffb86c", "#f1fa8c", "#ff5555", "#6272a4", "#f8f8f2"
ACCENT = reader.ACCENT
I_WIKI, I_DL, I_BOOK, I_LINK, I_DICE = "", "", "", "", ""
THEME = reader.THEMES["Dracula"]
PREFS = {"justify": False, "spacing": True}

# Whole class names (not substrings: the page itself carries classes like "vector-toc-not-available").
SKIP_CLASS = {"infobox", "navbox", "vertical-navbox", "sidebar", "reflist", "references", "mw-references-wrap",
              "metadata", "ambox", "sistersitebox", "noprint", "mw-editsection", "reference", "mw-cite-backlink",
              "portalbox", "toc", "mw-empty-elt", "navigation-not-searchable", "mw-ref", "catlinks", "printfooter"}
VOID = {"br", "img", "hr", "meta", "link", "input", "wbr", "source", "col", "area", "base", "embed", "param", "track"}


# ---------- article parsing ----------
class WikiHtml(reader.HtmlToBlocks):
    """reader.HtmlToBlocks plus: skip boxes and references, tables as rows, collect links."""

    def __init__(self):
        super().__init__()
        self.stack = []           # (tag, started a skip, passed to the base parser)
        self.links = []           # (text, href)
        self.link = None          # (start offset, href) of the open <a>

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        skip = tag not in VOID and not self.skip and bool(classes & SKIP_CLASS or a.get("role") == "navigation")
        passed = not skip and not self.skip
        if tag not in VOID:
            self.stack.append((tag, skip, passed))
        if skip:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in ("td", "th"):
            if self.cur.plain.strip():
                self.cur.append("  │  ", GRAY)
            return
        if tag == "a" and a.get("href") and not a["href"].startswith(("http:", "https:", "#", "mailto:")):
            self.link = (len(self.cur.plain), a["href"])
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        passed = False
        if any(t == tag for t, _, _ in self.stack):
            while self.stack:
                t, skip, passed = self.stack.pop()
                if skip:
                    self.skip = max(0, self.skip - 1)
                if t == tag:
                    break
        if not passed:
            return
        if tag in ("td", "th"):
            return
        if tag == "a" and self.link:
            start, href = self.link
            text = self.cur.plain[start:].strip()
            if text:
                self.cur.stylize(f"underline {CYAN}", start, len(self.cur.plain))
                self.links.append((text, href))
            self.link = None
        super().handle_endtag(tag)


def parse_article(html_bytes):
    p = WikiHtml()
    try:
        p.feed(html_bytes.decode("utf-8", errors="replace"))
        p.close()
    except Exception:
        pass
    seen, links = set(), []
    for text, href in p.links:
        if href not in seen:
            seen.add(href)
            links.append((text, href))
    return reader.Chapter(p.first_heading, p.blocks, p.anchors), links


# ---------- ZIM helpers ----------
def zim_files():
    os.makedirs(WIKI, exist_ok=True)
    return sorted(f"{WIKI}/{f}" for f in os.listdir(WIKI) if f.endswith(".zim"))


def zim_title(archive, path):
    try:
        return archive.get_metadata("Title").decode() or os.path.basename(path)
    except Exception:
        return os.path.basename(path)


def resolve(archive, path):
    """Follow redirects; returns the entry or None."""
    path = urllib.parse.unquote(path.split("#")[0])
    for p in (path, path.replace(" ", "_")):
        if archive.has_entry_by_path(p):
            e = archive.get_entry_by_path(p)
            for _ in range(10):
                if not e.is_redirect:
                    return e
                e = e.get_redirect_entry()
    return None


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit in ("GB", "MB") else f"{n:.0f} {unit}"
        n /= 1024


# ---------- downloads ----------
def catalog(lang="eng"):
    """Wikipedia and friends from the Kiwix library: [(title, name, flavour, size, url, summary)]."""
    out = []
    for cat in ("wikipedia", "wiktionary", "wikivoyage", "wikibooks", "gutenberg"):
        q = urllib.parse.urlencode({"lang": lang, "count": 200, "category": cat})
        req = urllib.request.Request(f"{CATALOG}?{q}", headers={"User-Agent": "deck-wiki/1.0"})
        root = ET.fromstring(urllib.request.urlopen(req, timeout=20).read())
        A = "{http://www.w3.org/2005/Atom}"
        for e in root.findall(A + "entry"):
            acq = [l for l in e.findall(A + "link") if "acquisition" in (l.get("rel") or "")]
            if not acq:
                continue
            url = acq[0].get("href", "").removesuffix(".meta4")
            out.append({"title": e.findtext(A + "title") or "?", "name": e.findtext(A + "name") or "",
                        "flavour": e.findtext(A + "flavour") or "", "size": int(acq[0].get("length") or 0),
                        "url": url, "summary": e.findtext(A + "summary") or "", "cat": cat})
    return out


def dl_state_path():
    return f"{RUN}/deck-wiki-downloads.json"


def start_download(item):
    dest = f"{WIKI}/{os.path.basename(urllib.parse.urlparse(item['url']).path)}"
    part = dest + ".part"
    # curl resumes (-C -) and the file is renamed only when complete; it keeps going if the app closes.
    script = f'curl -fsSL -C - -o "{part}" "{item["url"]}" && mv "{part}" "{dest}"'
    subprocess.Popen(["sh", "-c", script], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    state = reader.load_json(dl_state_path(), {})
    state[dest] = {"title": f"{item['title']} ({item['flavour'] or 'full'})", "size": item["size"], "t": time.time()}
    reader.save_json(dl_state_path(), state)
    return dest


def downloads():
    """Downloads still in progress: [(title, done_bytes, size)]."""
    state = reader.load_json(dl_state_path(), {})
    out = []
    for dest, d in list(state.items()):
        if os.path.exists(dest):
            state.pop(dest)
            continue
        part = dest + ".part"
        done = os.path.getsize(part) if os.path.exists(part) else 0
        out.append((d["title"], done, d["size"], dest))
    reader.save_json(dl_state_path(), state)
    return out


# ---------- screens ----------
class Picker(ModalScreen):
    """Filterable list (contents, links). Returns the chosen index or None."""
    BINDINGS = [Binding("escape", "cancel", show=False)]
    DEFAULT_CSS = """
    Picker { align: center middle; background: #282a36 60%; }
    Picker > Vertical { width: 84; max-width: 95%; height: 90%; background: #21222c; border: round #bd93f9; padding: 1 2; }
    Picker Input { background: #282a36; border: tall #44475a; }
    Picker Input:focus { border: tall #bd93f9; }
    Picker OptionList { background: #21222c; border: none; height: 1fr; scrollbar-color: #bd93f9; }
    Picker OptionList > .option-list--option-highlighted,
    Picker OptionList:focus > .option-list--option-highlighted { background: #44475a; }
    """.replace("#bd93f9", ACCENT)

    def __init__(self, title, items, filterable=True):
        super().__init__()
        self.title_, self.items, self.filterable = title, items, filterable

    def compose(self):
        with Vertical():
            yield Static(Text(self.title_, style=f"bold {ACCENT}"))
            if self.filterable:
                yield Input(placeholder="type to filter")
            yield OptionList()

    def on_mount(self):
        self.fill("")
        (self.query_one(Input) if self.filterable else self.query_one(OptionList)).focus()

    def fill(self, q):
        ol = self.query_one(OptionList)
        ol.clear_options()
        q = q.lower()
        ol.add_options([Option(Text(label, style=FG), id=str(i)) for i, label in enumerate(self.items)
                        if q in label.lower()])
        if ol.option_count:
            ol.highlighted = 0

    def on_input_changed(self, e):
        self.fill(e.value)

    def on_input_submitted(self, e):
        e.stop()
        ol = self.query_one(OptionList)
        if ol.option_count:
            self.dismiss(int(ol.get_option_at_index(ol.highlighted).id))

    def on_key(self, e):
        ol = self.query_one(OptionList)
        if self.filterable and self.query_one(Input).has_focus and e.key in ("down", "up") and ol.option_count:
            ol.focus()
            ol.action_cursor_down() if e.key == "down" else None
            e.stop()
        elif e.key == "right" and ol.has_focus:
            ol.action_select()
            e.stop()

    def on_option_list_option_selected(self, e):
        e.stop()
        self.dismiss(int(e.option.id))

    def action_cancel(self):
        self.dismiss(None)


class ArticleScreen(Screen):
    BINDINGS = [
        Binding("down,j", "scroll(1)", show=False), Binding("up,k", "scroll(-1)", show=False),
        Binding("space,pagedown,right", "page(1)", show=False), Binding("pageup,shift+space", "page(-1)", show=False),
        Binding("home,g", "top", show=False), Binding("end,G", "bottom", show=False),
        Binding("t", "contents", show=False), Binding("l", "links", show=False),
        Binding("left,backspace,h", "back", show=False), Binding("escape", "to_search", show=False),
        Binding("slash", "to_search", show=False), Binding("q", "app.quit", show=False),
        Binding("question_mark", "help", show=False),
    ]
    DEFAULT_CSS = """
    ArticleScreen { background: #282a36; }
    ArticleScreen #ahead { height: 2; padding: 0 3; }
    ArticleScreen VerticalScroll { align-horizontal: center; scrollbar-color: #bd93f9; scrollbar-background: #21222c; }
    ArticleScreen #body { width: auto; height: auto; }
    ArticleScreen #afoot { height: 1; padding: 0 3; }
    """.replace("#bd93f9", ACCENT)

    def __init__(self, archive, entry):
        super().__init__()
        self.archive = archive
        self.history = []
        self.entry = entry

    def compose(self):
        yield Static(id="ahead")
        with VerticalScroll():
            yield Static(id="body")
        yield Static(id="afoot")

    def on_mount(self):
        self.query_one(VerticalScroll).can_focus = False
        self.load(self.entry)

    def on_resize(self, _):
        if getattr(self, "chapter", None):
            self.render_article(keep=True)

    def load(self, entry, anchor=None):
        self.entry = entry
        item = entry.get_item()
        if not item.mimetype.startswith("text/html"):
            self.app.notify(f"This is a {item.mimetype} file, not an article", timeout=2)
            return
        self.chapter, self.links = parse_article(bytes(item.content))
        self.render_article()
        if anchor and anchor in self.chapter.anchors:
            b = self.chapter.anchors[anchor]
            line = next((i for i, ln in enumerate(self.lines) if ln.block >= b), 0)
            self.query_one(VerticalScroll).scroll_to(y=line, animate=False)
        else:
            self.query_one(VerticalScroll).scroll_home(animate=False)

    def render_article(self, keep=False):
        width = min(80, max(30, self.size.width - 6))
        self.lines = reader.layout(self.chapter, width, PREFS, THEME)
        body = Text(no_wrap=True, overflow="crop")
        for i, ln in enumerate(self.lines):
            if i:
                body.append("\n")
            body.append_text(ln.text)
        b = self.query_one("#body", Static)
        b.styles.width = width
        b.update(body)
        heads = [k for k, _ in self.chapter.blocks if k in ("h2", "h3")]
        self.query_one("#ahead").update(Text.assemble(
            (I_WIKI + "  ", FG), (self.entry.title, f"bold {ACCENT}"),
            (f"   {sum(len(t.plain.split()) for _, t in self.chapter.blocks):,} words", GRAY)))
        self.query_one("#afoot").update(Text.assemble(
            ("t", CYAN), (f" contents ({len(heads)})  ", GRAY), ("l", CYAN), (f" links ({len(self.links)})  ", GRAY),
            ("Left", CYAN), (" back  " if self.history else " -  ", GRAY), ("/", CYAN), (" search  ", GRAY),
            ("Space", CYAN), (" page", GRAY)))

    def scroller(self):
        return self.query_one(VerticalScroll)

    def action_scroll(self, n):
        self.scroller().scroll_relative(y=n, animate=False)

    def action_page(self, d):
        self.scroller().scroll_relative(y=d * max(1, self.scroller().size.height - 2), animate=False)

    def action_top(self):
        self.scroller().scroll_home(animate=False)

    def action_bottom(self):
        self.scroller().scroll_end(animate=False)

    def action_contents(self):
        heads = []
        for b, (kind, t) in enumerate(self.chapter.blocks):
            if kind in ("h2", "h3"):
                heads.append((("    " if kind == "h3" else "") + t.plain.strip(), b))
        if not heads:
            self.app.notify("This article has no sections", timeout=1.5)
            return

        def done(i):
            if i is not None:
                b = heads[i][1]
                line = next((k for k, ln in enumerate(self.lines) if ln.block >= b), 0)
                self.scroller().scroll_to(y=max(0, line - 1), animate=False)
        self.app.push_screen(Picker("Contents", [h for h, _ in heads], filterable=len(heads) > 12), done)

    def action_links(self):
        if not self.links:
            self.app.notify("No links in this article", timeout=1.5)
            return

        def done(i):
            if i is None:
                return
            href = self.links[i][1]
            path, _, frag = href.partition("#")
            target = posixpath.normpath(posixpath.join(posixpath.dirname(self.entry.path), path)) if path else self.entry.path
            e = resolve(self.archive, target)
            if e is None:
                self.app.notify("That page is not in this ZIM file", timeout=2)
                return
            self.history.append((self.entry, self.scroller().scroll_y))
            self.load(e, frag or None)
        self.app.push_screen(Picker(f"Links in {self.entry.title}", [t for t, _ in self.links]), done)

    def action_back(self):
        if not self.history:
            return self.action_to_search()
        entry, y = self.history.pop()
        self.load(entry)
        self.call_after_refresh(lambda: self.scroller().scroll_to(y=y, animate=False))

    def action_to_search(self):
        self.app.pop_screen()

    def action_help(self):
        self.app.notify("Up/Down scroll, Space page, t contents, l links, Left back, / search, q quit", timeout=5)


class LibraryScreen(Screen):
    """ZIM files on the deck, downloads in progress, and the Kiwix catalog."""
    BINDINGS = [Binding("escape,left", "close", show=False), Binding("d,delete", "delete", show=False),
                Binding("right", "pick", show=False)]
    DEFAULT_CSS = """
    LibraryScreen { background: #282a36; }
    LibraryScreen #ltitle { height: 3; padding: 1 3 0 3; }
    LibraryScreen #lstatus { height: 2; padding: 0 4; color: #6272a4; }
    LibraryScreen OptionList, LibraryScreen OptionList:focus { background: #282a36; border: none; padding: 0 2;
        height: 1fr; scrollbar-color: #bd93f9; background-tint: #282a36 0%; }
    LibraryScreen OptionList > .option-list--option-highlighted,
    LibraryScreen OptionList:focus > .option-list--option-highlighted { background: #44475a; }
    """.replace("#bd93f9", ACCENT)

    def compose(self):
        yield Static(Text.assemble((I_DL + "  ", CYAN), ("Get offline libraries", f"bold {ACCENT}"),
                                   ("   from library.kiwix.org", GRAY)), id="ltitle")
        yield Static(id="lstatus")
        yield OptionList()

    def on_mount(self):
        self.items = []
        self.cat = None
        self.query_one(OptionList).focus()
        self.refresh_list()
        self.load_catalog()
        self.set_interval(2, self.refresh_list)

    @work(thread=True, exclusive=True)
    def load_catalog(self):
        try:
            cat = catalog()
        except Exception as ex:
            self.app.call_from_thread(self.query_one("#lstatus").update, f"Could not load the catalog: {ex}")
            return
        # Text-only ("nopic") and intro-only ("mini") versions suit a terminal; list them first.
        order = {"nopic": 0, "mini": 1, "": 2, "maxi": 3}
        cat.sort(key=lambda c: (c["cat"] != "wikipedia", c["name"] != "wikipedia_en_all", c["name"],
                                order.get(c["flavour"], 2)))
        self.cat = cat
        self.app.call_from_thread(self.refresh_list)

    def refresh_list(self):
        ol = self.query_one(OptionList)
        keep = ol.highlighted
        free = shutil.disk_usage(HOME).free
        self.items = []
        opts = []
        for f in zim_files():
            self.items.append(("have", f))
            opts.append(Option(Text.assemble((I_BOOK + "  ", GREEN), (os.path.basename(f), f"bold {FG}"),
                                             (f"   {human(os.path.getsize(f))}   on the deck   d deletes", GRAY)),
                               id=str(len(self.items) - 1)))
        for title, done, size, dest in downloads():
            pct = done / size * 100 if size else 0
            self.items.append(("dl", dest))
            opts.append(Option(Text.assemble((I_DL + "  ", YELLOW), (title, f"bold {FG}"),
                                             (f"   downloading {pct:.0f}%  {human(done)} of {human(size)}", YELLOW)),
                               id=str(len(self.items) - 1)))
        for c in self.cat or []:
            self.items.append(("cat", c))
            fits = c["size"] < free - 2 * 1024 ** 3
            label = {"nopic": "text only", "mini": "intros only", "maxi": "with pictures", "": ""}.get(c["flavour"], c["flavour"])
            t = Text.assemble((I_WIKI + "  ", FG if fits else GRAY), (c["title"], f"bold {FG}" if fits else GRAY),
                              (f"  {label}" if label else "", CYAN if fits else GRAY),
                              (f"   {human(c['size'])}", YELLOW if fits else RED))
            if not fits:
                t.append("  too big for the free space", RED)
            opts.append(Option(t, id=str(len(self.items) - 1)))
        ol.clear_options()
        ol.add_options(opts)
        if opts:
            ol.highlighted = min(keep or 0, ol.option_count - 1)
        n = len(self.cat or [])
        self.query_one("#lstatus").update(Text.assemble(
            (f"{human(free)} free on the deck.  ", FG),
            ("Enter downloads. Text-only versions suit this deck best." if n else "Loading the catalog...", GRAY)))

    def current(self):
        ol = self.query_one(OptionList)
        if ol.highlighted is None or not self.items:
            return None
        return self.items[int(ol.get_option_at_index(ol.highlighted).id)]

    def action_pick(self):
        self.query_one(OptionList).action_select()

    def on_option_list_option_selected(self, e):
        kind, val = self.items[int(e.option.id)]
        if kind == "cat":
            free = shutil.disk_usage(HOME).free
            if val["size"] > free - 2 * 1024 ** 3:
                self.app.notify("Not enough free space for this one", severity="warning", timeout=3)
                return
            start_download(val)
            self.app.notify(f"Downloading {val['title']} ({human(val['size'])}). It continues if you close the app.",
                            timeout=4)
            self.refresh_list()
        elif kind == "have":
            self.dismiss(val)

    def action_delete(self):
        cur = self.current()
        if cur and cur[0] == "have":
            os.remove(cur[1])
            self.app.notify(f"Deleted {os.path.basename(cur[1])}", timeout=2)
            self.refresh_list()

    def action_close(self):
        self.dismiss(None)


class Wiki(App):
    TITLE = "Wikipedia"
    CSS = """
    Screen { background: #282a36; }
    #title { height: 3; padding: 1 3 0 3; }
    #q { margin: 0 3; background: #21222c; border: tall #44475a; }
    #q:focus { border: tall #bd93f9; }
    #status { height: 1; padding: 0 4; color: #6272a4; }
    #list, #list:focus { background: #282a36; border: none; padding: 0 2; height: 1fr;
                         scrollbar-color: #bd93f9; scrollbar-background: #21222c; background-tint: #282a36 0%; }
    OptionList > .option-list--option-highlighted,
    OptionList:focus > .option-list--option-highlighted { background: #44475a; color: #f8f8f2; }
    #hint { height: 1; padding: 0 3; }
    """.replace("#bd93f9", ACCENT)
    BINDINGS = [Binding("ctrl+r", "random", show=False, priority=True), Binding("ctrl+g", "library", show=False, priority=True),
                Binding("tab", "next_zim", show=False, priority=True), Binding("escape", "focus_search", show=False),
                Binding("ctrl+q", "quit", show=False)]

    def __init__(self):
        super().__init__()
        self.zims = []
        self.archive = None
        self.zim_path = None
        self.results = []

    @property
    def main(self):
        return self.screen_stack[0]

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        yield Input(placeholder="search titles, then Enter or Down", id="q")
        yield Static(id="status")
        yield OptionList(id="list")
        yield Static(Text.assemble(("Enter", CYAN), (" read  ", GRAY), ("Ctrl+R", CYAN), (" random  ", GRAY),
                                   ("Tab", CYAN), (" next library  ", GRAY), ("Ctrl+G", CYAN), (" get libraries  ", GRAY),
                                   ("Ctrl+Q", CYAN), (" quit", GRAY)), id="hint")

    def on_mount(self):
        self.zims = zim_files()
        if not self.zims:
            self.main.query_one("#title").update(Text.assemble((I_WIKI + "  ", FG), ("Wikipedia", f"bold {ACCENT}")))
            self.status("No offline libraries yet. Opening the download list...")
            self.call_after_refresh(self.action_library)
            return
        self.open_zim(self.zims[0])

    def status(self, msg):
        self.main.query_one("#status").update(msg)

    def open_zim(self, path):
        try:
            self.archive = Archive(path)
        except Exception as ex:
            self.status(f"Could not open {os.path.basename(path)}: {ex}")
            return
        self.zim_path = path
        name = zim_title(self.archive, path)
        others = f"   {self.zims.index(path) + 1} of {len(self.zims)}, Tab for next" if len(self.zims) > 1 else ""
        self.main.query_one("#title").update(Text.assemble(
            (I_WIKI + "  ", FG), (name, f"bold {ACCENT}"),
            (f"   {self.archive.article_count:,} articles{others}", GRAY)))
        self.status("Type to search titles. Ctrl+R for a random article.")
        self.results = []
        self.main.query_one("#list", OptionList).clear_options()
        box = self.main.query_one("#q", Input)
        box.value = ""
        box.focus()
        self.show_start()

    def show_start(self):
        """Before searching: the library's main page and a random pick."""
        rows = []
        try:
            m = self.archive.main_entry
            m = m.get_redirect_entry() if m.is_redirect else m
            rows.append((m, f"{I_BOOK}  Main page"))
        except Exception:
            pass
        rows.append((None, f"{I_DICE}  Random article"))
        self.show_rows(rows)

    def show_rows(self, rows):
        self.results = rows
        ol = self.main.query_one("#list", OptionList)
        ol.clear_options()
        ol.add_options([Option(Text(label, style=f"bold {FG}"), id=str(i)) for i, (_, label) in enumerate(rows)])
        if rows:
            ol.highlighted = 0

    def on_input_changed(self, e):
        if e.input.id == "q" and self.archive:
            q = e.value.strip()
            if not q:
                self.show_start()
                return
            self.search(q)

    @work(thread=True, exclusive=True)
    def search(self, q):
        rows = []
        try:
            paths = list(SuggestionSearcher(self.archive).suggest(q).getResults(0, 40))
            for p in paths:
                e = self.archive.get_entry_by_path(p)
                rows.append((e, e.title))
            if len(rows) < 5 and self.archive.has_fulltext_index:
                found = Searcher(self.archive).search(Query().set_query(q)).getResults(0, 30)
                have = {r[0].path for r in rows}
                for p in found:
                    if p not in have:
                        e = self.archive.get_entry_by_path(p)
                        rows.append((e, f"{e.title}   (mentions it)"))
        except Exception as ex:
            self.call_from_thread(self.status, f"Search failed: {ex}")
            return
        self.call_from_thread(self._searched, q, rows)

    def _searched(self, q, rows):
        if self.main.query_one("#q", Input).value.strip() != q:
            return
        self.show_rows(rows)
        self.status(f"{len(rows)} titles" if rows else "No titles match. Try fewer words.")

    def on_input_submitted(self, e):
        if e.input.id == "q" and self.results:
            self.open_row(0)

    def on_key(self, event):
        if self.screen is not self.main:
            return
        box = self.main.query_one("#q", Input)
        ol = self.main.query_one("#list", OptionList)
        if box.has_focus and event.key == "down" and ol.option_count:
            ol.focus()
            event.stop()
        elif ol.has_focus and event.key == "up" and ol.highlighted == 0:
            box.focus()
            event.stop()
        elif ol.has_focus and event.key == "right":
            ol.action_select()
            event.stop()

    def on_option_list_option_selected(self, e):
        if e.option_list.id == "list":
            self.open_row(int(e.option.id))

    def open_row(self, i):
        entry, _ = self.results[i]
        if entry is None:
            return self.action_random()
        e = resolve(self.archive, entry.path) or entry
        self.push_screen(ArticleScreen(self.archive, e))

    def action_random(self):
        if not self.archive:
            return
        for _ in range(20):
            e = self.archive.get_random_entry()
            if e.get_item().mimetype.startswith("text/html"):
                self.push_screen(ArticleScreen(self.archive, e))
                return

    def action_next_zim(self):
        if self.screen is self.main and len(self.zims) > 1:
            self.open_zim(self.zims[(self.zims.index(self.zim_path) + 1) % len(self.zims)])

    def action_focus_search(self):
        if self.screen is self.main:
            self.main.query_one("#q", Input).focus()

    def action_library(self):
        def done(path):
            self.zims = zim_files()
            if path:
                self.open_zim(path)
            elif self.zims and not self.archive:
                self.open_zim(self.zims[0])
            elif not self.zims:
                self.status("No offline libraries yet. Ctrl+G opens the download list.")
        if not isinstance(self.screen, LibraryScreen):
            self.push_screen(LibraryScreen(), done)


if __name__ == "__main__":
    Wiki().run()
