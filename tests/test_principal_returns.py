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


def test_corrections_count_as_returns_overrides_and_opening_split(client):
    A, B, C = setup(client)
    cor = tx(client, kind="adjustment", account_id=A, amount=40)  # no reason (like imported 余额变更) -> returns
    x = inv(client, A)
    assert (x["principal"], x["returns"], x["unexplained"], x["correction_count"]) == (1000, 40, 0, 1)
    tx(client, kind="adjustment", account_id=A, amount=-15, adj_reason="correction")  # explicit Correction -> returns
    tx(client, kind="adjustment", account_id=A, amount=30, adj_reason="unrecorded_income")  # stays principal
    x = inv(client, A)
    assert (x["principal"], x["returns"], x["unexplained"]) == (1030, 25, 0)
    h = client.get(f"/api/accounts/{A}/history?period=ALL").json()
    row = next(t for t in h["items"] if t["id"] == cor["id"])
    assert row["pr_badge"] == "收益" and "auto" in row["pr_label"]
    assert h["invest"]["likely_opening_count"] == 0  # first record is the real opening balance
    # override: mark it as principal / other (other = genuine unexplained gap)
    client.put(f"/api/transactions/{cor['id']}", json={"pr_class": "principal"})
    assert (inv(client, A)["principal"], inv(client, A)["returns"]) == (1070, -15)
    client.put(f"/api/transactions/{cor['id']}", json={"pr_class": "other"})
    x = inv(client, A)
    assert (x["principal"], x["returns"], x["unexplained"]) == (1030, -15, 40)
    client.put(f"/api/transactions/{cor['id']}", json={"pr_class": "auto"})
    assert inv(client, A)["unexplained"] == 0
    d = tx(client, kind="income", account_id=A, amount=10, pr_class="returns")
    assert inv(client, A)["returns"] == 35 and d["pr_class"] == "returns"
    assert client.post("/api/transactions", json={"kind": "income", "account_id": A, "amount": 1, "pr_class": "bogus"}).status_code == 400
    # opening split: 200 of the 1000 opening balance were prior returns
    client.put(f"/api/accounts/{A}", json={"opening_returns": 200})
    x = inv(client, A)
    assert (x["principal"], x["returns"], x["unexplained"]) == (830, 235, 0)


def test_first_unclassified_record_hint_and_reconciliation_gap(client):
    r = client.post("/api/accounts", json={"name": "Imported Fund", "type": "brokerage", "currency": "USD"}).json()
    A = r["id"]
    first = tx(client, kind="adjustment", account_id=A, amount=5000, date="2026-01-05")  # imported starting balance
    tx(client, kind="adjustment", account_id=A, amount=250, date="2026-02-05")
    h = client.get(f"/api/accounts/{A}/history?period=ALL").json()["invest"]
    assert (h["principal"], h["returns"], h["likely_opening_count"], h["likely_opening_amount"]) == (0, 5250, 1, 5000)
    client.put(f"/api/transactions/{first['id']}", json={"adj_reason": "opening"})  # what Reclassify would do
    h = client.get(f"/api/accounts/{A}/history?period=ALL").json()["invest"]
    assert (h["principal"], h["returns"], h["likely_opening_count"]) == (5000, 250, 0)
    # a reconciliation checkpoint that differs from the ledger is a genuine gap -> unexplained
    rr = client.post(f"/api/accounts/{A}/reconcile", json={"balance": 5300, "as_of": "2026-03-01", "accept": True})
    assert rr.status_code == 200, rr.text
    x = inv(client, A)
    assert x["value"] == 5300 and x["unexplained"] == 50


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
