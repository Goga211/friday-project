"""Реестр устройств: resolve по алиасу и персистентность (SQLite)."""

from datetime import timedelta
from pathlib import Path

from friday.core.registry import DeviceRegistry
from friday.shared.net import detect_mac, format_mac
from friday.shared.protocol import Capability, CapabilityManifest


def _manifest(
    device_id: str, alias: str | None = None, mac: str | None = None, online: bool = True
):
    return CapabilityManifest(
        device_id=device_id,
        platform="linux",
        alias=alias,
        mac=mac,
        online=online,
        capabilities=[Capability(name="ping", description="живость")],
    )


def test_resolve_by_id_and_alias_case_insensitive() -> None:
    reg = DeviceRegistry()
    reg.update(_manifest("d1", alias="Ноутбук"))
    assert reg.resolve("d1") is not None
    resolved = reg.resolve("ноутбук")
    assert resolved is not None
    assert resolved.manifest.device_id == "d1"
    assert reg.resolve("тостер") is None


def test_resolve_alias_collision_prefers_online() -> None:
    # Живой случай 2026-07-05: в персистентном реестре осталась запись со старой
    # машины с тем же алиасом «пк» — resolve возвращал её (офлайн) вместо живой.
    reg = DeviceRegistry()
    reg.update(_manifest("desktop-old", alias="пк", online=False))  # старая — первой
    reg.update(_manifest("desktop-new", alias="пк", online=True))
    resolved = reg.resolve("пк")
    assert resolved is not None
    assert resolved.manifest.device_id == "desktop-new"


def test_resolve_alias_collision_all_offline_prefers_freshest() -> None:
    reg = DeviceRegistry()
    reg.update(_manifest("desktop-old", alias="пк", online=False))
    reg.update(_manifest("desktop-new", alias="пк", online=False))
    # разводим last_seen явно: часы Windows могут дать одинаковые метки подряд
    old_record = reg.get("desktop-old")
    assert old_record is not None
    old_record.last_seen -= timedelta(minutes=5)
    resolved = reg.resolve("пк")
    assert resolved is not None
    assert resolved.manifest.device_id == "desktop-new"


def test_persistent_registry_survives_restart(tmp_path: Path) -> None:
    db = str(tmp_path / "test.db")
    reg = DeviceRegistry(db)
    reg.update(_manifest("pc", alias="пк", mac="AA:BB:CC:DD:EE:FF"))
    reg.close()

    # «Рестарт Hub'а»: устройство известно, но офлайн; alias и MAC на месте (для WoL).
    reborn = DeviceRegistry(db)
    record = reborn.resolve("пк")
    assert record is not None
    assert record.manifest.online is False
    assert record.manifest.mac == "AA:BB:CC:DD:EE:FF"
    reborn.close()


def test_persistent_registry_updates_overwrite(tmp_path: Path) -> None:
    db = str(tmp_path / "test.db")
    reg = DeviceRegistry(db)
    reg.update(_manifest("pc", alias="пк"))
    reg.update(_manifest("pc", alias="большой пк"))
    reg.close()

    reborn = DeviceRegistry(db)
    assert len(reborn.all()) == 1
    assert reborn.resolve("большой пк") is not None
    reborn.close()


def test_format_mac() -> None:
    assert format_mac(0xAABBCCDDEEFF) == "AA:BB:CC:DD:EE:FF"


def test_detect_mac_valid_or_none() -> None:
    # На реальной машине — либо честный MAC, либо None (рандом uuid.getnode отбрасываем).
    mac = detect_mac()
    if mac is not None:
        octets = mac.split(":")
        assert len(octets) == 6
        assert int(octets[0], 16) & 0x01 == 0  # не multicast
