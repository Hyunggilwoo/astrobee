#!/bin/bash
# Instruction window on the virtual desktop.
export DISPLAY="${DISPLAY:-:99}"
sleep 1
exec xterm -geometry 100x28+40+40 -fa Monospace -fs 11 -T "Astrobee HCI" \
  -e /opt/hci/docker/scripts/desktop_shell.sh
