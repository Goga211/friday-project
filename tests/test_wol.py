"""Wake-on-LAN: формат магического пакета и реальная отправка через loopback."""

import socket

import pytest

from friday.shared.wol import magic_packet, parse_mac, send_magic_packet

MAC = "AA:BB:CC:DD:EE:FF"
MAC_BYTES = bytes.fromhex("AABBCCDDEEFF")


def test_parse_mac_colon_and_dash() -> None:
    assert parse_mac(MAC) == MAC_BYTES
    assert parse_mac("aa-bb-cc-dd-ee-ff") == MAC_BYTES


@pytest.mark.parametrize("bad", ["", "AABBCCDDEEFF", "AA:BB:CC:DD:EE", "GG:BB:CC:DD:EE:FF"])
def test_parse_mac_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_mac(bad)


def test_magic_packet_format() -> None:
    packet = magic_packet(MAC)
    assert len(packet) == 6 + 6 * 16
    assert packet[:6] == b"\xff" * 6
    assert packet[6:] == MAC_BYTES * 16


def test_send_magic_packet_over_loopback() -> None:
    # Реальная отправка UDP, но в 127.0.0.1 — без выхода в сеть.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(2.0)
        _, port = receiver.getsockname()
        send_magic_packet(MAC, broadcast="127.0.0.1", port=port)
        data, _addr = receiver.recvfrom(1024)
    assert data == magic_packet(MAC)
