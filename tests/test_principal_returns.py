"""Principal 本金投入 vs returns 投资收益 on investment accounts. FAKE data only."""


def mk(client, **kw):
    r = client.post("/api/accounts", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def tx(client, **kw):
    kw.setdefault("date", "2026-03-01")
    r = client.post("/api/transactions", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def inv(client, aid):
    return next(a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == aid)["invest"]


def setup(client):
    a = mk(client, name="Test Brokerage", type="brokerage", currency="USD", opening_balance=1000, start_date="2026-01-01")
    b = mk(client, name="Test 401k", type="retirement", currency="USD")
    c = mk(client, name="Test Checking", type="checking", currency="USD", opening_balance=5000, start_date="2026-01-01")
    return a["id"], b["id"], c["id"]


def test_principal_returns_math(client):
    A, B, C = setup(client)
    tx(client, kind="transfer", account_id=C, to_account_id=A, amount=500, date="2026-02-01")   # contribution
    tx(client, kind="dividend", account_id=A, amount=20, ticker="FAKE")                         # return
    tx(client, kind="invest_fee", account_id=A, amount=5, ticker="FAKE")                         # return (negative)
    tx(client, kind="adjustment", account_id=A, amount=100, adj_reason="investment_gain")      # return
    tx(client, kind="transfer", account_id=A, to_account_id=C, amount=200, date="2026-04-01")   # withdrawal
    x = inv(client, A)
    assert (x["principal"], x["returns"], x["value"], x["unexplained"]) == (1300, 115, 1415, 0)
    assert x["returns_pct"] == round(115 / 1300 * 100, 2)
    assert "invest" not in next(a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == C)


def test_transfer_between_investment_accounts_moves_principal(client):
    A, B, C = setup(client)
    tx(client, kind="transfer", account_id=A, to_account_id=B, amount=300)
    assert inv(client, A)["principal"] == 700 and inv(client, B)["principal"] == 300
    m = tx(client, kind="income", account_id=B, amount=50, note="Employer match")
    assert inv(client, B)["principal"] == 350
    h = client.get(f"/api/accounts/{B}/history?period=ALL").json()
    row = next(t for t in h["items"] if t["id"] == m["id"])
    assert row["pr_badge"] == "本金" and "Employer match" in row["pr_label"] and row["running_principal"] == 350
    # group total in the tree (both in the Investments group)
    g = next(n for n in client.get("/api/accounts").json()["tree"] if n.get("invest"))
    assert g["invest"]["principal"] == 1050 and g["invest"]["unexplained"] == 0


def test_corrections_are_unexplained_overrides_and_opening_split(client):
    A, B, C = setup(client)
    cor = tx(client, kind="adjustment", account_id=A, amount=40)  # no reason (like imported 余额变更) -> unexplained
    x = inv(client, A)
    assert (x["principal"], x["returns"], x["unexplained"], x["unclassified"], x["reconciliation_diff"]) == (1000, 0, 40, 40, 0)
    assert x["correction_count"] == 1 and x["returns_pct"] == 0
    tx(client, kind="adjustment", account_id=A, amount=-15, adj_reason="correction")  # explicit Correction -> unexplained
    tx(client, kind="adjustment", account_id=A, amount=30, adj_reason="unrecorded_income")  # principal
    tx(client, kind="adjustment", account_id=A, amount=60, adj_reason="investment_gain")  # returns
    x = inv(client, A)
    assert (x["principal"], x["returns"], x["unexplained"], x["value"]) == (1030, 60, 25, 1115)
    assert x["value"] == x["principal"] + x["returns"] + x["unexplained"]
    h = client.get(f"/api/accounts/{A}/history?period=ALL").json()
    row = next(t for t in h["items"] if t["id"] == cor["id"])
    assert row["pr_badge"] == "未分类" and row["pr_class"] == "unexplained" and row["running_unexplained"] == 40
    assert h["invest"]["correction_count"] == 2
    # the account-only reclassify preview lists them (read-only)
    pv = client.get(f"/api/adjustments/reclassify?account_ids={A}&only=1").json()
    assert {r["account_id"] for r in pv["rows"]} == {A} and pv["count"] == 2
    # overrides win
    client.put(f"/api/transactions/{cor['id']}", json={"pr_class": "returns"})
    x = inv(client, A)
    assert (x["returns"], x["unexplained"], x["correction_count"]) == (100, -15, 1)
    client.put(f"/api/transactions/{cor['id']}", json={"pr_class": "principal"})
    assert (inv(client, A)["principal"], inv(client, A)["returns"]) == (1070, 60)
    d = tx(client, kind="income", account_id=A, amount=10, pr_class="other")  # old name -> unexplained
    assert d["pr_class"] == "unexplained" and inv(client, A)["unexplained"] == -5
    assert client.post("/api/transactions", json={"kind": "income", "account_id": A, "amount": 1, "pr_class": "bogus"}).status_code == 400
    # opening split: 200 of the 1000 opening balance were prior returns
    client.put(f"/api/accounts/{A}", json={"opening_returns": 200})
    x = inv(client, A)
    assert (x["principal"], x["returns"], x["unexplained"]) == (870, 260, -5)
    assert x["returns_pct"] == round(260 / 870 * 100, 2)  # explicit returns only


def test_imported_first_record_and_reconciliation_diff(client):
    A = client.post("/api/accounts", json={"name": "Imported Fund", "type": "brokerage", "currency": "USD"}).json()["id"]
    first = tx(client, kind="adjustment", account_id=A, amount=5000, date="2026-01-05")  # imported starting balance
    tx(client, kind="adjustment", account_id=A, amount=250, date="2026-02-05", adj_reason="investment_gain")
    h = client.get(f"/api/accounts/{A}/history?period=ALL").json()["invest"]
    assert (h["principal"], h["returns"], h["unexplained"], h["likely_opening_count"]) == (0, 250, 5000, 1)
    client.put(f"/api/transactions/{first['id']}", json={"adj_reason": "opening"})  # what Reclassify would do
    h = client.get(f"/api/accounts/{A}/history?period=ALL").json()["invest"]
    assert (h["principal"], h["returns"], h["unexplained"], h["likely_opening_count"]) == (5000, 250, 0, 0)
    rr = client.post(f"/api/accounts/{A}/reconcile", json={"balance": 5300, "as_of": "2026-03-01", "accept": True})
    assert rr.status_code == 200, rr.text
    x = inv(client, A)
    assert (x["value"], x["unexplained"], x["unclassified"], x["reconciliation_diff"]) == (5300, 50, 0, 50)
    # tree group totals carry the split too
    g = next(n for n in client.get("/api/accounts").json()["tree"] if n.get("invest"))
    assert g["invest"]["unexplained"] == 50 and g["invest"]["reconciliation_diff"] == 50
    # series: value = principal + returns + unexplained at every point
    sr = client.get(f"/api/accounts/{A}/investment-series?from=2026-01-01&to=2026-03-31").json()
    assert all(abs(p["value"] - p["principal"] - p["returns"] - p["unexplained"]) < 0.01 for p in sr["points"])
    assert sr["points"][-1]["unexplained"] == 50 and "end_unexplained" in sr["summary"]


def test_trades_dividends_period_and_chart(client):
    A, B, C = setup(client)
    tx(client, kind="buy", account_id=A, ticker="FAKE", shares=5, price=100, fee=0, date="2026-02-01")
    tx(client, kind="sell", account_id=A, ticker="FAKE", shares=1, price=120, fee=0, date="2026-03-01")
    client.post("/api/prices", json={"ticker": "FAKE", "price": 130})
    x = inv(client, A)
    # 4 shares x 130 = 520 held; trade gain = 520 - 500 + 120 = 140
    assert (x["principal"], x["trade_gains"], x["returns"], x["unexplained"]) == (1000, 140, 140, 0)
    h = client.get(f"/api/accounts/{A}/history?period=ALL").json()
    p = h["invest"]["period"]
    assert p["contributions"] == 1000 and p["returns"] == 140 and p["md_return"] is not None
    pts = h["invest"]["series"]["points"]
    assert pts and all(abs(s["value"] - s["principal"] - s["returns"]) < 0.01 for s in pts)
    assert {t["pr_badge"] for t in h["items"]} >= {"交易", "本金"}


def test_investment_series_endpoint(client):
    A, B, C = setup(client)
    tx(client, kind="transfer", account_id=C, to_account_id=A, amount=500, date="2026-02-10")
    tx(client, kind="adjustment", account_id=A, amount=100, adj_reason="investment_gain", date="2026-03-15")
    r = client.get(f"/api/accounts/{A}/investment-series?from=2026-01-01&to=2026-03-31&interval=day").json()
    assert r["interval"] == "day" and len(r["points"]) == 90
    by = {p["date"]: p for p in r["points"]}
    assert by["2026-01-01"]["principal"] == 1000 and by["2026-02-10"]["principal"] == 1500
    assert by["2026-03-14"]["returns"] == 0 and by["2026-03-15"]["returns"] == 100 and by["2026-03-31"]["value"] == 1600
    sm = r["summary"]
    assert (sm["start_principal"], sm["end_principal"], sm["returns_change"]) == (1000, 1500, 100)
    assert sm["contributions"] == 1500  # opening deposit on 01-01 is in range and sm["return_pct"] is not None and sm["return_pct"] > 0
    # granularity by span
    assert client.get(f"/api/accounts/{A}/investment-series?from=2026-01-01&to=2026-03-31").json()["interval"] == "week"
    assert client.get(f"/api/accounts/{A}/investment-series?from=2026-01-01&to=2026-01-31").json()["interval"] == "day"
    m = client.get(f"/api/accounts/{A}/investment-series?from=2024-01-01&to=2026-03-31").json()
    assert m["interval"] == "month" and m["points"][0]["principal"] == 0 and m["points"][-1]["principal"] == 1500
    assert client.get(f"/api/accounts/{A}/investment-series?period=3Y").status_code == 200
    assert client.get(f"/api/accounts/{A}/investment-series?interval=hour").status_code == 400
    assert client.get(f"/api/accounts/{A}/investment-series?from=2026-03-01&to=2026-01-01").status_code == 400
    assert client.get(f"/api/accounts/{C}/investment-series").status_code == 400
