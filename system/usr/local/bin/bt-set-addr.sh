#!/bin/sh
# Give the Pi's Bluetooth chip a real address: Wi-Fi MAC + 1 (Raspberry Pi convention)
# btmgmt only exits cleanly with a terminal, so each call runs inside "script" (a pseudo-terminal)
mac=$(cat /sys/class/net/wlan0/address)
addr=$(printf '%s:%02x' "${mac%:*}" $(( (0x${mac##*:} + 1) % 256 )))
run() { timeout 5 script -qec "btmgmt --index 0 $*" /dev/null >/dev/null 2>&1; echo "$1: $?"; }
rfkill unblock bluetooth
run power off
run public-addr "$addr"
run power on
echo "Bluetooth address set to $addr"
exit 0
