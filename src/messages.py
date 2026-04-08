from __future__ import annotations

from models import SearchConfig


def start_message() -> str:
    return (
        "2 links: <b>OLX</b> and <b>Otodom</b>.\n\n"
        "Example:\n"
        "https://www.olx.pl/...\n"
        "https://www.otodom.pl/...\n\n"
        "Commands:\n"
        "/status - current link status\n"
        "/clear - delete current links"
    )


def saved_message(searches: list[SearchConfig]) -> str:
    lines = ["Link saved:", ""]
    for search in searches:
        lines.append(f"- {search.source.upper()}: {search.url}")
    return "\n".join(lines)


def status_message(searches: list[SearchConfig], chat_id: str) -> str:
    active = [search for search in searches if search.chat_id == chat_id]
    if not active:
        return "No active links."
    lines = ["Active links:", ""]
    for search in active:
        lines.append(f"- {search.source.upper()}: {search.url}")
    return "\n".join(lines)
