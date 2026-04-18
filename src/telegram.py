from __future__ import annotations

import html
import json

import httpx

from models import FlatListing


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
            timeout=httpx.Timeout(connect=10.0, read=float(
                read_timeout_seconds), write=30.0, pool=30.0),
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
        photos = self._limit_photos(listing.photos)

        if photos:
            photo_chunks = [photos[index: index + 10]
                            for index in range(0, len(photos), 10)]

            for chunk_index, chunk in enumerate(photo_chunks):
                media = []
                for photo_index, photo_url in enumerate(chunk):
                    item = {"type": "photo", "media": photo_url}
                    if chunk_index == 0 and photo_index == 0:
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
                if not response.is_success:
                    break
            else:
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
        escaped_link = html.escape(listing.link, quote=True)
        lines = [
            f"<b>{html.escape(listing.title)}</b>",
            f"<b>Price:</b> {html.escape(listing.price or 'brak')}",
        ]

        if listing.location:
            lines.append(
                f"<b>Location:</b> {html.escape(listing.location)}")

        for key, value in listing.details.items():
            if value:
                lines.append(
                    f"<b>{html.escape(key)}:</b> {html.escape(value)}")

        lines.append(f'<a href="{escaped_link}">Link</a>')
        return "\n".join(lines)

    def _limit_photos(self, photos: list[str]) -> list[str]:
        if self._max_photos_per_message <= 0:
            return photos
        return photos[: self._max_photos_per_message]
