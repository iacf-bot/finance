"""Cursor pagination, month summary (Home), account history (default range + running balance). FAKE data only."""
from datetime import date, timedelta

from app import ledger


def mk(client, **kw):
    r = client.post("/api/accounts", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def tx(client, **kw):
    r = client.post("/api/transactions", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def test_cursor_pagination_walks_everything_once(client):
    a = mk(client, name="Fake Checking", type="checking", opening_balance=0, start_date="2025-01-01")
    for i in range(130):  # several rows share a timestamp: ties are broken by id
        tx(client, kind="expense" if i % 3 else "income", amount=1 + i, account_id=a["id"],
           date=f"2026-0{1 + i % 5}-{1 + i % 20:02d} 12:00")
    first = client.get("/api/transactions", params={"limit": 50, "account_id": a["id"]}).json()
    assert first["total"] == 130 and first["summary"]["count"] == 130 and first["has_more"] and len(first["items"]) == 50
    seen, page, pages = [], first, 1
    while True:
        seen += [(t["date"], t["id"]) for t in page["items"]]
        if not page["has_more"]:
            break
        page = client.get("/api/transactions", params={"limit": 50, "account_id": a["id"], "before": page["next_cursor"]}).json()
        assert "total" not in page  # counted once, on the first page
        pages += 1
    assert pages == 3 and len(seen) == 130 and len(set(seen)) == 130
    assert seen == sorted(seen, reverse=True)
    # filters keep working with a cursor, and the summary reflects the filter
    inc = client.get("/api/transactions", params={"limit": 10, "kind": "income", "account_id": a["id"]}).json()
    assert inc["summary"]["expense"] == 0 and inc["total"] == 44
    nxt = client.get("/api/transactions", params={"limit": 10, "kind": "income", "account_id": a["id"], "before": inc["next_cursor"]}).json()
    assert all(t["kind"] == "income" for t in nxt["items"]) and {t["id"] for t in nxt["items"]}.isdisjoint({t["id"] for t in inc["items"]})
    assert client.get("/api/transactions", params={"before": "garbage"}).status_code == 400
    # old offset paging still works
    off = client.get("/api/transactions", params={"limit": 50, "offset": 50, "account_id": a["id"]}).json()
    assert [t["id"] for t in off["items"]] == [i for _, i in seen[50:100]]


def test_indexes_exist(client):
    names = {r["name"] for r in client.app.state.db.all("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ix_tx_date_id", "ix_tx_acct_date", "ix_tx_to_acct_date"} <= names


def test_month_summary(client):
    a = mk(client, name="Fake Checking", type="checking", opening_balance=0, start_date="2025-01-01")
    b = mk(client, name="Fake Broker", type="brokerage", opening_balance=0, start_date="2025-01-01")
    tx(client, kind="income", amount=3000, account_id=a["id"], date="2026-03-01 00:00")
    tx(client, kind="expense", amount=100, account_id=a["id"], date="2026-03-31 23:59")
    tx(client, kind="refund", amount=10, account_id=a["id"], date="2026-03-15")
    tx(client, kind="expense", amount=999, account_id=a["id"], date="2026-04-01 00:00")  # next month
    tx(client, kind="adjustment", amount=-40, account_id=a["id"], date="2026-03-10", adj_reason="unrecorded_spending")
    tx(client, kind="adjustment", amount=250, account_id=b["id"], date="2026-03-20", adj_reason="investment_gain")
    tx(client, kind="adjustment", amount=77, account_id=a["id"], date="2026-03-20", adj_reason="correction")  # excluded
    s = client.get("/api/summary/month", params={"month": "2026-03"}).json()
    assert s["income"] == 3000 and s["expense"] == 130 and s["net"] == 2870 and s["investment_income"] == 250
    assert (s["prev"], s["next"], s["start"], s["end"]) == ("2026-02", "2026-04", "2026-03-01", "2026-03-31")
    assert s["count"] == 6
    assert client.get("/api/summary/month", params={"month": "2026-12"}).json()["next"] == "2027-01"
    assert client.get("/api/summary/month", params={"month": "2026-13"}).status_code == 400
    # matches Reports for the same range
    rep = client.get("/api/reports", params={"start": "2026-03-01", "end": "2026-03-31"}).json()["totals"]
    assert (rep["income"], rep["expense"], rep["investment_income"]) == (s["income"], s["expense"], s["investment_income"])


def test_account_history_default_range_and_running_balance(client):
    today = date.today()
    d = lambda n: (today - timedelta(days=n)).isoformat()
    a = mk(client, name="Fake Checking", type="checking", opening_balance=1000, start_date=d(400))
    s = mk(client, name="Fake Savings", type="savings", opening_balance=0, start_date=d(400))
    old = tx(client, kind="expense", amount=100, account_id=a["id"], date=d(60) + " 10:00")
    t1 = tx(client, kind="income", amount=500, account_id=a["id"], date=d(20) + " 10:00")
    t2 = tx(client, kind="transfer", amount=200, account_id=a["id"], to_account_id=s["id"], date=d(10) + " 10:00")
    t3 = tx(client, kind="expense", amount=50, account_id=a["id"], date=d(5) + " 10:00", note="fake coffee beans")
    h = client.get(f"/api/accounts/{a['id']}/history").json()
    assert h["period"] == "1M" and h["start"] == ledger.period_start("1M", today)
    ids = [t["id"] for t in h["items"]]
    assert ids == [t3["id"], t2["id"], t1["id"]] and old["id"] not in ids  # default = last month only
    rb = {t["id"]: t["running_balance"] for t in h["items"]}
    assert rb[t1["id"]] == 1400 and rb[t2["id"]] == 1200 and rb[t3["id"]] == 1150
    assert h["balances"]["opening"] == 900 and h["balances"]["closing"] == 1150 == h["account"]["balance"]
    assert h["balances"]["inflow"] == 500 and h["balances"]["outflow"] == 250
    # transfer-in side
    hs = client.get(f"/api/accounts/{s['id']}/history").json()
    assert hs["items"][0]["running_balance"] == 200 and hs["items"][0]["account_effect"] == 200
    # longer periods / custom range
    assert old["id"] in [t["id"] for t in client.get(f"/api/accounts/{a['id']}/history", params={"period": "3M"}).json()["items"]]
    allp = client.get(f"/api/accounts/{a['id']}/history", params={"period": "ALL"}).json()
    assert allp["start"] is None and len(allp["items"]) == 5  # incl. the opening-balance adjustment
    cus = client.get(f"/api/accounts/{a['id']}/history", params={"start": d(25), "end": d(15)}).json()
    assert cus["period"] == "CUSTOM" and [t["id"] for t in cus["items"]] == [t1["id"]]
    assert cus["balances"]["opening"] == 900 and cus["balances"]["closing"] == 1400
    # a search filter hides rows but balances stay the account's real ones
    f = client.get(f"/api/accounts/{a['id']}/history", params={"q": "coffee"}).json()
    assert [t["id"] for t in f["items"]] == [t3["id"]] and f["items"][0]["running_balance"] == 1150
    # every running balance equals the app's balance right after that row
    db = client.app.state.db
    for t in allp["items"]:
        later_same_time = db.one("SELECT COUNT(*) n FROM transactions WHERE (account_id=? OR to_account_id=?) AND date=? AND id>?",
                                 (a["id"], a["id"], t["date"], t["id"]))["n"]
        if not later_same_time:
            assert t["running_balance"] == ledger.account_balance_at(db, a["id"], t["date"])
    assert client.get("/api/accounts/99999/history").status_code == 404


def test_running_balance_follows_reconciliation_checkpoints(client):
    today = date.today()
    d = lambda n: (today - timedelta(days=n)).isoformat()
    a = mk(client, name="Fake Card", type="credit_card", opening_balance=0, start_date=d(90))
    tx(client, kind="expense", amount=100, account_id=a["id"], date=d(40))
    r = client.post(f"/api/accounts/{a['id']}/reconcile", json={"balance": 130, "as_of": d(30)})  # statement owes 130 (drift)
    assert r.status_code == 200, r.text
    late = tx(client, kind="expense", amount=25, account_id=a["id"], date=d(35))  # before the checkpoint: no effect after it
    t = tx(client, kind="expense", amount=20, account_id=a["id"], date=d(10))
    h = client.get(f"/api/accounts/{a['id']}/history", params={"period": "3M"}).json()
    rows = {x["id"]: x for x in h["items"]}
    assert rows[t["id"]]["running_shown"] == 150 == h["account"]["owed"]          # 130 (checkpoint) + 20
    assert rows[late["id"]]["running_shown"] == 125                             # before the checkpoint: raw ledger
    assert h["balances"]["closing"] == 150 and h["balances"]["liability"]
    one = client.get(f"/api/accounts/{a['id']}/history", params={"start": d(29), "end": d(1)}).json()
    assert one["balances"]["opening"] == 130  # anchored to the checkpoint


def test_history_cursor_pages(client):
    a = mk(client, name="Fake Wallet", type="cash", opening_balance=0, start_date="2020-01-01")
    for i in range(75):
        tx(client, kind="income", amount=1, account_id=a["id"], date=f"2021-01-{1 + i % 28:02d} 0{i % 10}:00")
    p1 = client.get(f"/api/accounts/{a['id']}/history", params={"period": "ALL", "limit": 40}).json()
    p2 = client.get(f"/api/accounts/{a['id']}/history", params={"period": "ALL", "limit": 40, "before": p1["next_cursor"]}).json()
    assert len(p1["items"]) == 40 and len(p2["items"]) == 35 and not p2["has_more"] and "balances" not in p2
    assert p1["items"][0]["running_balance"] == 75 and p2["items"][-1]["running_balance"] == 1
