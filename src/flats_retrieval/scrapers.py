from __future__ import annotations

import re
from collections import OrderedDict
from datetime import date, timedelta
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from flats_retrieval.models import FlatListing, SearchConfig

OLX_BASE_URL = "https://www.olx.pl"
OTODOM_BASE_URL = "https://www.otodom.pl"


class FlatScraper:
    def supports(self, url: str) -> bool:
        raise NotImplementedError

    async def fetch_search_results(self, page, search: SearchConfig, limit: int) -> list[FlatListing]:
        raise NotImplementedError

    async def enrich_listing(self, page, listing: FlatListing) -> FlatListing:
        raise NotImplementedError


class OlxScraper(FlatScraper):
    def supports(self, url: str) -> bool:
        return "olx.pl" in urlparse(url).netloc

    async def fetch_search_results(self, page, search: SearchConfig, limit: int) -> list[FlatListing]:
        await _goto_with_retry(page, search.url)
        soup = BeautifulSoup(await page.content(), "html.parser")
        listings: list[FlatListing] = []
        seen_links: set[str] = set()

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if "/d/oferta/" not in href:
                continue

            link = urljoin(OLX_BASE_URL, href)
            if urlparse(link).netloc != "www.olx.pl":
                continue
            if link in seen_links:
                continue

            external_id = _extract_id_from_url(link) or _extract_olx_external_id(link)
            title = _clean(anchor.get_text(" ", strip=True))
            if not external_id or not title:
                continue

            listings.append(
                FlatListing(
                    source="olx",
                    external_id=external_id,
                    title=title,
                    price="",
                    link=link,
                )
            )
            seen_links.add(link)
            if len(listings) >= limit:
                break

        return listings

    async def enrich_listing(self, page, listing: FlatListing) -> FlatListing:
        await _goto_with_retry(page, listing.link)
        soup = BeautifulSoup(await page.content(), "html.parser")

        czynsz = _extract_olx_czynsz(soup)
        listing.price = _join_price_parts(_extract_olx_base_price(soup, listing.title), czynsz)
        listing.location = _extract_olx_location(soup)
        listing.description = _extract_section_text(soup, "Opis") or _extract_meta_description(soup)
        listing.published_at = _extract_olx_published_at(soup)
        listing.photos = _extract_photo_urls(soup, listing.title)
        listing.details = OrderedDict(
            [
                ("Powierzchnia", _extract_label_value(soup, "Powierzchnia")),
                ("Pokoje", _extract_label_value(soup, "Liczba pokoi")),
                ("Piętro", _extract_label_value(soup, "Poziom")),
            ]
        )
        listing.details = {key: value for key, value in listing.details.items() if value}
        return listing


class OtodomScraper(FlatScraper):
    def supports(self, url: str) -> bool:
        return "otodom.pl" in urlparse(url).netloc

    async def fetch_search_results(self, page, search: SearchConfig, limit: int) -> list[FlatListing]:
        await _goto_with_retry(page, search.url)
        soup = BeautifulSoup(await page.content(), "html.parser")
        listings: list[FlatListing] = []
        seen_links: set[str] = set()

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if "/pl/oferta/" not in href:
                continue

            link = urljoin(OTODOM_BASE_URL, href)
            if link in seen_links:
                continue

            external_id = _extract_id_from_url(link)
            title = _clean(anchor.get_text(" ", strip=True))
            if not external_id or not title:
                continue

            listings.append(
                FlatListing(
                    source="otodom",
                    external_id=external_id,
                    title=title,
                    price="",
                    link=link,
                )
            )
            seen_links.add(link)
            if len(listings) >= limit:
                break

        return listings

    async def enrich_listing(self, page, listing: FlatListing) -> FlatListing:
        await _goto_with_retry(page, listing.link)
        soup = BeautifulSoup(await page.content(), "html.parser")

        listing.title = _clean(soup.find("h1").get_text(" ", strip=True)) if soup.find("h1") else listing.title
        czynsz = _extract_otodom_czynsz(soup)
        listing.price = _join_price_parts(_extract_otodom_base_price(soup), czynsz)
        listing.location = _extract_otodom_location(soup)
        listing.description = _extract_section_text(soup, "Opis") or _extract_meta_description(soup)
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
        listing.details = {key: value for key, value in listing.details.items() if value}
        return listing


def get_scraper_for_url(url: str) -> FlatScraper:
    for scraper in (OlxScraper(), OtodomScraper()):
        if scraper.supports(url):
            return scraper
    raise ValueError(f"Unsupported search URL: {url}")


async def _goto_with_retry(page, url: str) -> None:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_load_state("networkidle", timeout=30_000)
    except PlaywrightTimeoutError:
        await page.goto(url, wait_until="domcontentloaded", timeout=90_000)


def _extract_id_from_url(url: str) -> str:
    match = re.search(r"-(ID[\w]+)(?:\.html)?", url)
    return match.group(1) if match else ""


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
    title_indexes = [index for index, text in enumerate(strings) if text == title]

    for start_index in reversed(title_indexes):
        for text in strings[start_index + 1 : start_index + 8]:
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
    texts = [_clean(text) for text in parent.parent.stripped_strings] if isinstance(parent.parent, Tag) else []
    texts = [text for text in texts if text and text != "Lokalizacja" and text != "Zobacz lokalizację na mapie"]
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
    match = re.search(r"Odśwież.{0,40}?(\d{1,2}\s+[a-ząćęłńóśźż]+\s+\d{4})", text, re.IGNORECASE)
    if not match:
        return None
    return _parse_polish_date(match.group(1))


def _extract_otodom_published_at(soup: BeautifulSoup) -> date | None:
    text = _clean(soup.get_text(" ", strip=True))
    match = re.search(r"Ostatnia aktualizacja:\s*(\d{1,2}\.\d{1,2}\.\d{4})", text)
    if not match:
        return None
    return _parse_numeric_date(match.group(1))


def _extract_section_text(soup: BeautifulSoup, heading_text: str) -> str:
    heading = soup.find(string=re.compile(rf"^{re.escape(heading_text)}$"))
    if not heading:
        return ""

    collected: list[str] = []
    current = heading.parent.next_sibling if isinstance(heading.parent, Tag) else None
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
    next_node = label_node.parent.next_sibling if isinstance(label_node.parent, Tag) else None
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
    match = re.fullmatch(r"(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})", value.strip(), re.IGNORECASE)
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
