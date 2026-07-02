"""Единая настройка логирования."""

from __future__ import annotations

import logging


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
    )
