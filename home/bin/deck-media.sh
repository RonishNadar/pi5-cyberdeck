#!/usr/bin/env bash
# deck-media.sh: system-wide media keys, called from sway
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
SINK=@DEFAULT_AUDIO_SINK@

msg() { tmux display-message -d 1200 "$*" 2>/dev/null; }
vol() { wpctl get-volume "$SINK" | awk '{v=int($2*100+0.5); m=($3=="[MUTED]")?"  muted":""; print v"%"m}'; }

# The player that is playing, else one that is paused, else spotifyd (YouTube audio runs in mpv).
player() {
  for p in $(playerctl -l 2>/dev/null); do
    [ "$(playerctl -p "$p" status 2>/dev/null)" = Playing ] && { echo "$p"; return; }
  done
  for p in $(playerctl -l 2>/dev/null); do
    [ "$(playerctl -p "$p" status 2>/dev/null)" = Paused ] && { echo "$p"; return; }
  done
  echo "spotifyd,%any"
}

case "$1" in
  home)
    swaymsg -q '[app_id="foot"] focus' 2>/dev/null
    tmux select-window -t deck:main 2>/dev/null || tmux select-window -t :1 2>/dev/null ;;
  mute)
    wpctl set-mute "$SINK" toggle; msg "Volume $(vol)" ;;
  down)
    wpctl set-mute "$SINK" 0; wpctl set-volume "$SINK" 5%-; msg "Volume $(vol)" ;;
  up)
    wpctl set-mute "$SINK" 0; wpctl set-volume -l 1.0 "$SINK" 5%+; msg "Volume $(vol)" ;;
  prev)
    playerctl -p "$(player)" previous ;;
  play)
    playerctl -p "$(player)" play-pause ;;
  next)
    playerctl -p "$(player)" next ;;
esac
