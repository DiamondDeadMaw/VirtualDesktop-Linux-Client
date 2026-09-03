"""NetMessage framing and the JSON settings messages."""
from __future__ import annotations

import json
import struct

import pytest

from vdclient.protocol.messages import (
    MESSAGE_TYPES,
    NAME_TO_TYPE,
    NetMessage,
    json_message,
    write_int32,
)


def test_header_is_big_endian():
    """NetworkBinaryWriter writes the payload size big-endian, unlike a stock
    .NET BinaryWriter. Getting this wrong makes every message unparseable."""
    msg = NetMessage(15, b"x" * 300)
    encoded = msg.encode()
    assert encoded[0] == 15
    assert struct.unpack_from(">I", encoded, 1)[0] == 300
    assert encoded[5:] == b"x" * 300


@pytest.mark.parametrize("body", [b"", b"hello", bytes(range(256)), b"\x00" * 1000])
def test_roundtrip(body):
    decoded = NetMessage.decode(NetMessage(42, body).encode())
    assert decoded.message_type == 42
    assert decoded.body == body


def test_known_type_names():
    # These drive the control-channel dispatch; a renumbering breaks it silently.
    assert NAME_TO_TYPE["StreamerSettingsReload"] == 15
    assert NAME_TO_TYPE["StreamerSettingsPropertyChanged"] == 16
    assert NAME_TO_TYPE["AddMonitor"] == 42
    assert NAME_TO_TYPE["RemoveMonitor"] == 43
    assert MESSAGE_TYPES[81] == "Disconnect"


def test_unknown_type_name_is_descriptive():
    assert NetMessage(200, b"").type_name == "Unknown(200)"


def test_empty_message():
    assert NetMessage.empty("Recenter").encode() == struct.pack(">BI", 29, 0)


@pytest.mark.parametrize("value", [-2147483648, -1, 0, 1, 255, 65536, 2147483647])
def test_write_int32_big_endian(value):
    assert write_int32(value) == struct.pack(">i", value)


def test_json_message_is_compact_pascal_case_utf8():
    """Newtonsoft's default contract resolver means PascalCase property names,
    plain UTF-8, no BOM."""
    frame = json_message("StreamerSettingsReload", {"CanAddMonitor": True})
    assert frame[0] == 15
    body = frame[5:]
    assert body == b'{"CanAddMonitor":true}'
    assert json.loads(body.decode("utf-8")) == {"CanAddMonitor": True}


def test_json_message_empty_settings():
    frame = json_message("StreamerSettingsReload", {})
    assert frame[5:] == b"{}"
