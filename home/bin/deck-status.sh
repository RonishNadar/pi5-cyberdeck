#!/usr/bin/env bash
# deck-status.sh: phone-style Wi-Fi, Bluetooth and battery icons for the tmux header

wifi() {
  local dev q
  dev=$(ip route show default 2>/dev/null | awk '{print $5; exit}')
  case "$dev" in
    wl*)
      q=$(awk -v d="$dev:" '$1==d {print int($3)}' /proc/net/wireless)   # link quality, 0-70
      q=${q:-0}
      if   [ "$q" -ge 56 ]; then printf '\U000f0928'   # 4 bars
      elif [ "$q" -ge 42 ]; then printf '\U000f0925'   # 3 bars
      elif [ "$q" -ge 28 ]; then printf '\U000f0922'   # 2 bars
      elif [ "$q" -ge 14 ]; then printf '\U000f091f'   # 1 bar
      else                       printf '\U000f092f'   # 0 bars
      fi ;;
    "") printf '\U000f092e' ;;                         # no connection
    *)  printf '\U000f0200' ;;                         # Ethernet
  esac
}

bt() {
  if ! timeout 2 bluetoothctl show 2>/dev/null | grep -q 'Powered: yes'; then
    printf '\U000f00b2'                                # off
  elif [ -n "$(timeout 2 bluetoothctl devices Connected 2>/dev/null)" ]; then
    printf '\U000f00b1'                                # connected
  else
    printf '\U000f00af'                                # on
  fi
}

battery() {   # icon and percent, nothing without the INA228 (see deck-battery)
  local pct state step
  local charging=(f089c f0086 f0087 f0088 f089d f0089 f089e f008a f008b f0085)   # md-battery_charging_10 .. _100
  set -- $("$HOME/bin/deck-battery" --short 2>/dev/null)
  [ -n "$1" ] || return
  pct=$1; state=$2
  step=$(( (pct + 5) / 10 ))                           # nearest 10%, 0-10
  if [ "$state" = charging ]; then
    [ "$step" -lt 1 ] && step=1
    printf "\\U000${charging[step - 1]}"
  elif [ "$pct" -lt 10 ]; then printf '\U000f0083'     # alert
  elif [ "$step" -ge 10 ]; then printf '\U000f0079'    # full
  else printf "\\U000$(printf '%x' $((0xf0079 + step)))"   # md-battery_10 .. _90
  fi
  printf ' %s%%' "$pct"
}

bat=$(battery)
printf '%s   %s%s \n' "$(wifi)" "$(bt)" "${bat:+   $bat}"
