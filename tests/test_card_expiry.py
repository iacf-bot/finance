"""Credit card expiry 信用卡到期: lead window, linked schedules, per-schedule confirm/reassign, card confirmation,
resolution, reset on a new expiry, notify/email output and the additive migration. FAKE data only."""
import sqlite3
from datetime import date

import pytest

from app import email_reports, reminders as R
from app.db import Database
from tests.test_principal_returns import mk

TODAY = date(2026, 6, 15)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(R, "today", lambda db: TODAY)


def card(client, **kw):
    base = dict(name="Test Visa", type="credit_card", currency="USD", opening_balance=0, start_date="2026-01-01",
                last4="0000", card_expiry="07/26")
    base.update(kw)
    return mk(client, **base)


def sched(client, name, acct, post_mode="post", **kw):
    r = client.post("/api/schedules", json={"name": name, "freq": "monthly", "month_mode": "day", "month_day": 3,
                                            "start_date": "2026-01-03", "post_mode": post_mode,
                                            "template": {"kind": "expense", "account_id": acct, "amount": 15}, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def exp_rows(db, view="open", on=TODAY):
    return [r for r in R.listing(db, view, on)["items"] if r["kind"] == "card_expiry"]


def test_parse_and_account_field(client):
    assert R.parse_expiry("08/29") == "2029-08" and R.parse_expiry("8/2029") == "2029-08"
    assert R.parse_expiry("2029-08") == "2029-08" and R.parse_expiry("") is None
    assert R.expiry_end("2028-02") == date(2028, 2, 29) and R.expiry_label("2029-08") == "08/2029"
    for bad in ("13/29", "ab", "0/29", "08/1999"):
        with pytest.raises(ValueError):
            R.parse_expiry(bad)
    assert client.post("/api/accounts", json={"name": "Bad", "type": "credit_card", "currency": "USD",
                                              "card_expiry": "14/27"}).status_code == 400
    c = card(client)
    a = next(x for x in client.get("/api/accounts").json()["accounts"] if x["id"] == c["id"])
    assert a["card_expiry"] == "2026-07"
    assert client.put(f"/api/accounts/{c['id']}", json={"card_expiry": ""}).status_code == 200
    a = next(x for x in client.get("/api/accounts").json()["accounts"] if x["id"] == c["id"])
    assert a["card_expiry"] is None


def test_lead_window_default_60_and_setting(client, db):
    c = card(client, card_expiry="09/26")                          # ends 2026-09-30: 107 days after TODAY
    assert exp_rows(db) == []
    on = date(2026, 8, 1)                                          # 60 days before 09-30 is 08-01
    assert [r["title"] for r in exp_rows(db, "open", on)] == ["Test Visa"]
    r = exp_rows(db, "open", on)[0]
    assert (r["due_date"], r["expiry_label"], r["state"], r["lead_days"]) == ("2026-09-30", "09/2026", "upcoming", 60)
    assert R.badge(db, on) == 1
    c2 = card(client, name="Other Card", last4="1111", card_expiry="10/26")   # ends 10-31: 138 days out
    assert client.put("/api/settings", json={"card_expiry_lead_days": "abc"}).status_code == 400
    assert client.put("/api/settings", json={"card_expiry_lead_days": "140"}).json()["card_expiry_lead_days"] == "140"
    assert {r["account_id"] for r in exp_rows(db)} == {c["id"], c2["id"]}
    archived = card(client, name="Old Card", card_expiry="07/26", archived=1)
    assert archived["id"] not in {r["account_id"] for r in exp_rows(db)}      # archived cards never remind
    cash = mk(client, name="Test Cash", type="cash", currency="USD", opening_balance=0, card_expiry="07/26")
    assert cash["id"] not in {r["account_id"] for r in exp_rows(db)}          # credit cards only


def test_linked_schedules_listed_incl_remind_only(client, db):
    c = card(client)
    chk = mk(client, name="Test Checking", type="checking", currency="USD", opening_balance=100, start_date="2026-01-01")
    a = sched(client, "Streaming", c["id"])
    b = sched(client, "Gym", c["id"], post_mode="remind")
    sched(client, "Rent", chk["id"])                               # other account: not linked
    p = sched(client, "Paused sub", c["id"])
    client.post(f"/api/schedules/{p['id']}/pause")                 # paused: not active
    sched(client, "Ended", c["id"], end_date="2026-03-31")          # ended: not active
    r = exp_rows(db)[0]
    assert sorted(s["name"] for s in r["card_schedules"]) == ["Gym", "Streaming"]
    assert r["pending_count"] == 2 and not r["card_confirmed"] and not r["resolved"]
    assert {s["schedule_id"] for s in r["card_schedules"]} == {a["id"], b["id"]}
    assert all(s["status"] == "pending" and s["next_date"] for s in r["card_schedules"])


def test_confirm_reassign_and_resolution(client, db):
    c = card(client)
    chk = mk(client, name="Test Checking", type="checking", currency="USD", opening_balance=100, start_date="2026-01-01")
    new_card = card(client, name="New Card", last4="2222", card_expiry="05/31")
    a = sched(client, "Streaming", c["id"])
    b = sched(client, "Gym", c["id"], post_mode="remind")
    rid = exp_rows(db)[0]["id"]
    assert client.post(f"/api/reminders/{rid}/schedules/{a['id']}/confirm").json()["pending_count"] == 1
    bad = client.post(f"/api/reminders/{rid}/schedules/{b['id']}/reassign", json={"account_id": c["id"]})
    assert bad.status_code == 400                                   # same card: use confirm instead
    r = client.post(f"/api/reminders/{rid}/schedules/{b['id']}/reassign", json={"account_id": new_card["id"]}).json()
    st = {s["name"]: s for s in r["card_schedules"]}
    assert st["Streaming"]["status"] == "confirmed" and st["Gym"]["status"] == "reassigned"
    assert st["Gym"]["to_account_name"] == "New Card" and r["pending_count"] == 0
    assert client.get(f"/api/schedules/{b['id']}").json()["template"]["account_id"] == new_card["id"]
    assert r["status"] == "open" and not r["resolved"]               # card itself not confirmed yet
    assert client.post(f"/api/reminders/{rid}/schedules/{chk['id'] + 999}/confirm").status_code == 400
    assert client.post(f"/api/reminders/{rid}/paid", json={}).status_code == 400
    r = client.post(f"/api/reminders/{rid}/card", json={"action": "acknowledge"}).json()
    assert r["resolved"] and r["status"] == "done" and r["done_reason"] == "resolved"
    assert exp_rows(db) == [] and R.badge(db) == 0
    # undo a confirmation on a reopened reminder brings the schedule back to pending
    client.post(f"/api/reminders/{rid}/reopen")
    client.post(f"/api/reminders/{rid}/card", json={"action": "undo"})
    r = client.post(f"/api/reminders/{rid}/schedules/{a['id']}/confirm", json={"undo": True}).json()
    assert r["status"] == "open" and r["pending_count"] == 1 and not r["card_confirmed"]


def test_renew_needs_schedules_too_then_new_cycle_resets(client, db):
    c = card(client)
    a = sched(client, "Streaming", c["id"])
    rid = exp_rows(db)[0]["id"]
    assert client.post(f"/api/reminders/{rid}/card", json={"action": "renew", "expiry": "06/26"}).status_code == 400  # not later
    r = client.post(f"/api/reminders/{rid}/card", json={"action": "renew", "expiry": "08/31"}).json()
    assert r["card_confirmed"] and r["card_state_eff"] == "renewed" and r["new_expiry_label"] == "08/2031"
    assert r["status"] == "open" and r["pending_count"] == 1          # a schedule still uses the card
    acct = next(x for x in client.get("/api/accounts").json()["accounts"] if x["id"] == c["id"])
    assert acct["card_expiry"] == "2031-08"
    r = client.post(f"/api/reminders/{rid}/schedules/{a['id']}/confirm").json()
    assert r["status"] == "done" and r["done_reason"] == "resolved"
    # 5 years later the renewed card enters its window: a NEW reminder with fresh (pending) confirmations
    later = date(2031, 7, 15)
    rows = exp_rows(db, "open", later)
    assert len(rows) == 1 and rows[0]["id"] != rid and rows[0]["expiry"] == "2031-08"
    assert rows[0]["pending_count"] == 1 and rows[0]["card_schedules"][0]["status"] == "pending"


def test_editing_expiry_on_account_confirms_or_retires(client, db):
    c = card(client)
    rid = exp_rows(db)[0]["id"]
    assert exp_rows(db)[0]["pending_count"] == 0
    client.put(f"/api/accounts/{c['id']}", json={"card_expiry": "08/30"})   # renewed on the account form, no schedules
    r = R.get(db, rid)
    assert r["status"] == "done" and r["done_reason"] == "resolved" and r["card_state"] == "renewed"
    d = card(client, name="Typo Card", last4="3333", card_expiry="07/26")
    rid2 = [r for r in exp_rows(db) if r["account_id"] == d["id"]][0]["id"]
    client.put(f"/api/accounts/{d['id']}", json={"card_expiry": "06/26"})   # corrected to an earlier month
    assert R.get(db, rid2)["status"] == "dismissed" and R.get(db, rid2)["done_reason"] == "expiry_changed"
    assert [r["expiry"] for r in exp_rows(db) if r["account_id"] == d["id"]] == ["2026-06"]
    client.put(f"/api/accounts/{d['id']}", json={"archived": 1})            # archived card = card confirmed (closed)
    assert not [r for r in exp_rows(db) if r["account_id"] == d["id"]]


def test_done_warns_and_expired_is_overdue(client, db):
    c = card(client, card_expiry="05/26")                           # expired 2026-05-31
    sched(client, "Streaming", c["id"])
    r = exp_rows(db)[0]
    assert r["state"] == "overdue" and r["expired"] and r["days_left"] == -15
    out = client.post(f"/api/reminders/{r['id']}/done").json()
    assert out["status"] == "done" and "Streaming" in out["warning"]
    c2 = card(client, name="Calm Card", last4="4444", card_expiry="07/26")
    r2 = [r for r in exp_rows(db) if r["account_id"] == c2["id"]][0]
    assert "warning" not in client.post(f"/api/reminders/{r2['id']}/done").json()


def test_notify_and_email_output(client, db):
    c = card(client, card_expiry="07/26")
    sched(client, "Streaming", c["id"])
    sched(client, "Gym", c["id"], post_mode="remind")
    e = client.get("/api/reminders/notify?lang=en&days=7").json()     # 46 days out but inside the 60-day window
    it = [i for i in e["items"] if i["kind"] == "card_expiry"][0]
    assert e["count"] == 1 and it["expiry_label"] == "07/2026" and sorted(it["pending_names"]) == ["Gym", "Streaming"]
    assert "Test Visa ••0000 expires 07/2026" in e["message"] and "Update the payment card for: Gym, Streaming" in e["message"]
    z = client.get("/api/reminders/notify?lang=zh").json()
    assert "将于 07/2026 到期" in z["message"] and "请更新以下定期付款的卡：Gym、Streaming" in z["message"]
    assert client.get("/api/reminders/notify?include_overdue=false").json()["count"] == 1
    m = email_reports.build(db, "reminders", TODAY, "en")
    assert m["count"] == 1 and "expires 07/2026" in m["text"] and "Update the payment card for: Gym, Streaming" in m["text"]
    assert "請更新以下定期付款的卡" in email_reports.build(db, "reminders", TODAY, "zh-TW")["text"]
    rid = exp_rows(db)[0]["id"]
    client.put(f"/api/accounts/{c['id']}", json={"card_expiry": "07/26", "archived": 0})
    for s in exp_rows(db)[0]["card_schedules"]:
        client.post(f"/api/reminders/{rid}/schedules/{s['schedule_id']}/confirm")
    client.post(f"/api/reminders/{rid}/card", json={"action": "acknowledge"})
    assert client.get("/api/reminders/notify?lang=en").json()["count"] == 0      # resolved: the reminder stops


def test_migration_backup_and_columns(tmp_path):
    p = tmp_path / "finance.db"
    db = Database(p)
    db.init()
    con = sqlite3.connect(p)
    con.execute("DROP INDEX ux_rem_cexp")
    con.execute("DROP TABLE card_expiry_checks")
    for t, col in (("accounts", "card_expiry"), ("reminders", "expiry"), ("reminders", "card_state"),
                   ("reminders", "card_state_at")):
        con.execute(f"ALTER TABLE {t} DROP COLUMN {col}")
    con.execute("INSERT INTO accounts(name, type, currency) VALUES('Old Card', 'credit_card', 'USD')")
    con.commit(); con.close()
    db2 = Database(p)
    assert set(db2.pending_migrations()) >= {"accounts.card_expiry", "reminders.expiry", "card_expiry_checks"}
    db2.init()
    assert db2.last_migration_backup and db2.last_migration_backup.exists()
    assert "before-migrate" in db2.last_migration_backup.name
    assert db2.pending_migrations() == []
    assert db2.one("SELECT card_expiry FROM accounts WHERE name='Old Card'")["card_expiry"] is None   # existing rows kept
    cols = {r["name"] for r in db2.all("PRAGMA table_info(reminders)")}
    assert {"expiry", "card_state", "card_state_at"} <= cols
    db3 = Database(p)
    db3.init()
    assert db3.last_migration_backup is None                                    # up to date: no extra backup
