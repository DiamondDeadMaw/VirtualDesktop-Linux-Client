"""
Netmessage codec: 1 byte type :: 4 byte length :: body. Integers are big endian (!!)
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass

MESSAGE_TYPES = {
    1: "PeerInfo",
    3: "CrashCallstack",
    4: "UpdateRequired",
    5: "UpdateUnavailable",
    6: "UpdateInProgress",
    11: "UserSettingsReload",
    12: "UserSettingsPropertyChanged",
    13: "MobileSettingsReload",
    14: "MobileSettingsPropertyChanged",
    15: "StreamerSettingsReload",
    16: "StreamerSettingsPropertyChanged",
    21: "BoundaryDataChanged",
    22: "SkeletonDataChanged",
    23: "BeginStreaming",
    24: "RestartStreaming",
    25: "SwitchMonitor",
    26: "ToggleDictation",
    27: "LaunchStreamVR",
    28: "ExitSteamVR",
    29: "Recenter",
    30: "RequestGames",
    31: "Games",
    32: "RequestGameThumbnails",
    33: "GameThumbnail",
    34: "LaunchGame",
    35: "ExitGame",
    36: "GameExited",
    40: "ShowDesktop",
    41: "DisposeGamepadEmulation",
    42: "AddMonitor",
    43: "RemoveMonitor",
    44: "SetMonitorPortrait",
    50: "RequestVideos",
    51: "Videos",
    52: "RequestVideoThumbnails",
    53: "VideoThumbnail",
    54: "PlayVideo",
    55: "DownloadVideo",
    56: "StopVideo",
    57: "VideoStopped",
    58: "SeekVideo",
    59: "CancelDownloadVideo",
    60: "AudioTracks",
    61: "SetAudioStreamIndex",
    62: "PasteUrl",
    63: "CancelPasteUrl",
    64: "VideoAdded",
    65: "SubtitleTracks",
    66: "SetSubtitleTrackId",
    67: "SetSubtitleStreamIndex",
    68: "SetSubtitleOffsetAmount",
    70: "ScreenshotToDesktop",
    71: "Exit",
    72: "EnableVRPassthrough",
    73: "DisableVRPassthrough",
    74: "ToggleVRPassthrough",
    75: "ToggleHandPassthrough",
    76: "ToggleDeskPassthrough",
    77: "TogglePerformanceOverlay",
    79: "ToggleFoveatedStreaming",
    80: "ChangeLanguage",
    81: "Disconnect",
    82: "RequestCursor",
    83: "Cursor",
    90: "WakeUpComputer",
    91: "ComputerAwaken",
    100: "Log",
}
NAME_TO_TYPE = {v: k for k, v in MESSAGE_TYPES.items()}


@dataclass
class NetMessage:
    message_type: int
    body: bytes

    @property
    def type_name(self) -> str:
        return MESSAGE_TYPES.get(self.message_type, f"Unknown({self.message_type})")

    def encode(self) -> bytes:
        return struct.pack(">BI", self.message_type, len(self.body)) + self.body

    @classmethod
    def decode(cls, data: bytes) -> "NetMessage":
        message_type, length = struct.unpack_from(">BI", data, 0)
        body = data[5 : 5 + length]
        return cls(message_type, body)

    @classmethod
    def empty(cls, type_name: str) -> "NetMessage":
        return cls(NAME_TO_TYPE[type_name], b"")


def write_int32(val: int) -> bytes:
    return struct.pack(">i", val)


def json_message(type_name: str, obj: dict) -> bytes:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return NetMessage(NAME_TO_TYPE[type_name], body).encode()
