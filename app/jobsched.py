"""Schedules for settings-based jobs (Drive backup, emailed reports/reminders), same rules as the email-check
schedule (mail_schedule): 'interval' (every N hours) or 'fixed' (HH:MM times in the app time zone, optionally only on
some weekdays 0=Mon…6=Sun, or on one day of the month). Fixed slots run once; a slot missed while the app was down
runs once when it comes back. Slots are claimed atomically in the settings table."""
from __future__ import annotations

from datetime import datetime, timedelta

from . import mail_schedule as ms
from .db import Database

FMT = ms.FMT


def _mb(cfg: dict) -> dict:
    return {"schedule_times": cfg.get("times") or "", "schedule_weekdays": cfg.get("weekdays") or ""}


def _month_ok(cfg: dict, d) -> bool:
    md = int(cfg.get("monthday") or 0)
    if not md:
        return True
    last = ((d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day
    return d.day == min(md, last)  # 31 → last day of short months


def latest_slot(cfg: dict, now: datetime) -> datetime | None:
    for back in range(0, 32):
        day = (now - timedelta(days=back)).date()
        if not _month_ok(cfg, day):
            continue
        past = [s for s in ms._slots_on(_mb(cfg), day) if s <= now]
        if past:
            return max(past)
    return None


def next_slot(cfg: dict, now: datetime) -> datetime | None:
    for ahead in range(0, 63):
        day = (now + timedelta(days=ahead)).date()
        if not _month_ok(cfg, day):
            continue
        fut = [s for s in ms._slots_on(_mb(cfg), day) if s > now]
        if fut:
            return min(fut)
    return None


def is_due(db: Database, cfg: dict, now: datetime | None = None) -> tuple[bool, str | None]:
    """cfg: enabled, mode, interval_hours, times, weekdays, monthday, last_run, last_slot, since."""
    if not cfg.get("enabled"):
        return False, None
    ln = ms.local_now(db, now)
    if (cfg.get("mode") or "fixed") == "interval":
        last = ms._parse(cfg.get("last_run"))
        hours = max(0.25, float(cfg.get("interval_hours") or 24))
        if last is None or ln - last >= timedelta(hours=hours):
            return True, "interval:" + ln.strftime(FMT)
        return False, None
    s = latest_slot(cfg, ln)
    if not s:
        return False, None
    key = s.strftime(FMT)
    if cfg.get("last_slot") and cfg["last_slot"] >= key:
        return False, None
    since = ms._parse(cfg.get("since"))
    if since and s < since:
        return False, None  # just switched on: no surprise catch-up of earlier slots
    return True, key


def next_run(db: Database, cfg: dict, now: datetime | None = None) -> str | None:
    if not cfg.get("enabled"):
        return None
    ln = ms.local_now(db, now)
    due, key = is_due(db, cfg, now)
    if due:
        return ln.strftime(FMT)
    if (cfg.get("mode") or "fixed") == "interval":
        last = ms._parse(cfg.get("last_run"))
        return (last + timedelta(hours=max(0.25, float(cfg.get("interval_hours") or 24)))).strftime(FMT) if last else ln.strftime(FMT)
    s = next_slot(cfg, ln)
    return s.strftime(FMT) if s else None


def claim(db: Database, setting: str, key: str) -> bool:
    """Atomically record slot `key` in setting `setting`; False when it (or a later slot) was already claimed."""
    if key.startswith("interval:"):
        return True
    with db.tx() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (setting,)).fetchone()
        if row and row[0] and row[0] >= key:
            return False
        con.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (setting, key))
    return True


def describe(cfg: dict, zh: bool = False) -> str:
    if (cfg.get("mode") or "fixed") == "interval":
        return f"每 {cfg.get('interval_hours') or 24} 小时" if zh else f"every {cfg.get('interval_hours') or 24} h"
    days = ms.parse_weekdays(cfg.get("weekdays") or "")
    times = ", ".join(ms.parse_times(cfg.get("times") or ""))
    md = int(cfg.get("monthday") or 0)
    if zh:
        nm = "一二三四五六日"
        return (f"每月 {md} 日 " if md else "") + times + (" · 周" + "、".join(nm[d] for d in days) if days else ("" if md else " 每天"))
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return "at " + times + (f" on day {md} of the month" if md else "") + (" on " + ", ".join(names[d] for d in days) if days else ("" if md else " daily"))


def validate(body: dict, prefix: str) -> dict:
    """Cleans schedule fields <prefix>mode / interval_hours / times / weekdays / monthday from a settings body."""
    out = {}
    if prefix + "mode" in body:
        m = str(body[prefix + "mode"] or "fixed")
        if m not in ("interval", "fixed"):
            raise ValueError("schedule mode must be interval or fixed")
        out[prefix + "mode"] = m
    if prefix + "interval_hours" in body:
        try:
            h = float(body[prefix + "interval_hours"])
        except (TypeError, ValueError):
            raise ValueError("interval must be a number of hours")
        if not 0.25 <= h <= 24 * 31:
            raise ValueError("interval must be between 0.25 and 744 hours")
        out[prefix + "interval_hours"] = f"{h:g}"
    if prefix + "times" in body:
        t = ms.parse_times(body[prefix + "times"])
        out[prefix + "times"] = ",".join(t)
    if prefix + "weekdays" in body:
        out[prefix + "weekdays"] = ",".join(str(d) for d in ms.parse_weekdays(body[prefix + "weekdays"]))
    if prefix + "monthday" in body:
        v = str(body[prefix + "monthday"] or "0").strip() or "0"
        if not v.isdigit() or not 0 <= int(v) <= 31:
            raise ValueError("day of month must be 1–31 (0 = any)")
        out[prefix + "monthday"] = v
    return out
