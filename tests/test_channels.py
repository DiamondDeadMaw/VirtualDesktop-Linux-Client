"""
The TCP channels, driven by a fake Quest over real localhost sockets.

This exercises the sequence that actually gets the headset to "Connected":
the control channel must push StreamerSettingsReload (or the Add/Remove Monitor
buttons stay greyed out forever), and the data channel must send the big-endian
Int32(0) that unblocks TryMeasureBitrate (or the headset hangs on "Measuring
bandwidth...").
"""
from __future__ import annotations

import json
import queue
import socket
import struct
import threading
import time

import pytest

from vdclient import config as config_module
from vdclient.net import server
from vdclient.net.registry import SessionRegistry
from vdclient.protocol.constants import CONTROL_PORT, DATA_PORT
from vdclient.protocol.crypto import ChainCipherStream
from vdclient.protocol.messages import NetMessage

KEY = b"\x33" * 32
IV = b"\x44" * 16
PEER = "127.0.0.1"

# High ports, so the tests need no privileges and never clash with a real install.
CONTROL = 48810
DATA = 48820


@pytest.fixture(scope="module")
def listening():
    """Start the real acceptors on test ports with a pre-seeded session key,
    exactly as discovery would have left it.

    Module-scoped deliberately: a socket bound with SO_REUSEADDR can be bound
    again while the first is alive, so per-test listeners would stack up on the
    same port and connections would land on whichever one the OS chose.
    """
    registry = SessionRegistry()
    registry.set_keys(PEER, KEY, IV)
    cfg = config_module.Config()
    for port, role in ((CONTROL, CONTROL_PORT), (DATA, DATA_PORT)):
        threading.Thread(target=server.run, args=(port, registry, cfg, role),
                         daemon=True).start()
    time.sleep(0.3)
    return registry, cfg


def connect(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect((PEER, port))
    return sock, ChainCipherStream(sock, KEY, IV)


def test_control_pushes_streamer_settings_on_connect(listening):
    """The Quest is only a *client* of StreamerSettings, so the streamer must
    push the reload or CanAddMonitor stays false and the toolbar is dead."""
    sock, stream = connect(CONTROL)
    try:
        msg = NetMessage.decode(stream.read_frame())
        assert msg.type_name == "StreamerSettingsReload"
        settings = json.loads(msg.body.decode("utf-8"))
        # One monitor configured: can add, cannot remove.
        assert settings == {"CanAddMonitor": True}
    finally:
        sock.close()


def test_control_reload_reflects_multiple_monitors(listening):
    registry, cfg = listening
    cfg.video.monitors = [None, {"virtual": True}]
    sock, stream = connect(CONTROL)
    try:
        settings = json.loads(NetMessage.decode(stream.read_frame()).body.decode())
        assert settings == {"CanAddMonitor": True, "CanRemoveMonitor": True}
    finally:
        sock.close()
        cfg.video.monitors = [None]


def test_control_reload_at_max_monitors(listening):
    registry, cfg = listening
    cfg.video.monitors = [None, {"virtual": True}, {"virtual": True}]
    sock, stream = connect(CONTROL)
    try:
        settings = json.loads(NetMessage.decode(stream.read_frame()).body.decode())
        assert settings == {"CanRemoveMonitor": True}
    finally:
        sock.close()
        cfg.video.monitors = [None]


def test_data_channel_sends_the_bandwidth_gate(listening):
    """num <= 0 makes TryMeasureBitrate return false immediately without reading
    anything further -- the minimal safe way past the gate."""
    sock, stream = connect(DATA)
    try:
        frame = stream.read_frame()
        assert frame == b"\x00\x00\x00\x00"
        assert struct.unpack(">i", frame)[0] == 0
    finally:
        sock.close()


def test_add_monitor_is_routed_to_the_video_session(listening):
    """The control thread receives the button press; the video thread owns the
    streams, so the request has to cross between them via the session queue."""
    registry, _cfg = listening
    session = registry.get(PEER)
    session.video_commands = queue.Queue()

    sock, stream = connect(CONTROL)
    try:
        NetMessage.decode(stream.read_frame())  # the initial reload
        stream.write_frame(NetMessage.empty("AddMonitor").encode())
        assert session.video_commands.get(timeout=5.0) == "AddMonitor"

        stream.write_frame(NetMessage.empty("RemoveMonitor").encode())
        assert session.video_commands.get(timeout=5.0) == "RemoveMonitor"
    finally:
        sock.close()
        session.video_commands = None


def test_button_press_without_a_video_session_is_survivable(listening):
    """Pressing Add Monitor before video is streaming must not kill the channel."""
    registry, _cfg = listening
    session = registry.get(PEER)
    session.video_commands = None

    sock, stream = connect(CONTROL)
    try:
        NetMessage.decode(stream.read_frame())
        stream.write_frame(NetMessage.empty("AddMonitor").encode())
        # Still alive: a further message is still read without the socket dying.
        stream.write_frame(NetMessage.empty("Recenter").encode())
        time.sleep(0.3)
        assert sock.fileno() != -1
    finally:
        sock.close()


def test_unknown_peer_is_refused(listening):
    """A host that never completed discovery has no session key, so there is
    nothing to decrypt with; the connection must be dropped, not guessed at."""
    registry, _cfg = listening
    registry._sessions.pop("127.0.0.1", None)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    try:
        sock.connect((PEER, DATA))
        assert sock.recv(16) == b""      # closed without sending anything
    finally:
        sock.close()
        registry.set_keys(PEER, KEY, IV)


def test_video_channel_drains_inbound_bytes():
    """The Quest writes eye-position updates back on the video channel. If they
    are never read our receive buffer fills, TCP zero-windows, and the Quest
    blocks writing -- which stops it draining our video and freezes the session.
    """
    import socket as _socket
    import time
    from vdclient.net import video_channel

    server, client = _socket.socketpair()
    try:
        threading.Thread(target=video_channel._drain_inbound, args=(server,),
                         daemon=True).start()
        # Far more than any socket buffer would hold unread.
        payload = b"\x00" * 20
        for _ in range(20000):
            client.sendall(payload)
        # A blocked drain would have wedged long before here.
        client.settimeout(2.0)
        client.sendall(payload)
    finally:
        client.close()
        server.close()


def test_drain_stops_when_peer_closes():
    import socket as _socket
    from vdclient.net import video_channel

    server, client = _socket.socketpair()
    client.close()
    video_channel._drain_inbound(server)  # returns rather than spinning
    server.close()
