"""Scheduled transactions 周期记账: recurrence rules, idempotent runner, catch-up, review vs auto-post. FAKE data."""
import subprocess
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app import schedules as sc

D = date.fromisoformat


def occ(until="2027-12-31", **rule):
    return [d.isoformat() for d in sc.iter_occurrences(rule, D(until))]


# ---------------------------------------------------------------- recurrence
def test_monthly_month_end_clamping_and_last_day():
    assert occ("2026-06-30", freq="monthly", start_date="2026-01-31", month_day=31) == [
        "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30"]
    assert occ("2026-04-30", freq="monthly", start_date="2026-01-15", month_day=-1) == [
        "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"]
    assert occ("2026-03-31", freq="monthly", start_date="2026-01-30", month_day=30)[1] == "2026-02-28"


def test_leap_year():
    assert occ("2028-12-31", freq="yearly", start_date="2024-02-29", month_day=29) == [
        "2024-02-29", "2025-02-28", "2026-02-28", "2027-02-28", "2028-02-29"]
    assert occ("2028-03-01", freq="monthly", start_date="2028-01-31", month_day=-1)[1] == "2028-02-29"


def test_nth_weekday():
    # 2nd Tuesday (weekday 1) of each month
    assert occ("2026-03-31", freq="monthly", start_date="2026-01-01", month_mode="nth", nth=2, nth_weekday=1) == [
        "2026-01-13", "2026-02-10", "2026-03-10"]
    # last Friday
    assert occ("2026-03-31", freq="monthly", start_date="2026-01-01", month_mode="nth", nth=-1, nth_weekday=4) == [
        "2026-01-30", "2026-02-27", "2026-03-27"]
    # 5th Monday only exists in some months
    assert occ("2026-06-30", freq="monthly", start_date="2026-01-01", month_mode="nth", nth=5, nth_weekday=0) == [
        "2026-03-30", "2026-06-29"]


def test_every_n_and_weekly():
    assert occ("2026-01-20", freq="daily", interval=7, start_date="2026-01-01") == ["2026-01-01", "2026-01-08", "2026-01-15"]
    # weekly Mon+Thu starting on a Wednesday (2026-01-07): first is Thu 01-08
    assert occ("2026-01-19", freq="weekly", weekdays=[0, 3], start_date="2026-01-07") == ["2026-01-08", "2026-01-12", "2026-01-15", "2026-01-19"]
    # every 2 weeks on Friday
    assert occ("2026-02-28", freq="weekly", interval=2, weekdays="4", start_date="2026-01-02") == [
        "2026-01-02", "2026-01-16", "2026-01-30", "2026-02-13", "2026-02-27"]
    assert occ("2026-12-31", freq="quarterly", start_date="2026-01-15") == ["2026-01-15", "2026-04-15", "2026-07-15", "2026-10-15"]
    assert occ("2026-12-31", freq="monthly", interval=5, start_date="2026-01-10") == ["2026-01-10", "2026-06-10", "2026-11-10"]
    assert occ("2030-01-01", freq="once", start_date="2026-05-05") == ["2026-05-05"]


def test_end_conditions():
    assert occ(freq="monthly", start_date="2026-01-05", end_date="2026-03-05") == ["2026-01-05", "2026-02-05", "2026-03-05"]
    assert len(occ(freq="weekly", weekdays=[0, 2, 4], start_date="2026-01-05", max_count=4)) == 4
    with pytest.raises(sc.ScheduleError):
        sc.normalize_rule({"freq": "monthly", "start_date": "2026-02-01", "end_date": "2026-01-01"})
    with pytest.raises(sc.ScheduleError):
        sc.normalize_rule({"freq": "hourly", "start_date": "2026-02-01"})


def test_dst_keeps_wall_clock_and_no_duplicates():
    # daily across US spring-forward (2026-03-08) and fall-back (2026-11-01)
    days = occ("2026-03-10", freq="daily", start_date="2026-03-06", time_of_day="02:30")
    assert days == ["2026-03-06", "2026-03-07", "2026-03-08", "2026-03-09", "2026-03-10"]
    days = occ("2026-11-03", freq="daily", start_date="2026-10-30")
    assert len(days) == len(set(days)) == 5
    # the daily trigger uses the user's local date: 23:30 PT on Nov 1 is already Nov 2 in UTC
    t = datetime(2026, 11, 2, 7, 30, tzinfo=ZoneInfo("UTC"))

    class FakeDB:
        def get_setting(self, k, d=None):
            return {"timezone": "America/Los_Angeles", "schedule_run_time": "00:15"}.get(k, d)
    assert sc.local_today(FakeDB(), t) == date(2026, 11, 1)
    assert sc.auto_run_due(FakeDB(), datetime(2026, 3, 8, 8, 10, tzinfo=ZoneInfo("UTC"))) is False  # 00:10 PST
    assert sc.auto_run_due(FakeDB(), datetime(2026, 3, 8, 8, 20, tzinfo=ZoneInfo("UTC"))) is True   # 00:20 PST


# ---------------------------------------------------------------- runner via API
def mk_sched(client, **kw):
    r = client.post("/api/schedules", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def tx_of(client, sid):
    return client.get("/api/transactions", params={"schedule_id": sid, "status": "all", "limit": 500}).json()["items"]


def test_idempotent_and_catch_up(client, accounts):
    client.put("/api/settings", json={"schedule_catchup_days": "365"})
    s = mk_sched(client, template={"kind": "expense", "amount": 15.99, "account_id": accounts["sapphire"]["id"], "merchant": "Fake Stream"},
                 freq="monthly", start_date="2026-01-03", time_of_day="08:00")
    r = client.post("/api/schedules/run", json={"date": "2026-04-10"}).json()
    assert r["generated"] == 4 and not r["errors"]
    again = client.post("/api/schedules/run", json={"date": "2026-04-10"}).json()
    assert again["generated"] == 0
    rows = tx_of(client, s["id"])
    assert sorted(t["date"] for t in rows) == ["2026-01-03 08:00", "2026-02-03 08:00", "2026-03-03 08:00", "2026-04-03 08:00"]
    assert all(t["source"] == "scheduled" and t["status"] == "posted" and t["schedule_id"] == s["id"] for t in rows)
    # deleting a generated transaction does not make it come back
    client.delete(f"/api/transactions/{rows[0]['id']}")
    assert client.post("/api/schedules/run", json={"date": "2026-04-10"}).json()["generated"] == 0
    # catch-up cap: a long outage only back-fills the last N days
    client.put("/api/settings", json={"schedule_catchup_days": "40"})
    r = client.post("/api/schedules/run", json={"date": "2026-09-10"}).json()
    assert r["generated"] == 2 and r["skipped_old"] == 3 and "skipped 3 older than 40 days" in r["summary"]
    runs = client.get("/api/schedules/runs").json()["items"]
    assert runs[0]["generated"] == 2 and runs[0]["trigger"] == "manual"
    # dry run writes nothing
    n = len(tx_of(client, s["id"]))
    d = client.post("/api/schedules/run", json={"date": "2026-10-10", "dry_run": True}).json()
    assert d["generated"] == 1 and len(tx_of(client, s["id"])) == n


def test_review_vs_autopost_and_variable(client, accounts):
    a = mk_sched(client, template={"kind": "expense", "amount": 50, "account_id": accounts["checking"]["id"]},
                 freq="once", start_date="2026-02-01", post_mode="review")
    b = mk_sched(client, template={"kind": "expense", "amount": 80, "account_id": accounts["checking"]["id"]},
                 freq="once", start_date="2026-02-01", variable_amount=True)
    c = mk_sched(client, template={"kind": "income", "amount": 1000, "account_id": accounts["checking"]["id"]},
                 freq="once", start_date="2026-02-01")
    r = client.post("/api/schedules/run", json={"date": "2026-02-01"}).json()
    assert r["generated"] == 3 and r["to_review"] == 2
    assert tx_of(client, a["id"])[0]["status"] == "review"
    vb = tx_of(client, b["id"])[0]
    assert vb["status"] == "review" and "confirm the amount" in vb["note"]
    assert tx_of(client, c["id"])[0]["status"] == "posted"


def test_transfer_and_adjustment_schedules(client, accounts):
    s = mk_sched(client, template={"kind": "transfer", "amount": 100, "account_id": accounts["checking"]["id"],
                                   "to_account_id": accounts["alipay"]["id"], "to_amount": 700},
                 freq="weekly", weekdays=[4], start_date="2026-03-02")
    client.post("/api/schedules/run", json={"date": "2026-03-15"})
    rows = tx_of(client, s["id"])
    assert len(rows) == 2 and all(t["to_account_id"] == accounts["alipay"]["id"] and t["to_amount"] == 700 for t in rows)
    adj = mk_sched(client, template={"kind": "adjustment", "amount": 3.2, "account_id": accounts["checking"]["id"], "adj_reason": "interest"},
                   freq="monthly", start_date="2026-03-31", month_day=-1)
    client.post("/api/schedules/run", json={"date": "2026-04-30"})
    rows = tx_of(client, adj["id"])
    assert [t["date"][:10] for t in rows] == ["2026-04-30", "2026-03-31"] and rows[0]["adj_reason"] == "interest"
    # invalid templates are rejected up front
    bad = client.post("/api/schedules", json={"template": {"kind": "transfer", "amount": 5, "account_id": accounts["checking"]["id"]},
                                              "freq": "daily", "start_date": "2026-01-01"})
    assert bad.status_code == 400


def test_edit_pause_delete_and_make_recurring(client, accounts):
    s = mk_sched(client, template={"kind": "expense", "amount": 9, "account_id": accounts["cash"]["id"]}, freq="daily", start_date="2026-05-01")
    client.post("/api/schedules/run", json={"date": "2026-05-03"})
    t = tx_of(client, s["id"])[0]
    client.put(f"/api/transactions/{t['id']}", json={"amount": 12})  # editing a generated tx leaves the schedule alone
    assert client.get(f"/api/schedules/{s['id']}").json()["template"]["amount"] == 9
    u = client.put(f"/api/schedules/{s['id']}", json={"template": {"kind": "expense", "amount": 11, "account_id": accounts["cash"]["id"]}}).json()
    assert u["template"]["amount"] == 11 and u["freq"] == "daily"
    client.post(f"/api/schedules/{s['id']}/pause")
    assert client.post("/api/schedules/run", json={"date": "2026-05-06"}).json()["generated"] == 0
    assert client.get("/api/schedules").json()["items"][0]["next_dates"] == []
    r = client.delete(f"/api/schedules/{s['id']}?keep_history=true").json()
    assert r["kept_history"] and len(tx_of(client, s["id"])) == 3
    s2 = mk_sched(client, template={"kind": "expense", "amount": 1, "account_id": accounts["cash"]["id"]}, freq="daily", start_date="2026-05-01")
    assert s2["id"] != s["id"]  # ids are never reused
    client.post("/api/schedules/run", json={"date": "2026-05-02"})
    assert client.delete(f"/api/schedules/{s2['id']}?keep_history=false").json()["deleted_transactions"] == 2
    # make recurring from an existing transaction: starts one month later (no duplicate of the original)
    orig = client.post("/api/transactions", json={"kind": "expense", "amount": 42, "account_id": accounts["cash"]["id"],
                                                  "merchant": "Fake Gym", "date": "2026-01-31 07:00"}).json()
    pre = client.get(f"/api/schedules/from-transaction/{orig['id']}").json()
    assert pre["start_date"] == "2026-02-28" and pre["month_day"] == 31 and pre["template"]["merchant"] == "Fake Gym"
    assert pre["time_of_day"] == "07:00"


def test_preview_upcoming_and_dashboard(client, accounts):
    today = date.today()
    mk_sched(client, template={"kind": "expense", "amount": 5, "account_id": accounts["cash"]["id"]},
             freq="weekly", weekdays=[0, 1, 2, 3, 4, 5, 6], start_date=today.isoformat())
    p = client.post("/api/schedules/preview", json={"freq": "monthly", "start_date": "2030-01-31", "month_day": -1}).json()
    assert p["next_dates"][:2] == ["2030-01-31 09:00", "2030-02-28 09:00"] and "last day" in p["description"]
    up = client.get("/api/schedules/upcoming", params={"days": 30}).json()["items"]
    assert len(up) >= 30
    assert len(client.get("/api/dashboard").json()["upcoming"]) == 8


def test_cli_dry_run(tmp_path):
    env = {"FINANCE_DB": str(tmp_path / "f.db"), "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, "-m", "app.cli", "run-schedules", "--date", "2026-01-01", "--dry-run"],
                       capture_output=True, text=True, env=env, cwd=str(__import__("pathlib").Path(__file__).parent.parent))
    assert r.returncode == 0 and "Would generate 0" in r.stdout
