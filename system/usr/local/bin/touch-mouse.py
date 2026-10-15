#!/usr/bin/env python3
# Turns the touchscreen into an absolute mouse: tap = left click,
# drag = left-drag, long press = right click. No scrolling.
import os, time, select
import evdev
from evdev import ecodes as e, UInput, AbsInfo

ROTATE = int(os.environ.get("ROTATE", "1"))   # 0-3, matches the display rotation
# Touch controllers whose panel needs a different rotation than ROTATE.
ROTATE_BY_NAME = {
    "MPI7002": 0,    # 7" HDMI Display-C (1024x600, landscape native)
}
LONG_PRESS = 0.6                               # seconds for right click
MOVE_FRAC = 0.015                              # movement that turns a tap into a drag

def find_touch():
    for path in evdev.list_devices():
        d = evdev.InputDevice(path)
        caps = d.capabilities(absinfo=False)
        if (e.ABS_X in caps.get(e.EV_ABS, []) and e.BTN_TOUCH in caps.get(e.EV_KEY, [])
                and e.INPUT_PROP_DIRECT in d.input_props()):
            return d
    return None

def rotation_for(dev):
    for key, rot in ROTATE_BY_NAME.items():
        if key in dev.name:
            return rot
    return ROTATE

def rotate(u, v, rot):
    return {0: (u, v), 1: (1 - v, u), 2: (1 - u, 1 - v), 3: (v, 1 - u)}[rot]

def main():
    dev = None
    while dev is None:
        dev = find_touch()
        if dev is None:
            time.sleep(2)
    xi, yi = dev.absinfo(e.ABS_X), dev.absinfo(e.ABS_Y)
    rot = rotation_for(dev)
    ui = UInput({e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
                 e.EV_ABS: [(e.ABS_X, AbsInfo(0, 0, 65535, 0, 0, 0)),
                            (e.ABS_Y, AbsInfo(0, 0, 65535, 0, 0, 0))]},
                name="deck-touch-mouse")
    dev.grab()

    def norm(x, y):
        u = (x - xi.min) / max(1, xi.max - xi.min)
        v = (y - yi.min) / max(1, yi.max - yi.min)
        return rotate(min(max(u, 0), 1), min(max(v, 0), 1), rot)

    def move(x, y):
        u, v = norm(x, y)
        ui.write(e.EV_ABS, e.ABS_X, int(u * 65535))
        ui.write(e.EV_ABS, e.ABS_Y, int(v * 65535))
        ui.syn()

    def click(btn, down):
        ui.write(e.EV_KEY, btn, 1 if down else 0)
        ui.syn()

    x = y = 0
    touching = False
    state = "idle"
    t0 = sx = sy = 0

    while True:
        r, _, _ = select.select([dev.fd], [], [], 0.05)
        if r:
            for ev in dev.read():
                if ev.type == e.EV_ABS and ev.code == e.ABS_X:
                    x = ev.value
                elif ev.type == e.EV_ABS and ev.code == e.ABS_Y:
                    y = ev.value
                elif ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH:
                    touching = bool(ev.value)
                elif ev.type == e.EV_SYN and ev.code == e.SYN_REPORT:
                    if touching and state == "idle":
                        state, t0, sx, sy = "pending", time.monotonic(), x, y
                        move(x, y)
                    elif touching and state in ("pending", "drag"):
                        if state == "pending":
                            du = abs(x - sx) / max(1, xi.max - xi.min)
                            dv = abs(y - sy) / max(1, yi.max - yi.min)
                            if max(du, dv) > MOVE_FRAC:
                                move(sx, sy); click(e.BTN_LEFT, True); state = "drag"
                        move(x, y)
                    elif not touching:
                        if state == "pending":
                            click(e.BTN_LEFT, True); click(e.BTN_LEFT, False)
                        elif state == "drag":
                            click(e.BTN_LEFT, False)
                        state = "idle"
        if state == "pending" and touching and time.monotonic() - t0 > LONG_PRESS:
            click(e.BTN_RIGHT, True); click(e.BTN_RIGHT, False)
            state = "done"

if __name__ == "__main__":
    while True:
        try:
            main()
        except OSError:
            time.sleep(2)
