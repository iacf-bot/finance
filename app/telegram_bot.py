"""Optional Telegram bot: send quick-add text, get a confirmation back.

Enabled only when TELEGRAM_BOT_TOKEN is set. Restrict who can use it with
TELEGRAM_ALLOWED_CHAT_IDS (comma-separated). Without it the bot only replies with the chat id.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import httpx

from .db import Database
from .ingest import quick_add

log = logging.getLogger("finance.telegram")


def _fmt(res: dict) -> str:
    if res.get("action") in ("set_balance", "reconcile"):
        return ("⚖️ " if res.get("status") == "posted" else "ℹ️ ") + (res.get("detail") or res.get("message", ""))
    t = res.get("transaction") or {}
    p = res["parsed"]
    head = "✅ Recorded" if res["status"] == "posted" else "📝 Sent to To-review"
    parts = [f"{head} (confidence {res['confidence']:.2f})", f"{t.get('kind', p['kind'])} {t.get('amount', p['amount'])} {t.get('currency') or ''}"]
    if t.get("account_name"):
        parts.append(f"account: {t['account_name']}" + (f" → {t['to_account_name']}" if t.get("to_account_name") else ""))
    if t.get("category_label"):
        parts.append(f"category: {t['category_label']}")
    parts.append(f"id #{t.get('id')}")
    return "\n".join(parts)


class TelegramBot(threading.Thread):
    def __init__(self, db: Database, token: str, allowed: set[str]):
        super().__init__(daemon=True, name="telegram-bot")
        self.db, self.api, self.allowed = db, f"https://api.telegram.org/bot{token}", allowed
        self.offset = 0
        self.stop_flag = threading.Event()

    def send(self, chat_id, text):
        try:
            httpx.post(f"{self.api}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=20)
        except Exception as e:  # noqa: BLE001
            log.warning("telegram send failed: %s", e)

    def handle(self, msg: dict):
        chat_id = str(msg["chat"]["id"])
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if chat_id not in self.allowed:
            self.send(chat_id, f"Not authorized. Add this chat id to TELEGRAM_ALLOWED_CHAT_IDS: {chat_id}")
            return
        if text in ("/start", "/help"):
            self.send(chat_id, "Send things like:\nlunch 25.5 chase sapphire\n午饭 35 支付宝\nsalary 5000 to checking\npaid 300 from checking to amex")
            return
        try:
            self.send(chat_id, _fmt(quick_add(self.db, text, source="bot")))
        except Exception as e:  # noqa: BLE001
            self.send(chat_id, f"⚠️ Error: {e}")

    def run(self):
        log.info("Telegram bot polling started")
        while not self.stop_flag.is_set():
            try:
                r = httpx.get(f"{self.api}/getUpdates", params={"timeout": 30, "offset": self.offset}, timeout=40)
                for u in r.json().get("result", []):
                    self.offset = u["update_id"] + 1
                    if "message" in u:
                        self.handle(u["message"])
            except Exception as e:  # noqa: BLE001
                log.warning("telegram polling error: %s", e)
                time.sleep(10)


def start_if_configured(db: Database) -> TelegramBot | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return None
    allowed = {x.strip() for x in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if x.strip()}
    bot = TelegramBot(db, token, allowed)
    bot.start()
    return bot
