"""Built-in Wi-Fi (NetworkManager) and Bluetooth (BlueZ) menus for the deck settings app."""
import re
import subprocess

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

PURPLE, PINK, CYAN, GREEN = "#bd93f9", "#ff79c6", "#8be9fd", "#50fa7b"
ORANGE, YELLOW, RED, GRAY, FG = "#ffb86c", "#f1fa8c", "#ff5555", "#6272a4", "#f8f8f2"
DOT = "\u25cf "
LOCK = "\uf023"


# ---------- helpers ----------
def sh(cmd: str, timeout: int = 20) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except subprocess.TimeoutExpired:
        return ""


def run(args, timeout: int = 45):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, (p.stdout + " " + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, "timed out"


def item(oid, icon, label, desc="", color=PURPLE, disabled=False):
    return Option(Text.assemble((icon, color), "  ", (f"{label:<24}", f"bold {FG}"), (desc, GRAY)),
                  id=oid, disabled=disabled)


def heading(text):
    return Option(Text(text, style=f"bold {GRAY}"), disabled=True)


def split_terse(line: str):
    """Split nmcli -t output on unescaped colons."""
    return [f.replace("\\:", ":") for f in re.split(r"(?<!\\):", line)]


# ---------- Wi-Fi readers ----------
def wifi_on() -> bool:
    return sh("nmcli radio wifi") == "enabled"


def wifi_networks():
    out = sh("nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY dev wifi list --rescan no")
    nets = {}
    for line in out.splitlines():
        f = split_terse(line)
        if len(f) < 4 or not f[1]:
            continue
        inuse, ssid, sig, sec = f[0] == "*", f[1], int(f[2] or 0), f[3]
        cur = nets.get(ssid)
        if cur is None or sig > cur["signal"]:
            nets[ssid] = {"ssid": ssid, "signal": sig, "secure": sec not in ("", "--"),
                          "inuse": inuse or bool(cur and cur["inuse"])}
        elif inuse:
            cur["inuse"] = True
    return sorted(nets.values(), key=lambda n: (not n["inuse"], -n["signal"]))


def wifi_saved():
    """List of (ssid, uuid) for saved Wi-Fi connections."""
    res = []
    for line in sh("nmcli -t -f UUID,TYPE con show").splitlines():
        f = split_terse(line)
        if len(f) >= 2 and f[1] == "802-11-wireless":
            ssid = sh(f"nmcli -g 802-11-wireless.ssid con show uuid {f[0]}")
            res.append((ssid, f[0]))
    return res


# ---------- Bluetooth readers ----------
def bt_powered() -> bool:
    return "Powered: yes" in sh("timeout 3 bluetoothctl show")


def bt_devices(kind: str = ""):
    devs = []
    for line in sh(f"timeout 3 bluetoothctl devices {kind}").splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[0] == "Device":
            devs.append((parts[1], parts[2]))
    return devs


def bt_info(mac: str) -> dict:
    info = {}
    for line in sh(f"timeout 3 bluetoothctl info {mac}").splitlines():
        if ":" in line:
            k, v = line.strip().split(":", 1)
            info[k.strip()] = v.strip()
    return info


# ---------- password prompt ----------
class PasswordPrompt(ModalScreen):
    DEFAULT_CSS = """
    PasswordPrompt { align: center middle; background: rgba(0, 0, 0, 0.6); }
    #pwdialog { width: 60; height: auto; padding: 1 2; border: round #bd93f9; background: #282a36; }
    #pwdialog Input { margin: 1 0; border: none; background: #44475a; color: #f8f8f2; }
    #pwdialog OptionList { height: auto; padding: 0; }
    """

    def __init__(self, ssid: str):
        super().__init__()
        self.ssid = ssid

    def compose(self) -> ComposeResult:
        with Vertical(id="pwdialog"):
            yield Static(Text(f"Password for {self.ssid}", style=f"bold {FG}"))
            yield Input(password=True, placeholder="password", id="pw")
            yield OptionList(item("ok", "\uf00c", "Connect", "", GREEN), item("cancel", "\uf00d", "Cancel", "", GRAY))

    def on_mount(self) -> None:
        self.query_one("#pw", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value or None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.query_one("#pw", Input).value or None if event.option.id == "ok" else None)


# ---------- menus ----------
class NetMixin:
    """Mixed into the Settings app, which provides MenuCls and ConfirmCls."""

    # ----- Wi-Fi -----
    def open_wifi(self):
        self.push_screen(self.MenuCls("Wi-Fi", self.wifi_items, self.wifi_pick))

    def wifi_items(self):
        on = wifi_on()
        opts = [item("toggle", "\uf1eb", "Wi-Fi", "on" if on else "off", GREEN if on else GRAY)]
        if not on:
            return opts
        opts.append(item("scan", "\uf002", "Scan for networks", "", CYAN))
        opts += [heading(""), heading("Networks")]
        self._nets = wifi_networks()
        if not self._nets:
            opts.append(item("none", "\uf05e", "No networks found", "tap Scan", GRAY, disabled=True))
        for i, n in enumerate(self._nets):
            desc = DOT + "connected" if n["inuse"] else f"{n['signal']}%" + (f"  {LOCK}" if n["secure"] else "")
            opts.append(item(f"net:{i}", "\uf1eb", n["ssid"][:24], desc, GREEN if n["inuse"] else ORANGE))
        opts += [heading(""), item("saved", "\uf02e", "Saved networks", "forget a network", PURPLE)]
        return opts

    def wifi_pick(self, menu, oid):
        if oid == "toggle":
            ok, msg = run(["nmcli", "radio", "wifi", "off" if wifi_on() else "on"])
            if not ok:
                self.notify(f"Wi-Fi: {msg[:80]}", severity="error")
        elif oid == "scan":
            run(["nmcli", "dev", "wifi", "rescan"])
            self.notify("Scanning for networks...")
            self.set_timer(5, menu.refresh_menu)
        elif oid.startswith("net:"):
            n = self._nets[int(oid[4:])]
            if n["inuse"]:
                self.push_screen(self.ConfirmCls(f"Disconnect from {n['ssid']}?", "Disconnect"),
                                 lambda yes: yes and run(["nmcli", "dev", "disconnect", "wlan0"]))
                return
            uuid = next((u for s, u in wifi_saved() if s == n["ssid"]), None)
            if uuid or not n["secure"]:
                self.wifi_connect(n["ssid"], None, uuid)
            else:
                self.push_screen(PasswordPrompt(n["ssid"]),
                                 lambda pw, s=n["ssid"]: pw and self.wifi_connect(s, pw, None))
        elif oid == "saved":
            self.push_screen(self.MenuCls("Saved networks", self.saved_items, self.saved_pick))

    @work(thread=True, exclusive=True, group="wifi")
    def wifi_connect(self, ssid, password, uuid):
        self.call_from_thread(self.notify, f"Connecting to {ssid}...")
        before = {u for _, u in wifi_saved()}
        if uuid:
            ok, msg = run(["nmcli", "con", "up", "uuid", uuid])
        else:
            args = ["nmcli", "dev", "wifi", "connect", ssid] + (["password", password] if password else [])
            ok, msg = run(args)
            if not ok:   # remove the half-made profile so a retry asks for the password again
                for _, u in wifi_saved():
                    if u not in before:
                        run(["nmcli", "con", "delete", "uuid", u])
        if ok:
            self.call_from_thread(self.notify, f"Connected to {ssid}")
        else:
            self.call_from_thread(self.notify, f"Could not connect to {ssid}: {msg[:70]}", severity="error")

    def saved_items(self):
        self._saved = wifi_saved()
        if not self._saved:
            return [item("none", "\uf05e", "No saved networks", "", GRAY, disabled=True)]
        return [item(f"saved:{i}", "\uf1eb", (s or "unnamed")[:24], "tap to forget", PURPLE)
                for i, (s, _) in enumerate(self._saved)]

    def saved_pick(self, menu, oid):
        if oid.startswith("saved:"):
            ssid, uuid = self._saved[int(oid[6:])]
            self.push_screen(self.ConfirmCls(f"Forget {ssid}?", "Forget"),
                             lambda yes: yes and (run(["nmcli", "con", "delete", "uuid", uuid]),
                                                  menu.refresh_menu()))

    # ----- Bluetooth -----
    def open_bt(self):
        self.push_screen(self.MenuCls("Bluetooth", self.bt_items, self.bt_pick))

    def bt_items(self):
        on = bt_powered()
        opts = [item("toggle", "\uf293", "Bluetooth", "on" if on else "off", GREEN if on else GRAY)]
        if not on:
            return opts
        opts.append(item("scan", "\uf002", "Scan for devices", "8 seconds", CYAN))
        paired = bt_devices("Paired")
        connected = {m for m, _ in bt_devices("Connected")}
        paired_macs = {m for m, _ in paired}
        opts += [heading(""), heading("My devices")]
        if not paired:
            opts.append(item("none1", "\uf05e", "No paired devices", "", GRAY, disabled=True))
        for mac, name in paired:
            desc = DOT + "connected" if mac in connected else "not connected"
            opts.append(item(f"dev:{mac}", "\uf293", name[:24], desc, GREEN if mac in connected else PINK))
        others = [(m, n) for m, n in bt_devices()
                  if m not in paired_macs and not re.fullmatch(r"([0-9A-F]{2}-){5}[0-9A-F]{2}", n)]
        opts += [heading(""), heading("Other devices")]
        if not others:
            opts.append(item("none2", "\uf05e", "None found", "tap Scan", GRAY, disabled=True))
        for mac, name in others:
            opts.append(item(f"dev:{mac}", "\uf293", name[:24], "tap to pair", ORANGE))
        return opts

    def bt_pick(self, menu, oid):
        if oid == "toggle":
            run(["bluetoothctl", "power", "off" if bt_powered() else "on"])
        elif oid == "scan":
            self.bt_scan(menu)
        elif oid.startswith("dev:"):
            mac = oid[4:]
            name = bt_info(mac).get("Name", mac)
            self.push_screen(self.MenuCls(name, lambda: self.bt_dev_items(mac),
                                          lambda m, o: self.bt_dev_pick(m, o, mac, name)))

    @work(thread=True, exclusive=True, group="bt")
    def bt_scan(self, menu):
        self.call_from_thread(self.notify, "Scanning for Bluetooth devices...")
        run(["bluetoothctl", "--timeout", "8", "scan", "on"], timeout=15)
        self.call_from_thread(menu.refresh_menu)
        self.call_from_thread(self.notify, "Scan finished")

    def bt_dev_items(self, mac):
        info = bt_info(mac)
        paired, connected = info.get("Paired") == "yes", info.get("Connected") == "yes"
        opts = [heading(f"{mac}   " + ("connected" if connected else "paired" if paired else "not paired")), heading("")]
        if not paired:
            opts.append(item("pair", "\uf0c1", "Pair and connect", "", GREEN))
        else:
            opts.append(item("disconnect", "\uf127", "Disconnect", "", ORANGE) if connected
                        else item("connect", "\uf0c1", "Connect", "", GREEN))
            opts.append(item("remove", "\uf1f8", "Remove device", "forget the pairing", RED))
        return opts

    def bt_dev_pick(self, menu, oid, mac, name):
        if oid == "pair":
            self.bt_action(name, [["bluetoothctl", "--agent", "NoInputNoOutput", "pair", mac],
                                  ["bluetoothctl", "trust", mac],
                                  ["bluetoothctl", "connect", mac]], "Paired and connected", menu)
        elif oid == "connect":
            self.bt_action(name, [["bluetoothctl", "connect", mac]], "Connected", menu)
        elif oid == "disconnect":
            self.bt_action(name, [["bluetoothctl", "disconnect", mac]], "Disconnected", menu)
        elif oid == "remove":
            self.push_screen(self.ConfirmCls(f"Remove {name}?", "Remove"),
                             lambda yes: yes and (run(["bluetoothctl", "remove", mac]), self.pop_screen()))

    @work(thread=True, exclusive=True, group="bt")
    def bt_action(self, name, steps, done_msg, menu):
        self.call_from_thread(self.notify, f"{name}: working...")
        for args in steps:
            ok, msg = run(args, timeout=30)
            if not ok:
                self.call_from_thread(self.notify, f"{name}: failed ({msg[:60]})", severity="error")
                self.call_from_thread(menu.refresh_menu)
                return
        self.call_from_thread(self.notify, f"{name}: {done_msg}")
        self.call_from_thread(menu.refresh_menu)
