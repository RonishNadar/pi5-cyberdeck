#!/home/admin/.venvs/deck/bin/python
"""Deck settings: a plain text menu for device configuration."""
import glob
import json
import os
import re
import subprocess

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option
from deck_net import NetMixin
from deck_prefs import PrefsMixin, current_accent

HOME = os.path.expanduser("~")
os.environ["PATH"] = f"{HOME}/bin:{HOME}/.local/bin:/usr/local/bin:" + os.environ.get("PATH", "")
FOOT_INI = f"{HOME}/.config/foot/foot.ini"
SWAY_CFG = f"{HOME}/.config/sway/config"

PURPLE, PINK, CYAN, GREEN = "#bd93f9", "#ff79c6", "#8be9fd", "#50fa7b"
ORANGE, YELLOW, RED, GRAY, FG = "#ffb86c", "#f1fa8c", "#ff5555", "#6272a4", "#f8f8f2"
DOT = "\u25cf "
ACCENT = current_accent()

TEXT_SIZES = [("Small", 22), ("Medium", 28), ("Large", 34), ("Extra large", 40)]
CURSOR_HIDE = [("0.5 seconds", 500), ("1 second", 1000), ("3 seconds", 3000), ("Never", 0)]


# ---------- helpers ----------
def sh(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def tmux(*args: str):
    return subprocess.run(["tmux", *args], capture_output=True, text=True)


def open_tab(tab: str, cmd: str) -> None:
    names = tmux("list-windows", "-F", "#{window_name}").stdout.split()
    if tab in names:
        tmux("select-window", "-t", f":{tab}")
    else:
        tmux("new-window", "-n", tab, "bash", "-ic", cmd)


def swaymsg(*args: str) -> None:
    """Send a command to sway using its current socket (it changes after every restart)."""
    env = os.environ.copy()
    socks = sorted(glob.glob(f"/run/user/{os.getuid()}/sway-ipc.*.sock"), key=os.path.getmtime)
    if socks:
        env["SWAYSOCK"] = socks[-1]
    subprocess.run(["swaymsg", *args], env=env, capture_output=True)


def item(oid, icon, label, desc="", color=PURPLE, disabled=False):
    return Option(Text.assemble((icon, color), "  ", (f"{label:<18}", f"bold {FG}"), (desc, GRAY)),
                  id=oid, disabled=disabled)


def heading(text):
    return Option(Text(text, style=f"bold {GRAY}"), disabled=True)


# ---------- readers ----------
def wifi_ssid() -> str:
    return sh("iw dev wlan0 link 2>/dev/null | awk -F': ' '/SSID/{print $2}'") or "not connected"


def bt_state() -> str:
    out = sh("timeout 2 bluetoothctl show 2>/dev/null")
    if not out:
        return "unavailable"
    on = "Powered: yes" in out
    conn = sh("timeout 2 bluetoothctl devices Connected 2>/dev/null | wc -l")
    return ("on" if on else "off") + (f", {conn} connected" if on and conn not in ("", "0") else "")


def volume():
    out = sh("wpctl get-volume @DEFAULT_AUDIO_SINK@").split()
    if len(out) < 2:
        return None, False
    return round(float(out[1]) * 100), "[MUTED]" in out


def sinks():
    try:
        data = json.loads(sh("pactl -f json list sinks") or "[]")
    except json.JSONDecodeError:
        data = []
    default = sh("pactl get-default-sink")
    return [(s["name"], s.get("description", s["name"]), s["name"] == default) for s in data]


def text_size() -> int:
    m = re.search(r"size=(\d+)", open(FOOT_INI).read()) if os.path.exists(FOOT_INI) else None
    return int(m.group(1)) if m else 28


def cursor_hide() -> int:
    m = re.search(r"hide_cursor (\d+)", open(SWAY_CFG).read()) if os.path.exists(SWAY_CFG) else None
    return int(m.group(1)) if m else 0


# ---------- screens ----------
class Menu(Screen):
    """A titled text list. builder() returns options; handler(menu, id) reacts to a pick."""
    BINDINGS = [("escape", "back", "Back"), ("left", "back", "Back"), ("right", "open", "Open")]

    def __init__(self, title, builder, handler, root=False):
        super().__init__()
        self.title_text, self.builder, self.handler, self.root = title, builder, handler, root

    def compose(self) -> ComposeResult:
        yield Static(Text(self.title_text, style=f"bold {ACCENT}"), classes="title")
        yield OptionList()

    def on_mount(self) -> None:
        self.refresh_menu()
        self.query_one(OptionList).focus()
        self.set_interval(5, self.refresh_menu)

    def on_screen_resume(self) -> None:
        self.refresh_menu()

    def refresh_menu(self) -> None:
        ol = self.query_one(OptionList)
        hi = ol.highlighted
        ol.clear_options()
        opts = [] if self.root else [item("back", "\uf060", "Back", "", GRAY)]
        ol.add_options(opts + self.builder())
        if hi is not None and hi < ol.option_count:
            ol.highlighted = hi

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == "back":
            self.app.pop_screen()
            return
        self.handler(self, event.option.id)
        if self.is_current:
            self.refresh_menu()

    def action_back(self) -> None:
        if not self.root:
            self.app.pop_screen()

    def action_open(self) -> None:
        self.query_one(OptionList).action_select()


class Confirm(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel"), ("left", "cancel", "Cancel"), ("right", "open", "Open")]

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_open(self) -> None:
        self.query_one(OptionList).action_select()

    def __init__(self, question, yes_label):
        super().__init__()
        self.question, self.yes_label = question, yes_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(self.question, style=f"bold {FG}"), id="q")
            yield OptionList(item("yes", "\uf00c", self.yes_label, "", RED),
                             item("no", "\uf00d", "Cancel", "", GRAY))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id == "yes")


class About(Screen):
    BINDINGS = [("escape", "back", "Back"), ("left", "back", "Back"), ("right", "open", "Open")]

    def compose(self) -> ComposeResult:
        yield Static(Text("About this deck", style=f"bold {ACCENT}"), classes="title")
        yield OptionList(item("back", "\uf060", "Back", "", GRAY), id="aboutback")
        yield Static("", id="info")

    def on_mount(self) -> None:
        self.update_info()
        self.set_interval(2, self.update_info)

    def update_info(self) -> None:
        iface = sh("ip route show default | awk '{print $5; exit}'")
        ip = sh(f"ip -4 -o addr show {iface} | awk '{{print $4}}' | cut -d/ -f1") if iface else ""
        rows = [
            ("Hostname", sh("hostname")),
            ("System", sh(". /etc/os-release; echo $PRETTY_NAME")),
            ("Kernel", sh("uname -r")),
            ("Uptime", sh("uptime -p | sed 's/^up //'")),
            ("IP address", ip or "offline"),
            ("Tailscale", sh("tailscale ip -4 2>/dev/null | head -1") or "off"),
            ("CPU temp", sh("awk '{printf \"%.1f C\", $1/1000}' /sys/class/thermal/thermal_zone0/temp")),
            ("Memory", sh("free -h | awk '/^Mem/{print $3 \" used of \" $2}'")),
            ("Disk", sh("df -h / | awk 'NR==2{print $3 \" used of \" $2 \" (\" $5 \")\"}'")),
        ]
        text = Text()
        for k, v in rows:
            text.append(f"  {k:<14}", style=f"bold {CYAN}")
            text.append(f"{v}\n", style=FG)
        self.query_one("#info", Static).update(text)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.app.pop_screen()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_open(self) -> None:
        self.query_one(OptionList).action_select()


# ---------- app ----------
class Settings(PrefsMixin, NetMixin, App):
    MenuCls, ConfirmCls = Menu, Confirm
    CSS = """
    Screen { background: #282a36; }
    .title { height: 3; padding: 1 3 0 3; }
    OptionList { background: #282a36; border: none; padding: 0 2; height: 1fr;
                 scrollbar-color: #bd93f9; scrollbar-background: #21222c; }
    OptionList:focus { border: none; }
    OptionList > .option-list--option { padding: 0 1; }
    OptionList > .option-list--option-highlighted { background: #44475a; }
    OptionList > .option-list--option-hover { background: #343746; }
    #aboutback { height: 3; }
    #info { padding: 1 2; }
    Confirm { align: center middle; background: rgba(0, 0, 0, 0.6); }
    #dialog { width: 50; height: auto; padding: 1 2; border: round #ff5555; background: #282a36; }
    #dialog OptionList { height: auto; padding: 0; }
    #q { height: 2; }
    """

    def on_mount(self) -> None:
        self.push_screen(Menu("Settings", self.main_items, self.main_pick, root=True))

    # main
    def main_items(self):
        pct, muted = volume()
        vol = "no audio" if pct is None else (f"muted ({pct}%)" if muted else f"{pct}%")
        hide = cursor_hide()
        return [
            item("wifi", "\uf1eb", "Wi-Fi", wifi_ssid(), ORANGE),
            item("bt", "\uf293", "Bluetooth", bt_state(), PINK),
            item("sound", "\uf028", "Sound", vol, YELLOW),
            item("display", "\uf108", "Display",
                 f"text {text_size()}, cursor hides {'never' if hide == 0 else f'after {hide} ms'}", CYAN),
            *self.prefs_main_items(),
            item("about", "\uf05a", "About this deck", sh("hostname"), GREEN),
            item("updates", "\uf021", "Software updates", "apt update and upgrade", PURPLE),
            item("power", "\uf011", "Power", "restart display, reboot, shut down", RED),
        ]

    def main_pick(self, menu, oid):
        if oid == "wifi":
            self.open_wifi()
        elif oid == "bt":
            self.open_bt()
        elif oid == "sound":
            self.push_screen(Menu("Sound", self.sound_items, self.sound_pick))
        elif oid == "display":
            self.push_screen(Menu("Display", self.display_items, self.display_pick))
        elif oid in ("datetime", "notify", "footer", "theme"):
            self.prefs_open(oid)
        elif oid == "about":
            self.push_screen(About())
        elif oid == "updates":
            open_tab("updates", "sudo apt update && sudo apt full-upgrade -y; echo; "
                                "read -p 'Done. Press Enter to close this tab. ' _")
        elif oid == "power":
            self.push_screen(Menu("Power", self.power_items, self.power_pick))

    # sound
    def sound_items(self):
        pct, muted = volume()
        current = next((d for _, d, is_def in sinks() if is_def), "none")
        return [
            item("up", "\uf028", "Volume up", "" if pct is None else f"{pct}%", YELLOW),
            item("down", "\uf027", "Volume down", "", YELLOW),
            item("mute", "\uf026", "Unmute" if muted else "Mute", "", YELLOW),
            item("output", "\uf025", "Output device", current, CYAN),
        ]

    def sound_pick(self, menu, oid):
        if oid == "up":
            sh("wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 5%+")
        elif oid == "down":
            sh("wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-")
        elif oid == "mute":
            sh("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle")
        elif oid == "output":
            self.push_screen(Menu("Output device", self.output_items, self.output_pick))

    def output_items(self):
        out = [item(f"sink:{name}", "\uf025", desc[:40], DOT + "in use" if is_def else "", CYAN)
               for name, desc, is_def in sinks()]
        return out or [item("none", "\uf05e", "No audio outputs found", "", GRAY, disabled=True)]

    def output_pick(self, menu, oid):
        if oid.startswith("sink:"):
            subprocess.run(["pactl", "set-default-sink", oid[5:]])

    # display
    def display_items(self):
        size, hide = text_size(), cursor_hide()
        opts = [heading("Text size (restarts the display)")]
        opts += [item(f"size:{v}", "\uf031", label, DOT + "current" if v == size else "", CYAN)
                 for label, v in TEXT_SIZES]
        opts += [heading(""), heading("Cursor hides after")]
        opts += [item(f"hide:{v}", "\uf245", label, DOT + "current" if v == hide else "", PINK)
                 for label, v in CURSOR_HIDE]
        return opts

    def display_pick(self, menu, oid):
        if oid.startswith("size:"):
            v = int(oid[5:])
            if v == text_size():
                return
            sh(f"sed -i -E 's/(font=[^:]*:size=)[0-9]+/\\1{v}/' {FOOT_INI}")
            self.notify("Text size changed: restarting the display...")
            self.set_timer(1.5, lambda: swaymsg("exit"))
        elif oid.startswith("hide:"):
            v = int(oid[5:])
            sh(f"sed -i -E 's/hide_cursor [0-9]+/hide_cursor {v}/' {SWAY_CFG}")
            swaymsg("seat", "seat0", "hide_cursor", str(v))

    # power
    def power_items(self):
        return [
            item("display", "\uf108", "Restart display", "reloads sway and the terminal, tmux keeps running", CYAN),
            item("reboot", "\uf01e", "Reboot", "", ORANGE),
            item("poweroff", "\uf011", "Shut down", "", RED),
        ]

    def power(self, what):
        r = subprocess.run(["systemctl", what], capture_output=True, text=True)
        if r.returncode != 0:
            self.notify(f"{what} failed: {(r.stderr or r.stdout).strip()[:80]}", severity="error")

    def power_pick(self, menu, oid):
        if oid == "display":
            swaymsg("exit")
        elif oid == "reboot":
            self.push_screen(Confirm("Reboot the deck?", "Reboot"),
                             lambda yes: yes and self.power("reboot"))
        elif oid == "poweroff":
            self.push_screen(Confirm("Shut down the deck?", "Shut down"),
                             lambda yes: yes and self.power("poweroff"))


Settings.CSS = Settings.CSS.replace("#bd93f9", ACCENT)

if __name__ == "__main__":
    Settings().run()
