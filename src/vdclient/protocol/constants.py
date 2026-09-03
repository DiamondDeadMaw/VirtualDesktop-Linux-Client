from __future__ import annotations

DISCOVERY_PORT = 38850
CONTROL_PORT = 38810
DATA_PORT = 38820
VIDEO_PORT = 38830
AUDIO_PORT = 38840

TCP_PORTS = (CONTROL_PORT, DATA_PORT, VIDEO_PORT, AUDIO_PORT)

CHANNEL_NAMES = {
    CONTROL_PORT: "control",
    DATA_PORT: "data",
    VIDEO_PORT: "video",
    AUDIO_PORT: "audio",
}

DISCOVERY_KEY = bytes(
    [
        32, 71, 236, 94, 194, 34, 85, 255, 165, 172, 187, 150, 6, 104, 106, 57,
        57, 62, 244, 114, 75, 174, 237, 9, 48, 36, 239, 82, 57, 98, 205, 80,
    ]
)
DISCOVERY_IV = bytes([82, 200, 129, 118, 144, 104, 249, 4, 62, 20, 120, 110, 20, 180, 63, 31])

MIN_PAIRING_REQUEST_LEN = 243
CONNECT_NUDGE_LEN = 17
DISCOVERY_REQUEST_FRAMES = 4

PLATFORM_NAMES = {
    0: "Oculus", 1: "Meta (OculusQuest)", 2: "Vive", 3: "Pico",
    5: "Google", 6: "PlayForDream", 7: "Apple", 8: "Steam",
}
MAX_MONITORS = 3
KEEPALIVE_INTERVAL = 10.0
