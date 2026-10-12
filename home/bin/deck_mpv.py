"""Background audio for the deck apps (YouTube, Podcasts, Radio): one mpv per app, driven over its
IPC socket. Starting playback in one app pauses the others and Spotify, so only one thing plays."""
import json
import os
import re
import socket
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
VENV_BIN = os.path.dirname(os.path.abspath(sys.executable))
RUN = f"/run/user/{os.getuid()}"
SOCKETS = {name: f"{RUN}/deck-{name}.sock" for name in ("tube", "pods", "radio")}


def env():
    """Environment for mpv and yt-dlp: venv tools and deno on PATH, and a Wayland display."""
    e = os.environ.copy()
    e["PATH"] = f"{VENV_BIN}:{HOME}/.local/bin:" + e.get("PATH", "")
    if not e.get("WAYLAND_DISPLAY"):           # tmux started over SSH has no display set
        socks = sorted((f for f in os.listdir(RUN) if re.fullmatch(r"wayland-\d+", f)), reverse=True)
        if socks:
            e["WAYLAND_DISPLAY"] = socks[0]
    e.setdefault("XDG_RUNTIME_DIR", RUN)
    return e


def ipc(sock, *command):
    """Send one command. Returns its data (True if it has none), or None if mpv is not running
    or the command failed."""
    s = socket.socket(socket.AF_UNIX)
    try:
        s.settimeout(1)
        s.connect(sock)
        s.sendall((json.dumps({"command": list(command), "request_id": 1}) + "\n").encode())
        buf = b""
        while True:
            chunk = s.recv(65536)
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


def start(sock, extra=(), log=None):
    """Start an idle background mpv on `sock` unless one answers already. True when it is ready."""
    if ipc(sock, "get_property", "idle-active") is not None:
        return True
    cmd = ["mpv", "--no-video", "--idle=once", f"--input-ipc-server={sock}", "--no-terminal", *extra]
    if log:
        cmd.append(f"--log-file={log}")
    subprocess.Popen(cmd, env=env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)    # keeps playing after the app closes
    for _ in range(50):
        time.sleep(0.1)
        if ipc(sock, "get_property", "idle-active") is not None:
            return True
    return False


def pause_others(mine=None):
    """Pause every other deck player and Spotify."""
    for sock in SOCKETS.values():
        if sock != mine:
            ipc(sock, "set_property", "pause", True)
    subprocess.run(["playerctl", "-p", "spotifyd", "pause"], capture_output=True)


def quote(s):
    """mpv key=value lists split on commas; %len% quoting keeps any text intact."""
    return f"%{len(s.encode())}%{s}"


def status(sock):
    """Now-playing info, or None when nothing is loaded."""
    title = ipc(sock, "get_property", "media-title")
    if title is None or ipc(sock, "get_property", "idle-active") is True:
        return None
    return {"title": title, "pos": ipc(sock, "get_property", "time-pos") or 0,
            "dur": ipc(sock, "get_property", "duration"), "paused": ipc(sock, "get_property", "pause") is True,
            "speed": ipc(sock, "get_property", "speed") or 1.0,
            "icy": ipc(sock, "get_property", "metadata/by-key/icy-title")}
