#!/bin/sh
# Runs inside sway: the deck terminal; when it closes, sway exits too.
# Font size and padding follow the connected display (see deck-display.sh).
set -- $(~/bin/deck-display.sh)
foot -o "main.font=JetBrainsMono Nerd Font:size=$1" -o "main.line-height=$2" \
     -o "main.pad=$3 center" -e ~/bin/deck.sh
swaymsg exit
