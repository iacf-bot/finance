"""Reason for difference 差额原因 on balance adjustments, investment returns and the reclassify tool. FAKE data."""
from datetime import date

import pytest

from app import returns


def mk(client, **kw):
    r = client.post("/api/accounts", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def tx(client, **kw):
    r = client.post("/api/transactions", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def setb(client, aid, balance, date_, reason=None):
    body = {"balance": balance, "date": date_}
    if reason:
        body["reason"] = reason
    r = client.post(f"/api/accounts/{aid}/set-balance", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def report(client, start, end, **kw):
    return client.get("/api/reports", params={"start": start, "end": end, **kw}).json()


# ---------------------------------------------------------------- reasons & reporting treatment
def test_default_reason_by_account_type(client):
    b = mk(client, name="Fake Broker", type="brokerage")
    c = mk(client, name="Fake Checking", type="checking")
    r1 = setb(client, b["id"], 1000, "2026-03-01")
    r2 = setb(client, c["id"], 500, "2026-03-01")
    assert r1["reason"] == "investment_gain" and r1["transaction"]["adj_reason"] == "investment_gain"
    assert r1["transaction"]["adj_reason_label"] == "Investment gain 投资收益"
    assert r2["reason"] == "correction"
    loss = setb(client, b["id"], 900, "2026-03-02")["transaction"]
    assert loss["adj_reason_label"] == "Investment loss 投资亏损" and "Investment loss" in loss["category_label"]
    assert client.post(f"/api/accounts/{c['id']}/set-balance", json={"balance": 1, "reason": "lottery"}).status_code == 400


@pytest.mark.parametrize("reason,expense,income,inv_income", [
    ("investment_gain", 0, 0, -40), ("interest", 0, 0, -40), ("fees", 40, 0, 0),
    ("unrecorded_spending", 40, 0, 0), ("unrecorded_income", 0, -40, 0), ("correction", 0, 0, 0), ("opening", 0, 0, 0)])
def test_reporting_treatment_per_reason(client, reason, expense, income, inv_income):
    """A balance decrease of 40 on a CHECKING account, classified with each reason."""
    c = mk(client, name="Fake Checking", type="checking", opening_balance=100, start_date="2026-01-01")
    setb(client, c["id"], 60, "2026-05-10", reason)
    rep = report(client, "2026-05-01", "2026-05-31")
    assert rep["totals"]["expense"] == expense and rep["totals"]["income"] == income
    assert rep["totals"]["investment_income"] == inv_income
    if reason == "unrecorded_spending":
        assert any("Unrecorded spending 未记支出 (balance adj. 余额调整)" == r["name"] for r in rep["rows"])
    dash = client.get("/api/dashboard", params={"month": "2026-05"}).json()
    assert dash["investment_income"]["total"] == inv_income


def test_fees_on_investment_account_are_investment_income(client):
    b = mk(client, name="Fake Broker", type="brokerage", opening_balance=1000, start_date="2026-01-01")
    setb(client, b["id"], 990, "2026-05-10", "fees")
    setb(client, b["id"], 1100, "2026-05-20", "investment_gain")
    rep = report(client, "2026-05-01", "2026-05-31")
    assert rep["totals"]["expense"] == 0 and rep["totals"]["investment_income"] == 100
    inv = client.get("/api/dashboard", params={"month": "2026-05"}).json()["investment_income"]
    assert inv["fees"] == -10 and inv["gains"] == 110


def test_reason_editable_on_transaction_and_kept_on_edit(client):
    b = mk(client, name="Fake Broker", type="brokerage")
    t = setb(client, b["id"], 1000, "2026-03-01")["transaction"]
    t2 = client.put(f"/api/transactions/{t['id']}", json={"adj_reason": "interest"}).json()
    assert t2["adj_reason"] == "interest"
    t3 = client.put(f"/api/transactions/{t['id']}", json={"note": "edited", "category_id": None}).json()
    assert t3["adj_reason"] == "interest"  # editing other fields keeps the reason
    t4 = tx(client, kind="adjustment", amount=-5, account_id=b["id"], date="2026-03-05", adj_reason="fees")
    assert t4["adj_reason"] == "fees"
    # switching an adjustment to an expense drops the reason category
    t5 = client.put(f"/api/transactions/{t4['id']}", json={"kind": "expense", "amount": 5}).json()
    assert t5["category_id"] is None


def test_quick_add_balance_with_reason(client):
    mk(client, name="Fake Broker", type="brokerage", aliases="fakebroker")
    mk(client, name="Fake Savings", type="savings", aliases="fakesave")
    r = client.post("/api/quick-add", json={"text": "balance fakesave 520 interest"}).json()
    assert r["reason"] == "interest" and r["transaction"]["adj_reason"] == "interest" and r["transaction"]["amount"] == 520
    r = client.post("/api/quick-add", json={"text": "balance fakebroker 1000"}).json()
    assert r["reason"] == "investment_gain"
    r = client.post("/api/quick-add", json={"text": "余额 fakesave 500 未记支出", "dry_run": True}).json()
    assert r["reason"] == "unrecorded_spending" and r["status"] == "preview"


def test_reason_categories_cannot_be_deleted(client):
    cid = next(c["id"] for c in client.get("/api/categories").json() if c["kind"] == "adjustment")
    assert client.delete(f"/api/categories/{cid}").status_code == 400


# ---------------------------------------------------------------- formulas
def test_modified_dietz_and_xirr_units():
    # 1000 start, +500 at mid-period, end 1650 -> gain 150, denominator 1000 + 0.5*500 = 1250 -> 12%
    assert returns.modified_dietz(1000, 1650, [(0.5, 500)]) == pytest.approx(0.12)
    assert returns.modified_dietz(0, 0, []) is None
    # 1000 -> 1100 after exactly one year: 10%
    assert returns.xirr([(0, -1000), (1, 1100)]) == pytest.approx(0.10, abs=1e-6)


def test_contributions_vs_gains_simple_and_dietz(client):
    chk = mk(client, name="Fake Checking", type="checking", opening_balance=10000, start_date="2025-01-01")
    b = mk(client, name="Fake Broker", type="brokerage")
    tx(client, kind="transfer", amount=1000, account_id=chk["id"], to_account_id=b["id"], date="2025-12-31 10:00")
    # period: 2026-01-01 .. 2026-01-30 (30 days)
    tx(client, kind="transfer", amount=500, account_id=chk["id"], to_account_id=b["id"], date="2026-01-16 00:00")  # 15 days left
    setb(client, b["id"], 1650, "2026-01-30 12:00", "investment_gain")  # +150 gain
    r = returns.compute(client.app.state.db, [b["id"]], "2026-01-01", "2026-01-30", "USD", today=date(2026, 6, 1))
    assert r["start_value"] == 1000 and r["end_value"] == 1650
    assert r["contributions"] == 500 and r["withdrawals"] == 0 and r["net_contributions"] == 500
    assert r["gain"] == 150 and r["components"]["investment_gain"] == 150 and r["components_total"] == 150
    assert r["simple_return"] == pytest.approx(10.0)  # 150 / (1000 + 500)
    assert r["md_return"] == pytest.approx(12.0)  # 150 / (1000 + 0.5*500)
    # a Correction adjustment is a flow (contribution), not a gain
    setb(client, b["id"], 1700, "2026-01-30 13:00", "correction")
    r = returns.compute(client.app.state.db, [b["id"]], "2026-01-01", "2026-01-30", "USD", today=date(2026, 6, 1))
    assert r["gain"] == 150 and r["net_contributions"] == 550
    # withdrawal
    tx(client, kind="transfer", amount=200, account_id=b["id"], to_account_id=chk["id"], date="2026-01-30 14:00")
    r = returns.compute(client.app.state.db, [b["id"]], "2026-01-01", "2026-01-30", "USD", today=date(2026, 6, 1))
    assert r["withdrawals"] == 200 and r["gain"] == 150 and r["end_value"] == 1500


def test_gain_identity_with_trades_dividends_and_groups(client):
    db = client.app.state.db
    chk = mk(client, name="Fake Checking", type="checking", opening_balance=50000, start_date="2025-01-01")
    b = mk(client, name="Fake Broker", type="brokerage")
    ira = mk(client, name="Fake IRA", type="retirement")
    tx(client, kind="transfer", amount=10000, account_id=chk["id"], to_account_id=b["id"], date="2026-02-01")
    tx(client, kind="buy", account_id=b["id"], ticker="FAKE", shares=10, price=100, fee=0, date="2026-02-02")
    tx(client, kind="sell", account_id=b["id"], ticker="FAKE", shares=5, price=120, fee=0, date="2026-03-01")
    tx(client, kind="dividend", amount=30, account_id=b["id"], ticker="FAKE", date="2026-03-15")
    tx(client, kind="invest_fee", amount=5, account_id=b["id"], ticker="FAKE", date="2026-03-16")
    client.post("/api/prices", json={"ticker": "FAKE", "price": 130})
    tx(client, kind="transfer", amount=2000, account_id=b["id"], to_account_id=ira["id"], date="2026-04-01")  # internal
    setb(client, ira["id"], 2100, "2026-05-01", "investment_gain")
    r = returns.compute(db, [b["id"]], "2026-01-01", "2026-06-30", "USD", today=date(2026, 6, 30))
    # market: sold 5 @120 (cost 100) = +100 realized; 5 left @130 = +150 unrealized
    assert r["components"]["market"] == 250 and r["components"]["dividends"] == 30 and r["components"]["fees"] == -5
    assert r["gain"] == r["components_total"] == 275
    assert r["contributions"] == 10000 and r["withdrawals"] == 2000  # transfer to the IRA leaves this account
    g = returns.compute(db, [b["id"], ira["id"]], "2026-01-01", "2026-06-30", "USD", today=date(2026, 6, 30))
    assert g["contributions"] == 10000 and g["withdrawals"] == 0  # internal transfer is not a flow for the group
    assert g["gain"] == 375 and g["components_total"] == 375
    rep = client.get("/api/investments/returns", params={"period": "all"}).json()
    names = {row["name"]: row for row in rep["rows"]}
    assert names["All investments 全部投资"]["gain"] == 375 and "Fake Checking" not in names
    assert rep["scope"]["series"][-1]["cumulative_gain"] == 375
    assert sum(y["gain"] for y in rep["scope"]["years"]) == pytest.approx(375)


def test_period_bounds():
    t = date(2026, 10, 11)
    assert returns.period_bounds("month", today=t) == (date(2026, 10, 1), t)
    assert returns.period_bounds("ytd", today=t) == (date(2026, 1, 1), t)
    assert returns.period_bounds("1y", today=t) == (date(2025, 10, 12), t)
    assert returns.period_bounds("all", first_date="2019-05-03 10:00", today=t) == (date(2019, 5, 3), t)
    assert returns.period_bounds("custom", "2024-01-01", "2024-12-31", today=t) == (date(2024, 1, 1), date(2024, 12, 31))
    with pytest.raises(ValueError):
        returns.period_bounds("custom", "2025-01-02", "2025-01-01", today=t)


# ---------------------------------------------------------------- reclassify tool
def test_reclassify_preview_does_not_write_and_apply_works(client):
    db = client.app.state.db
    b = mk(client, name="Fake 401k", type="retirement")
    s = mk(client, name="Fake Save", type="savings")
    c = mk(client, name="Fake Checking", type="checking")
    # imported-style unclassified adjustments (like 随手记 余额变更)
    first = tx(client, kind="adjustment", amount=5000, account_id=b["id"], date="2024-01-01", source="import")
    later = tx(client, kind="adjustment", amount=300, account_id=b["id"], date="2024-06-01", source="import")
    tx(client, kind="adjustment", amount=12, account_id=s["id"], date="2024-06-01", source="import")
    tx(client, kind="adjustment", amount=9, account_id=s["id"], date="2024-07-01", source="import")
    tx(client, kind="adjustment", amount=7, account_id=c["id"], date="2024-06-01", source="import")
    snap = lambda: db.all("SELECT id, category_id, updated_at FROM transactions ORDER BY id")
    before = snap()
    p = client.get("/api/adjustments/reclassify").json()
    assert snap() == before  # preview writes nothing
    assert p["count"] == 2
    prop = {r["id"]: r["proposed_reason"] for r in p["rows"]}
    assert prop == {first["id"]: "opening", later["id"]: "investment_gain"}
    p2 = client.get("/api/adjustments/reclassify", params={"include_savings": True}).json()
    assert p2["count"] == 4 and p2["by_proposed_reason"].get("interest") == 1
    assert snap() == before
    r = client.post("/api/adjustments/reclassify", json={"items": [{"id": later["id"], "reason": "investment_gain"}]}).json()
    assert r["updated"] == 1 and r["backup"].endswith(".db")
    assert client.get(f"/api/transactions/{later['id']}").json()["adj_reason"] == "investment_gain"
    assert client.get(f"/api/transactions/{first['id']}").json()["adj_reason"] == "correction"
    # applied rows drop out of the next preview
    assert client.get("/api/adjustments/reclassify").json()["count"] == 1
    assert client.post("/api/adjustments/reclassify", json={"items": [{"id": first["id"], "reason": "x"}]}).status_code == 400
