#!/bin/sh
# Builds the deck's tmux layout once, then attaches to it.
# On every display (re)start, refresh tmux's copy of sway's environment.
for v in SWAYSOCK WAYLAND_DISPLAY XDG_CURRENT_DESKTOP; do
  eval "val=\$$v"
  [ -n "$val" ] && tmux set-environment -g "$v" "$val" 2>/dev/null
done
if ! tmux has-session -t deck 2>/dev/null; then
  tmux new-session -d -s deck -n main
  tmux new-window -t deck -n apps "sh -c '~/bin/launcher.py; exec bash'"
  tmux new-window -t deck -n settings "sh -c '~/bin/settings.py; exec bash'"
  tmux select-window -t deck:main
fi
exec tmux attach -t deck
