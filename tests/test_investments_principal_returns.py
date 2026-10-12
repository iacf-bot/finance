"""Investments page: per-account principal / returns / unexplained table, and the returns report using the same
logic as the account pages. FAKE data only."""
from tests.test_principal_returns import inv, mk, tx


def setup(client):
    a = mk(client, name="Test Brokerage", type="brokerage", currency="USD", opening_balance=1000, start_date="2026-01-01")
    b = mk(client, name="Test 401k", type="retirement", currency="USD")
    j = mk(client, name="Test CN Broker", type="brokerage", currency="CNY", opening_balance=100000, start_date="2026-01-01")
    c = mk(client, name="Test Checking", type="checking", currency="USD", opening_balance=5000, start_date="2026-01-01")
    return a["id"], b["id"], j["id"], c["id"]


def test_table_matches_account_pages_and_totals(client):
    A, B, J, C = setup(client)
    tx(client, kind="transfer", account_id=C, to_account_id=A, amount=500, date="2026-02-01")
    tx(client, kind="dividend", account_id=A, amount=20, ticker="FAKE")
    tx(client, kind="adjustment", account_id=A, amount=40)                       # unexplained
    tx(client, kind="transfer", account_id=A, to_account_id=B, amount=300)        # principal moves
    tx(client, kind="adjustment", account_id=J, amount=5000, adj_reason="investment_gain")
    d = client.get("/api/investments/principal-returns").json()
    rows = {r["id"]: r for r in d["accounts"]}
    assert C not in rows
    for aid in (A, B, J):
        x = inv(client, aid)
        for k in ("value", "principal", "returns", "unexplained", "returns_pct"):
            assert rows[aid][k] == x[k], (aid, k)
    assert (rows[A]["principal"], rows[A]["returns"], rows[A]["unexplained"]) == (1200, 20, 40)
    assert rows[A]["correction_count"] == 1
    cur = {c["currency"]: c for c in d["by_currency"]}
    assert cur["USD"]["principal"] == 1500 and cur["CNY"]["returns"] == 5000
    # group subtotal / total are in base currency and add up
    t = d["total"]
    assert abs(t["value"] - (t["principal"] + t["returns"] + t["unexplained"])) < 0.02
    assert abs(sum(g["value"] for g in d["groups"]) - t["value"]) < 0.02
    assert d["series"] and abs(d["series"][-1]["value"] - t["value"]) < 0.05


def test_range_filter_and_period_figures(client):
    A, B, J, C = setup(client)
    tx(client, kind="transfer", account_id=C, to_account_id=A, amount=500, date="2026-02-01")
    tx(client, kind="adjustment", account_id=A, amount=60, adj_reason="investment_gain", date="2026-05-01")
    d = client.get("/api/investments/principal-returns?from=2026-01-01&to=2026-03-31").json()
    a = next(r for r in d["accounts"] if r["id"] == A)
    assert (d["from"], d["to"]) == ("2026-01-01", "2026-03-31")
    assert a["returns"] == 0 and a["principal"] == 1500 and a["period"]["contributions"] == 1500  # opening (in range) + transfer
    d2 = client.get("/api/investments/principal-returns?from=2026-04-01&to=2026-06-30").json()
    a2 = next(r for r in d2["accounts"] if r["id"] == A)
    assert a2["returns"] == 60 and a2["period"]["returns"] == 60 and a2["returns_change"] == 60
    assert client.get("/api/investments/principal-returns?interval=year").status_code == 400


def test_returns_report_uses_same_logic(client):
    A, B, J, C = setup(client)
    tx(client, kind="transfer", account_id=C, to_account_id=A, amount=500, date="2026-02-01")
    tx(client, kind="dividend", account_id=A, amount=20, ticker="FAKE")
    tx(client, kind="adjustment", account_id=A, amount=40)                       # unexplained: not gain, not contribution
    rep = client.get("/api/investments/returns?period=custom&start=2026-01-01&end=2026-12-31").json()
    row = next(r for r in rep["rows"] if r["level"] == "account" and r["id"] == A)
    pf = client.get(f"/api/accounts/{A}/history?period=CUSTOM&start=2026-01-01&end=2026-12-31").json()["invest"]["period"]
    assert row["gain"] == pf["returns"] == 20
    assert row["contributions"] == pf["contributions"] == 1500  # opening + transfer, like the account page
    assert row["unexplained"] == 40 and row["md_return"] == pf["md_return"]
    assert row["flow_gain"] == 20 and row["flow_contributions"] == 1540         # old ledger-flow figures kept
    # group & total: transfers between investment accounts cancel out
    tx(client, kind="transfer", account_id=A, to_account_id=B, amount=100)
    rep = client.get("/api/investments/returns?period=custom&start=2026-01-01&end=2026-12-31").json()
    g = next(r for r in rep["rows"] if r["level"] == "group" and A in r["account_ids"] and B in r["account_ids"])
    members = [r for r in rep["rows"] if r["level"] == "account" and r["id"] in g["account_ids"]]
    assert abs(g["contributions"] - sum(m["contributions"] for m in members)) < 0.05 and g["gain"] == 20
    assert next(m for m in members if m["id"] == B)["contributions"] == 100   # but the move shows per account
    assert all("gain" in y and "md_return" in y for y in rep["scope"]["years"])


def test_accounts_tree_still_has_api_fields(client):
    A, B, J, C = setup(client)
    acc = client.get("/api/accounts").json()
    assert "invest" in next(a for a in acc["accounts"] if a["id"] == A)
    assert any(n.get("invest") for n in acc["tree"])
