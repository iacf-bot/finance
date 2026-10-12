"""Reminder Center 提醒中心: credit-card statement/payment reminders and 'remind only' schedules.

Credit cards (accounts.stmt_day = 账单日, due_day = 还款日 or due_after_days = N days after the statement):
* each closed cycle gets one reminder (statement date, due date, statement balance = amount owed at the end of the
  statement date, honouring reconciliation checkpoints); only the latest closed cycle is generated (no backfill)
* auto-completed when transfers into the card after the statement date add up to the statement amount
  (amount_override if the user typed the real 账单金额); partial payments show the remaining amount
Schedules with post_mode='remind' create a reminder per occurrence instead of a transaction; marking it paid can create
the transaction from the schedule's template, and the reminder completes when that linked transaction is posted.
The job is idempotent (unique keys) and only touches accounts/schedules the user configured.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta

from . import ledger
from .db import Database, now_iso

log = logging.getLogger(__name__)
VIEWS = ("upcoming", "later", "all_upcoming", "overdue", "snoozed", "done", "open", "all")


class ReminderError(ValueError):
    pass


def _d(s) -> date | None:
    if not s:
        return None
    if isinstance(s, date):
        return s
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def today(db: Database) -> date:
    from .schedules import local_today
    return local_today(db)


def _dim(y: int, m: int, day: int) -> date:
    """Day `day` of month y-m, clamped to the month end (31 -> Feb 28/29)."""
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return date(y, m, min(day, (nxt - timedelta(days=1)).day))


def _shift(y: int, m: int, n: int) -> tuple[int, int]:
    k = y * 12 + (m - 1) + n
    return k // 12, k % 12 + 1


def statement_date(stmt_day: int, y: int, m: int) -> date:
    return _dim(y, m, int(stmt_day))


def last_statement(stmt_day: int, on: date) -> date:
    """Latest statement date <= on."""
    s = statement_date(stmt_day, on.year, on.month)
    if s <= on:
        return s
    y, m = _shift(on.year, on.month, -1)
    return statement_date(stmt_day, y, m)


def prev_statement(stmt_day: int, s: date) -> date:
    y, m = _shift(s.year, s.month, -1)
    return statement_date(stmt_day, y, m)


def next_statement(stmt_day: int, s: date) -> date:
    y, m = _shift(s.year, s.month, 1)
    return statement_date(stmt_day, y, m)


def due_date(acct: dict, s: date) -> date | None:
    """Payment due date for statement date s: N days after (due_after_days) or the next due_day after s."""
    if acct.get("due_after_days"):
        return s + timedelta(days=int(acct["due_after_days"]))
    if acct.get("due_day"):
        d = _dim(s.year, s.month, int(acct["due_day"]))
        if d <= s:
            y, m = _shift(s.year, s.month, 1)
            d = _dim(y, m, int(acct["due_day"]))
        return d
    return None


# ---------------------------------------------------------------- generation
def owed_at(db: Database, aid: int, d: date) -> float:
    """Amount owed on a liability account at the end of day d (reconciliation checkpoints honoured)."""
    return round(-ledger.account_balance_at(db, aid, f"{d.isoformat()} 23:59"), 2) + 0.0


def payments_after(db: Database, aid: int, s: date, until: date | None = None) -> float:
    """Posted money into the card after the statement date: transfers in (+ refunds/income booked on the card)."""
    q = """SELECT COALESCE(SUM(CASE WHEN kind='transfer' THEN COALESCE(to_amount, amount) ELSE amount END), 0) v
           FROM transactions WHERE status='posted' AND date > ? AND date <= ?
           AND ((kind='transfer' AND to_account_id=?) OR (kind IN ('refund') AND account_id=?))"""
    hi = (until.isoformat() if until else "9999-12-31") + " 23:59"
    return round(db.one(q, (f"{s.isoformat()} 23:59", hi, aid, aid))["v"], 2) + 0.0


def card_accounts(db: Database) -> list[dict]:
    return db.all("SELECT * FROM accounts WHERE type='credit_card' AND archived=0 AND stmt_day IS NOT NULL "
                  "AND (due_day IS NOT NULL OR due_after_days IS NOT NULL)")


def generate_cards(db: Database, on: date | None = None) -> list[int]:
    """One reminder for the latest closed cycle of each configured card (idempotent). Returns new reminder ids."""
    on = on or today(db)
    new = []
    for a in card_accounts(db):
        s = last_statement(a["stmt_day"], on)
        due = due_date(a, s)
        owed = owed_at(db, a["id"], s)
        amt = max(owed, 0.0)
        mn = round(amt * float(a["min_pay_pct"]) / 100, 2) if a.get("min_pay_pct") and amt else None
        with db.tx() as con:
            if con.execute("SELECT 1 FROM reminders WHERE kind='card' AND account_id=? AND statement_date=?",
                           (a["id"], s.isoformat())).fetchone():
                continue
            st, why = ("done", "nothing_due") if amt < 0.005 else ("open", None)
            rid = con.execute("""INSERT INTO reminders(kind, account_id, title, cycle_start, statement_date, due_date, amount,
                minimum, currency, status, done_reason, autopay, created_at, updated_at) VALUES('card',?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (a["id"], a["name"], (prev_statement(a["stmt_day"], s) + timedelta(days=1)).isoformat(), s.isoformat(),
                 due.isoformat() if due else s.isoformat(), amt, mn, a["currency"], st, why, a.get("autopay") or 0,
                 now_iso(), now_iso())).lastrowid
        new.append(rid)
    return new


def create_for_schedule(con, s: dict, tpl: dict, d: date) -> int:
    """Called by schedules.run for post_mode='remind' (inside its transaction)."""
    acct = con.execute("SELECT currency FROM accounts WHERE id=?", (tpl.get("account_id"),)).fetchone()
    return con.execute("""INSERT INTO reminders(kind, schedule_id, occurrence_date, account_id, to_account_id, title, due_date,
        amount, currency, status, created_at, updated_at) VALUES('schedule',?,?,?,?,?,?,?,?, 'open', ?, ?)""",
        (s["id"], d.isoformat(), tpl.get("account_id"), tpl.get("to_account_id"), s["name"], d.isoformat(),
         tpl.get("amount"), tpl.get("currency") or (acct[0] if acct else None), now_iso(), now_iso())).lastrowid


def refresh(db: Database, on: date | None = None) -> int:
    """Auto-complete open reminders: cards paid in full; schedule reminders whose linked transaction is posted."""
    n = 0
    for r in db.all("SELECT * FROM reminders WHERE status='open'"):
        why = None
        if r["kind"] == "card":
            amt = r["amount_override"] if r["amount_override"] is not None else r["amount"]
            paid = payments_after(db, r["account_id"], _d(r["statement_date"]))
            if amt is not None and paid + 0.005 >= amt:
                why = "auto_paid"
        elif r["transaction_id"]:
            t = db.one("SELECT status FROM transactions WHERE id=?", (r["transaction_id"],))
            if t and t["status"] == "posted":
                why = "tx_posted"
        if why:
            with db.tx() as con:
                con.execute("UPDATE reminders SET status='done', done_reason=?, done_at=?, updated_at=? WHERE id=? AND status='open'",
                            (why, now_iso(), now_iso(), r["id"]))
            n += 1
    return n


def run(db: Database, on: date | None = None, notify: bool = True) -> dict:
    on = on or today(db)
    new = generate_cards(db, on)
    done = refresh(db, on)
    sent = notify_due(db, on) if notify else 0
    return {"as_of": on.isoformat(), "created": len(new), "auto_completed": done, "notified": sent}


# ---------------------------------------------------------------- reading
def _decorate(db: Database, r: dict, on: date) -> dict:
    r = dict(r)
    amt = r["amount_override"] if r["amount_override"] is not None else r["amount"]
    r["statement_amount"] = amt
    if r["kind"] == "card" and r["statement_date"]:
        paid = payments_after(db, r["account_id"], _d(r["statement_date"]))
        r["paid"] = paid
        r["remaining"] = round(max((amt or 0) - paid, 0), 2) + 0.0
    else:
        r["paid"], r["remaining"] = None, amt
    due = _d(r["due_date"])
    r["days_left"] = (due - on).days if due else None
    snoozed = r["snooze_until"] and _d(r["snooze_until"]) > on
    r["state"] = ("done" if r["status"] in ("done", "dismissed") else "snoozed" if snoozed
                  else "overdue" if due and due < on else "upcoming")
    r["lead_days"] = lead_days(db, r)
    if r["state"] == "upcoming" and r["days_left"] is not None and r["days_left"] > r["lead_days"]:
        r["state"] = "later"  # not shown before its lead window (only in the 'all upcoming' filter)
    if r["account_id"]:
        a = db.one("SELECT name, last4 FROM accounts WHERE id=?", (r["account_id"],)) or {}
        r["account_name"], r["account_last4"] = a.get("name"), a.get("last4")
    return r


def lead_days(db: Database, r: dict) -> int:
    """Days before the due date a reminder shows up: schedule.lead_days, card remind_days, else the global setting
    reminder_lead_days (default 10)."""
    if r.get("kind") == "schedule" and r.get("schedule_id"):
        s = db.one("SELECT lead_days FROM schedules WHERE id=?", (r["schedule_id"],)) or {}
        if s.get("lead_days") is not None:
            return int(s["lead_days"])
    elif r.get("account_id"):
        a = db.one("SELECT remind_days FROM accounts WHERE id=?", (r["account_id"],)) or {}
        if a.get("remind_days") is not None:
            return int(a["remind_days"])
    return int(db.get_setting("reminder_lead_days") or 10)


def listing(db: Database, view: str = "open", on: date | None = None) -> dict:
    on = on or today(db)
    if view not in VIEWS:
        raise ReminderError(f"view must be one of {VIEWS}")
    rows = [_decorate(db, r, on) for r in db.all("SELECT * FROM reminders ORDER BY due_date, id")]
    counts = {k: sum(1 for r in rows if r["state"] == k) for k in ("upcoming", "later", "overdue", "snoozed", "done")}
    if view == "open":
        items = [r for r in rows if r["state"] in ("upcoming", "overdue")]
    elif view == "all_upcoming":
        items = [r for r in rows if r["state"] in ("upcoming", "later")]
    elif view == "all":
        items = rows
    else:
        items = [r for r in rows if r["state"] == view]
    if view == "done":
        items.sort(key=lambda r: (r["due_date"] or ""), reverse=True)
    return {"view": view, "today": on.isoformat(), "items": items, "counts": counts, "badge": badge(db, on, rows)}


def badge(db: Database, on: date | None = None, rows: list | None = None) -> int:
    """Nav badge: overdue + upcoming within each reminder's lead days."""
    on = on or today(db)
    rows = rows if rows is not None else [_decorate(db, r, on) for r in db.all("SELECT * FROM reminders WHERE status='open'")]
    return sum(1 for r in rows if r["state"] in ("overdue", "upcoming"))


def get(db: Database, rid: int) -> dict:
    r = db.one("SELECT * FROM reminders WHERE id=?", (rid,))
    if not r:
        raise KeyError(rid)
    return _decorate(db, r, today(db))


# ---------------------------------------------------------------- actions
def _set(db: Database, rid: int, **kw) -> dict:
    if not db.one("SELECT 1 x FROM reminders WHERE id=?", (rid,)):
        raise KeyError(rid)
    kw["updated_at"] = now_iso()
    with db.tx() as con:
        con.execute(f"UPDATE reminders SET {','.join(k + '=?' for k in kw)} WHERE id=?", list(kw.values()) + [rid])
    return get(db, rid)


EDITABLE = ("amount_override", "minimum", "reference", "note", "due_date", "title")


def edit(db: Database, rid: int, body: dict) -> dict:
    kw = {k: body[k] for k in EDITABLE if k in body}
    for k in ("amount_override", "minimum"):
        if k in kw:
            kw[k] = None if kw[k] in ("", None) else round(float(kw[k]), 2)
    if "due_date" in kw:
        kw["due_date"] = _d(kw["due_date"]).isoformat()
    r = _set(db, rid, **kw)
    refresh(db)  # a lower typed statement amount may already be covered
    return get(db, rid)


def mark_done(db: Database, rid: int, reason: str = "manual") -> dict:
    return _set(db, rid, status="done", done_reason=reason, done_at=now_iso())


def dismiss(db: Database, rid: int) -> dict:
    return _set(db, rid, status="dismissed", done_reason="dismissed", done_at=now_iso())


def reopen(db: Database, rid: int) -> dict:
    return _set(db, rid, status="open", done_reason=None, done_at=None, snooze_until=None)


def snooze(db: Database, rid: int, days: int | None = None, until=None) -> dict:
    d = _d(until) if until else today(db) + timedelta(days=int(days or 1))
    return _set(db, rid, snooze_until=d.isoformat())


def mark_paid(db: Database, rid: int, body: dict) -> dict:
    """Mark paid; optionally create the payment: card -> transfer from `from_account_id` to the card (default amount =
    remaining); schedule reminder -> the schedule's transaction (amount editable)."""
    r = get(db, rid)
    tx = None
    if body.get("create_payment"):
        amount = body.get("amount")
        amount = float(amount) if amount not in (None, "") else (r["remaining"] or r["statement_amount"])
        when = str(body.get("date") or datetime.now().strftime("%Y-%m-%d %H:%M")).replace("T", " ")
        if r["kind"] == "card":
            if not body.get("from_account_id"):
                raise ReminderError("Choose the account the payment comes from")
            tx = ledger.create_tx(db, {"kind": "transfer", "account_id": int(body["from_account_id"]), "to_account_id": r["account_id"],
                                       "amount": amount, "date": when, "note": f"Card payment 还款 · {r['title']}"
                                       + (f" · {r['reference']}" if r.get("reference") else ""), "source": "reminder"})
        else:
            s = db.one("SELECT template, name FROM schedules WHERE id=?", (r["schedule_id"],))
            tpl = json.loads(s["template"]) if s else {}
            data = {**tpl, "amount": amount, "date": when, "source": "reminder"}
            if body.get("from_account_id"):
                data["account_id"] = int(body["from_account_id"])
            tx = ledger.create_tx(db, data)
    kw = {"status": "done", "done_reason": "paid", "done_at": now_iso()}
    if tx:
        kw["transaction_id"] = tx["id"]
    out = _set(db, rid, **kw)
    out["transaction"] = tx
    return out


# ---------------------------------------------------------------- notifications
def _zh(lang: str, db: Database) -> bool:
    if lang in ("zh", "cn"):
        return True
    if lang == "en":
        return False
    return bool(re.search(r"[\u3400-\u9fff]", " ".join(a["name"] for a in db.all("SELECT name FROM accounts WHERE archived=0 LIMIT 50"))))


def digest(db: Database, days: int = 7, include_overdue: bool = True, lang: str = "auto", on: date | None = None) -> dict:
    """Short text for phone notifications (iPhone Shortcuts / Telegram)."""
    on = on or today(db)
    zh = _zh(lang, db)
    rows = listing(db, "open", on)["items"]  # 'open' already excludes items before their lead window
    items = [r for r in rows if (r["state"] == "overdue" and include_overdue) or
             (r["state"] == "upcoming" and r["days_left"] is not None and r["days_left"] <= days)]
    from .ingest import fmt_money
    lines = []
    for r in items:
        amt = r["remaining"] if r["remaining"] is not None else r["statement_amount"]
        due = _d(r["due_date"])
        when = (f"逾期 {-r['days_left']} 天" if zh else f"OVERDUE {-r['days_left']}d") if r["state"] == "overdue" else \
               (("今天" if zh else "today") if r["days_left"] == 0 else ("明天" if zh else "tomorrow") if r["days_left"] == 1
                else f"{due.month}/{due.day}")
        lines.append(f"{'⚠️ ' if r['state'] == 'overdue' else ''}{when} · {r['title']}" + (f" · {fmt_money(amt, r['currency'])}" if amt else ""))
    n = len(items)
    if zh:
        title = f"{n} 条提醒" if n else "没有待办提醒"
        msg = "\n".join(lines) if n else f"未来 {days} 天没有到期的账单 ✓"
    else:
        title = f"{n} reminder{'s' if n != 1 else ''} due" if n else "No reminders due"
        msg = "\n".join(lines) if n else f"Nothing due in the next {days} days ✓"
    return {"count": n, "title": title, "message": msg, "items": [
        {k: r.get(k) for k in ("id", "kind", "title", "due_date", "days_left", "state", "statement_amount", "remaining",
                               "currency", "account_name")} for r in items], "today": on.isoformat()}


def notify_due(db: Database, on: date | None = None) -> int:
    """Optional push (setting reminder_notify = 'telegram'; default off): once per reminder when it enters its lead
    window or becomes overdue."""
    if (db.get_setting("reminder_notify") or "off") != "telegram":
        return 0
    from . import telegram_bot
    sender = telegram_bot.notifier()
    if not sender:
        return 0
    on = on or today(db)
    rows = [r for r in listing(db, "open", on)["items"] if not r.get("notified_at")]
    if not rows:
        return 0
    d = digest(db, 3650, True, "auto", on)
    ids = {r["id"] for r in rows}
    text = d["title"] + "\n" + "\n".join(l for l, it in zip(d["message"].split("\n"), d["items"]) if it["id"] in ids)
    if sender(text):
        with db.tx() as con:
            con.executemany("UPDATE reminders SET notified_at=? WHERE id=?", [(now_iso(), i) for i in ids])
        return len(ids)
    return 0
