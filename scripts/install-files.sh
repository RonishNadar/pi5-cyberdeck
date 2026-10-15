#!/usr/bin/env bash
# install-files.sh: put the repo's configuration and scripts in place.
#
# - home/   is copied into your home folder (existing files are backed up first)
# - system/ is copied into / with sudo
# - boot/   is NOT applied automatically; see the README (Display section)
#
# The scripts assume the user is named "admin" (several files contain /home/admin).
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP="$HOME/.deck-backup-$(date +%Y%m%d-%H%M%S)"
say() { printf '\n\033[1;35m== %s ==\033[0m\n' "$*"; }

if [ "$(id -un)" != "admin" ]; then
  echo "Warning: you are '$(id -un)', not 'admin'. Paths like /home/admin inside the scripts"
  echo "must be changed. Quick fix: grep -rl /home/admin \"$REPO\" | xargs sed -i \"s|/home/admin|$HOME|g\""
  read -r -p "Continue anyway? [y/N] " a; [ "$a" = y ] || exit 1
fi

say "Home files (backup of anything replaced: $BACKUP)"
cd "$REPO/home"
find . -type f | while read -r f; do
  dest="$HOME/${f#./}"
  if [ -e "$dest" ] && ! cmp -s "$f" "$dest"; then
    mkdir -p "$BACKUP/$(dirname "${f#./}")"
    cp -a "$dest" "$BACKUP/${f#./}"
  fi
  mkdir -p "$(dirname "$dest")"
  cp -a "$f" "$dest"
  echo "  ${f#./}"
done
cd "$REPO"
chmod +x ~/bin/* 2>/dev/null

say "System files"
if [ -d "$REPO/system" ]; then
  sudo cp -a "$REPO/system/." /
  sudo chown -R root:root /usr/local/bin/touch-mouse.py /usr/local/bin/bt-set-addr.sh \
    /etc/systemd/system/touch-mouse.service /etc/systemd/system/bt-set-addr.service \
    /etc/systemd/system/getty@tty1.service.d /etc/polkit-1/rules.d /etc/udev/rules.d/70-deck-consumer-keys.rules /usr/share/plymouth/themes/deck \
    /lib/firmware/edid 2>/dev/null
  sudo chmod +x /usr/local/bin/touch-mouse.py /usr/local/bin/bt-set-addr.sh 2>/dev/null
fi

say "Load deck.bashrc from ~/.bashrc"
if ! grep -q '__deck_cmd_done\|deck/deck.bashrc' ~/.bashrc; then
  sed -i "/^alias alert='notify-send/d" ~/.bashrc
  printf '\n# Cyberdeck additions\n[ -f ~/.config/deck/deck.bashrc ] && . ~/.config/deck/deck.bashrc\n' >> ~/.bashrc
  echo "  added"
else
  echo "  already set up"
fi
# ~/.bash_profile starts sway on tty1; make sure it also loads ~/.bashrc
grep -q 'bashrc' ~/.bash_profile 2>/dev/null || sed -i '1i [ -f ~/.bashrc ] && . ~/.bashrc' ~/.bash_profile

say "Groups, services and timers"
sudo usermod -aG video,render,input,bluetooth,wireshark,dialout "$(id -un)"
sudo loginctl enable-linger "$(id -un)"
sudo systemctl daemon-reload
sudo systemctl enable --now touch-mouse.service bt-set-addr.service 2>/dev/null
sudo systemctl enable --now bluetooth NetworkManager avahi-daemon 2>/dev/null
sudo systemctl restart polkit
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=input --action=change
systemctl --user daemon-reload
systemctl --user enable --now pipewire pipewire-pulse wireplumber deck-watch.timer 2>/dev/null
command -v spotifyd >/dev/null && systemctl --user enable --now spotifyd 2>/dev/null
mkdir -p ~/.config/deck/flags ~/projects ~/Downloads ~/Music ~/Pictures ~/Books ~/Wiki ~/Notes

say "Boot splash theme"
if [ -f /usr/share/plymouth/themes/deck/deck.plymouth ]; then
  sudo update-alternatives --install /usr/share/plymouth/themes/default.plymouth default.plymouth \
    /usr/share/plymouth/themes/deck/deck.plymouth 200 >/dev/null
  sudo update-alternatives --set default.plymouth /usr/share/plymouth/themes/deck/deck.plymouth
fi

say "Plugins for tmux, Neovim and yazi"
[ -x ~/.tmux/plugins/tpm/bin/install_plugins ] && {
  tmux new -d -s setup 2>/dev/null; tmux source-file ~/.tmux.conf
  ~/.tmux/plugins/tpm/bin/install_plugins; tmux kill-session -t setup 2>/dev/null; }
command -v nvim >/dev/null && nvim --headless "+Lazy! sync" +qa 2>/dev/null
command -v ya >/dev/null && { ya pkg add yazi-rs/flavors:dracula 2>/dev/null || ya pack -a yazi-rs/flavors:dracula 2>/dev/null; }

say "Rebuild the initramfs (EDID and boot splash)"
sudo update-initramfs -u

cat <<'EOF'

Done. Still to do by hand (see the README):
  1. Display: add the EDID and splash options to /boot/firmware/current/cmdline.txt
  2. Networking: hand Wi-Fi over to NetworkManager
  3. Reboot, then tune touch rotation (ROTATE) if taps land in the wrong place
EOF
