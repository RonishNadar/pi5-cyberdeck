#!/bin/sh
# Dracula powerline-style footer for tmux. Blocks listed in
# ~/.config/deck/footer-hide (one name per line) are skipped.

DARK="#282a36"; BAR="#44475a"; GRAY="#6272a4"
SEP=$(printf '\356\202\262')   # powerline arrow (U+E0B2)
HIDE="$HOME/.config/deck/footer-hide"
show() { ! grep -qx "$1" "$HIDE" 2>/dev/null; }

out=""; prev="$BAR"
seg() { out="$out#[fg=$1,bg=$prev]$SEP#[fg=$DARK,bg=$1,bold] $2 "; prev="$1"; }

# Calendar: next appointment within 24 hours (only if there is one)
if show cal; then
  next=$(calcurse -n 2>/dev/null | sed -n "2p" | sed "s/^ *//; s/\[//; s/\]//" | cut -c1-30)
  [ -n "$next" ] && seg "#ff5555" "$next"
fi

# IP: interface that has the default route (Wi-Fi or Ethernet)
iface=$(ip route show default 2>/dev/null | awk '{print $5; exit}')
[ -n "$iface" ] && ip=$(ip -4 -o addr show "$iface" 2>/dev/null | awk '{print $4; exit}' | cut -d/ -f1)
online=1
if [ -z "$ip" ]; then ip="offline"; ipcol="$GRAY"; online=0; else ipcol="#8be9fd"; fi
show ip && seg "$ipcol" "IP $ip"

# CPU usage (compared with the previous sample) and temperature
if show cpu; then
  CPUSTATE=/tmp/deck-cpu
  set -- $(awk '/^cpu /{idle=$5+$6; total=0; for(i=2;i<=9;i++) total+=$i; print total, idle}' /proc/stat)
  total=$1; idle=$2; cpupct="--"
  if [ -s "$CPUSTATE" ]; then
    read ptotal pidle < "$CPUSTATE"
    dt=$((total - ptotal)); di=$((idle - pidle))
    [ "$dt" -gt 0 ] && cpupct="$(( (dt - di) * 100 / dt ))%"
  fi
  echo "$total $idle" > "$CPUSTATE"
  cputemp=$(awk '{printf "%.0f", $1/1000}' /sys/class/thermal/thermal_zone0/temp 2>/dev/null)
  [ -n "$cputemp" ] && cputemp="${cputemp}C" || cputemp="--"
  seg "#ffb86c" "CPU $cpupct $cputemp"
fi

if show mem; then
  mem=$(awk '/^MemTotal/{t=$2} /^MemAvailable/{a=$2} END{if(t>0) printf "%d%% %.1fG", (t-a)*100/t, (t-a)/1048576}' /proc/meminfo)
  seg "#f1fa8c" "MEM ${mem:---}"
fi

if show disk; then
  disk=$(df -h / 2>/dev/null | awk 'NR==2{print $5}')
  seg "#ff79c6" "DISK ${disk:---}"
fi

if show up; then
  up=$(uptime -p 2>/dev/null | sed 's/^up //; s/ hours\?/h/; s/ minutes\?/m/; s/ days\?/d/; s/,//g')
  seg "#bd93f9" "UP ${up:---}"
fi

# Weather: refresh every 15 min when online, keep valid readings only
if show weather; then
  CACHE=/tmp/deck-weather
  age=999999
  [ -s "$CACHE" ] && age=$(( $(date +%s) - $(stat -c %Y "$CACHE") ))
  if [ "$online" -eq 1 ] && [ "$age" -gt 900 ]; then
    ( r=$(curl -fsm 8 'https://wttr.in/?format=%C+%t&m' 2>/dev/null | sed 's/+//' | tr -s ' ')
      case "$r" in
        *°F*|*°C*) [ ${#r} -lt 40 ] && printf '%s' "$r" > "$CACHE" ;;
      esac ) >/dev/null 2>&1 &
  fi
  if [ -s "$CACHE" ]; then
    weather=$(cat "$CACHE")
    if [ "$age" -gt 3600 ]; then weather="$weather (old)"; wcol="$GRAY"; else wcol="#50fa7b"; fi
  else
    weather="no weather"; wcol="$GRAY"
  fi
  seg "$wcol" "$weather"
fi

printf '%s' "$out"
