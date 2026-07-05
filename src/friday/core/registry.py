"""Реестр устройств: device_id → манифест возможностей + статус online/offline.

Опционально персистентный (SQLite): известные устройства переживают рестарт Core и
поднимаются offline-записями — alias и MAC сохраняются, выключенный ПК можно разбудить
по Wake-on-LAN даже после перезагрузки Hub'а.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from friday.shared.protocol import CapabilityManifest


@dataclass
class DeviceRecord:
    manifest: CapabilityManifest
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))


class DeviceRegistry:
    def __init__(self, db_path: str | None = None) -> None:
        self._devices: dict[str, DeviceRecord] = {}
        self._conn: sqlite3.Connection | None = None
        if db_path is not None:
            self._conn = sqlite3.connect(db_path)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS devices(
                    device_id TEXT PRIMARY KEY,
                    manifest  TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                )
                """)
            self._conn.commit()
            self._load()

    def _load(self) -> None:
        """Поднять известные устройства как offline: живой статус объявят retained-манифесты."""
        assert self._conn is not None
        for raw, last_seen in self._conn.execute("SELECT manifest, last_seen FROM devices"):
            manifest = CapabilityManifest.model_validate_json(raw)
            manifest = manifest.model_copy(update={"online": False})
            self._devices[manifest.device_id] = DeviceRecord(
                manifest=manifest, last_seen=datetime.fromisoformat(last_seen)
            )

    def update(self, manifest: CapabilityManifest) -> None:
        record = DeviceRecord(manifest=manifest)
        self._devices[manifest.device_id] = record
        if self._conn is not None:
            self._conn.execute(
                "INSERT OR REPLACE INTO devices(device_id, manifest, last_seen) VALUES(?,?,?)",
                (manifest.device_id, manifest.model_dump_json(), record.last_seen.isoformat()),
            )
            self._conn.commit()

    def get(self, device_id: str) -> DeviceRecord | None:
        return self._devices.get(device_id)

    def resolve(self, name: str) -> DeviceRecord | None:
        """Найти устройство по device_id или алиасу (без учёта регистра).

        При коллизии алиасов (например, устаревшая запись со старой машины в
        персистентном реестре) предпочитаем online-устройство, затем самое
        свежее по last_seen — иначе живой ПК выглядел бы «офлайн».
        """
        record = self._devices.get(name)
        if record is not None:
            return record
        wanted = name.strip().casefold()
        matches = [
            rec
            for rec in self._devices.values()
            if rec.manifest.alias is not None and rec.manifest.alias.casefold() == wanted
        ]
        if not matches:
            return None
        return max(matches, key=lambda rec: (rec.manifest.online, rec.last_seen))

    def online_devices(self) -> list[str]:
        return [d for d, rec in self._devices.items() if rec.manifest.online]

    def all(self) -> dict[str, DeviceRecord]:
        return dict(self._devices)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
