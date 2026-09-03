"""
Golden regression: the Computer reply XML must stay byte-identical to the output
of the REAL DataContractSerializer(typeof(Computer)), captured by
reflection-loading the Quest APK's VirtualDesktop.Interfaces.dll on the net10
runtime (docs/PROTOCOL.md section 10).

If this drifts, the Quest's ComputerSerializer.ReadObject throws and the PC
silently vanishes from the computer list -- with no error anywhere.
"""
from __future__ import annotations

import pytest

from vdclient.protocol.computer import build_computer_xml

DOTNET_GROUND_TRUTH = (
    '<Computer xmlns="http://schemas.datacontract.org/2004/07/VirtualDesktop.Interfaces"'
    ' xmlns:i="http://www.w3.org/2001/XMLSchema-instance">'
    "<AllowRemoteConnections>false</AllowRemoteConnections>"
    "<ConnectionID>AQIDBA==</ConnectionID>"
    "<Description>Virtual Desktop (Linux)</Description>"
    "<EncryptLocalTraffic>true</EncryptLocalTraffic>"
    "<EncryptRemoteTraffic>true</EncryptRemoteTraffic>"
    "<ID>test-id-1234</ID>"
    "<IV>AAAAAAAAAAAAAAAAAAAAAA==</IV>"
    "<Key>AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=</Key>"
    "<Name>LINUX-TEST-PC</Name>"
    "<OS>Windows</OS>"
    "<PrivateAdapters><NetworkAdapter>"
    "<IPAddresString>192.168.137.55</IPAddresString>"
    "<IsGigabit>true</IsGigabit>"
    # IsUsb is not part of the original captured ground truth: it was added to
    # NetworkAdapter in streamer 1.34.22 (confirmed by decompiling the shipping
    # VirtualDesktop.Streamer.exe). Its position follows DCS alphabetical
    # ordering: IsGigabit < IsUsb < IsWireless.
    "<IsUsb>false</IsUsb>"
    "<IsWireless>false</IsWireless>"
    "<MACAddresString>AA:BB:CC:DD:EE:FF</MACAddresString>"
    "</NetworkAdapter></PrivateAdapters>"
    "<Region>AmericaWest</Region>"
    '<StreamerVersion xmlns:a="http://schemas.datacontract.org/2004/07/System">'
    "<a:_Build>5</a:_Build><a:_Major>1</a:_Major>"
    "<a:_Minor>32</a:_Minor><a:_Revision>0</a:_Revision>"
    "</StreamerVersion></Computer>"
)


def test_matches_dotnet_ground_truth():
    got = build_computer_xml(
        connection_id=bytes([1, 2, 3, 4]), key=bytes(32), iv=bytes(16),
        name="LINUX-TEST-PC", description="Virtual Desktop (Linux)",
        computer_id="test-id-1234", streamer_version="1.32.5.0",
        os_name="Windows", region="AmericaWest",
        adapters=[("192.168.137.55", "AA:BB:CC:DD:EE:FF", False, True)],
    )
    assert got == DOTNET_GROUND_TRUTH


def test_os_and_region_serialize_as_enum_names_not_integers():
    """The bug that kept the computer off the list for weeks: integers make the
    Quest's ReadObject throw and drop the computer silently."""
    xml = build_computer_xml(connection_id=bytes(16), key=bytes(32), iv=bytes(16),
                             computer_id="x")
    assert "<OS>Windows</OS>" in xml
    assert "<Region>AmericaWest</Region>" in xml


def test_members_are_alphabetical():
    xml = build_computer_xml(connection_id=bytes(16), key=bytes(32), iv=bytes(16),
                             computer_id="x")
    order = ["<AllowRemoteConnections>", "<ConnectionID>", "<Description>",
             "<EncryptLocalTraffic>", "<EncryptRemoteTraffic>", "<ID>", "<IV>",
             "<Key>", "<Name>", "<OS>", "<PrivateAdapters>", "<Region>",
             "<StreamerVersion"]
    positions = [xml.index(tag) for tag in order]
    assert positions == sorted(positions)


def test_version_nested_shape():
    xml = build_computer_xml(connection_id=bytes(16), key=bytes(32), iv=bytes(16),
                             computer_id="x", streamer_version="1.34.22.0")
    assert "<a:_Build>22</a:_Build>" in xml
    assert "<a:_Major>1</a:_Major>" in xml
    assert "<a:_Minor>34</a:_Minor>" in xml
    assert "<a:_Revision>0</a:_Revision>" in xml


def test_short_version_pads_to_four_parts():
    xml = build_computer_xml(connection_id=bytes(16), key=bytes(32), iv=bytes(16),
                             computer_id="x", streamer_version="2.1")
    assert "<a:_Build>0</a:_Build>" in xml
    assert "<a:_Revision>0</a:_Revision>" in xml


def test_empty_adapters_is_an_empty_but_present_element():
    xml = build_computer_xml(connection_id=bytes(16), key=bytes(32), iv=bytes(16),
                             computer_id="x", adapters=None)
    assert "<PrivateAdapters></PrivateAdapters>" in xml


@pytest.mark.parametrize("name,escaped", [("A & B", "A &amp; B"), ("<x>", "&lt;x&gt;")])
def test_names_are_xml_escaped(name, escaped):
    xml = build_computer_xml(connection_id=bytes(16), key=bytes(32), iv=bytes(16),
                             computer_id="x", name=name)
    assert f"<Name>{escaped}</Name>" in xml
