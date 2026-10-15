#!/home/admin/.venvs/deck/bin/python
"""Deck notes: Markdown files in ~/Notes, with a live preview, full-text search and autosave.

List: Up/Down pick, Enter/Right edit, n new note, v read view, r rename, d delete (to ~/Notes/.trash),
/ search, q quit. Editor: type away (it saves by itself), Ctrl+T tick a checkbox,
Ctrl+P read view, Esc back to the list.
"""
import asyncio
import os
import re
import shutil
import signal
import time

from rich.markdown import Markdown
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

import json
import deck_ui
deck_ui.install()

HOME = os.path.expanduser("~")
NOTES = f"{HOME}/Notes"
TRASH = f"{NOTES}/.trash"

PURPLE, PINK, CYAN, GREEN = "#bd93f9", "#ff79c6", "#8be9fd", "#50fa7b"
ORANGE, YELLOW, RED, GRAY, FG = "#ffb86c", "#f1fa8c", "#ff5555", "#6272a4", "#f8f8f2"
try:
    ACCENT = json.load(open(f"{HOME}/.config/deck/prefs.json")).get("accent", PURPLE)
except Exception:
    ACCENT = PURPLE
I_NOTE, I_PIN = "", ""


# ---------- files ----------
def all_notes():
    out = []
    for root, dirs, files in os.walk(NOTES):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.endswith((".md", ".txt")) and not f.startswith("."):
                out.append(os.path.join(root, f))
    return sorted(out, key=os.path.getmtime, reverse=True)


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def title_of(path, text=None):
    text = read(path) if text is None else text
    for line in text.splitlines():
        if line.strip():
            return re.sub(r"^#+\s*", "", line.strip())[:80]
    return os.path.splitext(os.path.basename(path))[0]


def slug(title):
    s = re.sub(r"[^\w\s-]", "", title).strip().lower()
    return re.sub(r"[\s_]+", "-", s)[:60] or time.strftime("note-%Y%m%d-%H%M%S")


def unique_path(title):
    base = f"{NOTES}/{slug(title)}"
    path, n = base + ".md", 2
    while os.path.exists(path):
        path, n = f"{base}-{n}.md", n + 1
    return path


def ago(ts):
    d = time.time() - ts
    if d < 60:
        return "just now"
    for secs, unit in ((86400 * 365, "year"), (86400 * 30, "month"), (86400 * 7, "week"), (86400, "day"),
                       (3600, "hour"), (60, "minute")):
        if d >= secs:
            n = int(d // secs)
            return f"{n} {unit}{'s' if n > 1 else ''} ago"


def snippet(text):
    body = [l.strip() for l in text.splitlines()[1:] if l.strip()]
    s = " ".join(body)
    s = re.sub(r"[#*_`>]+", "", s)
    return s[:90]


def rendered(text):
    """Markdown for display, with checklist boxes drawn as icons."""
    text = re.sub(r"(?m)^(\s*[-*]) \[ \] ", "\\1 \uf096  ", text)
    text = re.sub(r"(?m)^(\s*[-*]) \[[xX]\] ", "\\1 \uf14a  ", text)
    return Markdown(text, code_theme="dracula")


def counts(text):
    words = len(text.split())
    todo = len(re.findall(r"^\s*[-*] \[ \]", text, re.M))
    done = len(re.findall(r"^\s*[-*] \[[xX]\]", text, re.M))
    return words, todo, done


# ---------- modal screens ----------
MODAL = """
    {name} {{ align: center middle; background: #282a36 60%; }}
    {name} > Vertical {{ width: 70; max-width: 95%; height: auto; background: #21222c;
                       border: round {accent}; padding: 1 2; }}
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


# ---------- read view ----------
class ReadScreen(Screen):
    BINDINGS = [Binding("escape,q,left,v,ctrl+p", "close", show=False),
                Binding("down,j", "scroll(1)", show=False), Binding("up,k", "scroll(-1)", show=False),
                Binding("space,pagedown,right", "scroll(20)", show=False), Binding("pageup", "scroll(-20)", show=False),
                Binding("e,enter", "edit", show=False)]
    DEFAULT_CSS = """
    ReadScreen { background: #282a36; align-horizontal: center; }
    ReadScreen #rhead { height: 2; padding: 0 3; }
    ReadScreen VerticalScroll { width: 84; max-width: 100%; padding: 0 2; scrollbar-color: #bd93f9; }
    """.replace("#bd93f9", ACCENT)

    def __init__(self, path):
        super().__init__()
        self.path = path

    def compose(self):
        yield Static(Text.assemble((I_NOTE + "  ", PURPLE), (title_of(self.path), f"bold {ACCENT}"),
                                   ("   e edit   Esc back", GRAY)), id="rhead")
        with VerticalScroll():
            yield Static(rendered(read(self.path)))

    def action_scroll(self, n):
        self.query_one(VerticalScroll).scroll_relative(y=n, animate=False)

    def action_close(self):
        self.app.pop_screen()
        self.app.refresh_list()

    def action_edit(self):
        self.app.pop_screen()
        self.app.push_screen(EditScreen(self.path))


# ---------- editor ----------
class EditScreen(Screen):
    BINDINGS = [Binding("escape", "close", show=False, priority=True),
                Binding("ctrl+s", "save", show=False, priority=True),
                Binding("ctrl+t", "tick", show=False, priority=True),
                Binding("ctrl+p", "read", show=False, priority=True)]
    DEFAULT_CSS = """
    EditScreen { background: #282a36; }
    EditScreen #ehead { height: 2; padding: 0 3; }
    EditScreen TextArea { border: none; padding: 0 2; background: #282a36; }
    EditScreen TextArea:focus { border: none; }
    EditScreen #efoot { height: 1; padding: 0 3; }
    """

    def __init__(self, path, new=False):
        super().__init__()
        self.path = path
        self.saved_text = read(path)
        self.template = self.saved_text if new else None    # a new note left as created gets removed
        self.dirty_since = None

    def compose(self):
        yield Static(id="ehead")
        ta = TextArea(self.saved_text, language="markdown", theme="dracula", soft_wrap=True,
                      show_line_numbers=False, tab_behavior="indent")
        yield ta
        yield Static(Text.assemble(("Esc", CYAN), (" done  ", GRAY), ("Ctrl+T", CYAN), (" tick box  ", GRAY),
                                   ("Ctrl+P", CYAN), (" read view  ", GRAY), ("Ctrl+S", CYAN),
                                   (" save (it also saves by itself)", GRAY)), id="efoot")

    def on_mount(self):
        ta = self.query_one(TextArea)
        ta.focus()
        lines = self.saved_text.split("\n")
        ta.move_cursor((len(lines) - 1, len(lines[-1])))      # continue writing at the end
        self.set_interval(1.5, self.autosave)
        self.header("saved")

    def header(self, state):
        color = GREEN if state == "saved" else YELLOW
        self.query_one("#ehead").update(Text.assemble((I_NOTE + "  ", PURPLE),
                                                      (title_of(self.path, self.query_one(TextArea).text), f"bold {ACCENT}"),
                                                      ("   " + state, color)))

    def on_text_area_changed(self, _):
        if self.dirty_since is None:
            self.dirty_since = time.time()
            self.header("editing")

    def autosave(self):
        if self.dirty_since is not None and time.time() - self.dirty_since > 1:
            self.action_save()

    def action_save(self):
        text = self.query_one(TextArea).text
        if text != self.saved_text:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path + ".tmp", "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(self.path + ".tmp", self.path)
            self.saved_text = text
        self.dirty_since = None
        self.header("saved")

    def action_tick(self):
        ta = self.query_one(TextArea)
        row, _ = ta.cursor_location
        line = ta.document.get_line(row)
        if re.match(r"^\s*[-*] \[ \]", line):
            new = re.sub(r"\[ \]", "[x]", line, count=1)
        elif re.match(r"^\s*[-*] \[[xX]\]", line):
            new = re.sub(r"\[[xX]\]", "[ ]", line, count=1)
        elif re.match(r"^\s*[-*] ", line):
            new = re.sub(r"^(\s*[-*] )", r"\1[ ] ", line, count=1)
        else:
            indent = len(line) - len(line.lstrip())
            new = line[:indent] + "- [ ] " + line[indent:]
        ta.replace(new, (row, 0), (row, len(line)))

    def action_read(self):
        self.action_save()
        self.app.pop_screen()
        self.app.push_screen(ReadScreen(self.path))

    def action_close(self):
        self.action_save()
        if not self.saved_text.strip() or self.saved_text == self.template:   # nothing written: no empty file
            try:
                os.remove(self.path)
            except OSError:
                pass
        self.app.pop_screen()
        self.app.refresh_list()


# ---------- list ----------
class Notes(App):
    TITLE = "Notes"
    CSS = """
    Screen { background: #282a36; }
    #title { height: 3; padding: 1 3 0 3; }
    #search { margin: 0 3; background: #21222c; border: tall #44475a; display: none; }
    #search:focus { border: tall #bd93f9; }
    #body { height: 1fr; }
    #list, #list:focus { width: 46; background: #282a36; border: none; padding: 0 1 0 2; height: 1fr;
                         scrollbar-color: #bd93f9; scrollbar-background: #21222c; background-tint: #282a36 0%; }
    OptionList > .option-list--option { padding: 0 1; }
    OptionList > .option-list--option-highlighted,
    OptionList:focus > .option-list--option-highlighted { background: #44475a; color: #f8f8f2; }
    OptionList > .option-list--option-hover { background: #343746; }
    #preview { width: 1fr; height: 1fr; padding: 0 2; border-left: solid #44475a; scrollbar-color: #bd93f9; }
    #hint { height: 1; padding: 0 3; }
    """.replace("#bd93f9", ACCENT)
    BINDINGS = [
        Binding("q", "quit", show=False), Binding("right", "edit", show=False),
        Binding("n", "new", show=False), Binding("v", "read", show=False),
        Binding("r", "rename", show=False), Binding("d,delete", "delete", show=False),
        Binding("slash", "search", show=False), Binding("escape", "clear_search", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.paths = []
        self.query_text = ""

    @property
    def main(self):
        return self.screen_stack[0]

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        yield Input(placeholder="search all notes", id="search")
        with Horizontal(id="body"):
            yield OptionList(id="list")
            with VerticalScroll(id="preview"):
                yield Static(id="pv")
        yield Static(Text.assemble(("Enter", CYAN), (" edit  ", GRAY), ("n", CYAN), (" new  ", GRAY),
                                   ("v", CYAN), (" read  ", GRAY), ("r", CYAN), (" rename  ", GRAY),
                                   ("d", CYAN), (" delete  ", GRAY), ("/", CYAN), (" search  ", GRAY),
                                   ("q", CYAN), (" quit", GRAY)), id="hint")

    def on_mount(self):
        os.makedirs(NOTES, exist_ok=True)
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, self.save_and_exit)   # Back key close
        self.refresh_list()
        self.main.query_one("#list").focus()

    def save_and_exit(self):
        if isinstance(self.screen, EditScreen):
            self.screen.action_close()
        self.exit()

    def refresh_list(self, select=None):
        ol = self.main.query_one("#list", OptionList)
        keep = select
        if keep is None and ol.highlighted is not None and self.paths:
            try:
                keep = self.paths[int(ol.get_option_at_index(ol.highlighted).id)]
            except (ValueError, IndexError):
                pass
        paths = all_notes()
        q = self.query_text.lower()
        if q:
            paths = [p for p in paths if q in read(p).lower() or q in os.path.basename(p).lower()]
        self.paths = paths
        ol.clear_options()
        opts = [Option(Text.assemble((I_NOTE + "  ", PURPLE), "New note", ("   n", GRAY)), id="new")]
        for i, p in enumerate(paths):
            text = read(p)
            t = Text.assemble((title_of(p, text)[:38], f"bold {FG}"), "\n", (ago(os.path.getmtime(p)), GRAY))
            words, todo, done = counts(text)
            if todo or done:
                t.append(f"  ·  {done}/{todo + done} done", GREEN if not todo else YELLOW)
            opts.append(Option(t, id=str(i)))
        ol.add_options(opts)
        head = Text.assemble((I_NOTE + "  ", PURPLE), ("Notes", f"bold {ACCENT}"))
        head.append(f"   {len(paths)} matching “{self.query_text}”" if q else f"   {len(paths)} in ~/Notes", GRAY)
        self.main.query_one("#title").update(head)
        idx = 0
        if keep in paths:
            target = str(paths.index(keep))
            idx = next(i for i in range(ol.option_count) if ol.get_option_at_index(i).id == target)
        elif paths:
            idx = next(i for i in range(ol.option_count) if ol.get_option_at_index(i).id == "0")
        ol.highlighted = idx
        self.preview()

    def current_path(self):
        ol = self.main.query_one("#list", OptionList)
        if ol.highlighted is None:
            return None
        oid = ol.get_option_at_index(ol.highlighted).id
        return None if oid == "new" else self.paths[int(oid)]

    def preview(self):
        p = self.current_path()
        pv = self.main.query_one("#pv", Static)
        if not p:
            pv.update(Text("\nPress Enter or n to start a note.\n\nNotes are Markdown files in ~/Notes. "
                           "Use - [ ] for checklists. From any shell: note buy milk", style=GRAY))
            return
        text = read(p)
        words, todo, done = counts(text)
        meta = f"{words} words  ·  edited {ago(os.path.getmtime(p))}  ·  {os.path.relpath(p, HOME)}"
        pv.update(Text.assemble((meta, GRAY), "\n\n") if not text.strip() else rendered(text))
        self.main.query_one("#preview").scroll_home(animate=False)

    def on_option_list_option_highlighted(self, _):
        self.preview()

    def on_option_list_option_selected(self, e):
        self.action_edit()

    # ----- actions -----
    def action_edit(self):
        p = self.current_path()
        if p is None:
            return self.action_new()
        self.push_screen(EditScreen(p))

    def action_read(self):
        p = self.current_path()
        if p:
            self.push_screen(ReadScreen(p))

    def action_new(self):
        def done(title):
            if title is None:
                return
            title = title or time.strftime("%B %-d, %Y")
            path = unique_path(title)
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n")
            self.refresh_list(select=path)
            self.push_screen(EditScreen(path, new=True))
        self.push_screen(Prompt("New note", "title (Enter for today's date)"), done)

    def action_rename(self):
        p = self.current_path()
        if not p:
            return

        def done(title):
            if not title:
                return
            text = read(p)
            lines = text.split("\n")
            if lines and lines[0].startswith("#"):
                lines[0] = f"# {title}"
            else:
                lines.insert(0, f"# {title}\n")
            new = unique_path(title) if slug(title) != os.path.splitext(os.path.basename(p))[0] else p
            with open(new, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            if new != p:
                os.remove(p)
            self.refresh_list(select=new)
        self.push_screen(Prompt("Rename note", "new title", title_of(p)), done)

    def action_delete(self):
        p = self.current_path()
        if not p:
            return

        def done(yes):
            if yes:
                os.makedirs(TRASH, exist_ok=True)
                shutil.move(p, f"{TRASH}/{time.strftime('%Y%m%d-%H%M%S')}-{os.path.basename(p)}")
                self.notify("Moved to ~/Notes/.trash", timeout=2)
                self.refresh_list()
        self.push_screen(Confirm(f"Delete {title_of(p)}?"), done)

    def action_search(self):
        box = self.main.query_one("#search", Input)
        box.display = True
        box.value = self.query_text
        box.focus()

    def on_input_changed(self, e):
        if e.input.id == "search":
            self.query_text = e.value.strip()
            self.refresh_list()

    def on_input_submitted(self, e):
        if e.input.id == "search":
            self.main.query_one("#list").focus()

    def action_clear_search(self):
        box = self.main.query_one("#search", Input)
        if box.display:
            box.value = ""
            box.display = False
            self.query_text = ""
            self.refresh_list()
            self.main.query_one("#list").focus()

    def on_key(self, event):
        box = self.main.query_one("#search", Input)
        if self.screen is self.main and box.has_focus and event.key in ("down", "escape"):
            if event.key == "escape":
                self.action_clear_search()
            else:
                self.main.query_one("#list").focus()
            event.stop()

    def check_action(self, action, parameters):
        if self.screen is not self.main:          # list keys only work on the list
            return False
        if self.main.query_one("#search", Input).has_focus and action != "clear_search":
            return False
        return True


if __name__ == "__main__":
    Notes().run()
