"""Home KPI cards: scheduled income/expense still to come this month + projected month-end. FAKE data only."""
from datetime import date

import pytest

from app import schedules as SC
from tests.test_principal_returns import mk, tx

TODAY = date(2026, 6, 10)   # a Wednesday; 2026-06-01 is a Monday


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(SC, "local_today", lambda db, now=None: TODAY)


@pytest.fixture()
def acct(client):
    return mk(client, name="Test Checking", type="checking", currency="USD", opening_balance=1000, start_date="2026-01-01")


def sched(client, name, account_id, amount=10, kind="expense", **kw):
    rule = {"freq": "monthly", "month_mode": "day", "month_day": 20, "start_date": "2026-01-01"}
    rule.update(kw)
    r = client.post("/api/schedules", json={"name": name, **rule,
                                            "template": {"kind": kind, "account_id": account_id, "amount": amount,
                                                         **({"to_account_id": rule.pop("to", None)} if kind == "transfer" else {})}})
    assert r.status_code == 200, r.text
    return r.json()


def rem(db, month="2026-06", today=TODAY, **kw):
    return SC.month_remaining(db, month, today=today, **kw)


def test_weekly_and_monthly_expansion(client, db, acct):
    sched(client, "Groceries", acct["id"], 10, freq="weekly", weekdays=[0, 3])      # Mon + Thu
    sched(client, "Rent", acct["id"], 1500, month_day=1)                           # 06-01 already past
    sched(client, "Gym", acct["id"], 40, month_day=15)
    r = rem(db)
    g = [i["date"][:10] for i in r["items"] if i["name"] == "Groceries"]
    assert g == ["2026-06-11", "2026-06-15", "2026-06-18", "2026-06-22", "2026-06-25", "2026-06-29"]
    assert "Rent" not in {i["name"] for i in r["items"]}
    assert (r["expense"], r["expense_count"], r["income"], r["count"]) == (100.0, 7, 0.0, 7)
    assert (r["from"], r["to"]) == ("2026-06-10", "2026-06-30")


def test_month_end_boundaries(client, db, acct):
    sched(client, "Last day", acct["id"], 5, month_day=31)                          # clamps to 06-30
    sched(client, "Daily", acct["id"], 1, freq="daily", start_date="2026-06-01")
    sched(client, "Ends mid-month", acct["id"], 7, freq="weekly", weekdays=[4], end_date="2026-06-20")   # Fridays
    sched(client, "Two left", acct["id"], 3, freq="daily", start_date="2026-06-08", max_count=4)         # 08..11
    r = rem(db, today=date(2026, 6, 28))
    names = [(i["name"], i["date"][:10]) for i in r["items"]]
    assert ("Last day", "2026-06-30") in names
    assert [d for n, d in names if n == "Daily"] == ["2026-06-28", "2026-06-29", "2026-06-30"]   # today counts, July doesn't
    assert not [n for n, d in names if n in ("Ends mid-month", "Two left")]
    r = rem(db)
    assert [d for n, d in [(i["name"], i["date"][:10]) for i in r["items"]] if n == "Ends mid-month"] == ["2026-06-12", "2026-06-19"]
    assert [i["date"][:10] for i in r["items"] if i["name"] == "Two left"] == ["2026-06-10", "2026-06-11"]
    assert rem(db, "2026-05")["count"] == 0                                        # past month: nothing remaining
    jul = rem(db, "2026-07")
    assert jul["from"] == "2026-07-01" and len([i for i in jul["items"] if i["name"] == "Daily"]) == 31   # future: whole month


def test_excludes_generated_paused_transfers_and_handles_remind_only(client, db, acct):
    card = mk(client, name="Test Card", type="credit_card", currency="USD", opening_balance=0, start_date="2026-01-01")
    today_s = sched(client, "Due today", acct["id"], 20, month_day=10)
    p = sched(client, "Paused", acct["id"], 30, month_day=25)
    client.post(f"/api/schedules/{p['id']}/pause")
    sched(client, "Card payment", acct["id"], 500, kind="transfer", month_day=25, to=card["id"])
    r1 = client.post("/api/schedules", json={"name": "Water bill", "freq": "monthly", "month_mode": "day", "month_day": 15,
                                             "start_date": "2026-01-01", "post_mode": "remind", "lead_days": 10,
                                             "template": {"kind": "expense", "account_id": acct["id"], "amount": 60}}).json()
    names = lambda: {i["name"] for i in rem(db)["items"]}
    assert names() == {"Due today", "Water bill"}                                  # paused + transfer excluded
    assert {i["name"]: i["post_mode"] for i in rem(db)["items"]}["Water bill"] == "remind"
    client.post("/api/schedules/run", json={"date": "2026-06-10"})                  # posts today's + creates the reminder
    assert names() == {"Water bill"}                                               # generated today: in actual, not remaining
    r = [x for x in client.get("/api/reminders?view=all").json()["items"] if x["schedule_id"] == r1["id"] and x["occurrence_date"] == "2026-06-15"][0]
    assert r["status"] == "open"                                                   # reminder open: still to pay
    client.post(f"/api/reminders/{r['id']}/dismiss")
    assert names() == set()
    assert today_s["id"]


def test_currency_conversion_and_variable(client, db, acct):
    assert client.put("/api/fx", json={"currency": "CNY", "rate": 0.14}).status_code == 200
    cny = mk(client, name="Test Alipay", type="ewallet", currency="CNY", opening_balance=0, start_date="2026-01-01")
    sched(client, "Tuition", cny["id"], 700, month_day=20)
    v = client.post("/api/schedules", json={"name": "Electric", "freq": "monthly", "month_mode": "day", "month_day": 22,
                                            "start_date": "2026-01-01", "variable_amount": True,
                                            "template": {"kind": "expense", "account_id": acct["id"]}})
    assert v.status_code == 200, v.text
    r = rem(db)
    t = next(i for i in r["items"] if i["name"] == "Tuition")
    assert (t["amount"], t["currency"], t["amount_base"]) == (700.0, "CNY", 98.0)
    e = next(i for i in r["items"] if i["name"] == "Electric")
    assert e["amount"] is None and e["variable_amount"] and r["unknown_amount_count"] == 1
    assert r["expense"] == 98.0 and r["currency"] == "USD"


def test_stats_rules_income_projection_and_api(client, db, acct):
    sched(client, "Salary", acct["id"], 3000, kind="income", month_day=25)
    sched(client, "Rent", acct["id"], 1200, month_day=28)
    rb = client.post("/api/schedules", json={"name": "Expense report", "freq": "monthly", "month_mode": "day", "month_day": 26,
                                             "start_date": "2026-01-01",
                                             "template": {"kind": "reimbursement", "account_id": acct["id"], "amount": 80}})
    assert rb.status_code == 200, rb.text                                           # reimbursements are never income
    tx(client, kind="expense", account_id=acct["id"], amount=100, date="2026-06-05")
    tx(client, kind="income", account_id=acct["id"], amount=50, date="2026-06-03")
    tx(client, kind="expense", account_id=acct["id"], amount=70, date="2026-06-04", reimbursable=True)  # counts 0 (open)
    s = client.get("/api/summary/month?month=2026-06").json()
    assert (s["income"], s["expense"]) == (50, 100)
    sc = s["scheduled"]
    assert (sc["income"], sc["expense"], sc["net"], sc["count"]) == (3000, 1200, 1800, 2)
    assert "Expense report" not in {i["name"] for i in sc["items"]}
    assert s["projected"] == {"income": 3050, "expense": 1300, "net": 1750}
    d = client.get("/api/dashboard?month=2026-06").json()
    assert d["scheduled_remaining"]["expense"] == 1200
    with db.tx() as con:                                                          # ledger filter
        lid = con.execute("INSERT INTO ledgers(name) VALUES('Side ledger')").lastrowid
    assert client.get(f"/api/summary/month?month=2026-06&ledger_id={lid}").json()["scheduled"]["count"] == 0
    assert rem(db, ledger_id=int(db.get_setting("default_ledger_id", "1") or 1))["count"] == 2
