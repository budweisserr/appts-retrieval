from __future__ import annotations

import asyncio
import logging

import httpx

from config import Settings
from messages import saved_message, start_message, status_message
from models import FlatListing, SearchConfig
from scrapers import FlatScraper, get_scraper_for_url
from storage import SeenStorage
from telegram import TelegramClient
from urls import extract_supported_urls, source_from_url, validate_search_urls

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
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=10.0, read=30.0, write=30.0, pool=30.0),
                follow_redirects=True,
            ) as client:
                await asyncio.gather(
                    self._poll_searches_loop(client),
                    self._poll_telegram_updates_loop(),
                )
        finally:
            await self._telegram.close()
            self._storage.close()

    async def _poll_searches_loop(self, client: httpx.AsyncClient) -> None:
        while True:
            await self._poll_once(client)
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
            await self._telegram.send_message(chat_id, start_message())
            return

        if text.startswith("/status"):
            await self._telegram.send_message(chat_id, status_message(self._storage.list_searches(), chat_id))
            return

        if text.startswith("/clear"):
            self._storage.replace_searches(chat_id, [])
            await self._telegram.send_message(chat_id, "Send links to parse.")
            return

        urls = extract_supported_urls(text)
        if not urls:
            await self._telegram.send_message(chat_id, "Send 1 or 2 links (OLX/Otodom PL).")
            return

        validation_error = validate_search_urls(urls)
        if validation_error:
            await self._telegram.send_message(chat_id, validation_error)
            return

        searches = [
            SearchConfig(chat_id=chat_id, url=url,
                         source=source_from_url(url), is_seeded=False)
            for url in urls
        ]
        self._storage.replace_searches(chat_id, searches)
        await self._telegram.send_message(chat_id, saved_message(searches))

    async def _poll_once(self, client: httpx.AsyncClient) -> None:
        searches = self._storage.list_searches()
        if not searches:
            return

        semaphore = asyncio.Semaphore(self._settings.max_parallel_searches)

        async def run_search(search: SearchConfig) -> None:
            async with semaphore:
                scraper = get_scraper_for_url(search.url)
                try:
                    await self._handle_search(client, scraper, search)
                except Exception:
                    LOGGER.exception(
                        "Search failed for chat=%s url=%s", search.chat_id, search.url)

        await asyncio.gather(*(run_search(search) for search in searches))

    async def _process_listing(
        self,
        client: httpx.AsyncClient,
        scraper: FlatScraper,
        chat_id: str,
        listing: FlatListing,
    ) -> str:
        try:
            enriched = await scraper.enrich_listing(client, listing)
            await self._telegram.send_listing(chat_id, enriched)
            self._remember_listing(chat_id, enriched)
            return "posted"
        except httpx.TimeoutException:
            LOGGER.warning("Timed out loading listing %s", listing.link)
            return "timeout"
        except Exception:
            LOGGER.exception("Failed to process listing %s", listing.link)
            return "error"

    async def _process_listings_fast(
        self,
        client: httpx.AsyncClient,
        scraper: FlatScraper,
        chat_id: str,
        fresh: list[FlatListing],
    ) -> tuple[int, int, int]:
        semaphore = asyncio.Semaphore(self._settings.max_parallel_enrichments)

        async def run_one(listing: FlatListing) -> str:
            async with semaphore:
                return await self._process_listing(client, scraper, chat_id, listing)

        outcomes = await asyncio.gather(*(run_one(listing) for listing in fresh))
        posted = outcomes.count("posted")
        timeouts = outcomes.count("timeout")
        errors = outcomes.count("error")
        return posted, timeouts, errors

    async def _handle_search(
        self,
        client: httpx.AsyncClient,
        scraper: FlatScraper,
        search: SearchConfig,
    ) -> None:
        listings = await scraper.fetch_search_results(
            client,
            search,
            self._settings.max_listings_per_search,
        )

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

        if self._settings.max_new_listings_per_cycle > 0 and len(fresh) > self._settings.max_new_listings_per_cycle:
            LOGGER.info(
                "Limiting processing to %s newest listings for chat=%s url=%s",
                self._settings.max_new_listings_per_cycle,
                search.chat_id,
                search.url,
            )
            fresh = fresh[: self._settings.max_new_listings_per_cycle]

        LOGGER.info("Found %s unseen flats for chat=%s url=%s",
                    len(fresh), search.chat_id, search.url)
        posted_count, timeout_count, error_count = await self._process_listings_fast(
            client,
            scraper,
            search.chat_id,
            fresh,
        )
        LOGGER.info(
            "Search summary chat=%s url=%s: posted=%s timeouts=%s errors=%s",
            search.chat_id,
            search.url,
            posted_count,
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
