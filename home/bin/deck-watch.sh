#!/bin/sh
# Warn about high temperature, a full disk, or undervoltage (once per change)
state=${XDG_STATE_HOME:-$HOME/.local/state}/deck/watch
mkdir -p "$state"

check() {   # check NAME IS_BAD "problem message" "cleared message"
  if [ "$2" = 1 ] && [ ! -f "$state/$1" ]; then
    touch "$state/$1"; ~/bin/notify -l warn -s -t 10 "$3"
  elif [ "$2" = 0 ] && [ -f "$state/$1" ]; then
    rm -f "$state/$1"; ~/bin/notify -l ok "$4"
  fi
}

temp=$(awk '{printf "%d", $1/1000}' /sys/class/thermal/thermal_zone0/temp 2>/dev/null)
[ -n "$temp" ] && { [ "$temp" -ge 75 ] && hot=1 || hot=0; check hot $hot "CPU is hot: ${temp}C" "CPU temperature back to normal: ${temp}C"; }

disk=$(df --output=pcent / | tail -1 | tr -dc '0-9')
[ -n "$disk" ] && { [ "$disk" -ge 90 ] && full=1 || full=0; check disk $full "Disk almost full: ${disk}%" "Disk usage back below 90%: ${disk}%"; }

uv=0
for h in /sys/class/hwmon/hwmon*; do
  [ "$(cat "$h/name" 2>/dev/null)" = "rpi_volt" ] && [ "$(cat "$h/in0_lcrit_alarm" 2>/dev/null)" = 1 ] && uv=1
done
check power $uv "Undervoltage detected: check the power supply" "Power supply voltage back to normal"
