from __future__ import annotations

import asyncio
import httpx
from importlib import import_module
from typing import Protocol


class SearchClient(Protocol):
    async def get(self, url: str | httpx.URL, headers: dict[str, str] | None = None) -> httpx.Response:
        ...

    async def aclose(self) -> None:
        ...


class HttpxSearchClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0),
            follow_redirects=True,
        )

    async def get(self, url: str | httpx.URL, headers: dict[str, str] | None = None) -> httpx.Response:
        return await self._client.get(url, headers=headers)

    async def aclose(self) -> None:
        await self._client.aclose()


class CamoufoxSearchClient:
    def __init__(self, timeout_seconds: float = 30.0, max_attempts: int = 3) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max(1, max_attempts)
        self._camoufox_cm = None
        self._browser = None

    async def _ensure_browser(self):
        if self._browser is not None:
            return self._browser

        try:
            camoufox_module = import_module("camoufox.async_api")
            async_camoufox = getattr(camoufox_module, "AsyncCamoufox")
        except ImportError as exc:
            raise RuntimeError(
                "CAMOUFOX_ENABLE=1 but camoufox is not installed. Run: pdm add camoufox"
            ) from exc

        self._camoufox_cm = async_camoufox(headless=True)
        self._browser = await self._camoufox_cm.__aenter__()
        return self._browser

    async def get(self, url: str | httpx.URL, headers: dict[str, str] | None = None) -> httpx.Response:
        target_url = str(url)
        browser = await self._ensure_browser()
        wait_strategies = ["domcontentloaded", "load", "commit"]
        timeout_ms = int(self._timeout_seconds * 1000)
        last_error: Exception | None = None

        for attempt in range(self._max_attempts):
            page = await browser.new_page()
            try:
                if headers:
                    await page.set_extra_http_headers(headers)

                response = await page.goto(
                    target_url,
                    wait_until=wait_strategies[min(attempt, len(wait_strategies) - 1)],
                    timeout=timeout_ms,
                )
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

                html = await page.content()
                status_code = 200
                final_url = page.url or target_url
                if response is not None:
                    status_code = response.status
                    final_url = response.url

                request = httpx.Request("GET", final_url, headers=headers)
                return httpx.Response(status_code=status_code, request=request, text=html)
            except Exception as exc:
                if not _is_playwright_timeout(exc):
                    raise
                last_error = exc
                if attempt < self._max_attempts - 1:
                    await asyncio.sleep(0.5)
                    continue
            finally:
                await page.close()

        raise httpx.TimeoutException(str(last_error)) from last_error

    async def aclose(self) -> None:
        if self._camoufox_cm is None:
            return
        await self._camoufox_cm.__aexit__(None, None, None)
        self._camoufox_cm = None
        self._browser = None


def _is_playwright_timeout(exc: Exception) -> bool:
    return exc.__class__.__name__ == "TimeoutError"
