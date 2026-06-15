"""
World Cup alert bot — uses Telegram Bot API directly via requests,
no python-telegram-bot library needed.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime

import pytz
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_BASE = f"https://api.telegram.org/bot{TOKEN}"
SUBSCRIBERS_FILE = "data/subscribers.json"
POLL_INTERVAL = 120  # seconds between score checks
ISRAEL_TZ = pytz.timezone("Asia/Jerusalem")
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"

_sent_alerts: dict = {}


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
subscribers_lock = threading.Lock()


# ---------- Telegram API helpers ----------

def tg_get(method: str, params: dict = None):
    try:
        r = requests.get(f"{TG_BASE}/{method}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logging.error("Telegram GET %s error: %s", method, e)
        time.sleep(5)
        return {}


def tg_send(chat_id: int, text: str):
    try:
        requests.post(f"{TG_BASE}/sendMessage",
                      json={"chat_id": chat_id, "text": text},
                      timeout=10)
    except Exception as e:
        logging.warning("Failed to send to %s: %s", chat_id, e)


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
    away = next((t for t in teams if t.get("homeAway") == "away"), teams[-1])
    status = comp.get("status", {})
    clock = status.get("displayClock", "?")
    period = status.get("period", 1)
    period_label = {1: "1H", 2: "2H", 3: "ET1", 4: "ET2", 5: "PEN"}.get(period, f"P{period}")
    return (
        f"{away.get('team', {}).get('shortDisplayName', '?')} "
        f"{away.get('score', '0')} - "
        f"{home.get('score', '0')} "
        f"{home.get('team', {}).get('shortDisplayName', '?')}  "
        f"[{clock}' {period_label}]"
    )


def should_alert(event: dict) -> tuple:
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

    if period >= 5:
        key = f"{event_id}:pen"
        if key not in _sent_alerts:
            _sent_alerts[key] = True
            return True, "penalties"

    if period in (3, 4):
        key = f"{event_id}:et"
        if key not in _sent_alerts:
            _sent_alerts[key] = True
            return True, "extra_time"

    if period == 2 and minute >= 75 and diff <= 1:
        key = f"{event_id}:min75"
        if key not in _sent_alerts:
            _sent_alerts[key] = True
            return True, "close_75"

    if period == 2 and minute >= 85 and diff <= 2:
        key = f"{event_id}:min85"
        if key not in _sent_alerts:
            _sent_alerts[key] = True
            return True, "close_85"

    return False, ""


def build_alert(event: dict, reason: str) -> str:
    comp = event["competitions"][0]
    teams = comp.get("competitors", [])
    home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
    away = next((t for t in teams if t.get("homeAway") == "away"), teams[-1])
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
        f"{away_name}  {away_score} - {home_score}  {home_name}"
        f"{tv_line}\n"
        f"Israel time: {now_il}"
    )


# ---------- command handlers ----------

def handle_start(chat_id: int):
    with subscribers_lock:
        subscribers.add(chat_id)
        save_subscribers(subscribers)
    tg_send(chat_id,
            "Subscribed to World Cup alerts!\n\n"
            "I'll notify you when a game enters its final minutes with a close score, "
            "goes to extra time, or reaches penalties.\n\n"
            "/status — see live games now\n"
            "/stop   — unsubscribe")


def handle_stop(chat_id: int):
    with subscribers_lock:
        subscribers.discard(chat_id)
        save_subscribers(subscribers)
    tg_send(chat_id, "Unsubscribed. Send /start anytime to re-subscribe.")


def handle_status(chat_id: int):
    games = fetch_live_games()
    if not games:
        tg_send(chat_id, "No live World Cup games right now.")
        return
    lines = ["Live World Cup games:\n"] + [game_summary(g) for g in games]
    tg_send(chat_id, "\n".join(lines))


COMMANDS = {
    "/start": handle_start,
    "/stop": handle_stop,
    "/status": handle_status,
}


# ---------- update polling loop ----------

def poll_updates():
    offset = None
    while True:
        data = tg_get("getUpdates", {"timeout": 25, "offset": offset})
        for update in data.get("result", []):
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            text = (msg.get("text") or "").strip().split("@")[0]  # strip @botname
            if chat_id and text in COMMANDS:
                COMMANDS[text](chat_id)


# ---------- score polling loop ----------

def poll_scores():
    while True:
        time.sleep(POLL_INTERVAL)
        with subscribers_lock:
            subs = set(subscribers)
        if not subs:
            continue
        for game in fetch_live_games():
            worth, reason = should_alert(game)
            if not worth:
                continue
            msg = build_alert(game, reason)
            for cid in subs:
                tg_send(cid, msg)


# ---------- entry point ----------

if __name__ == "__main__":
    logging.info("Bot starting...")
    threading.Thread(target=poll_scores, daemon=True).start()
    logging.info("Polling for updates... Send /start to your bot in Telegram.")
    poll_updates()
