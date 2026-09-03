"""
Chained AES-CBC wire framing with varint length prefix.
CBC state persists across messages for the whole connection.
"""
from __future__ import annotations

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes


def encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def decode_varint(buf: bytes, offset: int = 0) -> tuple[int, int]:
    result = 0
    shift = 0
    pos = offset
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos - offset
        shift += 7


def round_up_16(n: int) -> int:
    return (n + 15) & ~15


def encrypt_frame(payload: bytes, cipher) -> bytes:
    hdr = encode_varint(len(payload))
    logical = hdr + payload
    block_len = round_up_16(len(logical))
    pad = get_random_bytes(block_len - len(logical)) if block_len > len(logical) else b""
    return cipher.encrypt(logical + pad)


class BufferSocket:
    """Socket adapter over a buffer so ChainCipherStream works on UDP packets too."""

    def __init__(self, data: bytes = b""):
        self._data = data
        self._pos = 0
        self._out = bytearray()

    def recv(self, n: int) -> bytes:
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def sendall(self, data: bytes) -> None:
        self._out.extend(data)

    @property
    def sent(self) -> bytes:
        return bytes(self._out)


def read_frames_from_blob(data: bytes, key: bytes, iv: bytes, count: int) -> list[bytes]:
    stream = ChainCipherStream(BufferSocket(data), key, iv)
    return [stream.read_frame() for _ in range(count)]


def write_frames_to_blob(frames: list[bytes], key: bytes, iv: bytes) -> bytes:
    sock = BufferSocket()
    stream = ChainCipherStream(sock, key, iv)
    for payload in frames:
        stream.write_frame(payload)
    return sock.sent


class ChainCipherStream:
    def __init__(self, sock, key: bytes, iv: bytes):
        self._sock = sock
        self._enc = AES.new(key, AES.MODE_CBC, iv)
        self._dec = AES.new(key, AES.MODE_CBC, iv)

    @property
    def socket(self):
        return self._sock

    def write_frame(self, payload: bytes) -> None:
        self._sock.sendall(encrypt_frame(payload, self._enc))

    def _recv_exact(self, n: int) -> bytes:
        chunks = []
        remaining = n
        while remaining:
            chunk = self._sock.recv(remaining)
            if not chunk:
                raise ConnectionError("socket closed mid-frame")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def read_frame(self) -> bytes:
        first = self._dec.decrypt(self._recv_exact(16))
        length, hdr_len = decode_varint(first, 0)
        logical_len = hdr_len + length
        block_len = round_up_16(logical_len)
        if block_len > 16:
            rest = self._dec.decrypt(self._recv_exact(block_len - 16))
            full = first + rest
        else:
            full = first
        return full[hdr_len:logical_len]
