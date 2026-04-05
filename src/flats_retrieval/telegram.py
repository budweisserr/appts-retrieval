from __future__ import annotations

import html
import json

import httpx

from flats_retrieval.models import FlatListing


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        *,
        read_timeout_seconds: int,
        max_photos_per_message: int,
    ) -> None:
        self._max_photos_per_message = max_photos_per_message
        self._client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{bot_token}/",
            timeout=httpx.Timeout(connect=10.0, read=float(read_timeout_seconds), write=30.0, pool=30.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_updates(self, *, offset: int, timeout: int) -> list[dict]:
        response = await self._client.get(
            "getUpdates",
            params={"offset": offset, "timeout": timeout},
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("result", [])

    async def send_message(self, chat_id: str, text: str) -> None:
        response = await self._client.post(
            "sendMessage",
            data={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )
        response.raise_for_status()

    async def send_listing(self, chat_id: str, listing: FlatListing) -> None:
        caption = self._build_caption(listing)
        photos = listing.photos[: self._max_photos_per_message]

        if photos:
            media = []
            for index, photo_url in enumerate(photos):
                item = {"type": "photo", "media": photo_url}
                if index == 0:
                    item["caption"] = caption
                    item["parse_mode"] = "HTML"
                media.append(item)

            response = await self._client.post(
                "sendMediaGroup",
                data={
                    "chat_id": chat_id,
                    "media": json.dumps(media, ensure_ascii=False),
                },
            )
            if response.is_success:
                return

        response = await self._client.post(
            "sendMessage",
            data={
                "chat_id": chat_id,
                "text": caption,
                "parse_mode": "HTML",
                "disable_web_page_preview": "false",
            },
        )
        response.raise_for_status()

    def _build_caption(self, listing: FlatListing) -> str:
        lines = [
            f"<b>{html.escape(listing.title)}</b>",
            f"<b>Cena:</b> {html.escape(listing.price or 'brak')}",
        ]

        if listing.location:
            lines.append(f"<b>Lokalizacja:</b> {html.escape(listing.location)}")

        for key, value in listing.details.items():
            if value:
                lines.append(f"<b>{html.escape(key)}:</b> {html.escape(value)}")

        if listing.description:
            lines.append("")
            lines.append(html.escape(_truncate(listing.description, 900)))

        lines.append("")
        lines.append(f'<a href="{html.escape(listing.link, quote=True)}">Otwórz ogłoszenie</a>')
        return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
