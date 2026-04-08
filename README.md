# appts-retrieval

Fast Telegram bot that watches OLX and Otodom rental searches and sends only newly listed flats.

## Goals

- notify as quickly as possible after a flat appears in search results
- preserve your website filters by using your own OLX and Otodom links
- avoid duplicates with SQLite-based per-chat dedupe
- keep runtime lightweight (no browser)

## Project structure

The code lives under `src/` using a clean package layout:

- `src/service.py` - orchestration and polling loops
- `src/scrapers.py` - OLX/Otodom extraction and enrichment
- `src/telegram.py` - Telegram API client
- `src/storage.py` - SQLite persistence
- `src/urls.py` - URL extraction/validation
- `src/messages.py` - bot message templates
- `src/config.py` - env-based configuration

## Setup with PDM

1. Install PDM.
2. Install dependencies:

```bash
pdm install
```

3. Create `.env` from `.env.example` and set `TELEGRAM_BOT_TOKEN`.

## Run

```bash
pdm run appts-retrieval
```

or:

```bash
pdm run python -m appt_retrieval
```

## Telegram usage

1. Send `/start`.
2. Send exactly two links: one OLX and one Otodom.

Example:

```text
https://www.olx.pl/nieruchomosci/mieszkania/wynajem/<city>/?search%5Bfilter_float_price%3Ato%5D=2500
https://www.otodom.pl/pl/wyniki/wynajem/mieszkanie/<city>?priceMax=2500
```

Commands:

- `/status` - show active links
- `/clear` - remove active links

## Speed tuning for very fresh flats

Default values are already tuned for quick notifications:

- `POLL_INTERVAL_SECONDS=8`
- `MAX_PARALLEL_SEARCHES=2`
- `MAX_PARALLEL_ENRICHMENTS=4`

You can tune further in `.env`, but lower intervals increase request pressure and timeout risk.

## Environment variables

- `TELEGRAM_BOT_TOKEN` (required)
- `POLL_INTERVAL_SECONDS` (default: `8`, minimum enforced: `3`)
- `MAX_PARALLEL_SEARCHES` (default: `2`)
- `MAX_PARALLEL_ENRICHMENTS` (default: `4`)
- `TELEGRAM_POLL_TIMEOUT_SECONDS` (default: `20`)
- `TELEGRAM_READ_TIMEOUT_SECONDS` (default: `65`)
- `MAX_LISTINGS_PER_SEARCH` (default: `25`, `0` means no cap)
- `MAX_NEW_LISTINGS_PER_CYCLE` (default: `6`, `0` means no cap)
- `MAX_PHOTOS_PER_MESSAGE` (default: `5`, `0` means no cap)
- `SEED_EXISTING_ON_START` (default: `1`)
- `STATE_DB_PATH` (default: `data/flats.db`)
