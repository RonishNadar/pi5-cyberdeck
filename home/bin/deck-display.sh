#!/bin/sh
# Prints "font-size line-height padding" for the connected display, picked by its model.
# Add a case for each new panel (model from: swaymsg -t get_outputs).
# With --model it prints only the model name.
model=$(swaymsg -t get_outputs -r 2>/dev/null | python3 -c '
import json, sys
outs = [o for o in json.load(sys.stdin) if o.get("active")]
print(outs[0]["model"] if outs else "")' 2>/dev/null)

[ "$1" = --model ] && { echo "$model"; exit; }

case "$model" in
  MPI7002)   # 7" HDMI Display-C, 1024x600
    size=10; line=12; pad=8x4 ;;
  *)         # Waveshare 5.5" 1440x2560 (our own EDID) and anything unknown
    size=28; line=32; pad=24x16 ;;
esac

echo "$size $line $pad"
