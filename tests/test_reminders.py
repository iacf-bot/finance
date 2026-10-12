"""Reminder Center 提醒中心: card cycles, auto-complete, remind-only schedules, Shortcut notify. FAKE data only."""
from datetime import date

from app import reminders as R
from tests.test_principal_returns import mk, tx


def card(client, **kw):
    base = dict(name="Test Card", type="credit_card", currency="USD", opening_balance=0, start_date="2026-01-01",
                stmt_day=15, due_day=5)
    base.update(kw)
    return mk(client, **base)


def test_cycle_math_month_end_feb_and_n_days():
    assert R.statement_date(31, 2026, 2) == date(2026, 2, 28)
    assert R.statement_date(31, 2028, 2) == date(2028, 2, 29)
    assert R.last_statement(31, date(2026, 3, 30)) == date(2026, 2, 28)
    assert R.last_statement(31, date(2026, 3, 31)) == date(2026, 3, 31)
    assert R.last_statement(15, date(2026, 1, 10)) == date(2025, 12, 15)
    assert R.prev_statement(30, date(2026, 3, 30)) == date(2026, 2, 28)
    assert R.next_statement(31, date(2026, 2, 28)) == date(2026, 3, 31)
    assert R.due_date({"due_day": 5}, date(2026, 12, 15)) == date(2027, 1, 5)      # next month, year rollover
    assert R.due_date({"due_day": 25}, date(2026, 3, 15)) == date(2026, 3, 25)    # same month
    assert R.due_date({"due_day": 31}, date(2026, 1, 31)) == date(2026, 2, 28)    # clamped
    assert R.due_date({"due_after_days": 25}, date(2026, 2, 15)) == date(2026, 3, 12)
    assert R.due_date({"due_after_days": 20, "due_day": 1}, date(2026, 1, 31)) == date(2026, 2, 20)


def test_generation_partial_and_auto_complete_idempotent(client, db):
    c = card(client)
    chk = mk(client, name="Test Checking", type="checking", currency="USD", opening_balance=5000, start_date="2026-01-01")
    tx(client, kind="expense", account_id=c["id"], amount=300, date="2026-03-10")
    tx(client, kind="expense", account_id=c["id"], amount=50, date="2026-03-20")   # next cycle
    on = date(2026, 3, 22)
    assert R.run(db, on, notify=False)["created"] == 1
    assert R.run(db, on, notify=False)["created"] == 0          # idempotent
    r = R.listing(db, "all_upcoming", on)["items"][0]
    assert (r["statement_date"], r["due_date"], r["statement_amount"], r["remaining"]) == ("2026-03-15", "2026-04-05", 300, 300)
    tx(client, kind="transfer", account_id=chk["id"], to_account_id=c["id"], amount=100, date="2026-03-21")
    R.refresh(db)
    r = R.get(db, r["id"])
    assert r["status"] == "open" and r["paid"] == 100 and r["remaining"] == 200
    tx(client, kind="transfer", account_id=chk["id"], to_account_id=c["id"], amount=200, date="2026-03-25")
    R.refresh(db)
    r = R.get(db, r["id"])
    assert r["status"] == "done" and r["done_reason"] == "auto_paid"
    assert any(x["id"] == r["id"] for x in R.listing(db, "done", on)["items"])


def test_override_amount_overdue_snooze_and_nothing_due(client, db):
    c = card(client, due_after_days=10, due_day=None)
    tx(client, kind="expense", account_id=c["id"], amount=80, date="2026-03-01")
    R.run(db, date(2026, 3, 16), notify=False)
    rid = R.listing(db, "all_upcoming", date(2026, 3, 16))["items"][0]["id"]
    assert R.get(db, rid)["due_date"] == "2026-03-25"
    assert client.put(f"/api/reminders/{rid}", json={"amount_override": 75.5, "reference": "ST-1"}).json()["statement_amount"] == 75.5
    assert R.listing(db, "overdue", date(2026, 3, 30))["items"][0]["id"] == rid
    assert R.badge(db, date(2026, 3, 30)) == 1
    R.snooze(db, rid, until="2026-04-02")
    assert R.listing(db, "snoozed", date(2026, 3, 30))["counts"]["snoozed"] == 1
    assert R.badge(db, date(2026, 3, 30)) == 0
    e = card(client, name="Empty Card")
    R.run(db, date(2026, 3, 16), notify=False)
    z = [x for x in R.listing(db, "done", date(2026, 3, 16))["items"] if x["account_id"] == e["id"]][0]
    assert z["done_reason"] == "nothing_due"


def test_mark_paid_creates_transfer(client, db):
    c = card(client)
    chk = mk(client, name="Test Checking", type="checking", currency="USD", opening_balance=1000, start_date="2026-01-01")
    tx(client, kind="expense", account_id=c["id"], amount=120, date="2026-03-10")
    R.run(db, date(2026, 3, 20), notify=False)
    rid = R.listing(db, "all_upcoming", date(2026, 3, 20))["items"][0]["id"]
    assert client.post(f"/api/reminders/{rid}/paid", json={"create_payment": True}).status_code == 400  # no from account
    r = client.post(f"/api/reminders/{rid}/paid", json={"create_payment": True, "from_account_id": chk["id"]}).json()
    t = r["transaction"]
    assert r["status"] == "done" and t["kind"] == "transfer" and t["amount"] == 120 and t["to_account_id"] == c["id"]
    assert client.post(f"/api/reminders/{rid}/reopen").json()["status"] == "open"
    assert client.post(f"/api/reminders/{rid}/dismiss").json()["status"] == "dismissed"


def test_remind_only_one_time_schedule(client, db):
    chk = mk(client, name="Test Checking", type="checking", currency="USD", opening_balance=1000, start_date="2026-01-01")
    s = client.post("/api/schedules", json={"name": "Property tax", "freq": "once", "start_date": "2026-10-15",
                                            "post_mode": "remind", "template": {"kind": "expense", "account_id": chk["id"], "amount": 900}})
    assert s.status_code == 200, s.text
    n = client.get("/api/transactions").json()
    before = n.get("total", len(n.get("items", n.get("transactions", []))))
    run = client.post("/api/schedules/run", json={"date": "2026-10-12"}).json()
    client.post("/api/schedules/run", json={"date": "2026-10-12"})
    rows = [r for r in R.listing(db, "all", date(2026, 10, 12))["items"] if r["kind"] == "schedule"]
    assert len(rows) == 1 and rows[0]["due_date"] == "2026-10-15" and rows[0]["statement_amount"] == 900, run
    n = client.get("/api/transactions").json()
    assert n.get("total", len(n.get("items", n.get("transactions", [])))) == before   # no transaction posted
    r = client.post(f"/api/reminders/{rows[0]['id']}/paid", json={"create_payment": True, "amount": 905}).json()
    assert r["transaction"]["amount"] == 905 and r["status"] == "done"


def test_notify_endpoint(client, db):
    e = client.get("/api/reminders/notify?lang=en").json()
    assert e["count"] == 0 and e["title"] == "No reminders due" and "Nothing due" in e["message"] and e["items"] == []
    assert client.get("/api/reminders/notify?lang=zh").json()["title"] == "没有待办提醒"
    c = card(client)
    tx(client, kind="expense", account_id=c["id"], amount=42, date="2026-03-10")
    R.run(db, date(2026, 3, 16), notify=False)
    rid = R.listing(db, "all_upcoming", date(2026, 3, 16))["items"][0]["id"]
    d = R.digest(db, 30, True, "en", date(2026, 3, 28))
    assert d["count"] == 1 and d["title"] == "1 reminder due" and "Test Card" in d["message"] and "4/5" in d["message"]
    assert R.digest(db, 7, True, "en", date(2026, 3, 16))["count"] == 0           # outside the window
    late = R.digest(db, 7, True, "zh", date(2026, 4, 8))
    assert late["count"] == 1 and late["title"] == "1 条提醒" and "逾期 3 天" in late["message"]
    assert R.digest(db, 7, False, "en", date(2026, 4, 8))["count"] == 0
    assert client.post(f"/api/reminders/{rid}/done").json()["status"] == "done"
    assert client.post("/api/reminders/99999/done").status_code == 404
    from fastapi.testclient import TestClient
    assert TestClient(client.app).get("/api/reminders/notify").status_code == 401  # X-API-Key required


def test_account_fields_validation_and_meta_badge(client):
    assert client.post("/api/accounts", json={"name": "Bad", "type": "credit_card", "currency": "USD", "stmt_day": 40}).status_code == 400
    c = card(client, remind_days=3, autopay=True)
    a = next(x for x in client.get("/api/accounts").json()["accounts"] if x["id"] == c["id"])
    assert (a["stmt_day"], a["due_day"], a["remind_days"], a["autopay"]) == (15, 5, 3, 1)


def test_lead_window_default_10_per_schedule_and_card(client, db):
    chk = mk(client, name="Test Checking", type="checking", currency="USD", opening_balance=1000, start_date="2026-01-01")
    mk_s = lambda name, d, **kw: client.post("/api/schedules", json={"name": name, "freq": "once", "start_date": d, "post_mode": "remind",
                                                                     "template": {"kind": "expense", "account_id": chk["id"], "amount": 10}, **kw}).json()
    a = mk_s("Default lead", "2026-10-25")                 # 13 days out: not yet created (default 10)
    b = mk_s("Long lead", "2026-11-05", lead_days=30)       # 24 days out, lead 30: shown
    assert client.post("/api/schedules", json={"name": "x", "freq": "once", "start_date": "2026-11-01", "post_mode": "remind",
                                               "lead_days": -1, "template": {"kind": "expense", "account_id": chk["id"], "amount": 1}}).status_code == 400
    client.post("/api/schedules/run", json={"date": "2026-10-12"})
    names = lambda view, on: [r["title"] for r in R.listing(db, view, on)["items"]]
    assert names("open", date(2026, 10, 12)) == ["Long lead"]
    client.post("/api/schedules/run", json={"date": "2026-10-15"})          # now within 10 days of 10-25
    assert names("open", date(2026, 10, 15)) == ["Default lead", "Long lead"]
    # card: statement 10-01, due 10-28 -> outside default 10-day window on 10-12, inside on 10-18
    c = card(client, stmt_day=1, due_day=28)
    tx(client, kind="expense", account_id=c["id"], amount=60, date="2026-09-20")
    R.run(db, date(2026, 10, 12), notify=False)
    assert "Test Card" not in names("open", date(2026, 10, 12))
    assert "Test Card" in names("all_upcoming", date(2026, 10, 12)) and "Test Card" in names("later", date(2026, 10, 12))
    assert "Test Card" not in [i["title"] for i in R.digest(db, 30, True, "en", date(2026, 10, 12))["items"]]  # notify respects lead
    assert "Test Card" in names("open", date(2026, 10, 18))
    client.put(f"/api/accounts/{c['id']}", json={"remind_days": 20})                # card's own lead wins
    assert "Test Card" in names("open", date(2026, 10, 12))
    client.put("/api/settings", json={"reminder_lead_days": "3"})
    assert "Default lead" not in names("open", date(2026, 10, 20))                # 5 days out > global 3
