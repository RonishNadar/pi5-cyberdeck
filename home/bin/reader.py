#!/home/admin/.venvs/deck/bin/python
"""Deck e-reader: paged reading of EPUB, TXT, Markdown and PDF (text) books from ~/Books.

Library: Up/Down pick, Right/Enter open, g get books (Project Gutenberg), d delete, r rescan.
Reading: Right/Space next page, Left previous, Up/Down scroll a line, t contents, b bookmark, m bookmarks,
/ search, n next match, [ ] chapter, v view, f focus, +/- width, a theme, j justify, ? help, Esc back.
Tap the right two thirds of the page for next, the left third for previous.
"""
import html
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser
from posixpath import dirname, normpath, join as pjoin

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
import dataclasses

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.theme import BUILTIN_THEMES
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

import deck_ui
deck_ui.install()

HOME = os.path.expanduser("~")
BOOKS = f"{HOME}/Books"
STATE = f"{HOME}/.config/deck/reader.json"
CACHE = f"{HOME}/.cache/deck/reader-meta.json"
EXTS = (".epub", ".txt", ".md", ".pdf")

PURPLE, PINK, CYAN, GREEN = "#bd93f9", "#ff79c6", "#8be9fd", "#50fa7b"
ORANGE, YELLOW, RED, GRAY, FG = "#ffb86c", "#f1fa8c", "#ff5555", "#6272a4", "#f8f8f2"
try:
    ACCENT = json.load(open(f"{HOME}/.config/deck/prefs.json")).get("accent", PURPLE)
except Exception:
    ACCENT = PURPLE

# name: background, text, dim text, heading, emphasis
THEMES = {
    "Dracula": ("#282a36", "#f8f8f2", "#6272a4", ACCENT, "#f1fa8c"),
    "Night":   ("#000000", "#9a9aa6", "#44475a", "#8a74b8", "#b8b48a"),
    "Paper":   ("#f4ecd8", "#3b3228", "#9a8c74", "#7a4e2d", "#5b3a8c"),
}
WIDTHS = [50, 60, 66, 72, 80, 90, 100]
VIEWS = ["Single", "Fit", "Split"]   # one column at the set width, full width, two pages side by side
GUTTER = 7                           # space between the two pages in Split, with a spine in the middle
WPM = 250


# ---------- state ----------
def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


class State:
    def __init__(self):
        d = load_json(STATE, {})
        self.books = d.get("books", {})
        self.prefs = {"theme": "Dracula", "width": 72, "justify": True, "spacing": True,
                      "view": "Single", "focus": False}
        self.prefs.update(d.get("prefs", {}))

    def book(self, path):
        return self.books.setdefault(os.path.relpath(path, BOOKS), {})

    def save(self):
        save_json(STATE, {"books": self.books, "prefs": self.prefs})


# ---------- parsing ----------
# A chapter is a list of blocks: (kind, Text). kind: h1 h2 h3 p quote pre li hr img.
# anchors maps an element id to a block index, for table of contents links.
BLOCK_TAGS = {"p", "div", "section", "article", "body", "blockquote", "li", "dt", "dd", "tr",
              "h1", "h2", "h3", "h4", "h5", "h6", "pre", "figure", "figcaption", "table",
              "ul", "ol", "dl", "header", "footer", "aside", "nav", "td", "th", "center"}
SKIP_TAGS = {"script", "style", "head", "title", "svg", "math"}


class Chapter:
    def __init__(self, title="", blocks=None, anchors=None):
        self.title = title
        self.blocks = blocks or []
        self.anchors = anchors or {}
        self.words = sum(len(t.plain.split()) for _, t in self.blocks)


class HtmlToBlocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.anchors = [], {}
        self.cur = Text()
        self.kind = "p"
        self.styles = []          # stack of (tag, style)
        self.skip = 0
        self.pre = 0
        self.quote = 0
        self.list_depth = 0
        self.pending_ids = []
        self.first_heading = ""

    def flush(self, next_kind=None):
        t = self.cur
        if not self.pre:
            t = collapse(t)
        if t.plain.strip():
            for i in self.pending_ids:
                self.anchors.setdefault(i, len(self.blocks))
            self.pending_ids = []
            kind = self.kind
            if kind == "p" and self.quote:
                kind = "quote"
            if kind in ("h1", "h2", "h3") and not self.first_heading:
                self.first_heading = t.plain.strip()
            self.blocks.append((kind, t))
        self.cur = Text()
        self.kind = next_kind or ("pre" if self.pre else "p")

    def style(self):
        return " ".join(s for _, s in self.styles if s) or None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        for key in ("id", "name"):
            if a.get(key):
                self.pending_ids.append(a[key])
        if tag in BLOCK_TAGS:
            if tag in ("h1", "h2"):
                self.flush("h1" if tag == "h1" else "h2")
            elif tag in ("h3", "h4", "h5", "h6"):
                self.flush("h3")
            elif tag == "li":
                self.flush("li")
                self.cur.append("  " * max(0, self.list_depth - 1) + "• ")
            elif tag == "pre":
                self.flush("pre")
                self.pre += 1
            elif not (self.kind == "li" and self.cur.plain.strip() in ("", "\u2022")):
                self.flush()
            if tag == "blockquote":
                self.quote += 1
            if tag in ("ul", "ol"):
                self.list_depth += 1
        elif tag == "br":
            self.cur.append("\n" if self.pre else BR)
        elif tag == "hr":
            self.flush()
            self.blocks.append(("hr", Text("")))
        elif tag == "img":
            alt = (a.get("alt") or "").strip()
            if alt and len(alt) < 120 and not re.match(r"(?i)^(image|cover|img)?\s*\d*$", alt):
                self.flush()
                self.blocks.append(("img", Text(alt)))
        if tag in ("b", "strong"):
            self.styles.append((tag, "bold"))
        elif tag in ("i", "em", "cite", "var", "dfn"):
            self.styles.append((tag, "italic"))
        elif tag in ("code", "kbd", "tt", "samp"):
            self.styles.append((tag, "code"))
        elif tag in ("sup", "sub", "small", "a", "span", "u", "s", "abbr", "q", "big", "font"):
            self.styles.append((tag, "underline" if tag == "u" else ""))

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if self.styles and any(t == tag for t, _ in self.styles):
            while self.styles:
                t, _ = self.styles.pop()
                if t == tag:
                    break
        if tag in BLOCK_TAGS:
            if not (self.kind == "li" and self.cur.plain.strip() in ("", "\u2022")) or tag in ("li", "ul", "ol"):
                self.flush()
            if tag == "pre":
                self.pre = max(0, self.pre - 1)
                self.kind = "pre" if self.pre else "p"
            if tag == "blockquote":
                self.quote = max(0, self.quote - 1)
            if tag in ("ul", "ol"):
                self.list_depth = max(0, self.list_depth - 1)

    def handle_data(self, data):
        if self.skip:
            return
        self.cur.append(data, self.style())

    def close(self):
        super().close()
        self.flush()


BR = "\u2028"   # marks a <br>, so it survives whitespace collapsing


def collapse(t: Text) -> Text:
    """Collapse runs of whitespace like a browser does, keeping styles and <br> breaks."""
    out, prev_space = Text(), True
    for s, style in _runs(t):
        buf = []
        for ch in s:
            if ch == BR:
                while buf and buf[-1] == " ":
                    buf.pop()
                buf.append("\n")
                prev_space = True
            elif ch.isspace():
                if not prev_space:
                    buf.append(" ")
                prev_space = True
            else:
                buf.append(ch)
                prev_space = False
        if buf:
            out.append("".join(buf), style)
    out.rstrip()
    return out


def _runs(t: Text):
    """Split a Text into (string, style) runs."""
    plain = t.plain
    marks = {0, len(plain)}
    for sp in t.spans:
        marks.update((sp.start, sp.end))
    pts = sorted(m for m in marks if 0 <= m <= len(plain))
    for a, b in zip(pts, pts[1:]):
        styles = [str(sp.style) for sp in t.spans if sp.start <= a and sp.end >= b and sp.style]
        yield plain[a:b], " ".join(styles) or None


def html_chapter(raw: bytes) -> Chapter:
    p = HtmlToBlocks()
    try:
        p.feed(raw.decode("utf-8", errors="replace"))
        p.close()
    except Exception:
        pass
    return Chapter(p.first_heading, p.blocks, p.anchors)


class Book:
    """Loaded book: chapters (parsed lazily for EPUB), toc [(title, chapter, anchor)], meta."""

    def __init__(self, path):
        self.path = path
        self.title = os.path.splitext(os.path.basename(path))[0]
        self.author = ""
        self.toc = []
        self._chapters = {}
        self.sizes = []
        ext = path.lower().rsplit(".", 1)[-1]
        getattr(self, f"_open_{ext}")()
        self.total = sum(self.sizes) or 1

    def __len__(self):
        return len(self.sizes)

    def chapter(self, i) -> Chapter:
        if i not in self._chapters:
            self._chapters[i] = self._load(i)
        return self._chapters[i]

    def fraction(self, ch, frac):
        return (sum(self.sizes[:ch]) + self.sizes[ch] * frac) / self.total

    def chapter_title(self, ch):
        best = ""
        for title, c, _ in self.toc:
            if c <= ch:
                best = title
        return best or self.chapter(ch).title

    # --- EPUB ---
    def _open_epub(self):
        self.zip = zipfile.ZipFile(self.path)
        names = set(self.zip.namelist())
        cont = ET.fromstring(self.zip.read("META-INF/container.xml"))
        opf_path = next(e.get("full-path") for e in cont.iter() if e.tag.endswith("rootfile"))
        base = dirname(opf_path)
        opf = ET.fromstring(self.zip.read(opf_path))

        def tag(e):
            return e.tag.rsplit("}", 1)[-1]

        manifest, nav_href, ncx_href = {}, None, None
        for e in opf.iter():
            t = tag(e)
            if t == "title" and e.text and self.title == os.path.splitext(os.path.basename(self.path))[0]:
                self.title = e.text.strip()
            elif t == "creator" and e.text and not self.author:
                self.author = e.text.strip()
            elif t == "item":
                href = normpath(pjoin(base, urllib.parse.unquote(e.get("href", ""))))
                manifest[e.get("id")] = (href, e.get("media-type", ""))
                if "nav" in (e.get("properties") or "").split():
                    nav_href = href
                if e.get("media-type") == "application/x-dtbncx+xml":
                    ncx_href = href
        self.spine = []
        for e in opf.iter():
            if tag(e) == "itemref" and e.get("linear", "yes") != "no":
                href, mt = manifest.get(e.get("idref"), (None, ""))
                if href in names and "html" in mt:
                    self.spine.append(href)
        self.sizes = [max(1, self.zip.getinfo(h).file_size) for h in self.spine]
        index = {h: i for i, h in enumerate(self.spine)}

        def add(label, src, rel_base):
            path, _, frag = src.partition("#")
            full = normpath(pjoin(rel_base, urllib.parse.unquote(path)))
            if full in index and label.strip():
                self.toc.append((" ".join(label.split()), index[full], frag or None))

        try:
            if ncx_href and ncx_href in names:
                ncx = ET.fromstring(self.zip.read(ncx_href))
                for np in ncx.iter():
                    if tag(np) == "navPoint":
                        label = next(("".join(x.itertext()) for x in np if tag(x) == "navLabel"), "")
                        src = next((x.get("src") for x in np if tag(x) == "content"), None)
                        if src:
                            add(label, src, dirname(ncx_href))
            elif nav_href and nav_href in names:
                nav = ET.fromstring(self.zip.read(nav_href))
                for n in nav.iter():
                    if tag(n) == "nav" and any(v == "toc" for k, v in n.attrib.items() if k.endswith("type")):
                        for a in n.iter():
                            if tag(a) == "a" and a.get("href"):
                                add("".join(a.itertext()), a.get("href"), dirname(nav_href))
                        break
        except ET.ParseError:
            pass
        # Drop entries that point at the same spot as the one before (Gutenberg repeats a lot).
        seen, toc = set(), []
        for t in self.toc:
            if (t[1], t[2]) not in seen:
                seen.add((t[1], t[2]))
                toc.append(t)
        self.toc = toc

    def _load_epub(self, i):
        return html_chapter(self.zip.read(self.spine[i]))

    # --- plain text and markdown ---
    HEAD_RE = re.compile(r"^\s*((CHAPTER|Chapter|BOOK|Book|PART|Part|STAVE|Stave)\b.{0,60}|[IVXLC]+\.?\s*)$")

    def _open_txt(self, text=None, markdown=False):
        if text is None:
            with open(self.path, "rb") as f:
                text = f.read().decode("utf-8", errors="replace")
        text = text.replace("\r\n", "\n")
        if not markdown:
            for key in ("Title", "Author"):
                m = re.search(rf"^{key}:\s*(.+)$", text[:5000], re.M)
                if m:
                    setattr(self, key.lower(), m.group(1).strip())
            # Project Gutenberg plain text: keep only the book between the START and END markers
            m = re.search(r"^\*\*\* ?START OF.*$", text, re.M)
            if m:
                text = text[m.end():]
            m = re.search(r"^\*\*\* ?END OF.*$", text, re.M)
            if m:
                text = text[:m.start()]
        paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        chapters, cur, title = [], [], ""
        for p in paras:
            lines = p.strip("\n")
            if markdown and lines.startswith("#"):
                level = len(lines) - len(lines.lstrip("#"))
                head = lines.lstrip("#").strip()
                if level <= 2 and cur:
                    chapters.append((title, cur))
                    cur = []
                if level <= 2:
                    title = head
                cur.append(("h1" if level == 1 else "h2" if level == 2 else "h3", Text(head)))
                continue
            first, _, rest = lines.strip().partition("\n")
            if not markdown and "\n" not in rest.strip() and len(rest) < 80 and self.HEAD_RE.match(first):
                if cur:
                    chapters.append((title, cur))
                    cur = []
                title = " ".join(lines.split())
                cur.append(("h2", Text(title)))
                continue
            if markdown and lines.lstrip().startswith("```"):
                cur.append(("pre", Text(lines.strip("`\n"))))
                continue
            body = " ".join(lines.split())
            if markdown:
                lines = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", lines)
                items = re.split(r"\n\s*(?=[-*+] |\d+\. )", "\n" + lines.strip())
                if len(items) > 1 or re.match(r"\s*([-*+]|\d+\.) ", lines):
                    for it in items:
                        if it.strip():
                            it = re.sub(r"^\s*[-*+]\s", "• ", " ".join(it.split()))
                            cur.append(("li", md_inline(it)))
                else:
                    cur.append(("p", md_inline(" ".join(lines.split()))))
            else:
                cur.append(("p", md_inline(body)))
            if len(cur) > 400 and not title:    # unstructured text: cut into sections
                chapters.append((f"Section {len(chapters) + 1}", cur))
                cur = []
        if cur:
            chapters.append((title, cur))
        self._text_chapters = [Chapter(t, b) for t, b in chapters] or [Chapter("", [("p", Text("(empty)"))])]
        self.sizes = [max(1, sum(len(x.plain) for _, x in c.blocks)) for c in self._text_chapters]
        self.toc = [(c.title, i, None) for i, c in enumerate(self._text_chapters) if c.title]

    def _open_md(self):
        with open(self.path, "rb") as f:
            text = f.read().decode("utf-8", errors="replace")
        m = re.search(r"^#\s+(.+)$", text, re.M)
        if m:
            self.title = m.group(1).strip()
        self._open_txt(text, markdown=True)

    def _open_pdf(self):
        r = subprocess.run(["pdftotext", "-enc", "UTF-8", self.path, "-"], capture_output=True)
        pages = r.stdout.decode("utf-8", errors="replace").split("\f")
        info = subprocess.run(["pdfinfo", self.path], capture_output=True, text=True).stdout
        for line in info.splitlines():
            k, _, v = line.partition(":")
            if k == "Title" and v.strip():
                self.title = v.strip()
            if k == "Author" and v.strip():
                self.author = v.strip()
        chapters = []
        for start in range(0, len(pages), 10):
            blocks = []
            for n, pg in enumerate(pages[start:start + 10], start + 1):
                for p in re.split(r"\n\s*\n", pg):
                    body = re.sub(r"-\n(\w)", r"\1", p)
                    body = " ".join(body.split())
                    if body and not re.fullmatch(r"\d{1,4}", body):   # drop bare page numbers
                        blocks.append(("p", Text(body)))
            if blocks:
                chapters.append(Chapter(f"Pages {start + 1}-{min(start + 10, len(pages))}", blocks))
        self._text_chapters = chapters or [Chapter("", [("p", Text("No text found in this PDF."))])]
        self.sizes = [max(1, sum(len(x.plain) for _, x in c.blocks)) for c in self._text_chapters]
        self.toc = [(c.title, i, None) for i, c in enumerate(self._text_chapters)]

    def _load(self, i):
        if hasattr(self, "spine"):
            return self._load_epub(i)
        return self._text_chapters[i]


def md_inline(s: str) -> Text:
    t, pos = Text(), 0
    for m in re.finditer(r"\*\*(.+?)\*\*|__(.+?)__|\*(.+?)\*|_(.+?)_|`(.+?)`", s):
        t.append(s[pos:m.start()])
        if m.group(1) or m.group(2):
            t.append(m.group(1) or m.group(2), "bold")
        elif m.group(3) or m.group(4):
            t.append(m.group(3) or m.group(4), "italic")
        else:
            t.append(m.group(5), "code")
        pos = m.end()
    t.append(s[pos:])
    return t


def quick_meta(path):
    """Title and author without parsing the whole book (cached by mtime)."""
    if path.lower().endswith(".epub"):
        try:
            z = zipfile.ZipFile(path)
            cont = ET.fromstring(z.read("META-INF/container.xml"))
            opf_path = next(e.get("full-path") for e in cont.iter() if e.tag.endswith("rootfile"))
            opf = ET.fromstring(z.read(opf_path))
            title = next((e.text for e in opf.iter() if e.tag.endswith("}title") and e.text), None)
            author = next((e.text for e in opf.iter() if e.tag.endswith("}creator") and e.text), "")
            return {"title": (title or "").strip() or nice_name(path), "author": author.strip()}
        except Exception:
            pass
    if path.lower().endswith(".pdf"):
        info = subprocess.run(["pdfinfo", path], capture_output=True, text=True).stdout
        d = dict((l.partition(":")[0], l.partition(":")[2].strip()) for l in info.splitlines())
        return {"title": d.get("Title") or nice_name(path), "author": d.get("Author", "")}
    try:
        with open(path, "rb") as f:
            head = f.read(5000).decode("utf-8", errors="replace")
    except OSError:
        head = ""
    if path.lower().endswith(".md"):
        m = re.search(r"^#\s+(.+)$", head, re.M)
        return {"title": m.group(1).strip() if m else nice_name(path), "author": ""}
    t = re.search(r"^Title:\s*(.+)$", head, re.M)
    a = re.search(r"^Author:\s*(.+)$", head, re.M)
    return {"title": t.group(1).strip() if t else nice_name(path), "author": a.group(1).strip() if a else ""}


def nice_name(path):
    return re.sub(r"[_-]+", " ", os.path.splitext(os.path.basename(path))[0]).strip().title()


# ---------- layout ----------
class Line:
    __slots__ = ("text", "block", "offset")

    def __init__(self, text, block, offset):
        self.text, self.block, self.offset = text, block, offset


def wrap_text(t: Text, width: int, justify: bool):
    """Greedy word wrap. Yields (Text, start_offset). Justifies all but the last line of a paragraph."""
    plain = t.plain
    for para_start, para in _hard_lines(plain):
        words = [(m.start() + para_start, m.end() + para_start) for m in re.finditer(r"\S+", para)]
        if not words:
            yield Text(""), para_start
            continue
        i = 0
        while i < len(words):
            j, length = i + 1, words[i][1] - words[i][0]
            while j < len(words) and length + 1 + (words[j][1] - words[j][0]) <= width:
                length += 1 + words[j][1] - words[j][0]
                j += 1
            chunk = words[i:j]
            if length > width:   # one very long word: hard split
                s, e = chunk[0]
                yield t[s:s + width], s
                words[i] = (s + width, e)
                continue
            last = j >= len(words)
            gaps = len(chunk) - 1
            extra = width - length if (justify and not last and gaps) else 0
            line = Text()
            for k, (s, e) in enumerate(chunk):
                line.append_text(t[s:e])
                if k < gaps:
                    line.append(" " * (1 + extra // gaps + (1 if k < extra % gaps else 0)))
            yield line, chunk[0][0]
            i = j


def _hard_lines(plain):
    pos = 0
    for part in plain.split("\n"):
        yield pos, part
        pos += len(part) + 1


def layout(ch: Chapter, width: int, prefs, theme):
    """Turn a chapter into display lines. Each line remembers its block and character offset."""
    bg, fg, dim, head, emph = theme
    lines = []

    def blank(b):
        lines.append(Line(Text(""), b, 0))

    for b, (kind, t) in enumerate(ch.blocks):
        t = t.copy()
        t.spans = [sp._replace(style=" ".join(emph if w == "code" else w for w in str(sp.style).split()))
                   if "code" in str(sp.style).split() else sp for sp in t.spans]
        if kind in ("h1", "h2", "h3"):
            if lines:
                blank(b)
                if kind != "h3":
                    blank(b)
            style = f"bold {head}" if kind != "h3" else f"bold {fg}"
            for ln, off in wrap_text(t, width, False):
                ln.stylize(style)
                ln.align("center", width)
                lines.append(Line(ln, b, off))
            if kind == "h1" or kind == "h2":
                rule = Text("─" * min(width // 4, 12), style=dim)
                rule.align("center", width)
                lines.append(Line(rule, b, 0))
            blank(b)
            continue
        if kind == "hr":
            if lines and lines[-1].text.plain:
                blank(b)
            orn = Text("•   •   •", style=dim)
            orn.align("center", width)
            lines.append(Line(orn, b, 0))
            blank(b)
            continue
        if kind == "img":
            for ln, off in wrap_text(Text(f"  {t.plain}"), width - 4, False):
                ln.stylize(f"italic {dim}")
                ln.align("center", width)
                lines.append(Line(ln, b, off))
            blank(b)
            continue
        if kind == "pre":
            for ln, off in _pre_lines(t, width - 2):
                lines.append(Line(Text("  ") + Text(ln.plain, style=emph), b, off))
            blank(b)
            continue
        indent = {"quote": 4, "li": 2}.get(kind, 0)
        para_indent = 0 if (prefs["spacing"] or kind != "p") else 3
        for ln, off in wrap_text(Text("\u2800" * para_indent) + t if para_indent else t,
                                 width - indent, prefs["justify"] and kind in ("p", "quote")):
            if para_indent:
                off = max(0, off - para_indent)
            pre = Text(" " * indent)
            if kind == "quote":
                pre = Text("│   ", style=dim)
            lines.append(Line(pre + ln, b, off))
        nxt = ch.blocks[b + 1][0] if b + 1 < len(ch.blocks) else None
        if kind == "li" and nxt == "li":
            continue
        if prefs["spacing"] or kind != "p":
            blank(b)
    while lines and not lines[-1].text.plain:
        lines.pop()
    return lines or [Line(Text(""), 0, 0)]


def _pre_lines(t, width):
    for start, part in _hard_lines(t.plain):
        for k in range(0, max(1, len(part)), width):
            yield Text(part[k:k + width]), start + k


# ---------- reader ----------
class ReaderScreen(Screen):
    BINDINGS = [
        Binding("right,space,pagedown,l", "page(1)", "Next", show=False),
        Binding("left,pageup,h,shift+space", "page(-1)", "Prev", show=False),
        Binding("down,j", "scroll(1)", "Line down", show=False),
        Binding("up,k", "scroll(-1)", "Line up", show=False),
        Binding("home", "chapter_start", show=False),
        Binding("bracketright", "chapter(1)", show=False),
        Binding("bracketleft", "chapter(-1)", show=False),
        Binding("t", "toc", show=False),
        Binding("b", "bookmark", show=False),
        Binding("m", "bookmarks", show=False),
        Binding("slash", "search", show=False),
        Binding("n", "next_match", show=False),
        Binding("plus,equals_sign", "width(1)", show=False),
        Binding("minus", "width(-1)", show=False),
        Binding("a", "theme", show=False),
        Binding("J", "justify", show=False),
        Binding("s", "spacing", show=False),
        Binding("v", "view", show=False),
        Binding("f", "focus_mode", show=False),
        Binding("g", "goto", show=False),
        Binding("question_mark", "help", show=False),
        Binding("escape,q,backspace", "close", show=False),
    ]

    def __init__(self, book: Book, state: State):
        super().__init__()
        self.book, self.state = book, state
        self.rec = state.book(book.path)
        self.ch = self.rec.get("ch", 0) if self.rec.get("ch", 0) < len(book) else 0
        self.anchor = (self.rec.get("block", 0), self.rec.get("off", 0))
        self.lines, self.page_start, self.width, self.cols = [], 0, 72, 1
        self.match = None          # (query, chapter, block, start, end)
        self.search_q = ""

    def compose(self) -> ComposeResult:
        yield Static(id="head")
        with Horizontal(id="pagewrap"):
            yield Static(id="page")
        yield Static(id="foot")

    def on_mount(self):
        self.rec["opened"] = time.time()
        self.apply_theme()
        self.call_after_refresh(self.relayout)

    def on_resize(self, _):
        if self.lines:
            self.relayout()

    # --- geometry ---
    @property
    def theme(self):
        return THEMES.get(self.state.prefs["theme"], THEMES["Dracula"])

    def apply_theme(self):
        bg = self.theme[0]
        self.styles.background = bg
        for w in [*self.query(Static), self.query_one("#pagewrap")]:
            w.styles.background = bg

    def col_height(self):
        return max(3, self.query_one("#page").size.height)

    def page_height(self):
        """Lines shown on one screen: both columns in Split view."""
        return self.col_height() * self.cols

    def relayout(self):
        avail = max(20, self.size.width - 4)
        view = self.state.prefs.get("view", "Single")
        if view == "Split" and (avail - GUTTER) // 2 >= 30:
            self.cols, self.width = 2, (avail - GUTTER) // 2
            total = self.width * 2 + GUTTER
        else:
            self.cols = 1
            self.width = avail if view in ("Fit", "Split") else min(self.state.prefs["width"], avail)
            total = self.width
        self.query_one("#page").styles.width = total
        focus = self.state.prefs.get("focus", False)
        self.query_one("#head").display = not focus
        self.query_one("#foot").display = not focus
        self.query_one("#pagewrap").styles.padding = (1, 0) if focus else (0, 0)
        self.lines = layout(self.book.chapter(self.ch), self.width, self.state.prefs, self.theme)
        self.seek(*self.anchor)

    def seek(self, block, off):
        """Put the line holding (block, offset) at the top, or as high as it can go near the end."""
        idx = 0
        for i, ln in enumerate(self.lines):
            if (ln.block, ln.offset) <= (block, off):
                idx = i
            else:
                break
        self.page_start = min(idx, max(0, len(self.lines) - self.page_height()))
        self.render_page()

    def remember(self):
        ln = self.lines[min(self.page_start, len(self.lines) - 1)]
        self.anchor = (ln.block, ln.offset)
        self.rec.update(ch=self.ch, block=ln.block, off=ln.offset,
                        pct=round(self.progress() * 100, 1), opened=time.time())
        # Holding a key fires many moves; write the file once things settle.
        if getattr(self, "_save_timer", None):
            self._save_timer.stop()
        self._save_timer = self.set_timer(0.5, self.state.save)

    def progress(self):
        end = min(len(self.lines), self.page_start + self.page_height())
        return self.book.fraction(self.ch, end / max(1, len(self.lines)))

    # --- drawing ---
    def render_page(self):
        bg, fg, dim, head, emph = self.theme
        ch_h = self.col_height()
        h = ch_h * self.cols

        def cell(i):
            if i >= len(self.lines):
                return Text("")
            ln = self.lines[i]
            t = ln.text.copy()
            if self.match and self.match[1] == self.ch and ln.block == self.match[2]:
                self._highlight(t, ln, self.match[3], self.match[4])
            return t

        page = Text(style=fg, no_wrap=True, overflow="crop")
        for k in range(min(ch_h, max(0, len(self.lines) - self.page_start))):
            if k:
                page.append("\n")
            t = cell(self.page_start + k)
            if self.cols == 2:
                t.truncate(self.width, pad=True)
                spine = " " * (GUTTER // 2) + "\u2502" + " " * (GUTTER - GUTTER // 2 - 1)
                t.append(spine, dim)
                t.append_text(cell(self.page_start + ch_h + k))
            page.append_text(t)
        self.query_one("#page").update(page)
        if self.state.prefs.get("focus"):
            return

        title = self.book.title
        chap = self.book.chapter_title(self.ch)
        w = self.size.width - 4
        left = Text(title, style=f"bold {head}")
        right = Text(chap, style=dim)
        left.truncate(max(10, w - len(right.plain) - 4), overflow="ellipsis")
        right.truncate(max(0, w - len(left.plain) - 4), overflow="ellipsis")
        self.query_one("#head").update(Text.assemble(left, " " * max(2, w - len(left.plain) - len(right.plain)), right))

        pages = max(1, -(-len(self.lines) // ch_h))
        cur = min(pages, -(-self.page_start // ch_h) + 1)
        last = min(pages, cur + self.cols - 1)
        page_txt = f"page {cur} of {pages}" if last == cur else f"pages {cur}-{last} of {pages}"
        at_end = self.page_start + h >= len(self.lines)
        pct = self.progress()
        words_left = self.book.chapter(self.ch).words * (1 - min(1, (self.page_start + h) / len(self.lines)))
        mins = round(words_left / WPM)
        left_txt = "end of chapter" if at_end else (f"{mins} min left in chapter" if mins else "under a minute left")
        info = Text.assemble((f"{pct * 100:.0f}%", f"bold {head}"), ("   ", ""),
                             (page_txt, dim), ("   ", ""), (left_txt, dim))
        if any(b["ch"] == self.ch and self.page_start <= self._line_of(b) < self.page_start + h
               for b in self.rec.get("marks", [])):
            info.append("   ", PINK)
        bar_w = w
        filled = int(round(bar_w * pct))
        bar = Text.assemble(("━" * filled, head), ("─" * (bar_w - filled), dim))
        self.query_one("#foot").update(Text.assemble(bar, "\n", info))

    def _highlight(self, t, ln, ms, me):
        q = self.match[0]
        # find query inside this line's text (justification changes spacing, so match words loosely)
        pat = re.compile(r"\s+".join(map(re.escape, q.split())), re.I)
        for m in pat.finditer(t.plain):
            t.stylize(f"reverse {PINK}", m.start(), m.end())

    def _line_of(self, mark):
        idx = 0
        for i, ln in enumerate(self.lines):
            if (ln.block, ln.offset) <= (mark["block"], mark["off"]):
                idx = i
            else:
                break
        return idx

    # --- navigation ---
    def goto_chapter(self, ch, block=0, off=0, end=False):
        self.ch = max(0, min(len(self.book) - 1, ch))
        self.lines = layout(self.book.chapter(self.ch), self.width, self.state.prefs, self.theme)
        if end == "page":      # top of the chapter's last page
            self.page_start = (len(self.lines) - 1) // self.page_height() * self.page_height()
            self.render_page()
        elif end:              # last line at the bottom of the screen
            self.page_start = max(0, len(self.lines) - self.page_height())
            self.render_page()
        else:
            self.seek(block, off)
        self.remember()

    def action_page(self, d):
        # Pages are fixed blocks of lines from the chapter start. After scrolling by line,
        # Right/Left go to the top of the next/previous one of those pages.
        h = self.page_height()
        nxt = (self.page_start // h + 1) * h if d > 0 else (self.page_start - 1) // h * h
        if 0 <= nxt < len(self.lines):
            self.page_start = nxt
            self.render_page()
            self.remember()
        elif d > 0 and self.ch < len(self.book) - 1:
            self.goto_chapter(self.ch + 1)
        elif d < 0 and self.ch > 0:
            self.goto_chapter(self.ch - 1, end="page")
        elif d > 0:
            self.notify("The end.", timeout=2)

    def action_scroll(self, d):
        h = self.page_height()
        if d > 0 and self.page_start + h < len(self.lines):
            self.page_start += 1
        elif d < 0 and self.page_start > 0:
            self.page_start -= 1
        elif d > 0 and self.ch < len(self.book) - 1:
            return self.goto_chapter(self.ch + 1)
        elif d < 0 and self.ch > 0:
            return self.goto_chapter(self.ch - 1, end=True)
        else:
            return
        self.render_page()
        self.remember()

    def action_chapter(self, d):
        # jump between toc entries when there are any, otherwise between files
        starts = sorted({c for _, c, _ in self.book.toc}) or list(range(len(self.book)))
        if d > 0:
            nxt = next((c for c in starts if c > self.ch), None)
        else:
            prev = [c for c in starts if c < self.ch or (c == self.ch and self.page_start)]
            nxt = prev[-1] if prev else None
        if nxt is not None:
            self.goto_chapter(nxt)

    def action_chapter_start(self):
        self.goto_chapter(self.ch)

    def on_click(self, event):
        if event.screen_x < self.size.width / 3:
            self.action_page(-1)
        else:
            self.action_page(1)

    def on_mouse_scroll_down(self, _):
        self.action_scroll(1)

    def on_mouse_scroll_up(self, _):
        self.action_scroll(-1)

    def action_close(self):
        self.remember()
        self.state.save()
        self.app.pop_screen()

    # --- settings ---
    def action_width(self, d):
        cur = self.state.prefs["width"]
        opts = WIDTHS if d > 0 else WIDTHS[::-1]
        nxt = next((w for w in opts if (w > cur if d > 0 else w < cur)), cur)
        was = self.state.prefs.get("view", "Single")
        if was != "Single":      # width only applies to the single column
            nxt = cur
            self.state.prefs["view"] = "Single"
        self.state.prefs["width"] = nxt
        self.state.save()
        self.remember_and_relayout()
        self.notify(("Single page, " if was != "Single" else "") + f"line width {self.width}", timeout=1)

    def remember_and_relayout(self):
        ln = self.lines[min(self.page_start, len(self.lines) - 1)]
        self.anchor = (ln.block, ln.offset)
        self.relayout()

    def action_theme(self):
        names = list(THEMES)
        cur = self.state.prefs["theme"]
        self.state.prefs["theme"] = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
        self.state.save()
        self.apply_theme()
        self.remember_and_relayout()
        self.notify(f"Theme: {self.state.prefs['theme']}", timeout=1)

    def action_justify(self):
        self.state.prefs["justify"] = not self.state.prefs["justify"]
        self.state.save()
        self.remember_and_relayout()
        self.notify("Justified" if self.state.prefs["justify"] else "Ragged right", timeout=1)

    def action_view(self):
        cur = self.state.prefs.get("view", "Single")
        self.state.prefs["view"] = VIEWS[(VIEWS.index(cur) + 1) % len(VIEWS)] if cur in VIEWS else VIEWS[0]
        self.state.save()
        self.remember_and_relayout()
        msg = {"Single": "Single page", "Fit": "Fit to screen", "Split": "Two pages"}[self.state.prefs["view"]]
        if self.state.prefs["view"] == "Split" and self.cols == 1:
            msg = "Screen too narrow for two pages, using full width"
        self.notify(msg, timeout=1.5)

    def action_focus_mode(self):
        self.state.prefs["focus"] = not self.state.prefs.get("focus", False)
        self.state.save()
        # the page area changes height once the header and footer are hidden or shown
        ln = self.lines[min(self.page_start, len(self.lines) - 1)]
        self.anchor = (ln.block, ln.offset)
        self.relayout()
        self.call_after_refresh(self.relayout)

    def action_spacing(self):
        self.state.prefs["spacing"] = not self.state.prefs["spacing"]
        self.state.save()
        self.remember_and_relayout()
        self.notify("Spaced paragraphs" if self.state.prefs["spacing"] else "Indented paragraphs", timeout=1)

    # --- panels ---
    def action_toc(self):
        if not self.book.toc:
            entries = [(self.book.chapter(i).title or f"Part {i + 1}", i, None) for i in range(len(self.book))]
        else:
            entries = self.book.toc
        cur = max((k for k, (_, c, _) in enumerate(entries) if c <= self.ch), default=0)

        def done(k):
            if k is None:
                return
            _, c, frag = entries[k]
            block = self.book.chapter(c).anchors.get(frag, 0) if frag else 0
            self.goto_chapter(c, block, 0)

        items = [(t, "") for t, _, _ in entries]
        self.app.push_screen(Picker("Contents", items, cur), done)

    def action_bookmark(self):
        marks = self.rec.setdefault("marks", [])
        h = self.page_height()
        here = [m for m in marks if m["ch"] == self.ch and self.page_start <= self._line_of(m) < self.page_start + h]
        if here:
            for m in here:
                marks.remove(m)
            self.notify("Bookmark removed", timeout=1)
        else:
            ln = self.lines[self.page_start]
            blocks = self.book.chapter(self.ch).blocks
            b, off = ln.block, ln.offset
            while b < len(blocks) - 1 and blocks[b][0] in ("h1", "h2", "h3", "hr", "img"):
                b, off = b + 1, 0
            snippet = " ".join(blocks[b][1].plain[off:off + 120].split()) if blocks else ""
            marks.append({"ch": self.ch, "block": ln.block, "off": ln.offset,
                          "pct": round(self.progress() * 100), "text": snippet[:80], "t": time.time()})
            marks.sort(key=lambda m: (m["ch"], m["block"], m["off"]))
            self.notify("Bookmarked", timeout=1)
        self.state.save()
        self.render_page()

    def action_bookmarks(self):
        marks = self.rec.get("marks", [])
        if not marks:
            self.notify("No bookmarks yet. Press b to add one.", timeout=2)
            return

        def done(k):
            if k is not None:
                m = marks[k]
                self.goto_chapter(m["ch"], m["block"], m["off"])

        items = [(m["text"] or "(blank)", f"{m['pct']}%  {self.book.chapter_title(m['ch'])}") for m in marks]
        self.app.push_screen(Picker("Bookmarks", items, 0), done)

    def action_search(self):
        def done(q):
            if q:
                self.search_q = q
                self.find(q, self.ch, self.lines[self.page_start].block, -1)
        self.app.push_screen(Prompt("Search", "words to find", self.search_q), done)

    def action_next_match(self):
        if not self.search_q:
            return self.action_search()
        if self.match and self.match[1] == self.ch:
            self.find(self.search_q, self.ch, self.match[2], self.match[3])
        else:
            self.find(self.search_q, self.ch, self.lines[self.page_start].block, -1)

    def find(self, q, ch, block, after):
        pat = re.compile(r"\s+".join(map(re.escape, q.split())), re.I)
        n = len(self.book)
        for step in range(n + 1):
            c = (ch + step) % n
            blocks = self.book.chapter(c).blocks
            for b in range(block if step == 0 else 0, len(blocks)):
                start = after + 1 if (step == 0 and b == block) else 0
                m = pat.search(blocks[b][1].plain, start)
                if m:
                    self.match = (q, c, b, m.start(), m.end())
                    self.goto_chapter(c, b, m.start())
                    if step and c <= ch:
                        self.notify("Search wrapped to the start", timeout=1.5)
                    return
        self.match = None
        self.notify(f"No match for “{q}”", severity="warning", timeout=2)
        self.render_page()

    def action_goto(self):
        def done(v):
            try:
                pct = max(0.0, min(100.0, float((v or "").strip().rstrip("%")))) / 100
            except ValueError:
                return
            acc = 0
            for c, s in enumerate(self.book.sizes):
                if acc + s >= pct * self.book.total or c == len(self.book) - 1:
                    frac = (pct * self.book.total - acc) / s if s else 0
                    lines = layout(self.book.chapter(c), self.width, self.state.prefs, self.theme)
                    ln = lines[min(len(lines) - 1, int(frac * len(lines)))]
                    self.goto_chapter(c, ln.block, ln.offset)
                    return
                acc += s
        self.app.push_screen(Prompt("Go to", "percent, like 50", ""), done)

    def action_help(self):
        self.app.push_screen(Help())


# ---------- small modal screens ----------
MODAL_CSS = """
    align: center middle;
    background: #282a36 60%;
"""


class Picker(ModalScreen):
    """A list in a box. Returns the index chosen, or None."""
    BINDINGS = [Binding("escape,left,q", "cancel", show=False), Binding("right", "pick", show=False)]
    DEFAULT_CSS = """
    Picker { align: center middle; background: #282a36 60%; }
    Picker > Vertical { width: 90%; max-width: 100; height: 90%; background: #21222c;
                        border: round #bd93f9; padding: 0 1; }
    Picker .ptitle { height: 2; padding: 0 1; }
    Picker OptionList { background: #21222c; border: none; height: 1fr; scrollbar-color: #bd93f9; }
    """.replace("#bd93f9", ACCENT)

    def __init__(self, title, items, current=0):
        super().__init__()
        self.title_, self.items, self.current = title, items, current

    def compose(self):
        with Vertical():
            yield Static(Text(self.title_, style=f"bold {ACCENT}"), classes="ptitle")
            opts = []
            for k, (label, sub) in enumerate(self.items):
                t = Text(label, style=f"bold {FG}" if k == self.current else FG)
                if sub:
                    t.append(f"\n{sub}", GRAY)
                opts.append(Option(t, id=str(k)))
            yield OptionList(*opts)

    def on_mount(self):
        ol = self.query_one(OptionList)
        ol.focus()
        for i in range(ol.option_count):
            if ol.get_option_at_index(i).id == str(self.current):
                ol.highlighted = i
                ol.scroll_to_highlight(top=True)
                break

    def on_option_list_option_selected(self, e):
        self.dismiss(int(e.option.id))

    def action_pick(self):
        self.query_one(OptionList).action_select()

    def action_cancel(self):
        self.dismiss(None)


class Prompt(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", show=False)]
    DEFAULT_CSS = """
    Prompt { align: center middle; background: #282a36 60%; }
    Prompt > Vertical { width: 70%; max-width: 80; height: auto; background: #21222c;
                        border: round #bd93f9; padding: 1 2; }
    Prompt Input { background: #282a36; border: tall #44475a; }
    Prompt Input:focus { border: tall #bd93f9; }
    """.replace("#bd93f9", ACCENT)

    def __init__(self, title, placeholder, value=""):
        super().__init__()
        self.title_, self.placeholder, self.value = title, placeholder, value

    def compose(self):
        with Vertical():
            yield Static(Text(self.title_, style=f"bold {ACCENT}"))
            yield Input(value=self.value, placeholder=self.placeholder)

    def on_mount(self):
        self.query_one(Input).focus()

    def on_input_submitted(self, e):
        self.dismiss(e.value.strip())

    def action_cancel(self):
        self.dismiss(None)


HELP = [
    ("Next page", "Right  Space  tap right"),
    ("Previous page", "Left  tap left"),
    ("Scroll one line", "Up  Down"),
    ("Next / previous chapter", "]  ["),
    ("Contents", "t"),
    ("Bookmark this page", "b"),
    ("All bookmarks", "m"),
    ("Search, next match", "/  n"),
    ("Go to percent", "g"),
    ("View: single, fit, two pages", "v"),
    ("Focus (hide header and footer)", "f"),
    ("Line width (single view)", "+  -"),
    ("Theme (Dracula, Night, Paper)", "a"),
    ("Justify on or off", "J"),
    ("Spaced or indented paragraphs", "s"),
    ("Back to library", "Esc  q"),
]


class Help(ModalScreen):
    BINDINGS = [Binding("escape,q,question_mark,enter,left", "app.pop_screen", show=False)]
    DEFAULT_CSS = """
    Help { align: center middle; background: #282a36 60%; }
    Help > Static { width: 72; height: auto; background: #21222c; border: round #bd93f9; padding: 1 2; }
    """.replace("#bd93f9", ACCENT)

    def compose(self):
        t = Text("Reading keys\n\n", style=f"bold {ACCENT}")
        for what, keys in HELP:
            t.append(f"{what:<32}", FG)
            t.append(f"{keys}\n", CYAN)
        t.rstrip()
        yield Static(t)

    def on_click(self, _):
        self.app.pop_screen()


# ---------- Project Gutenberg ----------
OPDS = "https://www.gutenberg.org/ebooks/search.opds/?query={q}&sort_order=downloads"
ATOM = "{http://www.w3.org/2005/Atom}"


def gutenberg_search(q):
    req = urllib.request.Request(OPDS.format(q=urllib.parse.quote_plus(q)), headers={"User-Agent": "deck-reader"})
    root = ET.fromstring(urllib.request.urlopen(req, timeout=20).read())
    out = []
    for e in root.iter(f"{ATOM}entry"):
        m = re.search(r"/ebooks/(\d+)\.opds$", e.findtext(f"{ATOM}id") or "")
        if m:
            out.append((m.group(1), e.findtext(f"{ATOM}title") or "?", e.findtext(f"{ATOM}content") or ""))
    return out


def gutenberg_download(num, title, author):
    os.makedirs(BOOKS, exist_ok=True)
    name = re.sub(r"[^\w]+", "-", f"{author.split(',')[0]} {title}".lower()).strip("-")[:70] or num
    dest = f"{BOOKS}/{name}.epub"
    req = urllib.request.Request(f"https://www.gutenberg.org/ebooks/{num}.epub3.images",
                                 headers={"User-Agent": "deck-reader"})
    data = urllib.request.urlopen(req, timeout=60).read()
    with open(dest + ".part", "wb") as f:
        f.write(data)
    os.replace(dest + ".part", dest)
    return dest


class GutenbergScreen(Screen):
    BINDINGS = [Binding("escape", "back", show=False), Binding("slash", "focus_search", show=False)]
    CSS = """
    GutenbergScreen { background: #282a36; }
    GutenbergScreen .title { height: 3; padding: 1 3 0 3; }
    GutenbergScreen Input { margin: 0 3; background: #21222c; border: tall #44475a; }
    GutenbergScreen Input:focus { border: tall #bd93f9; }
    GutenbergScreen #status { height: 2; padding: 1 4 0 4; color: #6272a4; }
    """.replace("#bd93f9", ACCENT)

    def compose(self):
        yield Static(Text.assemble(("  Get books", f"bold {ACCENT}"),
                                   ("   free from Project Gutenberg", GRAY)), classes="title")
        yield Input(placeholder="title or author, then Enter")
        yield Static("Down moves into the results, Enter downloads, Esc goes back.", id="status")
        yield OptionList()

    def on_mount(self):
        self.query_one(Input).focus()

    def action_focus_search(self):
        self.query_one(Input).focus()

    def action_back(self):
        self.app.pop_screen()

    def on_key(self, event):
        if event.key == "down" and self.query_one(Input).has_focus:
            ol = self.query_one(OptionList)
            if ol.option_count:
                ol.focus()
                event.stop()
        elif event.key in ("up",) and self.query_one(OptionList).has_focus:
            ol = self.query_one(OptionList)
            if ol.highlighted in (None, 0):
                self.query_one(Input).focus()
                event.stop()
        elif event.key == "left" and self.query_one(OptionList).has_focus:
            self.action_back()

    def on_input_submitted(self, e):
        if e.value.strip():
            self.query_one("#status").update("Searching...")
            self.search(e.value.strip())

    @work(thread=True, exclusive=True)
    def search(self, q):
        try:
            res = gutenberg_search(q)
        except Exception as ex:
            self.app.call_from_thread(self.query_one("#status").update, f"Search failed: {ex}")
            return
        self.app.call_from_thread(self.show, res)

    def show(self, res):
        self.results = {n: (n, t, a) for n, t, a in res}
        ol = self.query_one(OptionList)
        ol.clear_options()
        ol.add_options([Option(Text.assemble(("  ", CYAN), (t, f"bold {FG}"), ("\n   " + a, GRAY)), id=n)
                        for n, t, a in res])
        self.query_one("#status").update(f"{len(res)} results" if res else "Nothing found.")
        if res:
            ol.focus()
            ol.highlighted = 0

    def on_option_list_option_selected(self, e):
        n, t, a = self.results[e.option.id]
        self.query_one("#status").update(f"Downloading {t}...")
        self.download(n, t, a)

    @work(thread=True)
    def download(self, n, t, a):
        try:
            gutenberg_download(n, t, a)
        except Exception as ex:
            self.app.call_from_thread(self.query_one("#status").update, f"Download failed: {ex}")
            return
        self.app.call_from_thread(self.query_one("#status").update, f"Saved {t} to ~/Books")
        self.app.call_from_thread(self.app.notify, f"Added {t}", timeout=2)


# ---------- library ----------
class Confirm(ModalScreen):
    BINDINGS = [Binding("y", "yes", show=False), Binding("escape,n,left", "no", show=False)]
    DEFAULT_CSS = """
    Confirm { align: center middle; background: #282a36 60%; }
    Confirm > Static { width: 60; height: auto; background: #21222c; border: round #ff5555; padding: 1 2; }
    """

    def __init__(self, msg):
        super().__init__()
        self.msg = msg

    def compose(self):
        yield Static(Text.assemble((self.msg, FG), ("\n\n", ""), ("y", f"bold {RED}"), (" delete   ", GRAY),
                                   ("n", f"bold {GREEN}"), (" keep", GRAY)))

    def action_yes(self):
        self.dismiss(True)

    def action_no(self):
        self.dismiss(False)


class LibraryScreen(Screen):
    CSS = """
    LibraryScreen { background: #282a36; }
    LibraryScreen .title { height: 3; padding: 1 3 0 3; }
    #lib { background: #282a36; border: none; padding: 0 2; height: 1fr;
           scrollbar-color: #bd93f9; scrollbar-background: #21222c; }
    #lib:focus { border: none; }
    LibraryScreen OptionList > .option-list--option { padding: 0 1; }
    #hint { height: 1; padding: 0 3; color: #6272a4; }
    """.replace("#bd93f9", ACCENT)
    BINDINGS = [
        Binding("right", "open", show=False),
        Binding("g", "get", show=False),
        Binding("r", "rescan", show=False),
        Binding("d,delete", "delete", show=False),
        Binding("q", "app.quit", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.state = State()
        self.meta = load_json(CACHE, {})
        self.paths = []

    def compose(self):
        yield Static(id="ltitle", classes="title")
        yield OptionList(id="lib")
        yield Static(Text.assemble(("Enter", CYAN), (" read   ", GRAY), ("g", CYAN), (" get books   ", GRAY),
                                   ("d", CYAN), (" delete   ", GRAY), ("r", CYAN), (" rescan", GRAY)), id="hint")

    def on_mount(self):
        self.refresh_library()
        self.query_one("#lib").focus()

    def on_screen_resume(self, _):
        if self.query("#lib"):
            self.refresh_library()

    def scan(self):
        found = []
        for root, _, files in os.walk(BOOKS):
            for f in files:
                if f.lower().endswith(EXTS) and not f.startswith("."):
                    found.append(os.path.join(root, f))
        changed = False
        for p in found:
            key = os.path.relpath(p, BOOKS)
            mt = os.path.getmtime(p)
            if self.meta.get(key, {}).get("mtime") != mt:
                self.meta[key] = dict(quick_meta(p), mtime=mt)
                changed = True
        if changed:
            save_json(CACHE, self.meta)
        books = self.state.books
        found.sort(key=lambda p: (-books.get(os.path.relpath(p, BOOKS), {}).get("opened", 0),
                                  self.meta[os.path.relpath(p, BOOKS)]["title"].lower()))
        return found

    def refresh_library(self):
        os.makedirs(BOOKS, exist_ok=True)
        keep = None
        ol = self.query_one("#lib", OptionList)
        if ol.highlighted is not None and self.paths:
            try:
                keep = ol.get_option_at_index(ol.highlighted).id
            except Exception:
                pass
        self.paths = self.scan()
        n = len(self.paths)
        self.query_one("#ltitle").update(Text.assemble(("Books", f"bold {ACCENT}"),
                                                       (f"   {n} in ~/Books" if n else "", GRAY)))
        ol.clear_options()
        if not self.paths:
            ol.add_options([Option(Text.assemble(("  ", CYAN), ("Get books", f"bold {FG}"),
                                                 ("   free classics from Project Gutenberg", GRAY)), id="get"),
                            Option(Text("Or copy .epub, .pdf, .txt or .md files into ~/Books", style=GRAY), disabled=True)])
            ol.highlighted = 0
            return
        opts = []
        for i, p in enumerate(self.paths):
            key = os.path.relpath(p, BOOKS)
            m, rec = self.meta[key], self.state.books.get(key, {})
            pct = rec.get("pct")
            ext = p.rsplit(".", 1)[-1].lower()
            icon = {"epub": "", "pdf": "", "md": "", "txt": ""}.get(ext, "")
            color = {"epub": CYAN, "pdf": RED, "md": GREEN, "txt": YELLOW}.get(ext, CYAN)
            t = Text.assemble((icon + "  ", color), (m["title"], f"bold {FG}"))
            sub = Text("\n   ")
            if m.get("author"):
                sub.append(m["author"], GRAY)
            if pct is not None:
                filled = int(pct / 10 + 0.5)
                sub.append("   " if m.get("author") else "")
                sub.append("■" * filled, ACCENT)
                sub.append("■" * (10 - filled), "#44475a")
                sub.append(f" {pct:.0f}%", GRAY)
                if rec.get("opened") and i == 0:
                    sub.append("   continue reading", PINK)
            elif not m.get("author"):
                sub.append("new", GRAY)
            t.append_text(sub)
            opts.append(Option(t, id=key))
        ol.add_options(opts)
        idx = 0
        if keep:
            for k in range(ol.option_count):
                if ol.get_option_at_index(k).id == keep:
                    idx = k
        ol.highlighted = idx

    def action_open(self):
        self.query_one("#lib", OptionList).action_select()

    def on_option_list_option_selected(self, e):
        if e.option_list.id != "lib":
            return
        if e.option.id == "get":
            return self.action_get()
        path = os.path.join(BOOKS, e.option.id)
        try:
            book = Book(path)
        except Exception as ex:
            self.notify(f"Could not open this book: {ex}", severity="error")
            return
        if not len(book):
            self.notify("This book has no readable text", severity="warning")
            return
        self.app.push_screen(ReaderScreen(book, self.state))

    def action_get(self):
        self.app.push_screen(GutenbergScreen())

    def action_rescan(self):
        self.meta = {}
        self.refresh_library()
        self.notify("Library rescanned", timeout=1)

    def action_delete(self):
        ol = self.query_one("#lib", OptionList)
        if ol.highlighted is None:
            return
        key = ol.get_option_at_index(ol.highlighted).id
        if key == "get":
            return

        def done(yes):
            if yes:
                os.remove(os.path.join(BOOKS, key))
                self.state.books.pop(key, None)
                self.state.save()
                self.refresh_library()
        self.app.push_screen(Confirm(f"Delete {self.meta[key]['title']}?"), done)


class Reader(App):
    TITLE = "Books"
    CSS = """
    Screen, LibraryScreen, GutenbergScreen { background: #282a36; }
    .title, #hint, #status { background: #282a36; }
    #lib, #lib:focus, GutenbergScreen OptionList, GutenbergScreen OptionList:focus {
        background: #282a36; background-tint: #282a36 0%; }
    #head { height: 2; padding: 0 2; }
    #pagewrap { height: 1fr; align-horizontal: center; }
    #page { height: 1fr; }
    #foot { height: 2; padding: 0 2; }
    OptionList { scrollbar-color: #bd93f9; scrollbar-background: #21222c; }
    OptionList > .option-list--option-highlighted,
    OptionList:focus > .option-list--option-highlighted { background: #44475a; color: #f8f8f2; }
    OptionList > .option-list--option-hover { background: #343746; }
    """.replace("#bd93f9", ACCENT)

    def on_mount(self):
        self.register_theme(dataclasses.replace(BUILTIN_THEMES["dracula"], name="deck", primary=ACCENT))
        self.theme = "deck"
        self.push_screen(LibraryScreen())


if __name__ == "__main__":
    Reader().run()
