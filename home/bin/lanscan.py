#!/home/admin/.venvs/deck/bin/python
"""LAN scanner TUI: find devices on the network and inspect their open ports."""
import asyncio
import ipaddress
import subprocess
import xml.etree.ElementTree as ET

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Footer, Header, Static


def sh(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def network_info():
    """Return (subnet, own_ip, gateway) for the interface with the default route."""
    iface = sh("ip route show default | awk '{print $5; exit}'")
    gateway = sh("ip route show default | awk '{print $3; exit}'")
    cidr = sh(f"ip -4 -o addr show {iface} | awk '{{print $4; exit}}'") if iface else ""
    if not cidr:
        return None, None, None
    iface_net = ipaddress.ip_interface(cidr)
    return str(iface_net.network), str(iface_net.ip), gateway


async def run(*cmd: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    return out.decode(errors="replace")


class LanScan(App):
    TITLE = "LAN Scan"
    CSS = """
    Screen { background: #282a36; color: #f8f8f2; }
    Header { background: #44475a; color: #bd93f9; }
    Footer { background: #44475a; }
    #bar { height: 3; background: #21222c; }
    #status { width: 1fr; height: 3; content-align: left middle; padding: 0 2; color: #f1fa8c; }
    Button { margin: 0 1; min-width: 12; height: 3; border: none; text-style: bold; }
    #scan { background: #50fa7b; color: #282a36; }
    #ports { background: #8be9fd; color: #282a36; }
    #quit { background: #ff5555; color: #282a36; }
    DataTable { background: #282a36; }
    DataTable > .datatable--header { background: #44475a; color: #bd93f9; text-style: bold; }
    DataTable > .datatable--cursor { background: #bd93f9; color: #282a36; }
    #hosts { height: 2fr; }
    #portlist { height: 1fr; border-top: solid #6272a4; }
    """
    BINDINGS = [("s", "scan", "Scan"), ("p", "ports", "Ports"), ("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="bar"):
            yield Static("Tap Scan to find devices", id="status")
            yield Button("Scan", id="scan")
            yield Button("Ports", id="ports")
            yield Button("Quit", id="quit")
        yield DataTable(id="hosts", cursor_type="row", zebra_stripes=True)
        yield DataTable(id="portlist", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#hosts", DataTable).add_columns("IP", "Hostname", "MAC", "Vendor", "Note")
        self.query_one("#portlist", DataTable).add_columns("Port", "Proto", "State", "Service", "Details")
        self.action_scan()

    def status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        {"scan": self.action_scan, "ports": self.action_ports, "quit": self.exit}[event.button.id]()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "hosts":
            self.action_ports()

    def selected_ip(self):
        table = self.query_one("#hosts", DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    @work(exclusive=True, group="scan")
    async def action_scan(self) -> None:
        subnet, own_ip, gateway = network_info()
        if not subnet:
            self.status("Offline: no network connection")
            return
        self.status(f"Scanning {subnet} ...")
        xml = await run("nmap", "--privileged", "-sn", "-oX", "-", subnet)
        hosts = []
        try:
            for host in ET.fromstring(xml).findall("host"):
                if host.find("status").get("state") != "up":
                    continue
                ip = mac = vendor = ""
                for a in host.findall("address"):
                    if a.get("addrtype") == "ipv4":
                        ip = a.get("addr")
                    elif a.get("addrtype") == "mac":
                        mac, vendor = a.get("addr", ""), a.get("vendor", "")
                hn = host.find("hostnames/hostname")
                name = hn.get("name") if hn is not None else ""
                note = "this deck" if ip == own_ip else ("router" if ip == gateway else "")
                hosts.append((ip, name, mac, vendor, note))
        except ET.ParseError:
            self.status("Scan failed: could not read nmap output")
            return
        hosts.sort(key=lambda h: ipaddress.ip_address(h[0]))
        table = self.query_one("#hosts", DataTable)
        table.clear()
        for h in hosts:
            table.add_row(*h, key=h[0])
        self.status(f"{len(hosts)} devices on {subnet}  |  tap one, then Ports")

    @work(exclusive=True, group="ports")
    async def action_ports(self) -> None:
        ip = self.selected_ip()
        if not ip:
            self.status("Scan first, then select a device")
            return
        self.status(f"Checking top 100 ports on {ip} ...")
        xml = await run("nmap", "--privileged", "-Pn", "-sV", "--version-light",
                        "--top-ports", "100", "-oX", "-", ip)
        table = self.query_one("#portlist", DataTable)
        table.clear()
        count = 0
        try:
            for port in ET.fromstring(xml).iter("port"):
                state = port.find("state").get("state")
                if state != "open":
                    continue
                svc = port.find("service")
                name = svc.get("name", "") if svc is not None else ""
                details = " ".join(filter(None, [svc.get("product"), svc.get("version")])) if svc is not None else ""
                table.add_row(port.get("portid"), port.get("protocol"), state, name, details)
                count += 1
        except ET.ParseError:
            self.status(f"Port scan of {ip} failed")
            return
        self.status(f"{ip}: {count} open ports" if count else f"{ip}: no open ports in the top 100")


if __name__ == "__main__":
    LanScan().run()
