#!/usr/bin/env bash
# deck-status.sh: phone-style Wi-Fi and Bluetooth icons for the tmux header

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

printf '%s  %s\n' "$(wifi)" "$(bt)"
