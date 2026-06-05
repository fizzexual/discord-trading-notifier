import asyncio
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import aiohttp
import discord
import yfinance as yf
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY", "").strip()
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))
DB_PATH = Path(__file__).with_name("targets.db")

# td_symbol = Twelve Data symbol (real-time when available)
# yf_ticker = Yahoo Finance ticker (15-min delayed, used as fallback)
COMMODITIES = {
    "gold":       {"yf_ticker": "GC=F", "td_symbol": "XAU/USD", "label": "Gold",        "unit": "USD/oz",    "realtime_with_td": True},
    "silver":     {"yf_ticker": "SI=F", "td_symbol": "XAG/USD", "label": "Silver",      "unit": "USD/oz",    "realtime_with_td": True},
    "naturalgas": {"yf_ticker": "NG=F", "td_symbol": "NG",      "label": "Natural Gas", "unit": "USD/MMBtu", "realtime_with_td": False},
}

# Accept friendly aliases on prefix commands so "!gas", "!ng", "!nat gas" all work
COMMODITY_ALIASES = {
    "gold": "gold", "xau": "gold",
    "silver": "silver", "xag": "silver",
    "naturalgas": "naturalgas", "natural_gas": "naturalgas",
    "natgas": "naturalgas", "ng": "naturalgas", "gas": "naturalgas",
}

DIRECTION_CHOICES = [
    app_commands.Choice(name="above", value="above"),
    app_commands.Choice(name="below", value="below"),
]

COMMODITY_CHOICES = [
    app_commands.Choice(name="Gold",        value="gold"),
    app_commands.Choice(name="Silver",      value="silver"),
    app_commands.Choice(name="Natural Gas", value="naturalgas"),
]


# ---------- DB ----------

def db_init() -> None:
    with closing(sqlite3.connect(DB_PATH)) as con, con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS targets (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                commodity  TEXT    NOT NULL,
                price      REAL    NOT NULL,
                direction  TEXT    NOT NULL,
                baseline   REAL    NOT NULL
            )
        """)


def db_add(user_id, channel_id, commodity, price, direction, baseline) -> int:
    with closing(sqlite3.connect(DB_PATH)) as con, con:
        cur = con.execute(
            "INSERT INTO targets (user_id, channel_id, commodity, price, direction, baseline) VALUES (?,?,?,?,?,?)",
            (user_id, channel_id, commodity, price, direction, baseline),
        )
        return cur.lastrowid


def db_list(user_id):
    with closing(sqlite3.connect(DB_PATH)) as con:
        return con.execute(
            "SELECT id, commodity, price, direction FROM targets WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()


def db_all():
    with closing(sqlite3.connect(DB_PATH)) as con:
        return con.execute(
            "SELECT id, user_id, channel_id, commodity, price, direction, baseline FROM targets"
        ).fetchall()


def db_delete(target_id, user_id) -> bool:
    with closing(sqlite3.connect(DB_PATH)) as con, con:
        cur = con.execute("DELETE FROM targets WHERE id = ? AND user_id = ?", (target_id, user_id))
        return cur.rowcount > 0


def db_delete_unchecked(target_id) -> None:
    with closing(sqlite3.connect(DB_PATH)) as con, con:
        con.execute("DELETE FROM targets WHERE id = ?", (target_id,))


# ---------- Price ----------

async def _fetch_twelve_data(symbol: str) -> float | None:
    if not TWELVE_DATA_KEY:
        return None
    url = "https://api.twelvedata.com/price"
    params = {"symbol": symbol, "apikey": TWELVE_DATA_KEY}
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url, params=params) as r:
                data = await r.json()
        if "price" in data:
            return float(data["price"])
        # Twelve Data returns {"code": ..., "message": ...} on errors / rate limits
        print(f"[twelvedata] {symbol} -> {data}", flush=True)
        return None
    except Exception as exc:
        print(f"[twelvedata] {symbol} request failed: {exc}", flush=True)
        return None


async def _fetch_yfinance(ticker: str) -> float | None:
    def _blocking():
        t = yf.Ticker(ticker)
        try:
            p = t.fast_info.get("last_price")
            if p:
                return float(p)
        except Exception:
            pass
        hist = t.history(period="1d", interval="1m")
        if hist.empty:
            hist = t.history(period="5d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])

    try:
        return await asyncio.to_thread(_blocking)
    except Exception as exc:
        print(f"[yfinance] failed to fetch {ticker}: {exc}", flush=True)
        return None


async def fetch_price(commodity_key: str) -> tuple[float | None, str]:
    """Return (price, source_label). Tries Twelve Data first if a key is set, falls back to yfinance."""
    info = COMMODITIES[commodity_key]
    if TWELVE_DATA_KEY:
        price = await _fetch_twelve_data(info["td_symbol"])
        if price is not None:
            source = "Twelve Data, real-time" if info["realtime_with_td"] else "Twelve Data, ~15min delayed"
            return price, source
    price = await _fetch_yfinance(info["yf_ticker"])
    return price, "Yahoo Finance, ~15min delayed"


# ---------- Shared command logic ----------

async def price_response(commodity: str) -> str:
    info = COMMODITIES[commodity]
    price, source = await fetch_price(commodity)
    if price is None:
        return f"Couldn't fetch {info['label']} price right now — try again in a minute."
    return f"**{info['label']}**: `{price:,.2f}` {info['unit']}  _(via {source})_"


def normalize_commodity(raw: str) -> str | None:
    return COMMODITY_ALIASES.get(raw.lower().replace(" ", "").replace("-", ""))


async def set_target_logic(user_id: int, channel_id: int, commodity: str, price: float, direction: str) -> str:
    if commodity not in COMMODITIES:
        return f"Unknown commodity `{commodity}`. Use one of: gold, silver, naturalgas."
    if direction not in ("above", "below"):
        return f"Direction must be `above` or `below`, not `{direction}`."

    info = COMMODITIES[commodity]
    current, _ = await fetch_price(commodity)
    if current is None:
        return "Couldn't fetch current price to set a baseline — try again shortly."

    if direction == "above" and price <= current:
        return (f"{info['label']} is already `{current:,.2f}` — an `above {price:,.2f}` target would fire immediately. "
                "Pick a price above the current value, or use `below`.")
    if direction == "below" and price >= current:
        return (f"{info['label']} is already `{current:,.2f}` — a `below {price:,.2f}` target would fire immediately. "
                "Pick a price below the current value, or use `above`.")

    tid = db_add(user_id, channel_id, commodity, price, direction, current)
    return (f"Target **#{tid}** set: alert when **{info['label']}** goes **{direction} `{price:,.2f}` {info['unit']}** "
            f"(current: `{current:,.2f}`). You'll get a DM and a ping here.")


def mytargets_text(user_id: int) -> str:
    rows = db_list(user_id)
    if not rows:
        return "You have no active targets. Use `/settarget` (or `!settarget`) to add one."
    lines = []
    for tid, commodity, price, direction in rows:
        info = COMMODITIES[commodity]
        lines.append(f"`#{tid}`  {info['label']}  **{direction} {price:,.2f}** {info['unit']}")
    return "**Your targets:**\n" + "\n".join(lines)


def remove_target_text(target_id: int, user_id: int) -> str:
    if db_delete(target_id, user_id):
        return f"Removed target `#{target_id}`."
    return f"No target `#{target_id}` belongs to you."


# ---------- Bot setup ----------

intents = discord.Intents.default()
intents.message_content = True  # required for ! prefix commands
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# ---------- Slash commands ----------

@bot.tree.command(name="gold", description="Current gold price")
async def gold_slash(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    await interaction.followup.send(await price_response("gold"))


@bot.tree.command(name="silver", description="Current silver price")
async def silver_slash(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    await interaction.followup.send(await price_response("silver"))


@bot.tree.command(name="naturalgas", description="Current natural gas price")
async def naturalgas_slash(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    await interaction.followup.send(await price_response("naturalgas"))


@bot.tree.command(name="settarget", description="Alert me when a commodity crosses a price")
@app_commands.describe(
    commodity="Which commodity to watch",
    price="Target price (in the commodity's native unit)",
    direction="Trigger when price goes above or below the target",
)
@app_commands.choices(commodity=COMMODITY_CHOICES, direction=DIRECTION_CHOICES)
async def settarget_slash(
    interaction: discord.Interaction,
    commodity: app_commands.Choice[str],
    price: float,
    direction: app_commands.Choice[str],
):
    await interaction.response.defer(thinking=True)
    msg = await set_target_logic(interaction.user.id, interaction.channel_id, commodity.value, price, direction.value)
    await interaction.followup.send(msg)


@bot.tree.command(name="mytargets", description="List your active price targets")
async def mytargets_slash(interaction: discord.Interaction):
    await interaction.response.send_message(mytargets_text(interaction.user.id), ephemeral=True)


@bot.tree.command(name="removetarget", description="Remove one of your targets by id")
@app_commands.describe(target_id="The id shown by /mytargets")
async def removetarget_slash(interaction: discord.Interaction, target_id: int):
    await interaction.response.send_message(remove_target_text(target_id, interaction.user.id), ephemeral=True)


# ---------- Prefix (!) commands ----------

@bot.command(name="gold")
async def gold_prefix(ctx: commands.Context):
    async with ctx.typing():
        await ctx.send(await price_response("gold"))


@bot.command(name="silver")
async def silver_prefix(ctx: commands.Context):
    async with ctx.typing():
        await ctx.send(await price_response("silver"))


@bot.command(name="naturalgas", aliases=["ng", "gas", "natgas"])
async def naturalgas_prefix(ctx: commands.Context):
    async with ctx.typing():
        await ctx.send(await price_response("naturalgas"))


@bot.command(name="settarget")
async def settarget_prefix(ctx: commands.Context, commodity: str, price: float, direction: str):
    """Usage: !settarget <gold|silver|naturalgas> <price> <above|below>"""
    canonical = normalize_commodity(commodity)
    if canonical is None:
        await ctx.send(f"Unknown commodity `{commodity}`. Use one of: gold, silver, naturalgas (or aliases: ng, gas).")
        return
    async with ctx.typing():
        msg = await set_target_logic(ctx.author.id, ctx.channel.id, canonical, price, direction.lower())
    await ctx.send(msg)


@settarget_prefix.error
async def settarget_prefix_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument) or isinstance(error, commands.BadArgument):
        await ctx.send("Usage: `!settarget <gold|silver|naturalgas> <price> <above|below>`\nExample: `!settarget gold 2500 above`")
    else:
        raise error


@bot.command(name="mytargets")
async def mytargets_prefix(ctx: commands.Context):
    await ctx.send(mytargets_text(ctx.author.id))


@bot.command(name="removetarget", aliases=["rmtarget"])
async def removetarget_prefix(ctx: commands.Context, target_id: int):
    await ctx.send(remove_target_text(target_id, ctx.author.id))


@removetarget_prefix.error
async def removetarget_prefix_error(ctx, error):
    if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        await ctx.send("Usage: `!removetarget <id>` — get the id from `!mytargets`.")
    else:
        raise error


@bot.command(name="help")
async def help_prefix(ctx: commands.Context):
    await ctx.send(
        "**Commands** (slash `/` or prefix `!`):\n"
        "• `gold` / `silver` / `naturalgas` (aliases: `ng`, `gas`) — current price\n"
        "• `settarget <commodity> <price> <above|below>` — set alert\n"
        "• `mytargets` — list your alerts\n"
        "• `removetarget <id>` — remove an alert"
    )


# ---------- Alert loop ----------

@tasks.loop(seconds=CHECK_INTERVAL)
async def check_targets():
    targets = db_all()
    if not targets:
        return

    needed = {row[3] for row in targets}
    prices: dict[str, float] = {}
    for commodity in needed:
        p, _ = await fetch_price(commodity)
        if p is not None:
            prices[commodity] = p

    for tid, user_id, channel_id, commodity, target_price, direction, baseline in targets:
        current = prices.get(commodity)
        if current is None:
            continue
        crossed = (
            (direction == "above" and baseline < target_price and current >= target_price) or
            (direction == "below" and baseline > target_price and current <= target_price)
        )
        if not crossed:
            continue

        info = COMMODITIES[commodity]
        msg = (f"Target hit — **{info['label']}** is now `{current:,.2f}` {info['unit']} "
               f"({direction} `{target_price:,.2f}`).")

        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception:
                channel = None
        if channel is not None:
            try:
                await channel.send(f"<@{user_id}> {msg}")
            except Exception as exc:
                print(f"[alert] channel send failed for target {tid}: {exc}", flush=True)

        try:
            user = bot.get_user(user_id) or await bot.fetch_user(user_id)
            await user.send(msg)
        except Exception as exc:
            print(f"[alert] DM failed for target {tid}: {exc}", flush=True)

        db_delete_unchecked(tid)


@bot.event
async def on_ready():
    db_init()
    await bot.tree.sync()
    if not check_targets.is_running():
        check_targets.start()
    print(f"Logged in as {bot.user} (id={bot.user.id}). Watching every {CHECK_INTERVAL}s.", flush=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN missing. Edit .env and paste your bot token.")
    bot.run(TOKEN)
