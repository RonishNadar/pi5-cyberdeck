#!/usr/bin/env bash
# collect.sh: copy the deck's live configuration from this Pi into the repo.
#
# Run it on a working deck whenever you change something, then commit:
#   ./scripts/collect.sh && git add -A && git commit -m "Update configs"
#
# Secrets are never collected: aerc account files, Wi-Fi passwords (netplan,
# NetworkManager), SSH keys, Spotify logins (~/.config/deck/spotify*.json,
# ~/.cache/spotifyd/oauth) and the YouTube login (~/.config/deck/youtube-{api,token}.json)
# are skipped on purpose.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
H="$HOME"

say()  { printf '\033[1;35m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m+\033[0m %s\n' "$*"; }
miss() { printf '  \033[33m-\033[0m missing (skipped): %s\n' "$*"; }

# copy SRC DEST_IN_REPO   (files or directories, as the current user)
copy() {
  local src="$1" dst="$REPO/$2"
  if [ -e "$src" ]; then
    mkdir -p "$(dirname "$dst")"
    rm -rf "$dst"
    cp -a "$src" "$dst"
    ok "$2"
  else
    miss "$src"
  fi
}

# scopy SRC DEST_IN_REPO   (root-owned files, copied with sudo, then owned by you)
scopy() {
  local src="$1" dst="$REPO/$2"
  if sudo test -e "$src"; then
    mkdir -p "$(dirname "$dst")"
    sudo rm -rf "$dst"
    sudo cp -a "$src" "$dst"
    sudo chown -R "$(id -u):$(id -g)" "$dst"
    ok "$2"
  else
    miss "$src"
  fi
}

say "Home configuration"
copy "$H/.config/foot/foot.ini"                     home/.config/foot/foot.ini
copy "$H/.config/sway/config"                       home/.config/sway/config
copy "$H/.tmux.conf"                                home/.tmux.conf
copy "$H/.config/starship.toml"                     home/.config/starship.toml
copy "$H/.config/yazi/yazi.toml"                    home/.config/yazi/yazi.toml
copy "$H/.config/yazi/theme.toml"                   home/.config/yazi/theme.toml
copy "$H/.config/nvim/init.lua"                     home/.config/nvim/init.lua
copy "$H/.config/lazygit/config.yml"                home/.config/lazygit/config.yml
copy "$H/.config/ncdu/config"                       home/.config/ncdu/config
copy "$H/.config/glow/glow.yml"                     home/.config/glow/glow.yml
copy "$H/.config/calcurse/conf"                     home/.config/calcurse/conf
copy "$H/.config/newsboat/config"                   home/.config/newsboat/config
copy "$H/.config/newsboat/urls"                     home/.config/newsboat/urls
copy "$H/.config/cmus/rc"                           home/.config/cmus/rc
copy "$H/.config/cmus/dracula.theme"                home/.config/cmus/dracula.theme
copy "$H/.config/aerc/aerc.conf"                    home/.config/aerc/aerc.conf
copy "$H/.config/termshark/termshark.toml"          home/.config/termshark/termshark.toml
copy "/home/admin/.config/ncspot/config.toml"                 home/.config/ncspot/config.toml
copy "$H/.w3m/config"                               home/.w3m/config
copy "$H/.w3m/keymap"                               home/.w3m/keymap
copy "$H/.config/systemd/user/deck-watch.service"   home/.config/systemd/user/deck-watch.service
copy "$H/.config/systemd/user/deck-watch.timer"     home/.config/systemd/user/deck-watch.timer
copy "$H/.config/spotifyd/spotifyd.conf"            home/.config/spotifyd/spotifyd.conf
copy "$H/.config/systemd/user/spotifyd.service"     home/.config/systemd/user/spotifyd.service
copy "$H/.icons/DeckCircle"                         home/.icons/DeckCircle
copy "$H/.bash_profile"                             home/.bash_profile
copy "$H/.hushlogin"                               home/.hushlogin
[ -f "$H/.config/containers/systemd/filebrowser.container" ] && \
  copy "$H/.config/containers/systemd/filebrowser.container" home/.config/containers/systemd/filebrowser.container

say "Deck scripts (~/bin)"
mkdir -p "$REPO/home/bin"
for f in "$H"/bin/*; do
  [ -f "$f" ] || continue
  case "$(basename "$f")" in
    *.pyc|osk.py) continue ;;
  esac
  copy "$f" "home/bin/$(basename "$f")"
done
# deck.sh lives in ~/bin on newer setups and in ~ on older ones
[ -f "$H/deck.sh" ] && [ ! -f "$H/bin/deck.sh" ] && copy "$H/deck.sh" home/bin/deck.sh

say "Assets"
copy "$H/Pictures/ronad_logo.png"                   assets/logo.png

say "System files (needs sudo)"
scopy /usr/local/bin/touch-mouse.py                          system/usr/local/bin/touch-mouse.py
scopy /usr/local/bin/bt-set-addr.sh                          system/usr/local/bin/bt-set-addr.sh
scopy /usr/local/bin/deck-backlight-setup                   system/usr/local/bin/deck-backlight-setup
scopy /etc/systemd/system/touch-mouse.service                system/etc/systemd/system/touch-mouse.service
scopy /etc/systemd/system/bt-set-addr.service                system/etc/systemd/system/bt-set-addr.service
scopy /etc/systemd/system/deck-backlight.service             system/etc/systemd/system/deck-backlight.service
scopy /etc/bluetooth/main.conf                             system/etc/bluetooth/main.conf
scopy /etc/systemd/system/getty@tty1.service.d/autologin.conf system/etc/systemd/system/getty@tty1.service.d/autologin.conf
scopy /etc/systemd/system/plymouth-quit.service.d/retain-splash.conf system/etc/systemd/system/plymouth-quit.service.d/retain-splash.conf
scopy /etc/polkit-1/rules.d/50-deck-power.rules              system/etc/polkit-1/rules.d/50-deck-power.rules
scopy /etc/polkit-1/rules.d/51-deck-time.rules               system/etc/polkit-1/rules.d/51-deck-time.rules
scopy /etc/polkit-1/rules.d/52-deck-network.rules             system/etc/polkit-1/rules.d/52-deck-network.rules
scopy /etc/udev/rules.d/70-deck-consumer-keys.rules        system/etc/udev/rules.d/70-deck-consumer-keys.rules
scopy /etc/dracut.conf.d/edid.conf                           system/etc/dracut.conf.d/edid.conf
scopy /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg   system/etc/cloud/cloud.cfg.d/99-disable-network-config.cfg
scopy /usr/share/plymouth/themes/deck                        system/usr/share/plymouth/themes/deck
scopy /lib/firmware/edid                                     system/lib/firmware/edid

say "Boot configuration (reference copies)"
scopy /boot/firmware/current/cmdline.txt                     boot/cmdline.txt
scopy /boot/firmware/config.txt                              boot/config.txt

say "Secret scan"
hits=$(grep -rIniE '(password|passwd|psk|api[_-]?key|secret|token)\s*[=:]' \
        "$REPO/home" "$REPO/system" "$REPO/boot" 2>/dev/null \
      | grep -viE 'password=True|placeholder|password for|app password|\$pass|pw\b|"password"' || true)
if [ -n "$hits" ]; then
  printf '\033[31mPossible secrets found, check before committing:\033[0m\n%s\n' "$hits"
else
  ok "nothing that looks like a secret"
fi

say "Done. Review with: git -C \"$REPO\" status"
