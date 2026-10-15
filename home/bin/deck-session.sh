#!/bin/sh
# Runs inside sway: the deck terminal; when it closes, sway exits too.
# Font size and padding follow the connected display (see deck-display.sh).
# When a display with a different model is plugged in, only foot restarts
# with the new settings; tmux keeps running and the session reattaches.
run=/run/user/$(id -u)
pidfile=$run/deck-foot.pid
swapflag=$run/deck-display-swap
rm -f "$swapflag"

watch_displays() {
  swaymsg -t subscribe -m '["output"]' 2>/dev/null | while read -r _; do
    sleep 1   # let the output settle
    model=$(~/bin/deck-display.sh --model)
    [ -n "$model" ] && [ "$model" != "$(cat "$run/deck-display-model" 2>/dev/null)" ] || continue
    touch "$swapflag"
    kill "$(cat "$pidfile")" 2>/dev/null
  done
}
watch_displays &
watcher=$!

while :; do
  ~/bin/deck-display.sh --model > "$run/deck-display-model"
  set -- $(~/bin/deck-display.sh)
  foot -o "main.font=JetBrainsMono Nerd Font:size=$1" -o "main.line-height=$2" \
       -o "main.pad=$3 center" -e ~/bin/deck.sh &
  echo $! > "$pidfile"
  wait $!
  [ -e "$swapflag" ] || break
  rm -f "$swapflag"
done

pkill -P $watcher 2>/dev/null
kill $watcher 2>/dev/null
swaymsg exit
