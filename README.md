# flats-retrieval

Small Python service that runs as a Telegram bot, accepts your rent search URLs from `olx.pl` and `otodom.pl`, deduplicates listings, and sends only new flats back to the same chat.

## What it does

- works through Telegram commands and messages
- uses your own search URLs, so all preferences stay in the website filters
- polls every 15 seconds by default
- uses Camoufox for browser anti-detection
- stores per-chat search URLs and seen listing IDs in SQLite so flats are not sent twice
- opens new listings, extracts title, price, location, description, photos, and direct link
- sends the result back into the same Telegram chat that configured the search

## Setup

1. Activate the virtualenv.
2. Install dependencies:

```bash
pip install -e .
playwright install firefox
camoufox fetch
```

3. Create `.env` from `.env.example`.
4. Start a chat with your bot in Telegram.
5. Send `/start`.
6. Send exactly 2 search URLs: one from OLX and one from Otodom.

Example message to the bot:

```text
https://www.olx.pl/nieruchomosci/mieszkania/wynajem/warszawa/?search%5Bfilter_float_price%3Ato%5D=3500
https://www.otodom.pl/pl/wyniki/wynajem/mieszkanie/mazowieckie/warszawa/warszawa/warszawa?priceMax=3500
```

## Run

```bash
python -m flats_retrieval
```

Or with the console script:

```bash
flats-retrieval
```

## VPS Deploy

The bot is already set up for headless VPS usage because it launches Camoufox with virtual headless mode.

Example Ubuntu VPS setup:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip xvfb libgtk-3-0 libdbus-glib-1-2 libasound2t64
git clone <your-repo-url> /opt/flats-retrieval
cd /opt/flats-retrieval
python3 -m venv venv
./venv/bin/pip install -e .
./venv/bin/playwright install firefox
./venv/bin/camoufox fetch
```

Then create `/opt/flats-retrieval/.env`, adjust `deploy/flats-retrieval.service`, and install the service:

```bash
sudo cp deploy/flats-retrieval.service /etc/systemd/system/flats-retrieval.service
sudo systemctl daemon-reload
sudo systemctl enable --now flats-retrieval
sudo systemctl status flats-retrieval
```

If your VPS user is not `ubuntu`, change `User=` in the service file.

## Notes

- `SEED_EXISTING_ON_START=1` means the first run will remember current results without sending them, then only notify about newly appearing flats.
- If you send new links later, the bot replaces the old active searches for that chat.
- The bot requires exactly one OLX search URL and one Otodom search URL.
- Supported commands: `/start`, `/status`, `/clear`.
- `POLL_INTERVAL_SECONDS=15` is aggressive. Even with Camoufox, sites can still rate limit or change markup.
- The parser is intentionally lightweight and may need selector updates if OLX or Otodom changes page structure.
