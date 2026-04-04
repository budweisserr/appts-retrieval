from __future__ import annotations

from datetime import date
from dataclasses import dataclass, field


@dataclass(slots=True)
class SearchConfig:
    chat_id: str
    url: str
    source: str
    is_seeded: bool = False


@dataclass(slots=True)
class FlatListing:
    source: str
    external_id: str
    title: str
    price: str
    link: str
    location: str = ""
    description: str = ""
    published_at: date | None = None
    photos: list[str] = field(default_factory=list)
    details: dict[str, str] = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        return f"{self.source}:{self.external_id}"
