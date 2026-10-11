#!/bin/sh
# Runs inside sway: the deck terminal; when it closes, sway exits too
foot -e ~/bin/deck.sh
swaymsg exit
