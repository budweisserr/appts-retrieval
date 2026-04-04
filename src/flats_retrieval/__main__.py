from __future__ import annotations

import asyncio
import logging

from flats_retrieval.config import load_settings
from flats_retrieval.service import FlatMonitorService


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    asyncio.run(FlatMonitorService(settings).run())


if __name__ == "__main__":
    main()
