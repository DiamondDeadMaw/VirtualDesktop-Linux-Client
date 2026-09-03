#!/usr/bin/env bash
# Undo enable_virtual_monitor.sh: turn the phantom output off and let the
# connector go back to real hotplug detection (disconnected).
#
# Usage: sudo ./disable_virtual_monitor.sh [DRM_CONNECTOR]   (default HDMI-A-1)
set -u

DRM_CONN="${1:-HDMI-A-1}"
CARD_CONN="card0-${DRM_CONN}"
OVERRIDE="/sys/kernel/debug/dri/0/${DRM_CONN}/edid_override"
STATUS="/sys/class/drm/${CARD_CONN}/status"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0 $*" >&2
    exit 1
fi

XR_NAME="$(echo "$DRM_CONN" | sed 's/HDMI-A-/HDMI-/')"
XUSER="${SUDO_USER:-$(logname 2>/dev/null)}"
XHOME="$(getent passwd "$XUSER" | cut -d: -f6)"

sudo -u "$XUSER" env DISPLAY=:0 XAUTHORITY="$XHOME/.Xauthority" \
    xrandr --output "$XR_NAME" --off 2>/dev/null || true
[ -e "$STATUS" ] && echo detect > "$STATUS"
[ -e "$OVERRIDE" ] && printf '' > "$OVERRIDE" 2>/dev/null || true
echo "Reverted $XR_NAME ($DRM_CONN) to auto-detect (disconnected)."
