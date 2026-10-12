#!/home/admin/.venvs/deck/bin/python
"""Deck apps: same text-list style as settings; tap or Right/Enter opens an app in its own tab."""
import json
import os
import shutil
import subprocess

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

HOME = os.path.expanduser("~")
os.environ["PATH"] = f"{HOME}/bin:{HOME}/.local/bin:/usr/local/bin:" + os.environ.get("PATH", "")

PURPLE, PINK, CYAN, GREEN = "#bd93f9", "#ff79c6", "#8be9fd", "#50fa7b"
ORANGE, YELLOW, GRAY, FG = "#ffb86c", "#f1fa8c", "#6272a4", "#f8f8f2"
try:
    ACCENT = json.load(open(f"{HOME}/.config/deck/prefs.json")).get("accent", PURPLE)
except Exception:
    ACCENT = PURPLE

# id, icon, label, description, tab name (None = always new), command, program to check, color
APPS = [
    ("term",  "\uf120", "Terminal",  "new shell",            None,       "bash",                                  "bash",       PURPLE),
    ("cam",   "\uf030", "Camera",    "photos and video",     "camera",   "~/bin/camera.py",                       "camera.py",  PINK),
    ("files", "\uf07c", "Files",     "yazi file manager",    "files",    "yazi",                                  "yazi",       CYAN),
    ("mon",   "\uf0e4", "Monitor",   "btop system monitor",  "monitor",  "btop",                                  "btop",       GREEN),
    ("spotify", "\uf1bc", "Spotify",   "spot, keyboard player", "spotify", f"{HOME}/bin/spot.py", "spot.py", GREEN),
    ("music", "\uf001", "Music",     "cmus player",          "music",    "cmus",                                  "cmus",       YELLOW),
    ("mail",  "\uf0e0", "Mail",      "aerc email",           "mail",     "aerc",                                  "aerc",       PURPLE),
    ("news",  "\uf1ea", "News",      "newsboat feeds",       "news",     "newsboat",                              "newsboat",   CYAN),
    ("cal",   "\uf073", "Calendar",  "calcurse agenda",      "calendar", "calcurse",                              "calcurse",   GREEN),
    ("lan",   "\uf0e8", "LAN Scan",  "devices on network",   "lan",      f"{HOME}/bin/lanscan.py",                "lanscan.py", ORANGE),
    ("git",   "\uf1d3", "Git",       "lazygit",              "git",      "cd ~/projects 2>/dev/null; lazygit",    "lazygit",    PINK),
    ("claude", "\uf120", "Claude Code", "AI coding assistant", "claude", "cd ~/projects 2>/dev/null; claude", "claude", ORANGE),
    ("web",   "\uf0ac", "Web",       "w3m text browser",     "web",      "w3m https://lite.duckduckgo.com/lite/", "w3m",        YELLOW),
]
BY_ID = {a[0]: a for a in APPS}


def tmux(*args: str):
    return subprocess.run(["tmux", *args], capture_output=True, text=True)


def open_app(tab, cmd: str) -> None:
    """Switch to the app's tab if it is open, otherwise open it in a new tab."""
    if tab is None:
        tmux("new-window", "-n", "term", "bash", "-i")
        return
    names = tmux("list-windows", "-F", "#{window_name}").stdout.split()
    if tab in names:
        tmux("select-window", "-t", f":{tab}")
    else:
        tmux("new-window", "-n", tab, "bash", "-ic", cmd)


def item(oid, icon, label, desc="", color=PURPLE):
    return Option(Text.assemble((icon, color), "  ", (f"{label:<18}", f"bold {FG}"), (desc, GRAY)), id=oid)


class Apps(App):
    CSS = """
    Screen { background: #282a36; }
    .title { height: 3; padding: 1 3 0 3; }
    OptionList { background: #282a36; border: none; padding: 0 2; height: 1fr;
                 scrollbar-color: #bd93f9; scrollbar-background: #21222c; }
    OptionList:focus { border: none; }
    OptionList > .option-list--option { padding: 0 1; }
    OptionList > .option-list--option-highlighted { background: #44475a; }
    OptionList > .option-list--option-hover { background: #343746; }
    """
    BINDINGS = [("right", "open", "Open")]

    def compose(self) -> ComposeResult:
        yield Static(Text("Apps", style=f"bold {ACCENT}"), classes="title")
        yield OptionList(*[item(a, icon, label, desc, color)
                           for a, icon, label, desc, _, _, _, color in APPS])

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def action_open(self) -> None:
        self.query_one(OptionList).action_select()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        _, _, label, _, tab, cmd, need, _ = BY_ID[event.option.id]
        if shutil.which(need) is None and not os.path.exists(f"{HOME}/bin/{need}"):
            self.notify(f"{label} is not installed", severity="warning")
            return
        open_app(tab, cmd)


Apps.CSS = Apps.CSS.replace("#bd93f9", ACCENT)

if __name__ == "__main__":
    Apps().run()
