# CLAUDE.md: pi5-cyberdeck

This repo documents and reproduces a terminal-first Raspberry Pi 5 cyberdeck. The repo itself is
**not** what runs on the deck. The live files are in `~` and `/`, and `scripts/collect.sh` copies
them into the repo. Claude Code runs **on the deck itself** (host `cyberdeck`, user `admin`), so you
are editing the live system.

## Hardware
- Raspberry Pi 5 (8 GB). Ubuntu Server 26.04.1 LTS (arm64), hostname `cyberdeck`, user `admin`.
- Waveshare 5.5" 1440x2560 HDMI LCD with capacitive touch (case A, SKU 23113). It is a portrait panel
  used in landscape. It sends **no EDID**, so we supply our own (see Display).
- Camera: IMX708 Wide NoIR (Camera Module 3 type) on **CAM1**. It reports module ID 0x0000, so the
  tuning file is forced to `imx708_wide_noir.json`.
- USB keyboard with media F keys. Audio comes out of the panel's 3.5 mm jack over HDMI.
- Power: runs on a **2S** Li-ion pack (two 3.7 V 10000 mAh cells in series: 7.4 V nominal, 8.4 V full,
  10 Ah), in use since 2026-10-17 (the 1S plan from 2026-10-12 is dropped). An **INA228** on I2C (0x40,
  pins 1/3/5/6) sits in the battery line; `~/bin/deck-battery` reads it (defaults `cells: 2`,
  `capacity_mah: 10000`, 0.015 ohm shunt; overrides in `~/.config/deck/battery.json`). Typical draw is
  about 0.8 A at 8 V (6 W). Keep the 5 A fuse and switch. Nothing can mount under the Pi, because the
  display is there.

## Boot and display (Ubuntu-specific gotchas)
- Ubuntu boots the Pi with `os_prefix=current/`. The kernel cmdline that is actually used is
  `/boot/firmware/current/cmdline.txt` (flash-kernel copies it to `new/`). Edit every
  `/boot/firmware/*/cmdline.txt` copy, not only the top-level one.
- The cmdline includes `drm.edid_firmware=HDMI-A-1:edid/1440x2560_50_audio.bin video=HDMI-A-1:1440x2560@50,rotate=90`
  (no `e` flag) plus the quiet/splash options.
- EDID: `display/1440x2560_50.S` is built with edid-generator. `scripts/edid-add-audio.py` then sets
  byte 20 to 0x80 (digital) and appends a CEA-861 block (LPCM 2ch, HDMI VSDB 00-0C-03). Without that
  the Pi uses DVI mode and there is no HDMI audio (error 524). The files live in `/lib/firmware/edid/`.
- The initramfs is **dracut** (not initramfs-tools): `/etc/dracut.conf.d/edid.conf` includes the
  EDID. Rebuild with `sudo update-initramfs -u` (Ubuntu wraps dracut).
- `/boot/firmware/config.txt`: `disable_splash=1`, `camera_auto_detect=0`, and `dtoverlay=imx708`
  under `[all]`. There may be a temporary `arm_freq=1800` (remove it after the power upgrade).
- Boot splash: a Plymouth script theme in `/usr/share/plymouth/themes/deck`, selected with
  `update-alternatives` (`plymouth-set-default-theme` does not exist here).
- Second panel: 7" HDMI Display-C (1024x600, own EDID with audio, model `MPI7002`, USB touch
  "QDTECH MPI7002"). It goes on **HDMI-A-2**; the forced EDID only covers HDMI-A-1 (Waveshare).
  Per-panel settings are picked by model: `~/bin/deck-display.sh` (foot size/line-height/pad, used by
  `deck-session.sh`), sway `output "MPI MPI7002 MPI7002"`, and `ROTATE_BY_NAME` in touch-mouse.py.

## Session stack
- tty1 autologin, then `~/.bash_profile` (first line sources `~/.bashrc`), then `exec sway`.
- sway (`~/.config/sway/config`): `output HDMI-A-1 mode 1440x2560@50Hz transform 270`, no borders or
  gaps, `xwayland disable`, `hide_cursor 500`, cursor theme `DeckCircle`, `exec ~/bin/deck-session.sh`.
  It also holds the **media keys block** (`# >>> deck media keys` ... `# <<< deck media keys`):
  F2 to F8 and the XF86 media keys call `~/bin/deck-media.sh`.
  `XF86Back` calls `~/bin/deck-close.sh`: tmux popup confirm on the foot client, then SIGTERM to the
  tab's foreground job and `kill-window` after 3 s. Terminal tabs (no start command or a bare shell)
  and main/apps/settings are skipped. Camera gets SIGUSR1 and confirms in its own window.
  Back comes from the keyboard's "Consumer Control" device, which udev tags as a tablet pad (sway then
  ignores it). `/etc/udev/rules.d/70-deck-consumer-keys.rules` retags both the event node and its parent
  inputN (libinput ORs both). Changes only apply after the keyboard reconnects (USB unbind/bind 2-2).
- `deck-session.sh` runs `foot -e deck.sh`, which attaches or creates tmux session `deck` with
  windows `main`, `apps` (`~/bin/launcher.py`) and `settings` (`~/bin/settings.py`).
- tmux (`~/.tmux.conf`): Dracula theme via TPM, `@dracula-plugins "custom:deck-status.sh time"`
  (phone-style Wi-Fi bars and Bluetooth icons). `~/.tmux/plugins/tmux/scripts/deck-status.sh` is a
  symlink to `~/bin/deck-status.sh`. `prefix2 F1`. A bottom footer is drawn on the pane border by
  `~/bin/deck-footer.sh`. The last line is `source-file -q ~/.config/deck/accent.tmux`.
- foot uses JetBrainsMono Nerd Font (use the non-Mono variant so icons render full size). foot only
  reads its config at start (font size, line-height and pad are overridden per panel by
  `deck-session.sh`, so change those in `deck-display.sh`, not foot.ini), so restarting the display (`swaymsg exit`) is needed after font
  changes. tmux keeps running and the session reattaches.
- Touch: `/usr/local/bin/touch-mouse.py` (systemd `touch-mouse.service`, `ROTATE=3` default,
  per-controller overrides in `ROTATE_BY_NAME`) turns the
  touchscreen into an absolute uinput mouse, with tap, drag and long-press. The cursor grows while
  touching (via sway IPC).

## Deck apps (all in `~/bin`, Python apps use the venv `~/.venvs/deck`, Textual 8.x)
- `launcher.py` (Apps tab), `settings.py` + `deck_net.py` (Wi-Fi via nmcli, Bluetooth via
  bluetoothctl) + `deck_prefs.py` (date/time, notifications, footer blocks, accent color).
  Navigation: arrows, Right/Enter = open, Left/Esc = back.
- `deck_ui.py`: `install()` monkeypatches OptionList to put a blank, unselectable row between items.
  It is imported by launcher.py and settings.py. Textual 8 does **not** render vertical padding on
  `.option-list--option` (items vanish), so do not try that.
- `spot.py`: keyboard-first Spotify client (Web API + spotifyd). `--login` does PKCE with redirect
  `http://127.0.0.1:8888/callback` (paste the redirected address back). Config is in
  `~/.config/deck/spotify.json` (client_id, device) and the token in `spotify-token.json`
  (**secrets, never commit**).
  - Spotify Feb 2026 dev-mode rules: the owner needs Premium, search returns at most 10 results per
    type, `/playlists/{id}/items` only works for playlists you own or collaborate on (others are
    played by context only), use `/me/library` with URIs (the old `/me/tracks` PUT/DELETE are gone),
    and there is no artist top-tracks endpoint.
  - Player commands can return 200 with a non-JSON body, so `req()` tolerates that.
  - When the active device is the deck, play/pause/next/prev/seek/volume go to spotifyd over MPRIS
    (`playerctl -p spotifyd`). Otherwise they go through the Web API. The UI updates optimistically and
    holds the change so polling cannot jump it back.
  - Liked Songs plays as a URI list (spotifyd cannot resolve the `user:...:collection` context).
- `camera.py`: GTK4 app `tech.ronad.Camera`, MJPEG preview from `rpicam-vid` (sensor mode
  `2304:1296:10:P`), photos with `rpicam-still`, video with ffmpeg copy to .mkv, saved in
  `~/Pictures/Camera`.
- `tube.py`: YouTube via yt-dlp (pip, in the deck venv; needs `deno` in `~/.local/bin` or
  `/usr/local/bin`) and mpv (+ mpv-mpris). Search is yt-dlp `ytsearch`, subscriptions are channel RSS
  feeds (no Google login). Background audio is a detached mpv with IPC socket
  `/run/user/1000/deck-tube.sock`. State: `~/.config/deck/youtube.json`,
  `~/.local/share/deck/youtube-history.json`. `deck-media.sh` sends media keys to the playing player.
- `pods.py`: podcasts. Apple search/lookup + top-charts JSON (no key), RSS feeds cached in
  `~/.cache/deck/podcasts`, positions in `~/.local/share/deck/podcasts-progress.json`.
- `radio.py`: radio-browser.info (`all`/`de1` servers; tag lists use `stations/search?tag=..&tagExact=true`).
  The played list becomes the mpv playlist so n/p and media keys change station.
- `notes.py` + `note`: Markdown notes in `~/Notes` (TextArea with `textual[syntax]` highlighting, autosave,
  delete moves to `~/Notes/.trash`). `~/Notes` is user data and is not collected.
- `weather.py`: Open-Meteo forecast + geocoding + air quality, first place from ipwho.is.
  Config `~/.config/deck/weather.json`, cache `~/.cache/deck/weather.json`.
- `wiki.py`: offline Wikipedia via `libzim` (pip). Reuses `reader.HtmlToBlocks`/`reader.layout`; skips
  elements by whole class name (substring matching once hid whole pages). ZIMs in `~/Wiki` (data, not
  collected); downloads are detached `curl -C -` jobs tracked in `/run/user/1000/deck-wiki-downloads.json`.
- `qr.py`: rpicam-vid `--codec yuv420` 640x360@8 to stdout; the Y plane goes to zxingcpp (pip) and a
  half-block preview. Wi-Fi QR joins via nmcli after a confirm. Tests must stub nmcli/wl-copy/tmux.
- `deck_mpv.py`: shared background mpv helpers (sockets `/run/user/1000/deck-{tube,pods,radio}.sock`,
  `pause_others()` also pauses spotifyd). Unix socket paths max 108 bytes, so tests must use a short
  path like `/run/user/1000/deck-X-test.sock`, never the scratchpad.
- Textual key names: `[` is `left_square_bracket`, `]` `right_square_bracket`, `.` `full_stop`.
- `deck-battery` (INA228 over raw `/dev/i2c-1`, stdlib only; prints nothing without the sensor),
  `lanscan.py` (nmap TUI), `notify` (tmux banners + sounds, honors `~/.config/deck/flags/*`),
  `deck-watch.sh` (user timer: temp, disk, undervoltage), `deck-footer.sh`, `deck-status.sh`,
  `deck-media.sh`, `make-logo.sh`.

## Services and system pieces
- spotifyd 0.4.2 is **built from source** (the release binary needs OpenSSL 1.1, which Ubuntu 26.04
  does not have): `cargo build --release --locked -j 2 --features pulseaudio_backend`. It runs as the
  user service `~/.config/systemd/user/spotifyd.service`. Config: `~/.config/spotifyd/spotifyd.conf`
  (device `cyberdeck`, pulseaudio backend, `use_mpris = true`, `no_audio_cache = true`; a corrupted
  cache once made it skip through songs silently). Login: `~/.cache/spotifyd/oauth` (**secret**).
- Audio: PipeWire + WirePlumber (user services, linger enabled). Volume: `wpctl ... @DEFAULT_AUDIO_SINK@`.
- Networking: NetworkManager (netplan/networkd handed over; cloud-init network config disabled),
  avahi gives `cyberdeck.local`.
- Bluetooth: `bt-set-addr.service` sets a fixed address (the default was 43:45:c0:00:1f:ac). btmgmt
  only works under systemd when wrapped in `script -qec`.
- polkit rules in `/etc/polkit-1/rules.d/` for user admin: `50-deck-power` (reboot/poweroff),
  `51-deck-time` (timedate1), `52-deck-network` (all NetworkManager actions). They are needed because
  tmux may have been started over SSH, which makes the apps an inactive session.

## Working on the deck
- Over SSH there is no `SWAYSOCK`. Use
  `export SWAYSOCK=$(ls -t /run/user/$(id -u)/sway-ipc.*.sock | head -1)` before `swaymsg`.
- `sway --validate` fails over SSH. Use `WLR_BACKENDS=headless sway --validate`, or just
  `swaymsg reload` and read the error.
- Restart one deck tab: `tmux respawn-window -k -t deck:apps "sh -c '~/bin/launcher.py; exec bash'"`
  (same for `deck:settings`).
- Restart the whole display (sway + foot, tmux survives): `swaymsg exit`.
- Power and thermal check: `sudo vcgencmd get_throttled` (the user cannot open /dev/vcio without sudo).
- Logs: `journalctl --user -u spotifyd`, `journalctl -u touch-mouse`.
- Commands that touch the network (`nmcli radio wifi off`, NetworkManager restarts) can cut the SSH
  session. Warn before running them.

## Repo workflow
- After changing live files: `./scripts/collect.sh`, then check its **Secret scan** line and
  `git status`, then commit and push.
- `collect.sh` copies `~/bin/*` automatically. New config files outside `~/bin` need a `copy` or
  `scopy` line added to `collect.sh`. New packages go in `scripts/install-packages.sh`. New services
  or symlinks go in `scripts/install-files.sh`. Document user-visible changes in `README.md`.
- Never collect or commit: aerc accounts and passwords, Wi-Fi passwords/`*.nmconnection`, SSH keys,
  `~/.config/deck/spotify*.json`, `~/.cache/spotifyd/oauth`. `.gitignore` covers these. Keep it that
  way.
- Scripts assume user `admin` and `/home/admin` paths.

## How the owner likes to work
- Prefer doing the change directly (or giving complete copy-paste commands: heredocs, sed, python
  patch scripts) over telling them to edit files by hand.
- Keep the Dracula look (#282a36 background, #bd93f9 purple accent, read from
  `~/.config/deck/prefs.json` "accent") and Nerd Font icons consistent across apps.
- Keyboard first: touch works but the screen is small. Every app needs full keyboard control.
- Explanations: plain language, no emojis, no em dashes.
- Ask before rebooting, restarting the display, or anything that could drop the network.

## Open items
- Battery: wired and read by the INA228. The footer block (`bat`, via `deck-battery --short`) is done. Still to do: a
  Battery entry in Settings, low-battery warnings, and a clean shutdown at a low cell voltage
  (about 3.2 V per cell, 6.4 V for the pack; INA228 VBUS register 0x05).
- Now that the battery powers the deck (if its regulator gives 5 A): `PSU_MAX_CURRENT=5000` in EEPROM (`rpi-eeprom-config --apply`), remove
  `arm_freq=1800`, restore the camera preview to 1536x864 at 30 fps.
- Power button: momentary switch on the Pi 5 J2 (PWR_BTN) header, logind `HandlePowerKey=poweroff`,
  EEPROM `POWER_OFF_ON_HALT=1`, plus a hidden latching master switch in the battery line.
- Display brightness: no software control. It needs Waveshare's PWM hardware mod (wiki FAQ), then a
  Pi PWM pin.
- Deferred by the owner: uv/new-app template, Podman, local LLM, Arduino/PlatformIO.
