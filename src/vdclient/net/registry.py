"""
Per-peer state shared by the discovery responder and TCP channel handlers, holds aes keys and coords monitor requests.
"""
from __future__ import annotations

import threading


class PeerSession:
    def __init__(self) -> None:
        self.key: bytes | None = None
        self.iv: bytes | None = None
        self.control_stream = None
        self.control_lock = threading.Lock()
        self.streamer_settings: dict = {}
        self.video_commands = None

    @property
    def keys(self) -> tuple[bytes, bytes] | None:
        if self.key is None or self.iv is None:
            return None
        return (self.key, self.iv)

    def write_control(self, frame: bytes) -> bool:
        """Needs a channel lock bc the aes cbc chained stream doesnt allow concurrent writes"""
        stream = self.control_stream
        if stream is None:
            return False
        with self.control_lock:
            stream.write_frame(frame)
        return True


class SessionRegistry:

    def __init__(self) -> None:
        self._sessions: dict[str, PeerSession] = {}
        self._lock = threading.Lock()

    def get_or_create(self, peer_ip: str) -> PeerSession:
        with self._lock:
            session = self._sessions.get(peer_ip)
            if session is None:
                session = PeerSession()
                self._sessions[peer_ip] = session
            return session

    def get(self, peer_ip: str) -> PeerSession | None:
        with self._lock:
            return self._sessions.get(peer_ip)

    def set_keys(self, peer_ip: str, key: bytes, iv: bytes) -> PeerSession:
        session = self.get_or_create(peer_ip)
        session.key = key
        session.iv = iv
        return session
