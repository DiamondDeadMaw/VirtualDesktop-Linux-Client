#!/usr/bin/env bash
# Make a disconnected video port appear as a REAL, connected monitor so a
# compositing desktop (Cinnamon/Muffin, GNOME, KDE) extends onto it natively --
# real cursor, wallpaper, windows you drag across -- which we then capture for
# the Quest. This is the only reliable way to get an extended desktop on a
# compositor that reverts `xrandr --fb`; here the output is genuinely "connected"
# as far as DRM/X are concerned, so the desktop treats it like a plugged-in screen.
#
# How: force the connector's status "on" and feed it an EDID (we reuse the
# laptop panel's own EDID, guaranteed valid). No packages, no reboot; undo with
# disable_virtual_monitor.sh.
#
# Usage: sudo ./enable_virtual_monitor.sh [DRM_CONNECTOR]
#   DRM_CONNECTOR defaults to HDMI-A-1 (its xrandr name is HDMI-1).
#   List options with: ls /sys/class/drm/ | grep -E 'HDMI|DP'
set -u

DRM_CONN="${1:-HDMI-A-1}"
CARD_CONN="card0-${DRM_CONN}"
OVERRIDE="/sys/kernel/debug/dri/0/${DRM_CONN}/edid_override"
STATUS="/sys/class/drm/${CARD_CONN}/status"
PANEL_EDID="/sys/class/drm/card0-eDP-1/edid"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0 $*" >&2
    exit 1
fi
for f in "$OVERRIDE" "$STATUS" "$PANEL_EDID"; do
    [ -e "$f" ] && continue
    echo "Missing: $f" >&2
    echo "Wrong connector? Options: $(ls /sys/class/drm/ | grep -oE '(HDMI-A|DP)-[0-9]+' | tr '\n' ' ')" >&2
    exit 1
done

# xrandr's name for the connector: HDMI-A-1 -> HDMI-1 (DP-1 stays DP-1).
XR_NAME="$(echo "$DRM_CONN" | sed 's/HDMI-A-/HDMI-/')"
XUSER="${SUDO_USER:-$(logname 2>/dev/null)}"
XHOME="$(getent passwd "$XUSER" | cut -d: -f6)"
run_xrandr() { sudo -u "$XUSER" env DISPLAY=:0 XAUTHORITY="$XHOME/.Xauthority" xrandr "$@"; }

# Grab the panel EDID (sysfs reports size 0 but reading yields the real bytes).
tmp_edid="$(mktemp)"
cat "$PANEL_EDID" > "$tmp_edid"
if [ ! -s "$tmp_edid" ]; then
    echo "Your panel (eDP-1) exposes no EDID via sysfs, so we can't reuse it." >&2
    echo "Tell me and I'll embed a standard 1600x900 EDID blob instead." >&2
    rm -f "$tmp_edid"
    exit 1
fi
echo "Panel EDID is $(wc -c < "$tmp_edid") bytes; applying it to $DRM_CONN ..."
cat "$tmp_edid" > "$OVERRIDE" || { echo "write to edid_override failed" >&2; exit 1; }
rm -f "$tmp_edid"

echo "Forcing $DRM_CONN connected ..."
echo on > "$STATUS" || { echo "write to status failed" >&2; exit 1; }
sleep 1

echo "Enabling $XR_NAME to the right of eDP-1 ..."
run_xrandr --output "$XR_NAME" --auto --right-of eDP-1
sleep 1

echo
echo "=== result ==="
run_xrandr --query | grep -E "^(eDP-1|$XR_NAME) " || true
geo="$(run_xrandr --query | awk -v o="$XR_NAME" '$1==o {for(i=1;i<=NF;i++) if($i ~ /^[0-9]+x[0-9]+\+[0-9]+\+[0-9]+$/){print $i; exit}}')"
if [ -n "$geo" ]; then
    echo
    echo "'$XR_NAME' is live at $geo. If Cinnamon shows two monitors (Menu > Display),"
    echo "the extended desktop works. To stream both to the Quest, set the"
    echo "\"monitors\" list in your vdclient.json to:"
    wh="${geo%%+*}"; off="${geo#*+}"
    echo "  \"monitors\": [null, [${off%+*}, ${off#*+}, ${wh%x*}, ${wh#*x}]]"
else
    echo "$XR_NAME did not come up connected -- paste this output and we'll adjust."
fi
echo "Undo any time with: sudo ./disable_virtual_monitor.sh $DRM_CONN"
