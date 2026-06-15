import asyncio
import json
import logging
import os
from datetime import datetime

import pytz
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
SUBSCRIBERS_FILE = "data/subscribers.json"
POLL_INTERVAL = 120  # seconds between score checks
ISRAEL_TZ = pytz.timezone("Asia/Jerusalem")

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"

# Tracks which alerts have already been sent per game to avoid spam
_sent_alerts: dict[str, bool] = {}


# ---------- persistence ----------

def load_subscribers() -> set:
    if os.path.exists(SUBSCRIBERS_FILE):
        with open(SUBSCRIBERS_FILE) as f:
            return set(json.load(f))
    return set()


def save_subscribers(subs: set):
    os.makedirs("data", exist_ok=True)
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump(list(subs), f)


subscribers = load_subscribers()


# ---------- ESPN helpers ----------

def fetch_live_games() -> list:
    try:
        r = requests.get(ESPN_URL, timeout=10)
        r.raise_for_status()
        events = r.json().get("events", [])
        return [
            e for e in events
            if e.get("competitions", [{}])[0]
               .get("status", {}).get("type", {}).get("state") == "in"
        ]
    except Exception as exc:
        logging.error("ESPN API error: %s", exc)
        return []


def parse_minute(clock: str) -> int:
    try:
        if "+" in clock:
            base, extra = clock.split("+", 1)
            return int(base) + int(extra.split(":")[0])
        return int(clock.split(":")[0])
    except Exception:
        return 0


def game_summary(event: dict) -> str:
    comp = event["competitions"][0]
    teams = comp.get("competitors", [])
    home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
    away = next((t for t in teams if t.get("homeAway") == "away"), teams[1] if len(teams) > 1 else teams[0])
    status = comp.get("status", {})
    clock = status.get("displayClock", "?")
    period = status.get("period", 1)

    period_label = {1: "1H", 2: "2H", 3: "ET1", 4: "ET2", 5: "PEN"}.get(period, f"P{period}")
    return (
        f"{away.get('team', {}).get('shortDisplayName', '?')} "
        f"{away.get('score', '0')} – "
        f"{home.get('score', '0')} "
        f"{home.get('team', {}).get('shortDisplayName', '?')}  "
        f"[{clock}' {period_label}]"
    )


def should_alert(event: dict) -> tuple[bool, str]:
    """Return (True, reason) if this game deserves a 'turn on the TV' alert."""
    comp = event["competitions"][0]
    teams = comp.get("competitors", [])
    status = comp.get("status", {})
    period = status.get("period", 1)
    clock = status.get("displayClock", "0:00")
    minute = parse_minute(clock)
    event_id = event.get("id", "unknown")

    try:
        scores = [int(t.get("score", 0)) for t in teams]
        diff = abs(scores[0] - scores[1])
    except Exception:
        return False, ""

    # Penalty shootout — always worth watching
    key_pen = f"{event_id}:pen"
    if period >= 5 and key_pen not in _sent_alerts:
        _sent_alerts[key_pen] = True
        return True, "penalties"

    # Extra time — always worth watching
    key_et = f"{event_id}:et"
    if period in (3, 4) and key_et not in _sent_alerts:
        _sent_alerts[key_et] = True
        return True, "extra_time"

    # Last 15 min of 2nd half, score within 1 goal
    key_75 = f"{event_id}:min75"
    if period == 2 and minute >= 75 and diff <= 1 and key_75 not in _sent_alerts:
        _sent_alerts[key_75] = True
        return True, "close_75"

    # Last 5 min of 2nd half, score within 2 goals (comeback possible)
    key_85 = f"{event_id}:min85"
    if period == 2 and minute >= 85 and diff <= 2 and key_85 not in _sent_alerts:
        _sent_alerts[key_85] = True
        return True, "close_85"

    return False, ""


def build_alert(event: dict, reason: str) -> str:
    comp = event["competitions"][0]
    teams = comp.get("competitors", [])
    home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
    away = next((t for t in teams if t.get("homeAway") == "away"), teams[1] if len(teams) > 1 else teams[0])
    status = comp.get("status", {})
    clock = status.get("displayClock", "?")

    home_name = home.get("team", {}).get("displayName", "?")
    away_name = away.get("team", {}).get("displayName", "?")
    home_score = home.get("score", "0")
    away_score = away.get("score", "0")

    broadcasts = comp.get("broadcasts", [])
    tv_channels = [n for b in broadcasts for n in b.get("names", [])]
    tv_line = f"\nTV: {', '.join(tv_channels)}" if tv_channels else ""

    now_il = datetime.now(ISRAEL_TZ).strftime("%H:%M")

    headers = {
        "penalties": "PENALTY SHOOTOUT — turn on the TV now!",
        "extra_time": "EXTRA TIME — could go either way!",
        "close_75": f"Exciting finish — {clock}' minute, tight game!",
        "close_85": f"Last 5 minutes — still alive!",
    }
    header = headers.get(reason, "Hot game alert!")

    return (
        f"WORLD CUP ALERT\n"
        f"{header}\n\n"
        f"{away_name}  {away_score} – {home_score}  {home_name}"
        f"{tv_line}\n"
        f"Israel time: {now_il}"
    )


# ---------- bot commands ----------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    subscribers.add(cid)
    save_subscribers(subscribers)
    await update.message.reply_text(
        "Subscribed to World Cup alerts!\n\n"
        "I'll ping you when a game is in its final minutes with a close score, "
        "goes to extra time, or reaches penalties.\n\n"
        "/status — see live games now\n"
        "/stop   — unsubscribe"
    )


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    subscribers.discard(cid)
    save_subscribers(subscribers)
    await update.message.reply_text("Unsubscribed. Use /start anytime to re-subscribe.")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    games = fetch_live_games()
    if not games:
        await update.message.reply_text("No live World Cup games right now.")
        return
    lines = ["Live World Cup games:\n"] + [game_summary(g) for g in games]
    await update.message.reply_text("\n".join(lines))


# ---------- background poller ----------

async def score_poller(app: Application):
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        if not subscribers:
            continue
        for game in fetch_live_games():
            worth, reason = should_alert(game)
            if not worth:
                continue
            msg = build_alert(game, reason)
            for cid in list(subscribers):
                try:
                    await app.bot.send_message(chat_id=cid, text=msg)
                except Exception as exc:
                    logging.warning("Could not send to %s: %s", cid, exc)


# ---------- entry point ----------

async def run():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))

    async with app:
        await app.start()
        await app.updater.start_polling()
        logging.info("Bot running. Checking scores every %ds.", POLL_INTERVAL)
        await score_poller(app)
        await app.updater.stop()
        await app.stop()


if __name__ == "__main__":
    asyncio.run(run())
