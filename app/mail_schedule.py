"""When to poll a mailbox: every N minutes ('interval', the original behaviour) or at fixed local times
('fixed': one or more HH:MM in the app time zone, setting `timezone`, optionally only on some weekdays 0=Mon…6=Sun).

Fixed times: a mailbox is due when the most recent scheduled slot (<= now) has not run yet. If the app was down at
slot time, that slot runs once when it comes back (only the latest missed slot, not every missed one). A run claims
its slot atomically (mailboxes.last_slot) before polling, so two runners can't both poll the same slot. Manual
"Check now" doesn't touch the slot bookkeeping."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .db import Database

MODES = ("interval", "fixed")
TIME_RX = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
FMT = "%Y-%m-%d %H:%M"


class ScheduleError(ValueError):
    pass


def parse_times(s) -> list[str]:
    items = s if isinstance(s, (list, tuple)) else re.split(r"[,\s;，]+", str(s or ""))
    out = set()
    for x in items:
        x = str(x).strip()
        if not x:
            continue
        m = TIME_RX.match(x)
        if not m:
            raise ScheduleError(f"Invalid time '{x}': use HH:MM (24h), e.g. 08:00, 18:30")
        out.add(f"{int(m.group(1)):02d}:{m.group(2)}")
    return sorted(out)


def parse_weekdays(s) -> list[int]:
    items = s if isinstance(s, (list, tuple)) else re.split(r"[,\s;]+", str(s or ""))
    out = set()
    for x in items:
        if str(x).strip() == "":
            continue
        try:
            d = int(x)
        except ValueError:
            raise ScheduleError("weekdays: numbers 0 (Mon) … 6 (Sun)")
        if not 0 <= d <= 6:
            raise ScheduleError("weekdays: numbers 0 (Mon) … 6 (Sun)")
        out.add(d)
    return sorted(out)


def tz(db: Database):
    from .schedules import tz as _tz
    return _tz(db)


def local_now(db: Database, now: datetime | None = None) -> datetime:
    """Naive local datetime in the app time zone (`now` may be aware, or naive = already local)."""
    z = tz(db)
    if now is None:
        return datetime.now(z).replace(tzinfo=None)
    return now.astimezone(z).replace(tzinfo=None) if now.tzinfo else now


def _slots_on(mb: dict, day) -> list[datetime]:
    wd = parse_weekdays(mb.get("schedule_weekdays") or "")
    if wd and day.weekday() not in wd:
        return []
    return [datetime.combine(day, datetime.strptime(t, "%H:%M").time()) for t in parse_times(mb.get("schedule_times") or "")]


def latest_slot(mb: dict, now: datetime) -> datetime | None:
    """Most recent fixed slot <= now (local naive)."""
    for back in range(0, 8):
        day = (now - timedelta(days=back)).date()
        past = [s for s in _slots_on(mb, day) if s <= now]
        if past:
            return max(past)
    return None


def next_slot(mb: dict, now: datetime) -> datetime | None:
    for ahead in range(0, 8):
        day = (now + timedelta(days=ahead)).date()
        fut = [s for s in _slots_on(mb, day) if s > now]
        if fut:
            return min(fut)
    return None


def _parse(s) -> datetime | None:
    if not s:
        return None
    for f in ("%Y-%m-%d %H:%M:%S", FMT):
        try:
            return datetime.strptime(str(s)[:19], f)
        except ValueError:
            continue
    return None


def is_due(db: Database, mb: dict, now: datetime | None = None) -> tuple[bool, str | None]:
    """(due?, slot key to claim). Interval mode returns slot None."""
    if not mb.get("enabled", 1):
        return False, None
    if (mb.get("schedule_mode") or "interval") != "fixed":
        last = _parse(mb.get("last_checked_at"))
        n = datetime.now() if now is None else (now.astimezone().replace(tzinfo=None) if now.tzinfo else now)
        return (last is None or n - last >= timedelta(minutes=max(1, int(mb.get("interval_minutes") or 15)))), None
    ln = local_now(db, now)
    s = latest_slot(mb, ln)
    if not s:
        return False, None
    key = s.strftime(FMT)
    done = mb.get("last_slot")
    if done and done >= key:
        return False, None
    # never-run mailbox switched to fixed times: only slots after it was set up count (no surprise catch-up)
    base = _parse(mb.get("schedule_since"))
    if not done and base and s < base:
        return False, None
    return True, key


def claim(db: Database, mb_id: int, key: str) -> bool:
    """Atomically mark slot `key` as run; False if another runner already claimed it (or a later one)."""
    with db.tx() as con:
        cur = con.execute("UPDATE mailboxes SET last_slot=? WHERE id=? AND (last_slot IS NULL OR last_slot < ?)",
                          (key, mb_id, key))
        return cur.rowcount == 1


def next_run(db: Database, mb: dict, now: datetime | None = None) -> str | None:
    """Next automatic check as local 'YYYY-MM-DD HH:MM' (app time zone for fixed; server time for interval)."""
    if not mb.get("enabled", 1):
        return None
    if (mb.get("schedule_mode") or "interval") == "fixed":
        due, key = is_due(db, mb, now)
        if due:
            return key  # missed/now: runs within the next minute
        s = next_slot(mb, local_now(db, now))
        return s.strftime(FMT) if s else None
    last = _parse(mb.get("last_checked_at"))
    n = datetime.now() if now is None else (now.astimezone().replace(tzinfo=None) if now.tzinfo else now)
    if not last:
        return n.strftime(FMT)
    return max(n, last + timedelta(minutes=max(1, int(mb.get("interval_minutes") or 15)))).strftime(FMT)


def describe(mb: dict) -> str:
    if (mb.get("schedule_mode") or "interval") != "fixed":
        return f"every {mb.get('interval_minutes') or 15} min"
    days = parse_weekdays(mb.get("schedule_weekdays") or "")
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return "at " + ", ".join(parse_times(mb.get("schedule_times") or "")) + (" on " + ", ".join(names[d] for d in days) if days else " daily")
