#!/home/admin/.venvs/deck/bin/python
"""Deck QR and barcode scanner: live preview from the camera (CAM1) and actions for what it finds.

Wi-Fi codes can join the network, links open in w3m, contact cards are shown, anything can be copied.
Enter do the highlighted action, c copy, Space pause the camera, Up/Down pick, q quit.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time

import numpy as np
import zxingcpp
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

import deck_ui
deck_ui.install()

HOME = os.path.expanduser("~")
HISTORY = f"{HOME}/.local/share/deck/qr-history.json"
W, H, FPS = 640, 360, 8
FRAME = W * H * 3 // 2                     # YUV420: the first W*H bytes are the grey image
SENSOR_MODE = "2304:1296:10:P"             # full field of view, as in the camera app
NOIR_TUNING = "/usr/share/libcamera/ipa/rpi/pisp/imx708_wide_noir.json"

PURPLE, PINK, CYAN, GREEN = "#bd93f9", "#ff79c6", "#8be9fd", "#50fa7b"
ORANGE, YELLOW, RED, GRAY, FG = "#ffb86c", "#f1fa8c", "#ff5555", "#6272a4", "#f8f8f2"
try:
    ACCENT = json.load(open(f"{HOME}/.config/deck/prefs.json")).get("accent", PURPLE)
except Exception:
    ACCENT = PURPLE
I_QR, I_WIFI, I_LINK, I_USER, I_COPY, I_TEXT, I_CAM, I_BAR = ("", "", "", "", "",
                                                             "", "", "")


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


# ---------- what a code means ----------
def parse_fields(body):
    """WIFI:/MECARD: style "K:value;K:value;" lists, where \\ escapes ; : , and backslash."""
    parts, cur, esc = [], "", False
    for ch in body:
        if esc:
            cur, esc = cur + ch, False
        elif ch == "\\":
            esc = True
        elif ch == ";":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        parts.append(cur)
    out = {}
    for part in parts:
        k, sep, v = part.partition(":")
        if sep:
            out[k.strip().upper()] = v
    return out


def classify(text, fmt):
    t = text.strip()
    if t.upper().startswith("WIFI:"):
        f = parse_fields(t[5:])
        return {"kind": "wifi", "ssid": f.get("S", ""), "password": f.get("P", ""),
                "security": (f.get("T") or "nopass").upper(), "hidden": f.get("H", "").lower() == "true"}
    if re.match(r"(?i)^https?://", t):
        return {"kind": "url", "url": t}
    if re.match(r"(?i)^www\.", t):
        return {"kind": "url", "url": "https://" + t}
    if t.upper().startswith("BEGIN:VCARD"):
        fields = {}
        for line in t.splitlines():
            k, _, v = line.partition(":")
            k = k.split(";")[0].upper()
            if k in ("FN", "TEL", "EMAIL", "ORG", "TITLE", "URL", "ADR") and v:
                fields.setdefault(k, v.replace(";", " ").strip())
        return {"kind": "contact", "fields": fields}
    if t.upper().startswith("MECARD:"):
        f = parse_fields(t[7:])
        return {"kind": "contact", "fields": {"FN": f.get("N", "").replace(",", " "), "TEL": f.get("TEL", ""),
                                              "EMAIL": f.get("EMAIL", ""), "URL": f.get("URL", "")}}
    if t.lower().startswith(("mailto:", "tel:", "sms:", "geo:")):
        return {"kind": "uri", "scheme": t.split(":")[0].lower()}
    if fmt not in ("QRCode", "MicroQRCode", "DataMatrix", "Aztec", "PDF417") and re.fullmatch(r"\d{8,14}", t):
        return {"kind": "product"}
    return {"kind": "text"}


def describe(rec):
    info, kind = rec["info"], rec["info"]["kind"]
    if kind == "wifi":
        return I_WIFI, f"Wi-Fi: {info['ssid']}", CYAN
    if kind == "url":
        return I_LINK, info["url"], CYAN
    if kind == "contact":
        return I_USER, info["fields"].get("FN") or "Contact", GREEN
    if kind == "product":
        return I_BAR, f"Product barcode {rec['text']}", YELLOW
    return I_TEXT, rec["text"].replace("\n", " ")[:80], FG


# ---------- camera ----------
class Camera:
    """rpicam-vid streaming raw frames; keeps the newest grey frame and decodes it."""

    def __init__(self):
        self.proc = None
        self.frame = None
        self.found = None          # (text, format, position, time)
        self.error = None
        self.running = False

    def start(self):
        if self.running:
            return
        cmd = ["rpicam-vid", "-t", "0", "--nopreview", "--codec", "yuv420", "--width", str(W), "--height", str(H),
               "--framerate", str(FPS), "--mode", SENSOR_MODE, "--autofocus-mode", "continuous", "-o", "-"]
        if os.path.exists(NOIR_TUNING):
            cmd += ["--tuning-file", NOIR_TUNING]
        self.error = None
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            self.error = "rpicam-vid is not installed"
            return
        self.running = True
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        out = self.proc.stdout
        while self.running:
            buf = out.read(FRAME)
            if not buf or len(buf) < FRAME:
                err = self.proc.stderr.read().decode(errors="replace") if self.proc.poll() is not None else ""
                if self.running:
                    self.error = ("The camera is busy (is the Camera app open?)" if "busy" in err.lower()
                                  else "The camera stopped. " + (err.strip().splitlines() or [""])[-1][:120])
                self.running = False
                break
            y = np.frombuffer(buf, dtype=np.uint8, count=W * H).reshape(H, W)
            self.frame = y
            try:
                res = zxingcpp.read_barcodes(y)
            except Exception:
                res = []
            if res:
                r = res[0]
                p = r.position
                pts = [(p.top_left.x, p.top_left.y), (p.top_right.x, p.top_right.y),
                       (p.bottom_right.x, p.bottom_right.y), (p.bottom_left.x, p.bottom_left.y)]
                self.found = (r.text, r.format.name, pts, time.time())

    def stop(self):
        self.running = False
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


def preview(y, cols, rows, box=None, age=99.0):
    """Grey half-block picture: each cell shows two pixels (upper and lower half)."""
    ph = rows * 2
    ys = (np.arange(ph) * H / ph).astype(int)
    xs = (np.arange(cols) * W / cols).astype(int)
    small = y[np.ix_(ys, xs)]
    inside = None
    if box and age < 1.5:
        bx = [x * cols / W for x, _ in box]
        by = [v * ph / H for _, v in box]
        x0, x1, y0, y1 = min(bx), max(bx), min(by), max(by)
        inside = (x0, x1, y0, y1)
    t = Text(no_wrap=True, overflow="crop")
    for r in range(rows):
        top, bot = small[2 * r], small[2 * r + 1]
        for c in range(cols):
            a, b = int(top[c]), int(bot[c])
            if inside and inside[0] <= c <= inside[1] and inside[2] <= 2 * r + 1 and 2 * r <= inside[3]:
                ta, tb = (min(255, a // 2 + 90), a // 3, min(255, a // 2 + 120)), (min(255, b // 2 + 90), b // 3, min(255, b // 2 + 120))
            else:
                ta, tb = (a, a, a), (b, b, b)
            t.append("▀", f"rgb({ta[0]},{ta[1]},{ta[2]}) on rgb({tb[0]},{tb[1]},{tb[2]})")
        if r < rows - 1:
            t.append("\n")
    return t


# ---------- screens ----------
class Confirm(ModalScreen):
    BINDINGS = [Binding("y", "yes", show=False), Binding("escape,n,left", "no", show=False)]
    DEFAULT_CSS = """
    Confirm { align: center middle; background: #282a36 60%; }
    Confirm > Static { width: 64; height: auto; background: #21222c; border: round #ffb86c; padding: 1 2; }
    """

    def __init__(self, msg):
        super().__init__()
        self.msg = msg

    def compose(self):
        yield Static(Text.assemble((self.msg, FG), "\n\n", ("y", f"bold {GREEN}"), (" yes   ", GRAY),
                                   ("n", f"bold {RED}"), (" no", GRAY)))

    def action_yes(self):
        self.dismiss(True)

    def action_no(self):
        self.dismiss(False)


class Qr(App):
    TITLE = "QR scanner"
    CSS = """
    Screen { background: #282a36; }
    #title { height: 3; padding: 1 3 0 3; }
    #main { height: 1fr; }
    #cam { width: auto; height: auto; padding: 0 1 0 3; }
    #side { width: 1fr; padding: 0 2; }
    #result { height: auto; max-height: 12; margin-bottom: 1; }
    #actions, #actions:focus { height: 1fr; background: #282a36; border: none; scrollbar-color: #bd93f9;
                               background-tint: #282a36 0%; }
    OptionList > .option-list--option-highlighted,
    OptionList:focus > .option-list--option-highlighted { background: #44475a; color: #f8f8f2; }
    #hint { height: 1; padding: 0 3; }
    """.replace("#bd93f9", ACCENT)
    BINDINGS = [Binding("q", "quit", show=False), Binding("space", "pause", show=False),
                Binding("c", "copy", show=False), Binding("right", "act", show=False),
                Binding("delete", "forget", show=False)]

    def __init__(self):
        super().__init__()
        self.cam = Camera()
        self.history = load_json(HISTORY, [])
        self.current = None        # the record shown on the right
        self.last_seen = None
        self.paused = False
        self.actions = []

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with Horizontal(id="main"):
            yield Static(id="cam")
            with Vertical(id="side"):
                yield Static(id="result")
                yield OptionList(id="actions")
        yield Static(Text.assemble(("Enter", CYAN), (" do it  ", GRAY), ("c", CYAN), (" copy  ", GRAY),
                                   ("Space", CYAN), (" pause camera  ", GRAY), ("Delete", CYAN), (" forget  ", GRAY),
                                   ("q", CYAN), (" quit", GRAY)), id="hint")

    def on_mount(self):
        self.query_one("#actions").focus()
        self.cam.start()
        self.set_interval(1 / 6, self.tick)
        self.show_result()

    def on_unmount(self):
        self.cam.stop()

    def preview_size(self):
        cols = max(20, min(72, self.size.width - 46))
        rows = max(6, min(self.size.height - 7, round(cols * 9 / 16 / 2)))
        return cols, rows

    def tick(self):
        cam = self.cam
        title = Text.assemble((I_QR + "  ", FG), ("QR scanner", f"bold {ACCENT}"))
        if cam.error:
            title.append(f"   {cam.error}", RED)
        elif self.paused:
            title.append("   camera paused, Space resumes", YELLOW)
        else:
            title.append("   point the camera at a QR code or barcode", GRAY)
        self.query_one("#title").update(title)
        cols, rows = self.preview_size()
        if cam.frame is not None and not self.paused:
            age = time.time() - cam.found[3] if cam.found else 99
            self.query_one("#cam").update(preview(cam.frame, cols, rows, cam.found[2] if cam.found else None, age))
        elif cam.frame is None and not cam.error:
            self.query_one("#cam").update(Text(f"\n  {I_CAM}  Starting the camera...", style=GRAY))
        if cam.found and cam.found[0] != self.last_seen:
            text, fmt, _, _ = cam.found
            self.last_seen = text
            self.new_code(text, fmt)

    def new_code(self, text, fmt):
        rec = {"text": text, "format": fmt, "info": classify(text, fmt), "t": time.time()}
        self.history = [rec] + [h for h in self.history if h["text"] != text][:99]
        save_json(HISTORY, self.history)
        self.current = rec
        self.bell()
        self.show_result()

    def show_result(self):
        res = self.query_one("#result", Static)
        rec = self.current
        actions = []
        if rec:
            icon, label, color = describe(rec)
            info = rec["info"]
            t = Text.assemble((f"{icon}  ", color), (label, f"bold {color}"), (f"\n{rec['format']}", GRAY))
            if info["kind"] == "wifi":
                t.append(f"\nSecurity {info['security']}" + (", hidden network" if info["hidden"] else ""), GRAY)
                actions.append(("wifi", f"{I_WIFI}  Join {info['ssid']}"))
            elif info["kind"] == "url":
                actions.append(("open", f"{I_LINK}  Open in w3m"))
            elif info["kind"] == "contact":
                for k, name in (("TEL", "Phone"), ("EMAIL", "Email"), ("ORG", "Company"), ("TITLE", "Title"),
                                ("URL", "Web"), ("ADR", "Address")):
                    if info["fields"].get(k):
                        t.append(f"\n{name:<9}", GRAY)
                        t.append(info["fields"][k], FG)
            elif info["kind"] == "product":
                actions.append(("open", f"{I_LINK}  Look it up (Open Food Facts)"))
            if info["kind"] in ("text", "uri") or len(rec["text"]) > 80:
                t.append("\n\n" + rec["text"][:600], FG)
            actions.append(("copy", f"{I_COPY}  Copy to the clipboard"))
        else:
            t = Text("Nothing scanned yet.\n\nHold a code 15 to 30 cm from the camera. It scans by itself.", style=GRAY)
        res.update(t)
        self.actions = actions
        ol = self.query_one("#actions", OptionList)
        ol.clear_options()
        opts = [Option(Text(label, style=f"bold {FG}"), id=f"a:{k}") for k, label in actions]
        if self.history:
            opts.append(Option(Text("\nEarlier scans", style=f"bold {ACCENT}"), disabled=True))
            for i, h in enumerate(self.history[:30]):
                icon, label, color = describe(h)
                when = time.strftime("%b %-d %H:%M", time.localtime(h["t"]))
                opts.append(Option(Text.assemble((f"{icon}  ", color), (label[:44], FG), (f"  {when}", GRAY)), id=f"h:{i}"))
        ol.add_options(opts)
        if opts:
            ol.highlighted = 0

    def on_option_list_option_selected(self, e):
        oid = e.option.id
        if oid.startswith("h:"):
            self.current = self.history[int(oid[2:])]
            self.show_result()
        else:
            self.do_action(oid[2:])

    def action_act(self):
        self.query_one("#actions", OptionList).action_select()

    def do_action(self, kind):
        rec = self.current
        if not rec:
            return
        info = rec["info"]
        if kind == "copy":
            self.action_copy()
        elif kind == "open":
            url = info.get("url") or f"https://world.openfoodfacts.org/product/{rec['text']}"
            subprocess.run(["tmux", "new-window", "-n", "web", "w3m", url], capture_output=True)
        elif kind == "wifi":
            ssid = info["ssid"]

            def done(yes):
                if yes:
                    self.join_wifi(info)
            self.push_screen(Confirm(f"Join the Wi-Fi network {ssid}?\n\nThe deck switches to it, which drops any "
                                     "SSH connection made over the current network."), done)

    def join_wifi(self, info):
        cmd = ["nmcli", "device", "wifi", "connect", info["ssid"]]
        if info["password"] and info["security"] != "NOPASS":
            cmd += ["password", info["password"]]
        if info["hidden"]:
            cmd += ["hidden", "yes"]
        self.notify(f"Connecting to {info['ssid']}...", timeout=3)

        def run():
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            msg = (f"Connected to {info['ssid']}" if r.returncode == 0
                   else f"Could not join: {(r.stderr or r.stdout).strip()[:120]}")
            self.call_from_thread(self.notify, msg, severity="information" if r.returncode == 0 else "error", timeout=5)
        threading.Thread(target=run, daemon=True).start()

    def action_copy(self):
        if not self.current:
            return
        env = os.environ.copy()
        if not env.get("WAYLAND_DISPLAY"):
            run = f"/run/user/{os.getuid()}"
            socks = sorted(f for f in os.listdir(run) if re.fullmatch(r"wayland-\d+", f))
            if socks:
                env["WAYLAND_DISPLAY"] = socks[-1]
        text = self.current["text"]
        ok = shutil.which("wl-copy") and subprocess.run(["wl-copy"], input=text.encode(), env=env).returncode == 0
        # tmux's buffer as well, so Prefix+] pastes it in any tab
        subprocess.run(["tmux", "set-buffer", "--", text], capture_output=True)
        self.notify("Copied" if ok else "Copied to the tmux buffer (Prefix+] pastes)", timeout=1.5)

    def action_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.cam.stop()
            self.cam.frame = None
        else:
            self.cam.start()

    def action_forget(self):
        ol = self.query_one("#actions", OptionList)
        if ol.highlighted is None:
            return
        oid = ol.get_option_at_index(ol.highlighted).id or ""
        if oid.startswith("h:"):
            gone = self.history.pop(int(oid[2:]))
            save_json(HISTORY, self.history)
            if self.current is gone:
                self.current = None
            self.show_result()

    def action_quit(self):
        self.cam.stop()
        self.exit()


if __name__ == "__main__":
    Qr().run()
