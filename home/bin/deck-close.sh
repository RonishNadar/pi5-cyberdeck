#!/bin/bash
# Back key (XF86Back, bound in sway): close the app on the deck screen after a confirm.
#   deck-close.sh            ask (popup on the deck's own tmux client)
#   deck-close.sh --ask ID   runs inside the popup: show the question, close on yes
# main, apps, settings and terminal tabs are never closed. Camera asks in its own window.

KEEP=" main apps settings term "
ACCENT=$(python3 -c 'import json;print(json.load(open("/home/admin/.config/deck/prefs.json")).get("accent","#bd93f9"))' 2>/dev/null)
ACCENT=${ACCENT:-#bd93f9}

label() {
  case "$1" in
    youtube) echo "YouTube" ;;  qr) echo "QR Scanner" ;;  lan) echo "LAN Scan" ;;
    wiki) echo "Wikipedia" ;;   claude) echo "Claude Code" ;;
    *) echo "${1^}" ;;
  esac
}

# Ask the app to quit (SIGTERM to the pane's foreground job), then close the tab if it is still there.
close_window() {
  local win=$1 pane pgid i
  pane=$(tmux display -p -t "$win" '#{pane_pid}' 2>/dev/null) || return
  pgid=$(ps -o tpgid= -p "$pane" | tr -d ' ')
  [ -n "$pgid" ] && [ "$pgid" -gt 1 ] && kill -TERM -- "-$pgid" 2>/dev/null
  for i in $(seq 30); do
    tmux list-windows -a -F '#{window_id}' | grep -qx "$win" || return
    sleep 0.1
  done
  tmux kill-window -t "$win"
}

if [ "$1" = "--ask" ]; then
  name=$(tmux display -p -t "$2" '#{window_name}' 2>/dev/null) || exit 0
  a=$(printf '%s' "$ACCENT" | sed 's/#//')
  fg() { printf '\e[38;2;%d;%d;%dm' "0x${1:0:2}" "0x${1:2:2}" "0x${1:4:2}"; }
  printf '\e[?25l\n  %s\e[1m%s\e[0m\n\n' "$(fg "$a")" "  Close $(label "$name")?"
  printf '  %sy\e[0m %sor\e[0m %sEnter\e[0m %sclose     %sn\e[0m %sor\e[0m %sEsc\e[0m %scancel\e[0m' \
    "$(fg 8be9fd)" "$(fg 6272a4)" "$(fg 8be9fd)" "$(fg 6272a4)" "$(fg 8be9fd)" "$(fg 6272a4)" "$(fg 8be9fd)" "$(fg 6272a4)"
  IFS= read -rsn1 key
  case "$key" in
    y|Y|"") close_window "$2" ;;
  esac
  exit 0
fi

# Camera is a fullscreen GTK window on top of the terminal: it shows its own confirm.
export SWAYSOCK=${SWAYSOCK:-$(ls -t /run/user/$(id -u)/sway-ipc.*.sock 2>/dev/null | head -1)}
if swaymsg -t get_tree 2>/dev/null | python3 -c '
import json, sys
def walk(n):
    if n.get("focused"): sys.exit(0 if n.get("app_id") == "tech.ronad.Camera" else 1)
    for c in n.get("nodes", []) + n.get("floating_nodes", []): walk(c)
walk(json.load(sys.stdin)); sys.exit(1)'; then
  pkill -USR1 -f '/bin/camera.py' && exit 0
fi

# The deck's own tmux client is the one running inside foot (not an SSH one).
client=""
while read -r pid name; do
  p=$pid
  while [ -n "$p" ] && [ "$p" -gt 1 ]; do
    [ "$(cat /proc/$p/comm 2>/dev/null)" = foot ] && { client=$name; break 2; }
    p=$(awk '/^PPid:/{print $2}' /proc/$p/status 2>/dev/null)
  done
done < <(tmux list-clients -F '#{client_pid} #{client_name}' 2>/dev/null)
[ -n "$client" ] || exit 0

# A tab started with no command or a bare shell is a terminal (apps start as "bash -ic <app>").
IFS=$'\t' read -r win name start < <(tmux display -p -c "$client" '#{window_id}	#{window_name}	#{pane_start_command}')
if [[ "$KEEP" == *" $name "* ]] || [[ -z "$start" || "$start" =~ ^(bash|sh|zsh)(\ -[a-bd-z]+)?$ ]]; then
  tmux display-message -c "$client" -d 1200 " Nothing to close here"
  exit 0
fi

tmux display-popup -c "$client" -w 46 -h 6 -b rounded -S "fg=$ACCENT" -s "bg=#282a36" \
  -E "$HOME/bin/deck-close.sh --ask $win"
