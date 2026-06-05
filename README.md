# Commodity Price Bot

A small, self-hosted Discord bot for live commodity prices and personal price alerts. Quote spot gold, silver, and natural gas on demand, and set targets that ping you (in-channel and via DM) the moment a price is crossed.

Works with both **slash commands** (`/gold`) and **prefix commands** (`!gold`).

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![discord.py](https://img.shields.io/badge/discord.py-2.x-5865F2)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

- **Live prices** for gold, silver, and natural gas
- **Target alerts** — set a price and direction (`above` / `below`); the bot watches and notifies you
- **Two notification channels** — DM and an `@you` ping in the channel where you set it
- **Per-user targets** — each user manages their own list; no cross-talk
- **Real-time metals** via [Twelve Data](https://twelvedata.com/) (free tier) for `XAU/USD` and `XAG/USD`
- **Graceful fallback** to [Yahoo Finance](https://finance.yahoo.com/) (no key, ~15 min delay) if the Twelve Data key is missing or rate-limited
- **Zero infrastructure** — single `bot.py`, single SQLite file, runs on anything that runs Python

---

## Requirements

- Python 3.10 or newer
- A Discord application + bot token ([Developer Portal](https://discord.com/developers/applications))
- *(Optional)* A [Twelve Data](https://twelvedata.com/) API key for real-time gold/silver

---

## Setup

### 1. Create the bot in Discord

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and create a new application.
2. Under **Bot**, click **Reset Token** and copy the token somewhere safe.
3. On the same page, enable **Message Content Intent** (required for `!` prefix commands).
4. Under **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `View Channel`, `Send Messages`
5. Open the generated URL, pick your server, and authorize.

### 2. Install and run

```bash
git clone https://github.com/fizzexual/discord-trading-notifier.git
cd discord-trading-notifier

python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt

cp .env.example .env
# open .env in your editor and paste your bot token (and optionally a Twelve Data key)

python bot.py
```

On first launch you should see:

```
Logged in as YourBot#1234 (id=...). Watching every 300s.
```

Global slash commands can take a minute to propagate the first time. After that, type `/` in any channel where the bot is and the commands appear.

---

## Configuration

All configuration lives in `.env`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_TOKEN` | yes | — | Your Discord bot token. |
| `TWELVE_DATA_KEY` | no | *empty* | Twelve Data API key. If set, gold and silver use real-time spot quotes. If empty, Yahoo Finance is used (~15 min delay). |
| `CHECK_INTERVAL_SECONDS` | no | `300` | How often the alert loop polls prices. |

---

## Commands

Both forms are equivalent — use whichever you prefer.

| Slash | Prefix | What it does |
|---|---|---|
| `/gold` | `!gold` | Current gold price (USD/oz) |
| `/silver` | `!silver` | Current silver price (USD/oz) |
| `/naturalgas` | `!naturalgas`, `!ng`, `!gas` | Current natural gas price (USD/MMBtu) |
| `/settarget <commodity> <price> <above\|below>` | `!settarget <commodity> <price> <above\|below>` | Set a price alert |
| `/mytargets` | `!mytargets` | List your active targets |
| `/removetarget <id>` | `!removetarget <id>` | Delete one of your targets by id |

### Examples

```
/settarget commodity:gold price:2800 direction:above
!settarget gold 2800 above
!settarget ng 2.50 below
!removetarget 3
```

When a target hits, the bot sends two messages:

- A DM with the trigger price
- A reply in the channel where you set the target, pinging you

The target is deleted after firing — set a new one if you want it re-armed.

---

## Data sources

| Commodity | With `TWELVE_DATA_KEY` set | Without |
|---|---|---|
| Gold | `XAU/USD` spot, real-time | `GC=F` futures, ~15 min delay |
| Silver | `XAG/USD` spot, real-time | `SI=F` futures, ~15 min delay |
| Natural Gas | `NG` futures, ~15 min delay | `NG=F` futures, ~15 min delay |

> Real-time natural gas futures are not available on any free tier — CME licenses that data directly. If you need real-time NG, you need a paid feed (Polygon, Databento, Barchart, CME direct).

---

## How alerts work

Every `CHECK_INTERVAL_SECONDS` (default 5 minutes) the bot:

1. Loads all active targets from SQLite.
2. Fetches the current price for each distinct commodity (one request per commodity, not per target).
3. For each target, compares the current price against the **baseline** stored when the target was created. An alert fires only when the price has actually *crossed* the target since baseline — so the same target never fires twice in a row.
4. Sends the channel ping and the DM, then deletes the target.

Targets persist across restarts (they live in `targets.db`). If the bot was down when the price crossed and is still on the trigger side when it restarts, the alert still fires on the next check.

---

## File layout

```
.
├── bot.py            # all the bot code
├── requirements.txt  # discord.py, yfinance, aiohttp, python-dotenv
├── .env.example      # template for your local .env
├── .gitignore        # excludes .env, .venv, *.db, etc.
└── README.md
```

`targets.db` is created automatically on first run and is gitignored.

---

## Troubleshooting

**`PrivilegedIntentsRequired` on startup**
Enable **Message Content Intent** in the Developer Portal (Bot → Privileged Gateway Intents). Slash commands work without it; `!` commands do not.

**Slash commands don't appear in Discord**
Global command sync can take up to an hour the first time. Try kicking and re-inviting the bot, or use a guild-scoped sync if you want instant updates during development.

**`WebSocket closed with 4004`**
Your token is invalid or revoked. Discord auto-revokes tokens that get posted publicly (GitHub, pastebins, screenshots). Reset the token in the Developer Portal and put the new value in `.env`.

**Bot can't DM me**
Your DMs from server members may be disabled. Enable **Allow direct messages from server members** in your Discord privacy settings, or rely on the in-channel ping.

**Twelve Data returns errors / rate limits**
The free tier is 800 requests/day, 8/min. The bot falls back to Yahoo Finance automatically when Twelve Data fails, so prices keep flowing even when you hit a limit.

---

## License

MIT — see `LICENSE` if included, otherwise feel free to adopt one.
