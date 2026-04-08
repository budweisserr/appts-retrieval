from __future__ import annotations

import asyncio
import json
import re
from collections import OrderedDict
from datetime import date, timedelta
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag
import httpx

from models import FlatListing, SearchConfig

OLX_BASE_URL = "https://www.olx.pl"
OTODOM_BASE_URL = "https://www.otodom.pl"


class FlatScraper:
    def supports(self, url: str) -> bool:
        raise NotImplementedError

    async def fetch_search_results(
        self,
        client: httpx.AsyncClient,
        search: SearchConfig,
        limit: int,
    ) -> list[FlatListing]:
        raise NotImplementedError

    async def enrich_listing(self, client: httpx.AsyncClient, listing: FlatListing) -> FlatListing:
        raise NotImplementedError


class OlxScraper(FlatScraper):
    def supports(self, url: str) -> bool:
        return "olx.pl" in urlparse(url).netloc

    async def fetch_search_results(
        self,
        client: httpx.AsyncClient,
        search: SearchConfig,
        limit: int,
    ) -> list[FlatListing]:
        html = await _fetch_html(client, search.url)
        soup = BeautifulSoup(html, "html.parser")
        listings: list[FlatListing] = []
        seen_links: set[str] = set()

        for link, title in _extract_olx_search_candidates(soup, html):
            canonical = _canonicalize_listing_url(link)
            if urlparse(canonical).netloc not in {"www.olx.pl", "olx.pl"}:
                continue
            if canonical in seen_links:
                continue

            external_id = _extract_id_from_url(
                canonical) or _extract_olx_external_id(canonical)
            if not external_id or not title:
                continue

            listings.append(
                FlatListing(
                    source="olx",
                    external_id=external_id,
                    title=title,
                    price="",
                    link=canonical,
                )
            )
            seen_links.add(canonical)
            if _should_stop(listings, limit):
                break

        return listings

    async def enrich_listing(self, client: httpx.AsyncClient, listing: FlatListing) -> FlatListing:
        html = await _fetch_html(client, listing.link)
        soup = BeautifulSoup(html, "html.parser")

        czynsz = _extract_olx_czynsz(soup)
        listing.price = _join_price_parts(
            _extract_olx_base_price(soup, listing.title), czynsz)
        listing.location = _extract_olx_location(soup)
        listing.description = _extract_section_text(
            soup, "Opis") or _extract_meta_description(soup)
        listing.published_at = _extract_olx_published_at(soup)
        listing.photos = _extract_photo_urls(soup, listing.title)
        listing.details = OrderedDict(
            [
                ("Powierzchnia", _extract_label_value(soup, "Powierzchnia")),
                ("Pokoje", _extract_label_value(soup, "Liczba pokoi")),
                ("Piętro", _extract_label_value(soup, "Poziom")),
            ]
        )
        listing.details = {key: value for key,
                           value in listing.details.items() if value}
        return listing


class OtodomScraper(FlatScraper):
    def supports(self, url: str) -> bool:
        return "otodom.pl" in urlparse(url).netloc

    async def fetch_search_results(
        self,
        client: httpx.AsyncClient,
        search: SearchConfig,
        limit: int,
    ) -> list[FlatListing]:
        html = await _fetch_html(client, search.url)
        soup = BeautifulSoup(html, "html.parser")
        listings: list[FlatListing] = []
        seen_links: set[str] = set()

        for link, title in _extract_otodom_search_candidates(soup, html):
            canonical = _canonicalize_listing_url(link)
            if canonical in seen_links:
                continue

            external_id = _extract_id_from_url(canonical)
            if not external_id or not title:
                continue

            listings.append(
                FlatListing(
                    source="otodom",
                    external_id=external_id,
                    title=title,
                    price="",
                    link=canonical,
                )
            )
            seen_links.add(canonical)
            if _should_stop(listings, limit):
                break

        return listings

    async def enrich_listing(self, client: httpx.AsyncClient, listing: FlatListing) -> FlatListing:
        html = await _fetch_html(client, listing.link)
        soup = BeautifulSoup(html, "html.parser")

        listing.title = _clean(soup.find("h1").get_text(
            " ", strip=True)) if soup.find("h1") else listing.title
        czynsz = _extract_otodom_czynsz(soup)
        listing.price = _join_price_parts(
            _extract_otodom_base_price(soup), czynsz)
        listing.location = _extract_otodom_location(soup)
        listing.description = _extract_section_text(
            soup, "Opis") or _extract_meta_description(soup)
        listing.published_at = _extract_otodom_published_at(soup)
        listing.photos = _extract_photo_urls(soup, listing.title)
        listing.details = OrderedDict(
            [
                ("Powierzchnia", _extract_text_after_label(soup, "Powierzchnia:")),
                ("Pokoje", _extract_text_after_label(soup, "Liczba pokoi:")),
                ("Piętro", _extract_text_after_label(soup, "Piętro:")),
                ("Dostępne od", _extract_text_after_label(soup, "Dostępne od:")),
            ]
        )
        listing.details = {key: value for key,
                           value in listing.details.items() if value}
        return listing


def get_scraper_for_url(url: str) -> FlatScraper:
    for scraper in (OlxScraper(), OtodomScraper()):
        if scraper.supports(url):
            return scraper
    raise ValueError(f"Unsupported search URL: {url}")


async def _fetch_html(client: httpx.AsyncClient, url: str) -> str:
    for attempt in range(2):
        try:
            response = await client.get(url, headers=_default_headers())
            response.raise_for_status()
            return response.text
        except httpx.TimeoutException:
            if attempt == 0:
                await asyncio.sleep(1)
                continue
            raise
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if attempt == 0 and status in {429, 500, 502, 503, 504}:
                await asyncio.sleep(1)
                continue
            raise
    raise RuntimeError(f"Failed to fetch {url}")


def _default_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
        ),
        "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def _should_stop(listings: list[FlatListing], limit: int) -> bool:
    return limit > 0 and len(listings) >= limit


def _extract_id_from_url(url: str) -> str:
    match = re.search(r"-(ID[\w]+)(?:\.html)?", url)
    return match.group(1) if match else ""


def _canonicalize_listing_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _extract_olx_search_candidates(soup: BeautifulSoup, html: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not isinstance(script, Tag):
            continue
        raw = script.string or script.get_text("", strip=True)
        if not raw:
            continue
        for item in _iter_json_ld_items(raw):
            if not isinstance(item, dict):
                continue
            url = _clean(str(item.get("url", "")))
            title = _clean(str(item.get("name", "")))
            if "/d/oferta/" in url and title:
                candidates.append((urljoin(OLX_BASE_URL, url), title))

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", ""))
        if "/d/oferta/" not in href:
            continue
        title = _clean(anchor.get_text(" ", strip=True))
        if not title:
            title = _clean(str(anchor.get("title", "")
                           or anchor.get("aria-label", "")))
        if not title:
            continue
        candidates.append((urljoin(OLX_BASE_URL, href), title))

    if not candidates:
        for match in re.finditer(r"(?P<url>/d/oferta/[^\"\s?#]+)", html):
            url = match.group("url")
            if url:
                candidates.append((urljoin(OLX_BASE_URL, url), ""))

    return candidates


def _extract_otodom_search_candidates(soup: BeautifulSoup, html: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", ""))
        if "/pl/oferta/" not in href:
            continue

        title = _clean(anchor.get_text(" ", strip=True))
        if not title:
            title = _clean(str(anchor.get("title", "")
                           or anchor.get("aria-label", "")))
        if not title and isinstance(anchor.parent, Tag):
            title = _clean(anchor.parent.get_text(" ", strip=True))

        candidates.append((urljoin(OTODOM_BASE_URL, href), title))

    if candidates:
        return candidates

    next_data = soup.find(
        "script", attrs={"id": "__NEXT_DATA__", "type": "application/json"})
    if isinstance(next_data, Tag):
        raw = next_data.string or next_data.get_text("", strip=True)
        if raw:
            for match in re.finditer(r"(?P<url>/pl/oferta/[a-z0-9\-]+-ID[\w]+)", raw, re.IGNORECASE):
                candidates.append(
                    (urljoin(OTODOM_BASE_URL, match.group("url")), ""))

    if candidates:
        return candidates

    for match in re.finditer(r"(?P<url>/pl/oferta/[a-z0-9\-]+-ID[\w]+)", html, re.IGNORECASE):
        candidates.append((urljoin(OTODOM_BASE_URL, match.group("url")), ""))

    return candidates


def _iter_json_ld_items(raw_json: str) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return items

    queue: list[object] = [parsed]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            if "itemListElement" in current and isinstance(current["itemListElement"], list):
                for element in current["itemListElement"]:
                    if isinstance(element, dict):
                        candidate = element.get(
                            "item") if "item" in element else element
                        if isinstance(candidate, dict):
                            items.append(candidate)
            for value in current.values():
                if isinstance(value, (dict, list)):
                    queue.append(value)
        elif isinstance(current, list):
            queue.extend(current)
    return items


def _extract_olx_external_id(url: str) -> str:
    match = re.search(r"/d/oferta/.+-(ID[\w]+)\.html", url)
    return match.group(1) if match else ""


def _extract_meta_content(soup: BeautifulSoup, property_name: str) -> str:
    node = soup.find("meta", attrs={"property": property_name})
    if not isinstance(node, Tag):
        return ""
    return _clean(node.get("content", ""))


def _extract_meta_description(soup: BeautifulSoup) -> str:
    node = soup.find("meta", attrs={"name": "description"})
    if not isinstance(node, Tag):
        return ""
    return _clean(node.get("content", ""))


def _extract_olx_base_price(soup: BeautifulSoup, title: str) -> str:
    strings = [_clean(text) for text in soup.stripped_strings]
    title_indexes = [index for index,
                     text in enumerate(strings) if text == title]

    for start_index in reversed(title_indexes):
        for text in strings[start_index + 1: start_index + 8]:
            if _looks_like_price(text) and "czynsz" not in text.casefold():
                return text

    for cleaned in strings:
        if _looks_like_price(cleaned) and "czynsz" not in cleaned.casefold():
            return cleaned
    return ""


def _extract_otodom_base_price(soup: BeautifulSoup) -> str:
    heading = soup.find("h1")
    if heading:
        for sibling in heading.parent.stripped_strings if isinstance(heading.parent, Tag) else []:
            cleaned = _clean(sibling)
            if _looks_like_price(cleaned) and "czynsz" not in cleaned.casefold():
                return cleaned
    for text in soup.stripped_strings:
        cleaned = _clean(text)
        if _looks_like_price(cleaned) and "czynsz" not in cleaned.casefold():
            return cleaned
    return ""


def _join_price_parts(price: str, czynsz: str) -> str:
    if price and czynsz:
        return f"{price} + czynsz: {czynsz}"
    return price or czynsz


def _looks_like_price(text: str) -> bool:
    return "zł" in text and any(char.isdigit() for char in text)


def _extract_olx_location(soup: BeautifulSoup) -> str:
    location_heading = soup.find(string=re.compile(r"^Lokalizacja$"))
    if not location_heading:
        return ""
    parent = location_heading.parent
    if not isinstance(parent, Tag):
        return ""
    texts = [_clean(text) for text in parent.parent.stripped_strings] if isinstance(
        parent.parent, Tag) else []
    texts = [text for text in texts if text and text !=
             "Lokalizacja" and text != "Zobacz lokalizację na mapie"]
    return ", ".join(texts[:2])


def _extract_otodom_location(soup: BeautifulSoup) -> str:
    anchor = soup.find("a", href="#map")
    if isinstance(anchor, Tag):
        return _clean(anchor.get_text(" ", strip=True))
    return ""


def _extract_olx_czynsz(soup: BeautifulSoup) -> str:
    for text in soup.stripped_strings:
        cleaned = _clean(text)
        match = re.search(r"Czynsz \(dodatkowo\):\s*(.+)$", cleaned)
        if match:
            return _clean(match.group(1))
    return _extract_label_value(soup, "Czynsz (dodatkowo)")


def _extract_otodom_czynsz(soup: BeautifulSoup) -> str:
    for text in soup.stripped_strings:
        cleaned = _clean(text)
        match = re.search(r"^\+?\s*Czynsz\s+(.+)$", cleaned)
        if match:
            return _clean(match.group(1))
    return _extract_text_after_label(soup, "Czynsz:")


def _extract_olx_published_at(soup: BeautifulSoup) -> date | None:
    text = _clean(" ".join(soup.stripped_strings))
    if re.search(r"Odświeżono\s+dzisiaj", text, re.IGNORECASE):
        return date.today()
    if re.search(r"Odświeżono\s+wczoraj", text, re.IGNORECASE):
        return date.today() - timedelta(days=1)
    match = re.search(
        r"Odśwież.{0,40}?(\d{1,2}\s+[a-ząćęłńóśźż]+\s+\d{4})", text, re.IGNORECASE)
    if not match:
        return None
    return _parse_polish_date(match.group(1))


def _extract_otodom_published_at(soup: BeautifulSoup) -> date | None:
    text = _clean(soup.get_text(" ", strip=True))

    if re.search(r"\b(dzisiaj|dodane dzisiaj)\b", text, re.IGNORECASE):
        return date.today()
    if re.search(r"\b(wczoraj|dodane wczoraj)\b", text, re.IGNORECASE):
        return date.today() - timedelta(days=1)

    match = re.search(
        r"Ostatnia aktualizacja:\s*(\d{1,2}\.\d{1,2}\.\d{4})", text)
    if not match:
        return None
    return _parse_numeric_date(match.group(1))


def _extract_section_text(soup: BeautifulSoup, heading_text: str) -> str:
    heading = soup.find(string=re.compile(rf"^{re.escape(heading_text)}$"))
    if not heading:
        return ""

    collected: list[str] = []
    current = heading.parent.next_sibling if isinstance(
        heading.parent, Tag) else None
    while current:
        if isinstance(current, Tag):
            text = _clean(current.get_text(" ", strip=True))
            if text:
                if heading_text == "Opis" and text == "Pokaż więcej":
                    current = current.next_sibling
                    continue
                if len(text) > 30 and text != heading_text:
                    collected.append(text)
                    if heading_text == "Opis":
                        break
        current = current.next_sibling
    return "\n".join(collected)


def _extract_label_value(soup: BeautifulSoup, label: str) -> str:
    label_node = soup.find(string=re.compile(rf"^{re.escape(label)}:?$"))
    if not label_node:
        return ""
    parent = label_node.parent
    if not isinstance(parent, Tag):
        return ""
    texts = [_clean(text) for text in parent.stripped_strings]
    texts = [text for text in texts if text and text != label]
    if texts:
        return texts[0]
    sibling = parent.find_next_sibling()
    if isinstance(sibling, Tag):
        return _clean(sibling.get_text(" ", strip=True))
    return ""


def _extract_text_after_label(soup: BeautifulSoup, label: str) -> str:
    label_node = soup.find(string=re.compile(rf"^{re.escape(label)}$"))
    if not label_node:
        return ""
    texts: list[str] = []
    next_node = label_node.parent.next_sibling if isinstance(
        label_node.parent, Tag) else None
    while next_node and len(texts) < 1:
        if isinstance(next_node, Tag):
            text = _clean(next_node.get_text(" ", strip=True))
            if text:
                texts.append(text)
                break
        next_node = next_node.next_sibling
    return texts[0] if texts else ""


def _extract_photo_urls(soup: BeautifulSoup, title: str) -> list[str]:
    urls: list[str] = []
    title_normalized = title.casefold()
    for image in soup.find_all("img", src=True):
        src = image.get("src", "")
        if "apollo.olxcdn.com" not in src:
            continue
        alt = _clean(image.get("alt", ""))
        if alt and title_normalized and title_normalized not in alt.casefold() and "pełny obrazek" not in alt.casefold():
            continue
        if src not in urls:
            urls.append(src)
    return urls


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_numeric_date(value: str) -> date | None:
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", value.strip())
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_polish_date(value: str) -> date | None:
    match = re.fullmatch(
        r"(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})", value.strip(), re.IGNORECASE)
    if not match:
        return None

    month_names = {
        "stycznia": 1,
        "lutego": 2,
        "marca": 3,
        "kwietnia": 4,
        "maja": 5,
        "czerwca": 6,
        "lipca": 7,
        "sierpnia": 8,
        "września": 9,
        "pazdziernika": 10,
        "października": 10,
        "listopada": 11,
        "grudnia": 12,
    }

    day = int(match.group(1))
    month = month_names.get(match.group(2).casefold())
    year = int(match.group(3))
    if month is None:
        return None

    try:
        return date(year, month, day)
    except ValueError:
        return None
