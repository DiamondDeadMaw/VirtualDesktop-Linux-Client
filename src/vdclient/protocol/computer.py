"""
Builds Computer XML for discovery replies so quest lists us as connectable.
"""

from __future__ import annotations

import base64
import uuid
from xml.sax.saxutils import escape

NS = "http://schemas.datacontract.org/2004/07/VirtualDesktop.Interfaces"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
SYSTEM_NS = "http://schemas.datacontract.org/2004/07/System"


def _b(value: bool) -> str:
    return "true" if value else "false"


def _bytes(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _adapter_xml(
    ip: str, mac: str, is_wireless: bool, is_gigabit: bool, is_usb: bool = False
) -> str:
    return (
        "<NetworkAdapter>"
        f"<IPAddresString>{escape(ip)}</IPAddresString>"
        f"<IsGigabit>{_b(is_gigabit)}</IsGigabit>"
        f"<IsUsb>{_b(is_usb)}</IsUsb>"
        f"<IsWireless>{_b(is_wireless)}</IsWireless>"
        f"<MACAddresString>{escape(mac)}</MACAddresString>"
        "</NetworkAdapter>"
    )


def _version_xml(version: str) -> str:
    parts = [int(x) for x in version.split(".")]
    while len(parts) < 4:
        parts.append(0)
    major, minor, build, revision = parts[:4]
    return (
        f'<StreamerVersion xmlns:a="{SYSTEM_NS}">'
        f"<a:_Build>{build}</a:_Build>"
        f"<a:_Major>{major}</a:_Major>"
        f"<a:_Minor>{minor}</a:_Minor>"
        f"<a:_Revision>{revision}</a:_Revision>"
        "</StreamerVersion>"
    )


def build_computer_xml(
    *,
    connection_id: bytes,
    key: bytes,
    iv: bytes,
    name: str = "Linux Test PC",
    description: str = "Virtual Desktop (Linux)",
    computer_id: str | None = None,
    streamer_version: str = "1.34.22.0",
    allow_remote_connections: bool = False,
    encrypt_local_traffic: bool = True,
    encrypt_remote_traffic: bool = True,
    os_name: str = "Windows",       # quest drops connection if not Windows or MacOS
    region: str = "AmericaWest",    # enum
    adapters: list[tuple[str, str, bool, bool]] | None = None,
) -> str:
    computer_id = computer_id or str(uuid.uuid4())

    if adapters:
        adapters_xml = "".join(_adapter_xml(*a) for a in adapters)
    else:
        adapters_xml = ""

    # tags must stay alphabetical for datacontractserializer
    body = (
        f"<AllowRemoteConnections>{_b(allow_remote_connections)}</AllowRemoteConnections>"
        f"<ConnectionID>{_bytes(connection_id)}</ConnectionID>"
        f"<Description>{escape(description)}</Description>"
        f"<EncryptLocalTraffic>{_b(encrypt_local_traffic)}</EncryptLocalTraffic>"
        f"<EncryptRemoteTraffic>{_b(encrypt_remote_traffic)}</EncryptRemoteTraffic>"
        f"<ID>{escape(computer_id)}</ID>"
        f"<IV>{_bytes(iv)}</IV>"
        f"<Key>{_bytes(key)}</Key>"
        f"<Name>{escape(name)}</Name>"
        f"<OS>{escape(os_name)}</OS>"
        f"<PrivateAdapters>{adapters_xml}</PrivateAdapters>"
        f"<Region>{escape(region)}</Region>"
        f"{_version_xml(streamer_version)}"
    )
    return f'<Computer xmlns="{NS}" xmlns:i="{XSI_NS}">{body}</Computer>'
