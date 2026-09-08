"""
Hardware config for a specific machine. Does NOT support other hardware and does NOT generalize. 
Machine: i5 5500u
"""
from __future__ import annotations

# --- VAAPI ---------------------------------------------------------------
# fallback driver for older intel igpus where ihd has no encode
LIBVA_DRIVER = "i965"

VAAPI_DEVICE_CANDIDATES = ("/dev/dri/renderD128", "/dev/dri/renderD129", "/dev/dri/card0")
DEFAULT_VAAPI_DEVICE = "/dev/dri/renderD128"
DEFAULT_KMS_DEVICE = "/dev/dri/card0"

# H.264 profile- high doesnt work
VAAPI_PROFILE = "main"

# --- Virtual monitors ----------------------------------------------------
# Disconnected ports that we will use
VIRTUAL_OUTPUT_POOL = ("HDMI-A-1", "HDMI-A-2", "DP-1", "DP-2")

# The real panel, used as the EDID donor when forcing a connector on and as the anchor for the new monitor
PANEL_CONNECTOR = "eDP-1"

# Window managers tried on headless virtual display
WM_CANDIDATES = ("openbox", "fluxbox", "icewm", "twm")

# --- H.264 ---------------------------------------------------------------
# Both encode dimensions must be multiples of the macroblock size. A height that
# is not (e.g. 900 = 56.25 macroblock rows) makes the encoder code 912 rows and
# ask the decoder to crop 12 away via the SPS- a decoder that ignores that crop
# shows those uninitialised rows as a thin black bar along one edge.
MACROBLOCK = 16
