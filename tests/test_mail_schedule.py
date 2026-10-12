"""Mailbox polling schedule: interval (default) or fixed local times. FAKE mailbox, no network."""
from datetime import datetime, timezone

from app import ingest, mail_schedule as ms

UTC = timezone.utc


def mk(client, **kw):
    body = {"name": "Test box", "host": "imap.example.test", "username": "test@example.test", **kw}
    r = client.post("/api/mailboxes", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def row(db, mid):
    return db.one("SELECT * FROM mailboxes WHERE id=?", (mid,))


def test_existing_and_default_mailboxes_keep_interval(client, db):
    m = mk(client)
    assert m["schedule_mode"] == "interval" and m["schedule_text"] == "every 15 min" and m["next_run"]
    assert [x["id"] for x in ingest.due_mailboxes(db)] == [m["id"]]      # never checked -> due


def test_next_run_and_weekdays_in_app_timezone(client, db):
    db.set_setting("timezone", "America/Los_Angeles")
    m = mk(client, schedule_mode="fixed", schedule_times="18:30, 8:00", schedule_weekdays="0,2,4")
    assert m["schedule_times"] == "08:00,18:30" and m["schedule_weekdays"] == "0,2,4"
    mb = row(db, m["id"])
    # Wed 2026-10-14 16:00 UTC = 09:00 PDT -> next 18:30 same day
    assert ms.next_run(db, {**mb, "schedule_since": "2026-10-14 08:30"}, datetime(2026, 10, 14, 16, 0, tzinfo=UTC)) == "2026-10-14 18:30"
    # Fri 2026-10-16 19:00 PDT (Sat 02:00 UTC) -> next is Monday 08:00 (weekend skipped)
    assert ms.next_run(db, {**mb, "schedule_since": "2026-10-16 18:45"}, datetime(2026, 10, 17, 2, 0, tzinfo=UTC)) == "2026-10-19 08:00"
    db.set_setting("timezone", "Asia/Shanghai")      # same instant, other zone: 2026-10-14 16:00 UTC = 00:00 Thu CST
    assert ms.next_run(db, {**mb, "schedule_since": "2026-10-14 23:00"}, datetime(2026, 10, 14, 16, 0, tzinfo=UTC)) == "2026-10-16 08:00"
    # a missed slot (Wed 18:30 CST, after setup) shows as due now
    assert ms.next_run(db, {**mb, "schedule_since": "2026-10-14 00:00"}, datetime(2026, 10, 14, 16, 0, tzinfo=UTC)) == "2026-10-14 18:30"
    assert client.put(f"/api/mailboxes/{m['id']}", json={"schedule_times": "25:00"}).status_code == 400
    assert client.put(f"/api/mailboxes/{m['id']}", json={"schedule_times": ""}).status_code == 400
    assert client.post("/api/mailboxes", json={"name": "x", "host": "h", "username": "u", "schedule_mode": "fixed"}).status_code == 400


def test_due_on_time_catch_up_once_and_no_duplicates(client, db):
    db.set_setting("timezone", "America/Los_Angeles")
    m = mk(client, schedule_mode="fixed", schedule_times="08:00,12:00")
    with db.tx() as con:
        con.execute("UPDATE mailboxes SET schedule_since='2026-10-05 07:00' WHERE id=?", (m["id"],))
    at = lambda d, h, mi=0: datetime(2026, 10, d, h, mi)            # naive = already local
    assert ingest.due_mailboxes(db, at(5, 7, 59)) == []              # before the first slot
    assert [x["id"] for x in ingest.due_mailboxes(db, at(5, 8, 0))] == [m["id"]]
    assert ingest.due_mailboxes(db, at(5, 8, 0)) == []               # same slot: claimed, no double run
    assert ingest.due_mailboxes(db, at(5, 11, 0)) == []
    # app down from 11:00 until the next day 09:30: 12:00 and 08:00 were missed -> ONE catch-up run (latest slot)
    assert len(ingest.due_mailboxes(db, at(6, 9, 30))) == 1
    assert row(db, m["id"])["last_slot"] == "2026-10-06 08:00"
    assert ingest.due_mailboxes(db, at(6, 9, 31)) == []
    assert ms.claim(db, m["id"], "2026-10-06 08:00") is False          # a second runner can't claim it again
    assert len(ingest.due_mailboxes(db, at(6, 12, 0))) == 1


def test_switching_to_fixed_does_not_catch_up_old_slots(client, db):
    db.set_setting("timezone", "America/Los_Angeles")
    m = mk(client)
    r = client.put(f"/api/mailboxes/{m['id']}", json={"schedule_mode": "fixed", "schedule_times": "00:01"}).json()
    assert r["schedule_mode"] == "fixed" and row(db, m["id"])["schedule_since"]
    assert ingest.due_mailboxes(db) == []                            # 00:01 today is before the switch
    assert r["next_run"] and r["next_run"][11:] == "00:01"
    client.put(f"/api/mailboxes/{m['id']}", json={"enabled": 0})
    assert client.get("/api/mailboxes").json()[0]["next_run"] is None
