"""Сетевые утилиты: определение MAC-адреса машины (для Wake-on-LAN)."""

from __future__ import annotations

import uuid


def format_mac(node: int) -> str:
    """48-битное число → каноничный вид AA:BB:CC:DD:EE:FF."""
    raw = f"{node:012x}"
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2)).upper()


def detect_mac() -> str | None:
    """MAC сетевой карты этой машины или None, если определить честно не удалось.

    uuid.getnode() при недоступности MAC возвращает случайное число с выставленным
    multicast-битом (RFC 4122) — такое значение отбрасываем, чтобы не объявить в
    манифесте мусор, по которому WoL никогда не сработает.
    """
    node = uuid.getnode()
    if node >> 40 & 0x01:  # multicast-бит первого октета = случайное значение
        return None
    return format_mac(node)
