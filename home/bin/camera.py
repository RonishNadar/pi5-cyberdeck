#!/usr/bin/env python3
"""Deck Camera: touch camera app for the IMX708 (Camera Module 3 Wide NoIR).

Preview: rpicam-vid streams MJPEG to stdout from the full-sensor 2304x1296 mode;
         each frame is shown in a GTK 4 window.
Photos:  rpicam-still at full resolution with autofocus on capture.
Videos:  the preview stream is written to .mkv without re-encoding (the Pi 5 has
         no hardware H.264 encoder, so this costs almost no CPU).
"""
import glob
import os
import signal
import subprocess
import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

SAVE_DIR = os.path.expanduser("~/Pictures/Camera")
W, H, FPS = 1280, 720, 20
SENSOR_MODE = "2304:1296:10:P"          # full field of view (the 1536x864 mode is a crop)
NOIR_TUNING = next(iter(glob.glob("/usr/share/libcamera/ipa/rpi/pisp/imx708_wide_noir.json")), None)
NOTIFY = os.path.expanduser("~/bin/notify")

CSS = """
window { background: #000000; }
.side { background: rgba(40, 42, 54, 0.85); padding: 14px; border-radius: 28px; }
.round { border-radius: 9999px; min-width: 92px; min-height: 92px; padding: 0;
         background: #44475a; background-image: none; color: #f8f8f2;
         font-weight: bold; font-size: 15px; border: none; box-shadow: none; }
.round:active { background: #6272a4; }
.on { background: #bd93f9; color: #282a36; }
.shutter { min-width: 124px; min-height: 124px; background: #f8f8f2; color: #282a36;
           border: 6px solid #bd93f9; }
.rec { background: #ff5555; color: #282a36; }
.recording { background: #f8f8f2; color: #ff5555; }
.exit { background: #282a36; color: #ff5555; border: 3px solid #ff5555; }
.status { background: rgba(40, 42, 54, 0.85); color: #f8f8f2; padding: 10px 16px;
          border-radius: 14px; font-size: 17px; font-weight: bold; }
.flash { background: #ffffff; }
.ask { background: rgba(40, 42, 54, 0.95); padding: 28px 36px; border-radius: 28px;
       border: 3px solid #bd93f9; }
.ask-title { color: #f8f8f2; font-size: 24px; font-weight: bold; }
.ask-hint { color: #6272a4; font-size: 15px; }
.ask .round { min-width: 130px; min-height: 64px; }
"""


class Camera(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="tech.ronad.Camera")
        self.opts = {"hdr": False, "night": False, "noir": bool(NOIR_TUNING), "flip": False}
        self.proc = None
        self.rec = None
        self.rec_path = ""
        self.rec_start = 0
        self.rec_lock = threading.Lock()
        self.pending = False
        self.busy = False
        self.asking = False

    # ---------- window ----------
    def do_activate(self):
        provider = Gtk.CssProvider()
        try:
            provider.load_from_string(CSS)
        except AttributeError:
            provider.load_from_data(CSS.encode(), -1)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.win = Gtk.ApplicationWindow(application=self, title="Camera")
        self.win.connect("close-request", lambda *_: self.cleanup() or False)

        overlay = Gtk.Overlay()
        self.picture = Gtk.Picture(hexpand=True, vexpand=True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        overlay.set_child(self.picture)

        self.flash = Gtk.Box(hexpand=True, vexpand=True, can_target=False)
        self.flash.add_css_class("flash")
        self.flash.set_opacity(0)
        overlay.add_overlay(self.flash)

        self.status_label = Gtk.Label(label="Starting camera...", halign=Gtk.Align.START,
                                      valign=Gtk.Align.START, margin_start=18, margin_top=18)
        self.status_label.add_css_class("status")
        overlay.add_overlay(self.status_label)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                       halign=Gtk.Align.END, valign=Gtk.Align.CENTER, margin_end=18)
        side.add_css_class("side")
        self.buttons = {}

        def button(key, label, handler, *classes):
            b = Gtk.Button(label=label)
            b.add_css_class("round")
            for c in classes:
                b.add_css_class(c)
            if key in self.opts and self.opts[key]:
                b.add_css_class("on")
            b.connect("clicked", lambda *_: handler(key))
            side.append(b)
            self.buttons[key] = b

        button("hdr", "HDR", self.toggle)
        button("night", "NIGHT", self.toggle)
        if NOIR_TUNING:
            button("noir", "NoIR", self.toggle)
        button("flip", "FLIP", self.toggle)
        button("photo", "PHOTO", lambda _: self.take_photo(), "shutter")
        button("rec", "REC", lambda _: self.toggle_record(), "rec")
        button("pics", "PICS", lambda _: self.open_gallery())
        button("exit", "EXIT", lambda _: self.quit_app(), "exit")
        overlay.add_overlay(side)

        # Back key (deck-close.sh sends SIGUSR1): confirm before closing
        self.ask = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                           halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, visible=False)
        self.ask.add_css_class("ask")
        title = Gtk.Label(label="Close Camera?")
        title.add_css_class("ask-title")
        hint = Gtk.Label(label="Y or Enter to close, N or Esc to cancel")
        hint.add_css_class("ask-hint")
        row = Gtk.Box(spacing=18, halign=Gtk.Align.CENTER)
        for text, handler, cls in (("CLOSE", self.quit_app, "exit"), ("CANCEL", self.hide_ask, None)):
            b = Gtk.Button(label=text)
            b.add_css_class("round")
            if cls:
                b.add_css_class(cls)
            b.connect("clicked", lambda _b, h=handler: h())
            row.append(b)
        for w in (title, hint, row):
            self.ask.append(w)
        overlay.add_overlay(self.ask)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self.show_ask)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: self.quit_app() or False)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.win.add_controller(keys)

        self.win.set_child(overlay)
        self.win.fullscreen()
        self.win.present()

        os.makedirs(SAVE_DIR, exist_ok=True)
        self.start_stream()

    def on_key(self, _ctrl, keyval, _code, _state):
        if self.asking:
            if keyval in (Gdk.KEY_y, Gdk.KEY_Y, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self.quit_app()
            elif keyval in (Gdk.KEY_n, Gdk.KEY_N, Gdk.KEY_Escape):
                self.hide_ask()
            return True
        if keyval == Gdk.KEY_Escape:
            self.quit_app()
        elif keyval == Gdk.KEY_space:
            self.take_photo()
        return True

    def status(self, text):
        self.status_label.set_label(text)
        return False

    def notify(self, text):
        if os.path.exists(NOTIFY):
            subprocess.Popen([NOTIFY, "-l", "ok", text])

    # ---------- camera options ----------
    def extra_args(self):
        args = []
        if self.opts["hdr"]:
            args.append("--hdr=auto")
        if self.opts["night"]:
            args += ["--exposure", "long"]
        if self.opts["noir"] and NOIR_TUNING:
            args += ["--tuning-file", NOIR_TUNING]
        if self.opts["flip"]:
            args += ["--rotation", "180"]
        return args

    def toggle(self, key):
        if self.busy or self.rec:
            self.status("Stop recording first" if self.rec else "Busy...")
            return
        self.opts[key] = not self.opts[key]
        btn = self.buttons[key]
        (btn.add_css_class if self.opts[key] else btn.remove_css_class)("on")
        self.status(f"{key.upper()} {'on' if self.opts[key] else 'off'}")
        threading.Thread(target=self.restart_stream, daemon=True).start()

    # ---------- live preview ----------
    def start_stream(self):
        cmd = ["rpicam-vid", "-t", "0", "--nopreview", "--codec", "mjpeg",
               "--width", str(W), "--height", str(H), "--framerate", str(FPS),
               "--autofocus-mode", "continuous", "--mode", SENSOR_MODE, "-o", "-"] + self.extra_args()
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        threading.Thread(target=self.read_frames, args=(self.proc,), daemon=True).start()
        GLib.idle_add(self.status, "Ready")

    def stop_stream(self):
        proc, self.proc = self.proc, None
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    def restart_stream(self):
        self.busy = True
        self.stop_stream()
        self.start_stream()
        self.busy = False

    def read_frames(self, proc):
        buf = bytearray()
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:
                s = buf.find(b"\xff\xd8")
                if s < 0:
                    del buf[:-1]
                    break
                e = buf.find(b"\xff\xd9", s + 2)
                if e < 0:
                    del buf[:s]
                    break
                frame = bytes(buf[s:e + 2])
                del buf[:e + 2]
                self.on_frame(frame)
        if proc is self.proc:
            GLib.idle_add(self.status, "Camera stopped: is it connected?")

    def on_frame(self, frame):
        with self.rec_lock:
            if self.rec:
                try:
                    self.rec.stdin.write(frame)
                except (BrokenPipeError, ValueError):
                    pass
        if self.pending:
            return
        self.pending = True
        try:
            tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(frame))
        except GLib.Error:
            self.pending = False
            return
        GLib.idle_add(self.show_frame, tex)

    def show_frame(self, tex):
        self.picture.set_paintable(tex)
        self.pending = False
        return False

    # ---------- photo ----------
    def take_photo(self):
        if self.busy:
            return
        if self.rec:
            self.status("Stop recording first")
            return
        self.busy = True
        self.status("Focusing...")
        self.flash.set_opacity(0.8)
        GLib.timeout_add(120, lambda: self.flash.set_opacity(0) or False)
        threading.Thread(target=self.photo_worker, daemon=True).start()

    def photo_worker(self):
        path = os.path.join(SAVE_DIR, time.strftime("IMG_%Y%m%d_%H%M%S.jpg"))
        self.stop_stream()
        cmd = ["rpicam-still", "-n", "-t", "1500", "--autofocus-on-capture", "-o", path] + self.extra_args()
        try:
            ok = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=30).returncode == 0
        except subprocess.TimeoutExpired:
            ok = False
        self.start_stream()
        self.busy = False
        if ok:
            GLib.idle_add(self.status, f"Saved {os.path.basename(path)}")
            self.notify(f"Photo saved: {os.path.basename(path)}")
        else:
            GLib.idle_add(self.status, "Photo failed")

    # ---------- video ----------
    def toggle_record(self):
        btn = self.buttons["rec"]
        if self.rec:
            with self.rec_lock:
                rec, self.rec = self.rec, None
            try:
                rec.stdin.close()
                rec.wait(timeout=10)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                rec.kill()
            btn.set_label("REC")
            btn.remove_css_class("recording")
            self.status(f"Saved {os.path.basename(self.rec_path)}")
            self.notify(f"Video saved: {os.path.basename(self.rec_path)}")
            return
        if self.busy:
            return
        self.rec_path = os.path.join(SAVE_DIR, time.strftime("VID_%Y%m%d_%H%M%S.mkv"))
        rec = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-f", "mjpeg", "-framerate", str(FPS),
                                "-i", "-", "-c:v", "copy", self.rec_path],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self.rec_lock:
            self.rec = rec
        self.rec_start = time.time()
        btn.set_label("STOP")
        btn.add_css_class("recording")
        GLib.timeout_add(500, self.rec_tick)

    def rec_tick(self):
        if not self.rec:
            return False
        s = int(time.time() - self.rec_start)
        self.status(f"\u25cf REC  {s // 60:02d}:{s % 60:02d}")
        return True

    # ---------- other ----------
    def open_gallery(self):
        subprocess.Popen(["tmux", "new-window", "-n", "photos", "bash", "-ic", f"yazi {SAVE_DIR}"])
        self.quit_app()

    def cleanup(self):
        if self.rec:
            self.toggle_record()
        self.stop_stream()

    def show_ask(self):
        self.asking = True
        self.ask.set_visible(True)
        return True                     # keep the signal handler

    def hide_ask(self):
        self.asking = False
        self.ask.set_visible(False)

    def quit_app(self):
        self.cleanup()
        self.quit()


if __name__ == "__main__":
    Camera().run()
