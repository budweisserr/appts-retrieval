from __future__ import annotations

import asyncio
import logging

from config import load_settings
from service import FlatMonitorService


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    asyncio.run(FlatMonitorService(settings).run())
