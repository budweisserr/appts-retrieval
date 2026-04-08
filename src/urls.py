from __future__ import annotations

import re
from urllib.parse import urlparse


SUPPORTED_NETLOCS = {"www.olx.pl", "olx.pl", "www.otodom.pl", "otodom.pl"}


def extract_supported_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in re.findall(r"https?://\S+", text):
        candidate = match.rstrip(").,;]")
        netloc = urlparse(candidate).netloc.lower()
        if netloc in SUPPORTED_NETLOCS and candidate not in urls:
            urls.append(candidate)
    return urls


def source_from_url(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    if "olx.pl" in netloc:
        return "olx"
    if "otodom.pl" in netloc:
        return "otodom"
    raise ValueError(f"Unsupported URL: {url}")


def validate_search_urls(urls: list[str]) -> str | None:
    if len(urls) != 2:
        return "Send one OLX link and one Otodom link."

    sources = [source_from_url(url) for url in urls]
    if sources.count("olx") != 1 or sources.count("otodom") != 1:
        return "Send one OLX link and one Otodom link."

    return None
