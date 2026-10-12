#!/usr/bin/env bash
# install-packages.sh: install every program the deck uses.
# Ubuntu packages come from apt; tools that Ubuntu doesn't ship are downloaded
# from their official GitHub releases (ARM64 builds).
set -uo pipefail

say() { printf '\n\033[1;35m== %s ==\033[0m\n' "$*"; }

# Pick the download URL of the newest release asset matching a pattern.
# gh_asset OWNER/REPO REGEX   (GitHub redirects for moved repos are followed)
gh_asset() {
  curl -sL "https://api.github.com/repos/$1/releases/latest" \
    | grep -o '"browser_download_url": *"[^"]*"' | cut -d'"' -f4 \
    | grep -Ei "$2" | grep -vi android | head -1
}

# install_from_tarball NAME OWNER/REPO REGEX   (finds the binary NAME inside)
install_from_tarball() {
  local name="$1" url tmp
  command -v "$name" >/dev/null && { echo "$name already installed"; return; }
  url=$(gh_asset "$2" "$3")
  [ -z "$url" ] && { echo "Could not find a release for $name"; return; }
  echo "Downloading $url"
  tmp=$(mktemp -d)
  curl -fsSLo "$tmp/pkg" "$url" || { echo "Download failed: $name"; rm -rf "$tmp"; return; }
  mkdir -p "$tmp/x"
  case "$url" in
    *.zip) unzip -q "$tmp/pkg" -d "$tmp/x" ;;
    *)     tar xf "$tmp/pkg" -C "$tmp/x" ;;
  esac
  find "$tmp/x" -type f -name "$name" -exec sudo install -m 755 {} /usr/local/bin/"$name" \;
  # yazi also ships its "ya" helper
  find "$tmp/x" -type f -name ya -exec sudo install -m 755 {} /usr/local/bin/ya \;
  rm -rf "$tmp"
}

say "Ubuntu packages"
sudo apt update
echo "wireshark-common wireshark-common/install-setuid boolean true" | sudo debconf-set-selections
sudo DEBIAN_FRONTEND=noninteractive apt install -y \
  sway swaybg foot tmux wlr-randr x11-apps imagemagick fonts-jetbrains-mono \
  python3-venv python3-evdev git curl unzip build-essential \
  pipewire pipewire-pulse pipewire-alsa wireplumber pulseaudio-utils alsa-utils alsa-ucm-conf \
  sound-theme-freedesktop bluez libspa-0.2-bluetooth rfkill iw \
  network-manager avahi-daemon \
  btop neovim ripgrep fd-find fzf zoxide jq file ffmpegthumbnailer poppler-utils p7zip-full \
  ncdu calcurse newsboat w3m cmus aerc \
  nmap tcpdump tshark termshark \
  plymouth plymouth-themes edid-decode \
  mpv mpv-mpris playerctl pkg-config libasound2-dev libssl-dev libpulse-dev libdbus-1-dev

say "Packages that may or may not exist in your Ubuntu release"
for p in glow lazygit fonts-league-gothic; do
  sudo apt install -y "$p" 2>/dev/null && echo "$p from apt" || echo "$p not in apt (handled below)"
done

say "Tools from GitHub releases"
install_from_tarball yazi      sxyazi/yazi              'aarch64-unknown-linux-gnu\.zip$'
install_from_tarball bluetuith bluetuith-org/bluetuith  'linux.*arm64.*\.tar\.gz$'
install_from_tarball bandwhich imsnif/bandwhich         'aarch64-unknown-linux-musl\.tar\.gz$'
# deno: yt-dlp needs a JavaScript runtime for YouTube
install_from_tarball deno      denoland/deno            'deno-aarch64-unknown-linux-gnu\.zip$'
command -v glow    >/dev/null || install_from_tarball glow    charmbracelet/glow       'Linux_arm64\.tar\.gz$'
command -v lazygit >/dev/null || install_from_tarball lazygit jesseduffield/lazygit 'Linux_arm64\.tar\.gz$'
command -v bandwhich >/dev/null && sudo setcap cap_sys_ptrace,cap_dac_read_search,cap_net_raw,cap_net_admin+ep "$(command -v bandwhich)"
command -v nmap >/dev/null && sudo setcap cap_net_raw,cap_net_admin,cap_net_bind_service+eip "$(command -v nmap)"

say "Fonts"
mkdir -p ~/.local/share/fonts
if ! fc-list | grep -qi 'JetBrainsMono Nerd'; then
  curl -fsSLo /tmp/jbm.tar.xz https://github.com/ryanoasis/nerd-fonts/releases/latest/download/JetBrainsMono.tar.xz \
    && tar xf /tmp/jbm.tar.xz -C ~/.local/share/fonts && rm -f /tmp/jbm.tar.xz
fi
if ! fc-list | grep -qi 'league gothic'; then
  curl -fsSLo ~/.local/share/fonts/LeagueGothic.ttf \
    "https://github.com/google/fonts/raw/main/ofl/leaguegothic/LeagueGothic%5Bwdth%5D.ttf"
fi
fc-cache -f

say "Starship prompt"
command -v starship >/dev/null || curl -sS https://starship.rs/install.sh | sh -s -- -y

say "Python environment for the deck apps (Textual)"
[ -x ~/.venvs/deck/bin/python ] || python3 -m venv ~/.venvs/deck
~/.venvs/deck/bin/pip install --quiet --upgrade pip "textual[syntax]" requests "yt-dlp[default]" libzim zxing-cpp numpy

say "spotifyd (Spotify Connect speaker; built from source, the release binary needs OpenSSL 1.1)"
if ! command -v spotifyd >/dev/null; then
  command -v cargo >/dev/null || curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
  . "$HOME/.cargo/env"
  rm -rf ~/src/spotifyd && git clone -q --depth 1 --branch v0.4.2 https://github.com/Spotifyd/spotifyd ~/src/spotifyd
  (cd ~/src/spotifyd && cargo build --release --locked -j 2 --features pulseaudio_backend) \
    && sudo install -m 755 ~/src/spotifyd/target/release/spotifyd /usr/local/bin/spotifyd
  rm -rf ~/src/spotifyd/target
fi

say "Plugin manager for tmux"
[ -d ~/.tmux/plugins/tpm ] || git clone -q https://github.com/tmux-plugins/tpm ~/.tmux/plugins/tpm

say "Done"
echo "Next: ./scripts/install-files.sh"
