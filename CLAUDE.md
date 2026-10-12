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
- Power: the current supply is weak (the camera once caused a brown-out). Planned: 2S LiPo
  (2 x 3.7 V 10000 mAh), a 2S boost charger plus 2S BMS, a 5 A fuse, a switch, a TPS565201 buck set to
  5.1 V, a USB-C pigtail to the Pi, and an INA219 on I2C (0x40, pins 1/3/5/6) wired voltage-only
  (VIN+ and VIN- joined). Nothing can mount under the Pi, because the display is there.

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

## Session stack
- tty1 autologin, then `~/.bash_profile` (first line sources `~/.bashrc`), then `exec sway`.
- sway (`~/.config/sway/config`): `output HDMI-A-1 mode 1440x2560@50Hz transform 270`, no borders or
  gaps, `xwayland disable`, `hide_cursor 500`, cursor theme `DeckCircle`, `exec ~/bin/deck-session.sh`.
  It also holds the **media keys block** (`# >>> deck media keys` ... `# <<< deck media keys`):
  F2 to F8 and the XF86 media keys call `~/bin/deck-media.sh`.
- `deck-session.sh` runs `foot -e deck.sh`, which attaches or creates tmux session `deck` with
  windows `main`, `apps` (`~/bin/launcher.py`) and `settings` (`~/bin/settings.py`).
- tmux (`~/.tmux.conf`): Dracula theme via TPM, `@dracula-plugins "custom:deck-status.sh time"`
  (phone-style Wi-Fi bars and Bluetooth icons). `~/.tmux/plugins/tmux/scripts/deck-status.sh` is a
  symlink to `~/bin/deck-status.sh`. `prefix2 F1`. A bottom footer is drawn on the pane border by
  `~/bin/deck-footer.sh`. The last line is `source-file -q ~/.config/deck/accent.tmux`.
- foot uses JetBrainsMono Nerd Font (use the non-Mono variant so icons render full size). foot only
  reads its config at start, so restarting the display (`swaymsg exit`) is needed after font
  changes. tmux keeps running and the session reattaches.
- Touch: `/usr/local/bin/touch-mouse.py` (systemd `touch-mouse.service`, `ROTATE=1`) turns the
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
- `lanscan.py` (nmap TUI), `notify` (tmux banners + sounds, honors `~/.config/deck/flags/*`),
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
- Battery build: wire it, then add a battery footer block, a Battery entry in Settings, low-battery
  warnings, and a clean shutdown at about 6.4 V (INA219 bus voltage register 0x02).
- After the 5 A power source: `PSU_MAX_CURRENT=5000` in EEPROM (`rpi-eeprom-config --apply`), remove
  `arm_freq=1800`, restore the camera preview to 1536x864 at 30 fps.
- Power button: momentary switch on the Pi 5 J2 (PWR_BTN) header, logind `HandlePowerKey=poweroff`,
  EEPROM `POWER_OFF_ON_HALT=1`, plus a hidden latching master switch in the battery line.
- Display brightness: no software control. It needs Waveshare's PWM hardware mod (wiki FAQ), then a
  Pi PWM pin.
- Deferred by the owner: uv/new-app template, Podman, local LLM, Arduino/PlatformIO.
