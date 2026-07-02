"""Реестр устройств: device_id → манифест возможностей + статус online/offline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from friday.shared.protocol import CapabilityManifest


@dataclass
class DeviceRecord:
    manifest: CapabilityManifest
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))


class DeviceRegistry:
    def __init__(self) -> None:
        self._devices: dict[str, DeviceRecord] = {}

    def update(self, manifest: CapabilityManifest) -> None:
        self._devices[manifest.device_id] = DeviceRecord(manifest=manifest)

    def get(self, device_id: str) -> DeviceRecord | None:
        return self._devices.get(device_id)

    def online_devices(self) -> list[str]:
        return [d for d, rec in self._devices.items() if rec.manifest.online]

    def all(self) -> dict[str, DeviceRecord]:
        return dict(self._devices)
