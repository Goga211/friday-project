"""Wake-on-LAN: магический пакет чистым stdlib (socket, UDP broadcast)."""

from __future__ import annotations

import re
import socket

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$")
_REPEATS = 16  # по спецификации WoL: 6 байт 0xFF + MAC 16 раз


def parse_mac(mac: str) -> bytes:
    """MAC-строка (AA:BB:… или AA-BB-…) → 6 байт. ValueError на мусоре."""
    if not _MAC_RE.match(mac.strip()):
        raise ValueError(f"некорректный MAC-адрес: {mac!r}")
    return bytes.fromhex(mac.strip().replace(":", "").replace("-", ""))


def magic_packet(mac: str) -> bytes:
    """Собрать магический пакет: 6×0xFF + MAC × 16."""
    return b"\xff" * 6 + parse_mac(mac) * _REPEATS


def send_magic_packet(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    """Отправить магический пакет UDP-бродкастом (синхронно; один пакет — мгновенно)."""
    packet = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))
