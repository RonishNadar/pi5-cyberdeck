# ~/.config/deck/deck.bashrc
# Everything the deck adds to bash. Loaded from ~/.bashrc with:
#   [ -f ~/.config/deck/deck.bashrc ] && . ~/.config/deck/deck.bashrc
# Safe to source more than once.

# ---- PATH and editor ----
case ":$PATH:" in *":$HOME/bin:"*) ;; *) export PATH="$HOME/bin:$PATH" ;; esac
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
export EDITOR=nvim
export VISUAL=nvim
alias vim=nvim
alias vi=nvim

# ---- Prompt ----
command -v starship >/dev/null && eval "$(starship init bash)"

# ---- Files, disk, notes ----
# y: open yazi and cd to the folder you exit in
function y() {
  local tmp="$(mktemp -t "yazi-cwd.XXXXXX")" cwd
  yazi "$@" --cwd-file="$tmp"
  if cwd="$(command cat -- "$tmp")" && [ -n "$cwd" ] && [ "$cwd" != "$PWD" ]; then
    builtin cd -- "$cwd"
  fi
  rm -f -- "$tmp"
}
alias space='ncdu ~'
alias space-all='sudo ncdu /'
alias md='glow'
alias lg=lazygit

# ---- Apps ----
alias agenda='calcurse'
alias today='calcurse -d 1'
alias todo='calcurse -t'
alias news=newsboat
alias music=cmus
alias mail=aerc
alias lanscan=~/bin/lanscan.py

# ---- Web ----
alias web='w3m https://lite.duckduckgo.com/lite/'
function s() { w3m "https://lite.duckduckgo.com/lite/?q=$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote_plus(" ".join(sys.argv[1:])))' "$@")"; }
command -v carbonyl >/dev/null && alias browse='carbonyl --zoom=200 --no-sandbox'

# ---- Wi-Fi (nmtui in Dracula colors) ----
export NEWT_COLORS='root=white,black
window=white,black
border=magenta,black
title=magenta,black
textbox=white,black
button=black,magenta
actbutton=black,cyan
compactbutton=white,black
listbox=white,black
actlistbox=black,magenta
sellistbox=black,cyan
actsellistbox=black,magenta
entry=white,black
disentry=gray,black
checkbox=white,black
actcheckbox=black,magenta
label=cyan,black
helpline=black,magenta
roottext=magenta,black
emptyscale=,black
fullscale=,magenta'
alias wifi='nmtui connect'
alias wifi-scan='nmcli device wifi rescan; nmcli device wifi list'

# ---- Bluetooth ----
alias bt='bluetuith'
alias bt-list='bluetoothctl devices Connected; echo; bluetoothctl devices Paired'

# ---- Network tools ----
alias bw='bandwhich'
alias lan-scan='nmap -sn $(ip -4 -o addr show $(ip route show default | awk "{print \$5; exit}") | awk "{print \$4}")'
alias lan-ports='sudo nmap -sV --top-ports 100'
alias sniff='termshark -i $(ip route show default | awk "{print \$5; exit}")'

# ---- Notifications ----
# Ubuntu's default .bashrc defines an "alert" alias; replace it with a function.
unalias alert 2>/dev/null
# alert <command>: run a command, then notify with its result
function alert {
  local start=$(date +%s); "$@"; local st=$?; local dur=$(( $(date +%s) - start ))
  if [ $st -eq 0 ]; then notify -l ok -s "Finished in ${dur}s: $*"
  else notify -l error -s "Failed ($st) after ${dur}s: $*"; fi
  return $st
}

# Automatic alert when a command runs 30 seconds or longer.
# Turn off in Settings > Notifications (creates ~/.config/deck/flags/no-long-alerts).
__deck_start_file="${XDG_RUNTIME_DIR:-/tmp}/deck-cmd-start-$$"
[[ "$PS0" == *deck-cmd-start* ]] || PS0='$(date +%s > "'"$__deck_start_file"'")'"${PS0}"
__deck_cmd_done() {
  local st=$?
  [ -f "$HOME/.config/deck/flags/no-long-alerts" ] && { rm -f "$__deck_start_file"; return $st; }
  if [ -f "$__deck_start_file" ]; then
    local start=$(cat "$__deck_start_file"); rm -f "$__deck_start_file"
    local dur=$(( $(date +%s) - start ))
    if [ "$dur" -ge 30 ]; then
      local cmd=$(HISTTIMEFORMAT= history 1 | sed 's/^ *[0-9]* *//')
      case "${cmd%% *}" in
        nvim|vim|vi|btop|yazi|y|cmus|music|newsboat|news|aerc|mail|lazygit|lg|ssh|man|less|tmux|bt|bluetuith|termshark|sniff|w3m|web|s|carbonyl|browse|lanscan|agenda|calcurse|nmtui|wifi|glow|md|ncdu|space|space-all|bw|bandwhich|alert|claude) ;;
        *) if [ $st -eq 0 ]; then notify -l ok -s "Finished in ${dur}s: ${cmd:0:60}"
           else notify -l error -s "Failed ($st) after ${dur}s: ${cmd:0:60}"; fi ;;
      esac
    fi
  fi
  return $st
}
[[ "$PROMPT_COMMAND" == *__deck_cmd_done* ]] || PROMPT_COMMAND="__deck_cmd_done${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
alias notifs='tail -n 30 ${XDG_STATE_HOME:-$HOME/.local/state}/deck/notify.log'
