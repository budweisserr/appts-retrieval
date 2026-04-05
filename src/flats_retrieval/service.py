from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta
from urllib.parse import urlparse

from camoufox.async_api import AsyncCamoufox
import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from flats_retrieval.config import Settings
from flats_retrieval.models import FlatListing, SearchConfig
from flats_retrieval.scrapers import FlatScraper, get_scraper_for_url
from flats_retrieval.storage import SeenStorage
from flats_retrieval.telegram import TelegramClient

LOGGER = logging.getLogger(__name__)


class FlatMonitorService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._storage = SeenStorage(settings.state_db_path)
        self._telegram = TelegramClient(
            settings.telegram_bot_token,
            read_timeout_seconds=settings.telegram_read_timeout_seconds,
            max_photos_per_message=settings.max_photos_per_message,
        )

    async def run(self) -> None:
        try:
            async with AsyncCamoufox(
                headless="virtual" if self._settings.headless else False,
                humanize=1.2 if self._settings.humanize else False,
                block_webrtc=True,
                block_images=self._settings.block_images,
                locale=["pl-PL", "pl"],
                os=["windows", "macos", "linux"],
            ) as browser:
                await asyncio.gather(
                    self._poll_searches_loop(browser),
                    self._poll_telegram_updates_loop(),
                )
        finally:
            await self._telegram.close()
            self._storage.close()

    async def _poll_searches_loop(self, browser) -> None:
        while True:
            await self._poll_once(browser)
            await asyncio.sleep(self._settings.poll_interval_seconds)

    async def _poll_telegram_updates_loop(self) -> None:
        offset = self._storage.get_offset()
        while True:
            try:
                updates = await self._telegram.get_updates(
                    offset=offset,
                    timeout=self._settings.telegram_poll_timeout_seconds,
                )
            except httpx.ReadTimeout:
                continue
            except Exception:
                LOGGER.exception("Failed to fetch Telegram updates")
                await asyncio.sleep(2)
                continue

            for update in updates:
                offset = max(offset, update["update_id"] + 1)
                self._storage.set_offset(offset)
                try:
                    await self._handle_update(update)
                except Exception:
                    LOGGER.exception(
                        "Failed to process Telegram update %s", update.get("update_id"))

    async def _handle_update(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return

        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", "")).strip()
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            return

        if text.startswith("/start"):
            await self._telegram.send_message(chat_id, _start_message())
            return

        if text.startswith("/status"):
            await self._telegram.send_message(chat_id, _status_message(self._storage.list_searches(), chat_id))
            return

        if text.startswith("/clear"):
            self._storage.replace_searches(chat_id, [])
            await self._telegram.send_message(chat_id, "Send links to parse.")
            return

        urls = _extract_supported_urls(text)
        if not urls:
            await self._telegram.send_message(chat_id, "Send 1 or 2 links (OLX/Otodom PL).")
            return

        validation_error = _validate_search_urls(urls)
        if validation_error:
            await self._telegram.send_message(chat_id, validation_error)
            return

        searches = [
            SearchConfig(chat_id=chat_id, url=url,
                         source=_source_from_url(url), is_seeded=False)
            for url in urls
        ]
        self._storage.replace_searches(chat_id, searches)
        await self._telegram.send_message(chat_id, _saved_message(searches))

    async def _poll_once(self, browser) -> None:
        for search in self._storage.list_searches():
            scraper = get_scraper_for_url(search.url)
            try:
                await self._handle_search(browser, scraper, search)
            except Exception:
                LOGGER.exception(
                    "Search failed for chat=%s url=%s", search.chat_id, search.url)

    async def _handle_search(self, browser, scraper: FlatScraper, search: SearchConfig) -> None:
        page = await browser.new_page()
        try:
            listings = await scraper.fetch_search_results(page, search, self._settings.max_listings_per_search)
        finally:
            await page.close()

        if self._settings.seed_existing_on_start and not search.is_seeded:
            for listing in listings:
                self._remember_listing(search.chat_id, listing)
            self._storage.mark_seeded(search.chat_id, search.url)
            LOGGER.info("Seeded chat=%s url=%s with %s existing listings",
                        search.chat_id, search.url, len(listings))
            return

        fresh = [listing for listing in listings if not self._storage.has(
            search.chat_id, listing.dedupe_key)]
        if not fresh:
            LOGGER.info("No new flats for chat=%s url=%s",
                        search.chat_id, search.url)
            return

        if len(fresh) > self._settings.max_new_listings_per_cycle:
            LOGGER.info(
                "Limiting processing to %s newest listings for chat=%s url=%s",
                self._settings.max_new_listings_per_cycle,
                search.chat_id,
                search.url,
            )
            fresh = fresh[: self._settings.max_new_listings_per_cycle]

        LOGGER.info("Found %s unseen flats for chat=%s url=%s",
                    len(fresh), search.chat_id, search.url)
        detail_page = await browser.new_page()
        posted_count = 0
        skipped_old_count = 0
        timeout_count = 0
        error_count = 0
        try:
            for listing in fresh:
                try:
                    enriched = await scraper.enrich_listing(detail_page, listing)
                    if not _is_recent_listing(enriched):
                        self._remember_listing(search.chat_id, enriched)
                        skipped_old_count += 1
                        LOGGER.info(
                            "Skipping old listing %s (published_at=%s)",
                            enriched.link,
                            enriched.published_at,
                        )
                        continue
                    await self._telegram.send_listing(search.chat_id, enriched)
                    self._remember_listing(search.chat_id, enriched)
                    posted_count += 1
                except PlaywrightTimeoutError:
                    timeout_count += 1
                    LOGGER.warning("Timed out loading listing %s", listing.link)
                except Exception:
                    error_count += 1
                    LOGGER.exception("Failed to process listing %s", listing.link)
        finally:
            await detail_page.close()

        LOGGER.info(
            "Search summary chat=%s url=%s: posted=%s skipped_old=%s timeouts=%s errors=%s",
            search.chat_id,
            search.url,
            posted_count,
            skipped_old_count,
            timeout_count,
            error_count,
        )

    def _remember_listing(self, chat_id: str, listing: FlatListing) -> None:
        self._storage.add(
            chat_id,
            listing.dedupe_key,
            listing.source,
            listing.external_id,
            listing.title,
            listing.link,
        )


def _extract_supported_urls(text: str) -> list[str]:
    urls = []
    for match in re.findall(r"https?://\S+", text):
        candidate = match.rstrip(").,;]")
        netloc = urlparse(candidate).netloc.lower()
        if netloc in {"www.olx.pl", "olx.pl", "www.otodom.pl", "otodom.pl"} and candidate not in urls:
            urls.append(candidate)
    return urls


def _validate_search_urls(urls: list[str]) -> str | None:
    if len(urls) != 2:
        return "Send one OLX link and one Otodom link."

    sources = [_source_from_url(url) for url in urls]
    if sources.count("olx") != 1 or sources.count("otodom") != 1:
        return "Send one OLX link and one Otodom link."

    return None


def _source_from_url(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    if "olx.pl" in netloc:
        return "olx"
    if "otodom.pl" in netloc:
        return "otodom"
    raise ValueError(f"Unsupported URL: {url}")


def _start_message() -> str:
    return (
        "2 links: <b>OLX</b> and <b>Otodom</b>.\n\n"
        "Example:\n"
        "https://www.olx.pl/...\n"
        "https://www.otodom.pl/...\n\n"
        "Commands:\n"
        "/status - current link status\n"
        "/clear - delete current links"
    )


def _saved_message(searches: list[SearchConfig]) -> str:
    lines = ["Link saved:", ""]
    for search in searches:
        lines.append(f"- {search.source.upper()}: {search.url}")
    return "\n".join(lines)


def _status_message(searches: list[SearchConfig], chat_id: str) -> str:
    active = [search for search in searches if search.chat_id == chat_id]
    if not active:
        return "No active links."
    lines = ["Active links:", ""]
    for search in active:
        lines.append(f"- {search.source.upper()}: {search.url}")
    return "\n".join(lines)


def _is_recent_listing(listing: FlatListing) -> bool:
    if listing.published_at is None:
        return False
    return listing.published_at >= date.today() - timedelta(days=1)
