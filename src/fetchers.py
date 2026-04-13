from __future__ import annotations

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
    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self._timeout_seconds = timeout_seconds
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
        page = await browser.new_page()
        try:
            if headers:
                await page.set_extra_http_headers(headers)

            response = await page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=int(self._timeout_seconds * 1000),
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
            if exc.__class__.__name__ == "TimeoutError":
                raise httpx.TimeoutException(str(exc)) from exc
            raise
        finally:
            await page.close()

    async def aclose(self) -> None:
        if self._camoufox_cm is None:
            return
        await self._camoufox_cm.__aexit__(None, None, None)
        self._camoufox_cm = None
        self._browser = None
