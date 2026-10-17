<p align="center">
  <img src="assets/logo.png" alt="RONAD tech." width="480">
</p>

<h1 align="center">Pi 5 Cyberdeck</h1>

<p align="center">
A terminal-first cyberdeck on a Raspberry Pi 5 and a Waveshare 5.5" 2K touch display.<br>
Ubuntu Server 26.04, sway in kiosk mode, one fullscreen terminal, tmux, Dracula everywhere.
</p>

---

## Contents

- [What you get](#what-you-get)
- [Hardware](#hardware)
- [Software stack](#software-stack)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [Step by step](#step-by-step)
  - [1. Flash Ubuntu Server](#1-flash-ubuntu-server)
  - [2. First boot](#2-first-boot)
  - [3. Display: the custom EDID](#3-display-the-custom-edid)
  - [4. Packages and files](#4-packages-and-files)
  - [5. Boot options](#5-boot-options)
  - [6. Networking with NetworkManager](#6-networking-with-networkmanager)
  - [7. Touch, rotation and cursor](#7-touch-rotation-and-cursor)
  - [8. Audio over HDMI](#8-audio-over-hdmi)
  - [9. Bluetooth address fix](#9-bluetooth-address-fix)
  - [10. Accounts and extras](#10-accounts-and-extras)
  - [11. Camera](#11-camera)
- [Using the deck](#using-the-deck)
- [Spotify](#spotify)
- [YouTube](#youtube)
- [Podcasts](#podcasts)
- [Radio](#radio)
- [Notes](#notes)
- [Weather](#weather)
- [Offline Wikipedia](#offline-wikipedia)
- [QR scanner](#qr-scanner)
- [Books](#books)
- [Battery](#battery)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [How it fits together](#how-it-fits-together)
- [Troubleshooting](#troubleshooting)
- [Keeping this repo up to date](#keeping-this-repo-up-to-date)
- [Credits and links](#credits-and-links)

---

## What you get

- **Boots straight into a fullscreen terminal.** A custom boot splash, then sway (in kiosk mode) running one foot terminal with tmux. No desktop, no panels.
- **Native 1440x2560 on the Waveshare 5.5" panel**, rotated to landscape, through a hand-built EDID (the panel doesn't send a usable one).
- **Touch that behaves like a mouse.** Tap to click, drag to select, long-press for right click, with a ring cursor that grows when you touch and hides after half a second.
- **Three tabs at boot:** `main` (a shell), `apps` (a text menu that opens apps in their own tabs), `settings` (Wi-Fi, Bluetooth, sound, display, date and time, notifications, footer, theme, about, updates, power).
- **Dracula theme** across foot, tmux, Starship, Neovim, yazi, lazygit, btop, cmus, newsboat, aerc, termshark and the menus, with a selectable accent color.
- **A header and a footer.** Header: tabs, Wi-Fi name, date and time. Footer: next appointment, IP, CPU, memory, disk, uptime, weather, battery. Each block can be turned on and off.
- **Notifications** in the header (with sounds) for long-running commands, calendar reminders, high temperature, a full disk or undervoltage.
- **HDMI audio** through the panel's 3.5 mm jack, plus Bluetooth headphones.
- **Remote access** over SSH (`cyberdeck.local`) and optionally Tailscale.

## Hardware

| Part | Notes | Links |
|---|---|---|
| **Raspberry Pi 5, 8 GB** | 4 GB also works | [Product page](https://www.raspberrypi.com/products/raspberry-pi-5/) |
| **Waveshare 5.5" 1440x2560 LCD (with case A)**, SKU 23113 | HDMI display, 10-point capacitive USB touch, 3.5 mm audio out. The display-only version (SKU 23112) works the same. | [Product page](https://www.waveshare.com/5.5inch-1440x2560-lcd.htm?sku=23113), [Wiki](https://www.waveshare.com/wiki/5.5inch_1440x2560_LCD) |
| **Power supply, 5.1 V 5 A USB-C** | The official 27 W supply. Weaker supplies cause undervoltage and limit USB power to 600 mA, which the touch panel shares. | [27 W supply](https://www.raspberrypi.com/products/27w-power-supply/), [Pi 5 power info](https://rptl.io/rpi5-power-supply-info) |
| **microSD card, 32 GB or more** | A1/A2 rated | |
| **USB keyboard** | Needed for typing; touch covers everything else | |
| **Camera Module 3 Wide NoIR (IMX708)** (optional), on **CAM1** | 12 MP, autofocus, HDR, no IR filter (night vision). Needs a 22-pin Pi 5 camera cable. | [Camera docs](https://www.raspberrypi.com/documentation/accessories/camera.html), [Ubuntu camera guide](https://ubuntu.com/hardware/docs/boards/how-to/special_hardware/rpi-camera/) |
| **INA228 power monitor** (optional, for the battery) | I2C at 0x40 on pins 1 (3.3 V), 3 (SDA), 5 (SCL), 6 (GND). Measures pack voltage and, with its shunt in the battery line, current. | [TI INA228](https://www.ti.com/product/INA228) |
| **Headphones or speaker** (optional) | Plug into the panel's 3.5 mm jack, or use Bluetooth | |

**Wiring**
- The panel's **HDMI** goes to the Pi's **HDMI0** port (the micro-HDMI next to the USB-C power jack).
- The panel's **TOUCH** micro-USB goes to a Pi USB port. This both powers the panel (about 440 mA) and carries touch.
- The "with case" kit includes a rigid HDMI adapter sized for the Pi 4. On the Pi 5 it can fit loosely; if the display isn't detected, reseat it or use a normal micro-HDMI cable.

**Panel facts** (from the Waveshare wiki): 1440x2560 portrait IPS, 50 Hz at a 206 MHz pixel clock, 5 V 440 mA, 260 cd/m². The wiki's `hdmi_timings` setup is for the Pi 4's legacy firmware; the Pi 5 ignores it, which is why this repo uses a custom EDID instead.

## Software stack

| Layer | Choice | Why |
|---|---|---|
| OS | **Ubuntu Server 26.04 LTS** (arm64) | Certified on the Pi 5, five years of updates, no desktop to strip out |
| Boot splash | **Plymouth** script theme | Shows the logo while booting |
| Display server | **sway** as a kiosk | Rotation, ring cursor that hides itself, live cursor size changes |
| Terminal | **foot** | Fast Wayland terminal, true color, Nerd Font icons |
| Multiplexer | **tmux** + Dracula plugin | Tabs, header, footer, notifications |
| Touch | **touch-mouse** (Python + evdev) | Turns the touchscreen into an absolute mouse |
| Apps and settings | **Textual** (Python) | Touch-friendly text menus |
| Audio | **PipeWire** + WirePlumber | HDMI and Bluetooth audio |
| Networking | **NetworkManager** | Wi-Fi switching from the settings menu |
| Prompt | **Starship** | Dracula palette |
| Apps | yazi, Neovim, lazygit, btop, cmus, aerc, newsboat, calcurse, w3m, nmap, termshark, bandwhich, bluetuith | See [Using the deck](#using-the-deck) |

## Repository layout

```
pi5-cyberdeck/
├── README.md
├── assets/logo.png                  boot splash logo (made by home/bin/make-logo.sh)
├── boot/                            reference copies of cmdline.txt and config.txt
├── display/1440x2560_50.S           EDID source for edid-generator
├── home/                            copied into your home folder
│   ├── .bash_profile                starts sway on tty1
│   ├── .tmux.conf                   tabs, header, footer, Dracula
│   ├── .config/                     foot, sway, starship, nvim, yazi, lazygit, aerc, ...
│   │   └── deck/deck.bashrc         aliases, notifications hook, prompt
│   ├── .icons/DeckCircle/           ring cursor theme
│   └── bin/                         deck scripts
│       ├── deck.sh                  builds the tmux tabs at startup
│       ├── deck-session.sh          what sway runs: foot + deck.sh
│       ├── launcher.py              apps tab
│       ├── settings.py              settings tab
│       ├── deck_net.py              Wi-Fi and Bluetooth menus
│       ├── deck_prefs.py            date/time, notifications, footer, theme menus
│       ├── deck-footer.sh           footer blocks
│       ├── deck-battery             battery level from the INA228
│       ├── deck-watch.sh            temperature, disk and power warnings
│       ├── notify                   header notifications
│       ├── camera.py                camera app
│       ├── lanscan.py               LAN scanner
│       ├── reader.py                Books e-reader
│       ├── tube.py                  YouTube
│       ├── pods.py                  Podcasts
│       ├── radio.py                 internet radio
│       ├── notes.py, note           Notes app, quick capture
│       ├── weather.py               weather forecast
│       ├── wiki.py                  offline Wikipedia (Kiwix ZIM files)
│       ├── qr.py                    QR and barcode scanner
│       ├── deck_mpv.py              shared background player (mpv) for YouTube, Podcasts, Radio
│       └── make-logo.sh             draws the logo
├── system/                          copied into / with sudo
│   ├── usr/local/bin/touch-mouse.py, bt-set-addr.sh
│   ├── etc/systemd/system/          touch-mouse, bt-set-addr, tty1 autologin
│   ├── etc/polkit-1/rules.d/        reboot, power off and time settings without a password
│   ├── etc/dracut.conf.d/edid.conf  EDID inside the initramfs
│   ├── usr/share/plymouth/themes/deck/
│   └── lib/firmware/edid/           the finished EDID files
└── scripts/
    ├── install-packages.sh          every program the deck uses
    ├── install-files.sh             put home/ and system/ in place
    ├── collect.sh                   copy a working deck's files back into the repo
    └── edid-add-audio.py            make the EDID digital and add HDMI audio
```

## Quick start

On a fresh Ubuntu Server 26.04 install, logged in as `admin`:

```bash
git clone <this repo> ~/projects/pi5-cyberdeck
cd ~/projects/pi5-cyberdeck
./scripts/install-packages.sh
./scripts/install-files.sh
```

Then do [5. Boot options](#5-boot-options) and [6. Networking](#6-networking-with-networkmanager), and reboot. The rest of this README explains each piece and how to do it by hand.

> **Username:** the scripts assume the user is `admin` (several files contain `/home/admin`). With another name, run this once in the repo before installing:
> `grep -rl /home/admin . | xargs sed -i "s|/home/admin|$HOME|g"`

---

## Step by step

### 1. Flash Ubuntu Server

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your computer.
2. Choose **Raspberry Pi 5**, then **Other general-purpose OS → Ubuntu → Ubuntu Server 26.04 LTS (64-bit)**.
3. In the customization settings set:
   - hostname: `cyberdeck`
   - username: `admin` and a password
   - Wi-Fi network and country
   - enable SSH (password authentication)
4. Write the card.

Ubuntu 26.04 needs a Pi 5 bootloader (EEPROM) dated **2025-02-11 or later** ([Ubuntu on Raspberry Pi](https://ubuntu.com/raspberry-pi)). If the Pi was used with Raspberry Pi OS, update it there first with `sudo rpi-eeprom-update -a`.

### 2. First boot

Connect the panel (HDMI0 and TOUCH USB), boot, and SSH in:

```bash
ssh admin@cyberdeck.local          # or the IP from your router
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

Check the power supply:

```bash
sudo vcgencmd get_throttled        # 0x0 = healthy, 0x50000 = undervoltage happened since boot
```

### 3. Display: the custom EDID

**The problem.** The panel doesn't send a usable EDID to the Pi 5, so Linux falls back to 1024x768 and the panel shows three stacked copies of the image. Waveshare's `hdmi_timings` fix only works with the Pi 4's legacy firmware.

**The fix.** Give the kernel an EDID file built from Waveshare's own timings:

```
hdmi_timings=1440 0 10 10 140 2560 0 11 2 2 0 0 0 50 0 206000000 3
Modeline "1440x2560_50" 206.00  1440 1450 1460 1600  2560 2571 2573 2575 +hsync +vsync
```

The finished files are in `system/lib/firmware/edid/`, and `install-files.sh` installs them. To build them yourself:

```bash
sudo apt install -y git build-essential zsh dos2unix edid-decode automake
git clone https://github.com/akatrevorjay/edid-generator.git
cp display/1440x2560_50.S edid-generator/
cd edid-generator && make 1440x2560_50.bin && cd ..
python3 scripts/edid-add-audio.py edid-generator/1440x2560_50.bin 1440x2560_50_audio.bin
edid-decode 1440x2560_50_audio.bin | grep -iE "digital|1440x2560|audio|HDMI|checksum"
sudo mkdir -p /lib/firmware/edid && sudo cp 1440x2560_50_audio.bin /lib/firmware/edid/
```

Notes from getting this working:
- edid-generator's `modeline2edid` refuses a portrait mode (`Computed ratio: UNKNOWN`), so the `.S` file is written by hand with the ratio set to 16:9. That field is legacy and doesn't affect the mode.
- The kernel template declares an **analog** display. Linux only checks digital displays for HDMI support, so the Pi would drive the panel in **DVI mode with no audio**. `edid-add-audio.py` sets byte 20 to digital and adds a CEA-861 block with stereo LPCM audio and the HDMI vendor block.
- Ubuntu 26.04 builds its initramfs with **dracut**, not initramfs-tools, so the EDID is added through `/etc/dracut.conf.d/edid.conf`.

### 4. Packages and files

```bash
./scripts/install-packages.sh      # apt packages, GitHub tools, fonts, Starship, Textual
./scripts/install-files.sh         # configs, scripts, services, splash theme
```

`install-files.sh` backs up any file it replaces to `~/.deck-backup-<date>/`.

### 5. Boot options

Ubuntu on the Pi boots from `/boot/firmware/current/` (see `os_prefix` in `config.txt`), so the kernel options go into **`/boot/firmware/current/cmdline.txt`**. Kernel updates copy that file into `new/`, so the options carry over.

Add these to the **start of the single line**:

```bash
OPTS='quiet splash plymouth.ignore-serial-consoles loglevel=3 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 consoleblank=0 drm.edid_firmware=HDMI-A-1:edid/1440x2560_50_audio.bin video=HDMI-A-1:1440x2560@50,rotate=90'
for f in /boot/firmware/cmdline.txt /boot/firmware/*/cmdline.txt; do
  [ -f "$f" ] || continue
  grep -q edid_firmware "$f" || sudo sed -i "1s|^|$OPTS |" "$f"
done
grep -q '^disable_splash=1' /boot/firmware/config.txt || echo 'disable_splash=1' | sudo tee -a /boot/firmware/config.txt
sudo update-initramfs -u
grep -h -o 'edid_firmware=[^ ]*' /boot/firmware/*/cmdline.txt
```

| Option | Purpose |
|---|---|
| `drm.edid_firmware=...` | Use the custom EDID for HDMI0 |
| `video=HDMI-A-1:1440x2560@50,rotate=90` | Native mode; rotates the text console to landscape |
| `quiet splash plymouth.ignore-serial-consoles` | Show the boot splash on the panel instead of the serial console |
| `loglevel=3 systemd.show_status=false rd.udev.log_level=3` | Hide boot messages |
| `vt.global_cursor_default=0 consoleblank=0` | No blinking console cursor, no screen blanking |

`boot/cmdline.txt` in this repo is a reference copy of a working line. Don't copy it over yours: it ends with machine-specific values. Don't add an `e` after `@50` (it forces the output on and skips the step that detects HDMI mode).

### 6. Networking with NetworkManager

Imager's Wi-Fi setup uses netplan with systemd-networkd. The settings menu uses NetworkManager, so hand Wi-Fi over (this keeps your saved network). **SSH drops for about 20 seconds, and the IP may change**, so reconnect with `ssh admin@cyberdeck.local`:

```bash
for f in /etc/netplan/*.yaml; do
  if sudo grep -q 'renderer:' "$f"; then sudo sed -i 's/^\(\s*\)renderer:.*/\1renderer: NetworkManager/' "$f"
  else sudo sed -i '0,/^network:/s//network:\n  renderer: NetworkManager/' "$f"; fi
done
echo 'network: {config: disabled}' | sudo tee /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg
sudo netplan generate
sudo nohup sh -c '
  systemctl enable --now NetworkManager wpa_supplicant
  netplan apply
  systemctl stop netplan-wpa-wlan0.service
  systemctl restart NetworkManager
  sleep 8
  nmcli connection up "$(nmcli -t -f NAME,TYPE con show | awk -F: "\$2==\"802-11-wireless\"{print \$1; exit}")"
' > /tmp/nm-switch.log 2>&1 &
```

Afterwards, `nmcli device status` should show `wlan0 connected`.

### 7. Touch, rotation and cursor

- **Rotation:** sway rotates the panel with `output HDMI-A-1 ... transform 270` in `~/.config/sway/config` (sway counts the other way from `wlr-randr`, so 270 here is the same as 90 there).
- **Touch:** `touch-mouse.service` grabs the touchscreen and replays it as a virtual mouse: tap = left click, drag = left drag, long press (0.6 s) = right click, no scrolling. Its `ROTATE` value (0 to 3) must match the display rotation. If taps land in the wrong place, cycle it:

  ```bash
  cur=$(grep -oP 'ROTATE=\K[0-3]' /etc/systemd/system/touch-mouse.service); nxt=$(( (cur + 1) % 4 ))
  sudo sed -i "s/ROTATE=$cur/ROTATE=$nxt/" /etc/systemd/system/touch-mouse.service
  sudo systemctl daemon-reload && sudo systemctl restart touch-mouse && echo "ROTATE is now $nxt"
  ```

  That `ROTATE` is the default (the Waveshare). Panels listed in `ROTATE_BY_NAME` at the top of `touch-mouse.py` get their own rotation, matched by the touch controller's name.
- **Other displays:** the deck adapts to the connected panel by its model name. `~/bin/deck-display.sh` picks the foot font size and padding (the 7" [HDMI Display-C](https://www.lcdwiki.com/7inch_HDMI_Display-C), 1024x600, gets size 10; everything else gets the Waveshare's size 28), the sway config has an `output "<make> <model> <serial>"` line per panel, and `touch-mouse.py` picks the touch rotation. Panels that send their own EDID go in the port next to the audio jack (`HDMI-A-2`); keep the Waveshare in `HDMI-A-1`, because the forced EDID only applies there. To add a panel, get its model from `swaymsg -t get_outputs`, then add a case to `deck-display.sh`, an `output` line, and (if needed) a `ROTATE_BY_NAME` entry.
- **Cursor:** the `DeckCircle` theme is a purple ring with a pink dot (96 px, 160 px when pressed). `touch-mouse.py` tells sway to switch sizes over sway's IPC socket. `seat seat0 hide_cursor 500` hides it after half a second.

### 8. Audio over HDMI

The Pi 5 has no headphone jack; audio goes over HDMI to the panel's 3.5 mm jack. With the EDID from step 3 and PipeWire running:

```bash
wpctl status | sed -n '/Sinks:/,/Sources:/p'     # expect "Built-in Audio Digital Stereo (HDMI)"
pw-play /usr/share/sounds/alsa/Front_Center.wav
```

Volume and output device are in **settings → Sound**.

### 9. Bluetooth address fix

Ubuntu leaves the Pi's Bluetooth chip with a placeholder address (`dmesg` shows `BCM: Using default device address (43:45:c0:00:1f:ac)`), so BlueZ hides the adapter. `bt-set-addr.service` sets a real one at boot (Wi-Fi MAC + 1, the Raspberry Pi convention). `btmgmt` only exits cleanly with a terminal, so the script runs it inside `script` (a pseudo-terminal).

```bash
journalctl -u bt-set-addr -b --no-pager | tail -4   # three steps returning 0
bluetoothctl show | grep -E "Controller|Powered"
```

### 10. Accounts and extras

These hold personal data and are **not** in the repo:

- **Email (aerc):** create an app password with your provider, then run `~/bin/aerc-add-account.sh`. Passwords are stored in `~/.config/aerc/pass-<account>` (mode 600).
- **Git:** `git config --global user.name "..."` and `user.email "..."`.
- **Tailscale** (optional): `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up --hostname=cyberdeck`.
- **Claude Code** (optional): `curl -fsSL https://claude.ai/install.sh | bash` ([setup docs](https://code.claude.com/docs/en/setup)).
- **Boot logo:** `~/bin/make-logo.sh ~/Pictures/ronad_logo.png` draws it (League Gothic); for the splash it's rotated with `-rotate 270` and copied to `/usr/share/plymouth/themes/deck/logo.png`, followed by `sudo update-initramfs -u`.

### 11. Camera

The camera app (`~/bin/camera.py`, **Camera** in the apps tab) is a fullscreen GTK 4 window: live preview, PHOTO (full 12 MP with autofocus), REC (video), HDR, NIGHT (long exposure), NoIR tuning, FLIP, and PICS (opens the photos in yazi). Files go to `~/Pictures/Camera/`.

```bash
sudo apt install -y rpicam-apps libcamera-tools ffmpeg python3-gi gir1.2-gtk-4.0
rpicam-hello --list-cameras
```

- **Detection:** with `camera_auto_detect=1` the camera wasn't found on CAM1. Forcing the driver fixed it: `camera_auto_detect=0` and `dtoverlay=imx708` under `[all]` in `/boot/firmware/config.txt` (on the Pi 5, `imx708` without a port option means CAM1; use `dtoverlay=imx708,cam0` for CAM0).
- **Tuning:** the module reports `camera module ID 0x0000`, so libcamera doesn't know it's the Wide NoIR. The app uses `/usr/share/libcamera/ipa/rpi/pisp/imx708_wide_noir.json` by default (the NoIR button).
- **Field of view:** the 1536x864 sensor mode is a center crop, so the preview forces the full-sensor `2304:1296:10:P` mode and scales it down.
- **Video:** the Pi 5 has no hardware H.264 encoder, so videos save the MJPEG preview stream to `.mkv` without re-encoding.
- **Power:** the camera needs the 5 V 5 A supply. On a weaker supply the Pi browned out within seconds of starting the preview (`Undervoltage detected!`, then the log ends). Temporary workaround: `arm_freq=1800` in `config.txt` and a 1280x720, 20 fps preview.
- **sway:** `for_window [app_id="tech.ronad.Camera"] fullscreen enable` in the sway config.

---

## Using the deck

**Tabs:** tap a tab name in the header to switch. With a keyboard, `Ctrl+b` then a number.

| Keys | Action |
|---|---|
| `Ctrl+b c` | New tab |
| `Ctrl+b a` | Jump to the apps tab |
| `Ctrl+b X` | Close the current tab |
| `Ctrl+b ,` | Rename the tab |
| `Ctrl+Alt+F2` | Plain text console (recovery) |

**Apps and settings menus:** tap a line, or use `Up`/`Down`, `Right`/`Enter` to open, `Left`/`Esc` to go back.

**Apps tab:** Terminal, Camera, QR Scanner, Files (yazi), Monitor (btop), Spotify, YouTube, Podcasts, Radio, Notes, Weather, Wikipedia, Books, Music (cmus), Mail (aerc), News (newsboat), Calendar (calcurse), LAN Scan, Git (lazygit), Web (w3m), Claude Code. Edit the `APPS` list in `~/bin/launcher.py` to change it.

**Settings tab:** Wi-Fi (scan, connect, forget), Bluetooth (scan, pair, connect, remove), Sound (volume, output), Display (text size, cursor hide delay), Date & time (time zone, network time, 12/24-hour), Notifications (banners, sounds, long-command alerts, system warnings, history), Footer (blocks on/off), Theme (accent color), About, Software updates, Power (restart display, reboot, shut down).

**Shell shortcuts** (from `deck.bashrc`):

| Command | Does |
|---|---|
| `y` | yazi, and `cd` to where you leave it |
| `s some words` | DuckDuckGo search in w3m |
| `notify "text"` | Header notification (`-l ok/warn/error`, `-s` for sound) |
| `alert <command>` | Run a command, notify when done |
| `notifs` | Recent notifications |
| `lan-scan`, `lan-ports IP`, `sniff`, `bw` | Network tools |
| `space`, `md file.md`, `today`, `todo` | Disk usage, Markdown, calendar |

## Spotify

`spot` (`~/bin/spot.py`, the **Spotify** entry in Apps) is a keyboard-first Spotify client in the same style as the other deck apps. Audio is played by **spotifyd**, which turns the Pi into a Spotify Connect speaker called `cyberdeck`. spot browses your library through the Spotify Web API and tells spotifyd what to play. A Spotify Premium account is required.

Setup:

1. **Spotify developer app.** At developer.spotify.com/dashboard, create an app, add the redirect URI `http://127.0.0.1:8888/callback`, tick **Web API**, save, and copy the **Client ID**.
2. **spotifyd.** `install-packages.sh` builds it from source (the release binary needs OpenSSL 1.1, which Ubuntu 26.04 does not ship). Log it in with `spotifyd authenticate`: open the printed link on a phone or laptop, then copy the `127.0.0.1:...` address the browser ends on and replay it on the Pi with `curl "ADDRESS"` from a second SSH window. Then `systemctl --user enable --now spotifyd`.
3. **spot.** Run `~/bin/spot.py --login` once, paste the Client ID, and do the same copy-the-address step.

| Key | Action |
|---|---|
| `space` | play / pause |
| `n` `p` | next / previous |
| `,` `.` | seek 10 s |
| `-` `+` | Spotify volume |
| `s` `r` | shuffle / repeat |
| `1`-`5` | Home, Search, Liked, Albums, Queue |
| `/` | search |
| `Enter` / `Left` | open or play / back |
| `a` `f` | add to queue / like |
| `o` `i` | open album / artist of a song |
| `d` | choose speaker |
| `?` | all keys |

Notes:

- Spotify's 2026 developer rules apply: songs are only listed for playlists you own or collaborate on (others show a single "Play this playlist" row), and search returns at most 10 results per type per request.
- `use_mpris = true` in `spotifyd.conf` plus `playerctl` lets play/pause, skip, seek and volume go straight to spotifyd over D-Bus, which is instant.
- The audio cache is turned off (`no_audio_cache = true`). A corrupted cached file made spotifyd skip through songs silently.
- Spotify logins are never collected into this repo (`~/.config/deck/spotify*.json`, `~/.cache/spotifyd/oauth`).


## YouTube

`tube.py` (the **YouTube** entry in Apps) searches and plays YouTube without a Google account. **yt-dlp** finds and streams the videos, **mpv** plays them, and **Deno** runs YouTube's player JavaScript for yt-dlp. Videos open full screen in mpv; press `q` there to come back. Audio can also play in the background while you use other tabs.

There are five views: **Subscriptions**, **Search**, **History**, **Playlists** and **Queue**. Switch with `1` to `5`, or `Tab` and `Shift+Tab` (these work from the search box too).

- **Subscriptions** come from each channel's public RSS feed, so they need no login. To bring over your YouTube subscriptions, get `subscriptions.csv` from Google Takeout (select only "YouTube and YouTube Music", then "subscriptions"), copy it to `~/Downloads`, and press `I` (or run `tube.py --import FILE`).
- **Queue:** `a` listens now, `e` adds to the queue. Background audio keeps playing after you leave the app. The Queue view shows what is next; `Enter` jumps to an item and `Delete` removes it.
- **Playlists** are saved on the deck. `l` adds a video to one (or makes a new one), `A` listens to a whole playlist.
- **Settings and data:** subscriptions, playlists and options are in `~/.config/deck/youtube.json`. History (the last 200 videos) is in `~/.local/share/deck/youtube-history.json`.
- **Quality** defaults to 720p H.264. The Pi 5 decodes H.264 in software (it has no H.264 hardware decoder), and H.264 is the cheapest format for it. Change `"quality"` in `youtube.json`. Set `"hide_shorts": true` to hide Shorts.
- **Downloads** go to `~/Videos/YouTube` (video, .mkv) and `~/Music/YouTube` (audio, .m4a, playable in cmus).
- **Media keys** F6 to F8 control whichever player is playing (then whichever is paused, then Spotify), so they work for YouTube audio through mpv-mpris.
- When YouTube changes something and playback breaks, update yt-dlp: `~/.venvs/deck/bin/pip install -U "yt-dlp[default]"`.

| Key | Action |
|---|---|
| `Enter` `Right` | watch full screen (in Playlists: open; in Queue: play that item) |
| `a` / `e` | listen now / add to the queue |
| `l` / `A` | add to a playlist / listen to the whole playlist |
| `d` / `D` | download video / audio only |
| `s` / `c` | subscribe or unsubscribe the channel / open the channel |
| `Space` `,` `.` `-` `+` | pause, seek 10 seconds, volume (background audio) |
| `n` `p` `x` | next, previous, stop |
| `Delete` | remove from playlist, queue or history; delete a playlist |
| `1` to `5`, `Tab` | switch views |
| `/` `I` `r` | search, import subscriptions, refresh |
| `Left` `Esc` | back |
| `?` | all keys |

In the mpv player: `Space` pause, `Left`/`Right` seek 5 seconds, `Up`/`Down` seek 1 minute, `9`/`0` volume, `f` full screen, `q` back to the list.

## Podcasts

`pods.py` (the **Podcasts** entry in Apps) finds shows in Apple's free podcast directory (no account), follows them by RSS, and plays episodes in the background with mpv. It remembers where you stopped in every episode.

Four views, switched with `1` to `4` or `Tab`: **Latest** (newest episodes from your shows), **Shows**, **Find** (top charts, or search by name or topic) and **In progress**.

- Starting an episode pauses YouTube, Radio and Spotify, so only one thing plays.
- Playback speed (`[` `]`, 0.5x to 3x) is kept between episodes. mpv keeps voices at normal pitch.
- `d` downloads an episode to `~/Podcasts/<show>/`; downloaded episodes play from the file, so they work offline.
- Coming from another app? Export your subscriptions as OPML, put the file in `~/Downloads`, and press `I`.
- Subscriptions and speed: `~/.config/deck/podcasts.json`. Positions: `~/.local/share/deck/podcasts-progress.json`. Feeds are cached in `~/.cache/deck/podcasts` and refreshed every 30 minutes (or `r`).

| Key | Action |
|---|---|
| `Enter` `Right` | play from where you stopped (on a show: open it) |
| `s` | subscribe or unsubscribe |
| `d` / `m` / `i` | download / mark played or unplayed / show notes |
| `Space` | pause or play |
| `,` / `.` | back 15 seconds / forward 30 seconds |
| `[` `]` / `-` `+` | speed / volume |
| `x` | stop |
| `1` to `4`, `Tab` | switch views |
| `/` `I` `r` | find, import OPML, refresh |
| `Delete` | unsubscribe (Shows) or clear progress (In progress) |
| `Left` `Esc` | back |

## Radio

`radio.py` (the **Radio** entry in Apps) plays internet radio from radio-browser.info, a free, community-run directory of about 50,000 stations (no account).

Five views, switched with `1` to `5` or `Tab`: **Favorites**, **Top** (most played worldwide), **Search** (by name), **Genres** (pick one to list its stations) and **Heard** (a log of songs the stations announced, so you can look up "what was that song" later).

- When you play a station, the list it came from becomes the player's playlist: `n` / `p`, and the F6 / F8 media keys, switch to the next or previous station.
- The bar at the bottom shows the station, the current song when the station sends it, and whether it is live, paused or buffering.
- Starting a station pauses YouTube, Podcasts and Spotify.
- Favorites: `~/.config/deck/radio.json`. Songs heard (last 300): `~/.local/share/deck/radio-heard.json`.

| Key | Action |
|---|---|
| `Enter` `Right` | play (on a genre: open it) |
| `f` | add to or remove from favorites |
| `n` / `p` | next / previous station |
| `Space` / `-` `+` / `x` | pause / volume / stop |
| `1` to `5`, `Tab` | switch views |
| `/` `r` | search, refresh |
| `Delete` | remove a favorite or a heard song |
| `Left` `Esc` | back |

## Notes

`notes.py` (the **Notes** entry in Apps) keeps notes as plain Markdown files in `~/Notes`, so they stay readable anywhere and are easy to sync or back up.

- **List and preview:** notes are sorted by last edit, with a live preview on the right and a checklist count ("2/5 done").
- **Editor:** full screen with Markdown highlighting. It saves by itself a second after you stop typing, and again when you leave. A new note you leave untouched is not kept.
- **Checklists:** write `- [ ] item`. `Ctrl+T` adds a box to the current line or ticks and unticks it.
- **Search:** `/` searches the text of every note as you type.
- **Delete** moves the note to `~/Notes/.trash`, so nothing is lost by accident.
- **Quick capture from any shell:** `note buy milk` adds a timestamped line to `~/Notes/inbox.md`. `note` on its own opens the app.

| Key | List | Editor |
|---|---|---|
| `Enter` `Right` | edit | |
| `n` | new note (Enter alone uses today's date as the title) | |
| `v` | read view (rendered Markdown; `e` edits) | `Ctrl+P` read view |
| `r` / `d` | rename / delete | |
| `/` | search | |
| | | `Ctrl+T` tick a checkbox, `Ctrl+S` save |
| `Esc` | clear the search | back to the list |
| `q` | quit | |

## Weather

`weather.py` (the **Weather** entry in Apps) shows the weather from Open-Meteo, which is free and needs no account or key.

- **Now:** the temperature in large digits, the conditions, feels-like, wind and gusts with direction, humidity, pressure, sunrise and sunset, today's UV index, and air quality (US AQI).
- **Next 24 hours** in 2-hour steps, with the chance of rain.
- **7 days:** each day's low and high on a bar scaled to the week, plus the chance and amount of rain.
- **Places:** the first one is guessed from your internet address. `a` searches for and adds a place, `Left`/`Right` (or `Tab`) switch between places, `d` removes one.
- **Units:** US units (°F, mph, inches) in the US, metric elsewhere. `u` switches.
- Data refreshes every 15 minutes, and the last forecast stays visible when offline. Places and units are in `~/.config/deck/weather.json`.

## Offline Wikipedia

`wiki.py` (the **Wikipedia** entry in Apps) reads Kiwix ZIM files from `~/Wiki` with no network at all. Articles use the same page layout as Books: a centered column, headings, italics, links in cyan. Infoboxes, navigation boxes and reference lists are left out; tables show as rows with column separators.

- **Search:** type a title; results update as you type. `Enter` opens the first match, `Down` moves into the list. When few titles match, it also searches article text.
- **Reading:** `Up`/`Down` scroll, `Space` pages, `t` jumps to a section, `l` lists the article's links (type to filter, `Enter` follows), `Left` goes back, `/` or `Esc` returns to search.
- **More than one library** (Wikipedia, Wiktionary, Wikivoyage, Project Gutenberg...): `Tab` switches between them.
- **Getting libraries:** `Ctrl+G` lists the Kiwix catalog with sizes and your free space. `Enter` downloads; the download keeps going if you close the app, resumes if it is interrupted, and the file only appears in `~/Wiki` once it is complete.

Which English Wikipedia to get (October 2026 sizes):

| Version | Size | What you get |
|---|---|---|
| Best of Wikipedia, text only | 2.8 GB | about 50,000 most-read articles, complete |
| All articles, intros only | 14 GB | every article, first section only |
| Top 1 million, text only | 17 GB | the million most-read articles, complete |
| All articles, text only | 53 GB | everything (does not fit on a 64 GB card with this setup) |

Pictures cannot be shown in the terminal, so the "with pictures" versions only cost space here.

## QR scanner

`qr.py` (the **QR Scanner** entry in Apps) uses the camera on CAM1 to read QR codes and barcodes (QR, Data Matrix, Aztec, EAN/UPC and more, through zxing-cpp). The terminal shows a live grey preview for aiming, and the code turns purple when it is found. It scans by itself; there is nothing to press.

What it does with a code:

| Code | Actions |
|---|---|
| Wi-Fi (`WIFI:S:...`) | **Join** the network with NetworkManager (asks first: switching networks drops SSH sessions) |
| Link | **Open** in w3m in a new tab |
| Contact card (vCard, MECARD) | shows name, phone, email, company |
| Product barcode | look it up on Open Food Facts |
| Anything | **Copy** (Wayland clipboard and the tmux buffer, so `Prefix+]` pastes it in a terminal) |

- Keys: `Enter` does the highlighted action, `c` copies, `Space` pauses the camera, `Delete` forgets a scan, `q` quits.
- The last 100 scans are kept in `~/.local/share/deck/qr-history.json` and listed under "Earlier scans".
- The camera can only be used by one app at a time: close the Camera app first. The scanner runs the camera at 640x360 and 8 frames a second, which is light on power.

## Books

`reader.py` (the **Books** entry in Apps) is a paged e-reader. It reads EPUB, plain text, Markdown, and PDF (text only, through `pdftotext`) from `~/Books`, including subfolders. Pages flip instead of scroll, text is justified in a centered column, and each chapter shows a progress bar, the page number and the reading time left.

**Library:** books you opened most recently come first, with author and progress. `Enter` or `Right` opens a book, `g` gets free books from Project Gutenberg (search, then `Enter` downloads the EPUB into `~/Books`), `d` deletes, `r` rescans.

| Key | Reading |
|---|---|
| `Right` `Space`, tap the right two thirds | next page |
| `Left`, tap the left third | previous page |
| `Down` `Up`, mouse wheel | scroll one line (crosses into the next or previous chapter at the edges) |

After scrolling by line, `Right` and `Left` snap back to the top of the next or previous page, so pages always break in the same places.
| `]` `[` | next / previous chapter |
| `t` | contents |
| `b` / `m` | bookmark this page / list bookmarks |
| `/` / `n` | search / next match |
| `g` | go to a percentage |
| `v` | view: Single (one centered column) or Fit (full screen width) |
| `f` | focus: hide the header and footer |
| `+` `-` | line width (switches back to Single view) |
| `a` | theme: Dracula, Night (dim gray on black, since the panel has no brightness control), Paper |
| `J` / `s` | justify on or off / spaced or indented paragraphs |
| `?` | key help |
| `Esc` `q` | back to the library |

Reading positions, bookmarks and settings are saved in `~/.config/deck/reader.json`. Positions are stored as a place in the text, not a page number, so changing the width or theme keeps your place. Plain text from Gutenberg is trimmed to the book itself, and its chapters are detected from the `CHAPTER` headings.

## Battery

`~/bin/deck-battery` reads an INA228 power monitor over I2C and feeds the **BAT** block at the right end of the footer. The block stays hidden until the sensor answers, so nothing changes before it is wired.

- **Footer:** `BAT 82% 3h20m` while discharging, a bolt while charging. Green from 50%, yellow from 20%, red below.
- **Level:** pack voltage per cell on a Li-ion discharge curve (3.27 V empty, 4.20 V full), averaged over recent readings. When current is measured, the voltage sag under load is added back first, and time left is the remaining charge divided by the current draw.
- **Warnings and shutdown:** the one-minute `deck-watch` timer runs `deck-battery --check`. While discharging it shows a banner with a sound at 20% (low) and 10% (critical), each once until the level rises 5% above it again or a charger is connected. When the pack drops to 3.2 V per cell (6.4 V) it warns that the deck will shut down in a minute; if the next reading is still that low and no charger is connected, it runs `systemctl poweroff`. Thresholds are `warn_percent`, `critical_percent` and `shutdown_cell_volts` (0 turns the shutdown off) in the settings below.
- **Settings page:** Settings > Battery shows level, time left, voltage, current and sensor temperature (updated every 5 seconds), and lets you pick the low and critical warning levels and the shutdown voltage (or turn the shutdown off). The entry only appears when the sensor answers.
- **Report:** run `deck-battery` for voltage, current, power, time left and sensor temperature. `deck-battery --json` gives the same as JSON.
- **Settings:** optional, in `~/.config/deck/battery.json`. The defaults suit the deck's 2S pack (two 3.7 V 10000 mAh cells in series: 7.4 V nominal, 10000 mAh) and the usual 0.015 ohm shunt on INA228 breakout boards:

```json
{"bus": 1, "address": 64, "cells": 2, "capacity_mah": 10000,
 "shunt_ohms": 0.015, "invert_current": false, "internal_ohms": 0.05,
 "warn_percent": 20, "critical_percent": 10, "shutdown_cell_volts": 3.2}
```

  Set `cells` to 1 for a single cell (`capacity_mah` is the pack capacity, which series cells do not add up), `shunt_ohms` to 0 if the sensor only measures voltage, and `invert_current` to true if charging shows as discharging.
- **Preview without the sensor:** `DECK_BATTERY_FAKE="7.70,1.2" deck-battery` (volts, amps; positive amps = discharging).
- **Access:** `/dev/i2c-1` belongs to the `dialout` group. `install-files.sh` adds you to it (it takes effect after the next login), since the seat permission alone does not cover sessions started over SSH.

## Keyboard shortcuts

These work everywhere on the deck. Sway handles F2 to F8 and Back (see the `deck media keys` block in `home/.config/sway/config` and `home/bin/deck-media.sh`), and F1 is set in `.tmux.conf`.

| Key | Action |
|---|---|
| F1 | tmux prefix (same as Ctrl+B) |
| F2 | bring the terminal forward and go to the `main` tab |
| F3 | mute |
| F4 / F5 | system volume down / up |
| F6 / F7 / F8 | previous / play-pause / next (spotifyd or any MPRIS player) |
| Back (the keyboard's `XF86Back` key) | close the current app after a confirm (`y`/Enter close, `n`/Esc cancel). `main`, `apps`, `settings` and terminal tabs are never closed. The app gets SIGTERM first (Notes saves the open note, Camera stops recording), and the tab is force-closed after 3 s. See `home/bin/deck-close.sh`. It needs the udev rule `70-deck-consumer-keys.rules` (below the table) |

Both the plain F keys and the media key codes (`XF86AudioPlay` and so on) are bound, so the keys work with Fn lock on or off.

The keyboard's media-key interface (USB 1915:1025, "Consumer Control") reports a volume axis, so udev labels it a tablet pad and sway ignores its keys. `system/etc/udev/rules.d/70-deck-consumer-keys.rules` relabels it (and its parent input device, which libinput also reads) as a keyboard. After installing the rule, reconnect the keyboard once.

## How it fits together

```
power on
 └─ bootloader → kernel (custom EDID, quiet, splash) → Plymouth logo
     └─ getty@tty1 autologin (admin)
         └─ ~/.bash_profile on tty1 → exec sway
             └─ sway (rotation, ring cursor, hide after 500 ms)
                 └─ ~/bin/deck-session.sh → foot -e ~/bin/deck.sh
                     └─ tmux session "deck": main | apps | settings
                         ├─ header: Dracula plugin (tabs, Wi-Fi SSID, clock)
                         └─ footer: ~/bin/deck-footer.sh every 5 s

background services
 ├─ touch-mouse.service      touchscreen → virtual mouse, cursor size via sway IPC
 ├─ bt-set-addr.service      real Bluetooth address before bluetoothd starts
 ├─ deck-watch.timer (user)  temperature, disk, undervoltage warnings
 └─ PipeWire, NetworkManager, BlueZ, avahi (cyberdeck.local)
```

Restarting the display (**settings → Power → Restart display**, or `swaymsg exit`) restarts sway and foot; tmux and everything in it keeps running.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Display `disconnected` in `/sys/class/drm/card1-HDMI-A-*/status` | Panel not powered or adapter loose | TOUCH USB plugged in, HDMI0 port, reseat the adapter |
| Three stacked copies of the image | No EDID, so 1024x768 | Step 3 and 5 |
| `cat .../edid \| wc -c` prints `0` | Panel sends no EDID | Expected; the custom EDID replaces it |
| Picture upside down | Rotation direction | `transform 90` ↔ `270` in the sway config, then fix `ROTATE` |
| Boot logo upside down | Plymouth draws in panel orientation | Rotate `logo.png` by 180 and `sudo update-initramfs -u` |
| `speaker-test` fails with error 524 | Output in DVI mode | EDID must be digital with the HDMI block (`edid-add-audio.py`); check `HDMI_RAM_PACKET_CONFIG` in `/sys/kernel/debug/dri/1/hdmi0_regs` is not `0x00000000` |
| Only "Dummy Output" in `wpctl status` | Same as above | Same as above |
| `bluetoothctl`: no default controller | Placeholder Bluetooth address | Step 9 |
| A `btmgmt` service hangs | No terminal under systemd | Run it through `script -qec` (as `bt-set-addr.sh` does) |
| Reboot from settings does nothing | tmux tabs aren't the active login session | `system/etc/polkit-1/rules.d/50-deck-power.rules` |
| `sway --validate` fails over SSH | It tries to open the screen | `WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 sway --validate` |
| Text size change in settings works only once | Old sway socket | `settings.py` always picks the newest `/run/user/1000/sway-ipc.*.sock` |
| Starship missing in tmux tabs | `.bash_profile` doesn't load `.bashrc` | First line of `.bash_profile`: `[ -f ~/.bashrc ] && . ~/.bashrc` |
| `alert() {` syntax error | Ubuntu's `.bashrc` aliases `alert` | `deck.bashrc` runs `unalias alert` and uses `function alert` |
| ImageMagick "security policy" on `@file` | Ubuntu blocks reading draw commands from files | Pass them inline, as `make-logo.sh` does |
| Long pastes over SSH get cut off | Terminal paste buffer | Paste in smaller blocks |
| GitHub download returns 9 bytes | Repo moved or asset renamed | Follow redirects (`curl -L`) and pick the asset from the API, as `install-packages.sh` does |

Useful checks:

```bash
cat /proc/cmdline | tr ' ' '\n' | grep -E 'edid|video|splash'
cat /sys/class/drm/card1-HDMI-A-1/modes | head -1        # 1440x2560
sudo vcgencmd get_throttled
systemctl status touch-mouse bt-set-addr --no-pager
journalctl -b -t sway --no-pager | tail
```

## Keeping this repo up to date

On a working deck, copy the live files back into the repo and commit:

```bash
cd ~/projects/pi5-cyberdeck
./scripts/collect.sh
git status
git add -A && git commit -m "Update deck configuration"
```

`collect.sh` never copies account passwords, Wi-Fi passwords or SSH keys, and it scans what it copied for anything that looks like a secret.

## Credits and links

- Display: [Waveshare 5.5" 1440x2560 LCD](https://www.waveshare.com/5.5inch-1440x2560-lcd.htm?sku=23113) and its [wiki](https://www.waveshare.com/wiki/5.5inch_1440x2560_LCD)
- Raspberry Pi: [Raspberry Pi 5](https://www.raspberrypi.com/products/raspberry-pi-5/), [Imager](https://www.raspberrypi.com/software/), [Pi 5 power supply info](https://rptl.io/rpi5-power-supply-info)
- OS: [Ubuntu on Raspberry Pi](https://ubuntu.com/raspberry-pi)
- EDID: [edid-generator](https://github.com/akatrevorjay/edid-generator), [edid-decode](https://git.linuxtv.org/edid-decode.git/)
- Display stack: [sway](https://swaywm.org), [foot](https://codeberg.org/dnkl/foot), [tmux](https://github.com/tmux/tmux), [Plymouth](https://www.freedesktop.org/wiki/Software/Plymouth/)
- Theme: [Dracula](https://draculatheme.com), [dracula/tmux](https://github.com/dracula/tmux), [tpm](https://github.com/tmux-plugins/tpm), [Starship](https://starship.rs), [Nerd Fonts](https://www.nerdfonts.com), [League Gothic](https://www.theleagueofmoveabletype.com/league-gothic)
- Menus: [Textual](https://textual.textualize.io)
- Apps: [yazi](https://github.com/sxyazi/yazi), [Neovim](https://neovim.io), [lazygit](https://github.com/jesseduffield/lazygit), [btop](https://github.com/aristocratos/btop), [cmus](https://cmus.github.io), [aerc](https://aerc-mail.org), [newsboat](https://newsboat.org), [calcurse](https://calcurse.org), [glow](https://github.com/charmbracelet/glow), [bluetuith](https://github.com/bluetuith-org/bluetuith), [bandwhich](https://github.com/imsnif/bandwhich), [termshark](https://termshark.io), [nmap](https://nmap.org)
- Services: [wttr.in](https://wttr.in) (weather), [Tailscale](https://tailscale.com), [Claude Code](https://code.claude.com/docs/en/setup)

## License

MIT, see [LICENSE](LICENSE). Third-party tools keep their own licenses.
