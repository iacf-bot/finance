"""Balance, transfer, credit card, multi-currency and investment calculations (fake data)."""


def bal(client, aid):
    return next(a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == aid)


def test_opening_and_expense_income(client, accounts):
    c = accounts["checking"]["id"]
    client.post("/api/transactions", json={"kind": "expense", "amount": 40, "account_id": c, "date": "2026-10-01"})
    client.post("/api/transactions", json={"kind": "income", "amount": 500, "account_id": c, "date": "2026-10-02"})
    client.post("/api/transactions", json={"kind": "refund", "amount": 10, "account_id": c, "date": "2026-10-03"})
    assert bal(client, c)["balance"] == 1000 - 40 + 500 + 10


def test_credit_card_and_payment_transfer(client, accounts):
    c, cc = accounts["checking"]["id"], accounts["sapphire"]["id"]
    client.post("/api/transactions", json={"kind": "expense", "amount": 120, "account_id": cc})
    a = bal(client, cc)
    assert a["balance"] == -120 and a["owed"] == 120 and a["available_credit"] == 4880
    r = client.post("/api/transactions", json={"kind": "transfer", "amount": 100, "account_id": c, "to_account_id": cc})
    assert r.status_code == 200
    assert bal(client, cc)["owed"] == 20
    assert bal(client, c)["balance"] == 900


def test_transfer_validation(client, accounts):
    c = accounts["checking"]["id"]
    r = client.post("/api/transactions", json={"kind": "transfer", "amount": 10, "account_id": c, "to_account_id": c})
    assert r.status_code == 400
    r = client.post("/api/transactions", json={"kind": "expense", "amount": 0, "account_id": c})
    assert r.status_code == 400


def test_cross_currency_transfer_and_conversion(client, accounts):
    c, ali = accounts["checking"]["id"], accounts["alipay"]["id"]
    client.put("/api/fx", json={"currency": "CNY", "rate": 0.125})  # 8 CNY = 1 USD (fake rate)
    client.post("/api/transactions", json={"kind": "transfer", "amount": 100, "account_id": c, "to_account_id": ali})
    assert bal(client, ali)["balance"] == 500 + 800
    # expense entered in USD on a CNY account is converted into CNY
    t = client.post("/api/transactions", json={"kind": "expense", "amount": 10, "currency": "USD", "account_id": ali}).json()
    assert t["amount"] == 80 and t["orig_amount"] == 10 and t["orig_currency"] == "USD"
    assert bal(client, ali)["balance"] == 1220
    assert bal(client, ali)["balance_base"] == 152.5


def test_adjustment_endpoint(client, accounts):
    cash = accounts["cash"]["id"]
    client.post(f"/api/accounts/{cash}/adjust", json={"target_balance": 75})
    assert bal(client, cash)["balance"] == 75
    amex = accounts["amex"]["id"]
    client.post(f"/api/accounts/{amex}/adjust", json={"target_balance": 250, "as_owed": True})
    assert bal(client, amex)["owed"] == 250


def test_net_worth(client, accounts):
    nw = client.get("/api/accounts").json()["net_worth"]
    # fresh fixture: checking 1000, alipay 500 CNY*0.14=70, cash 50, amex owes 100
    assert nw["assets"] == 1120 and nw["liabilities"] == 100 and nw["net_worth"] == 1020


def test_investments(client, accounts):
    b = accounts["broker"]["id"]
    client.post("/api/transactions", json={"kind": "transfer", "amount": 5000, "account_id": accounts["checking"]["id"], "to_account_id": b})
    client.post("/api/transactions", json={"kind": "buy", "account_id": b, "ticker": "fake", "shares": 10, "price": 100, "fee": 1, "date": "2026-01-01"})
    client.post("/api/transactions", json={"kind": "buy", "account_id": b, "ticker": "FAKE", "shares": 10, "price": 200, "date": "2026-02-01"})
    client.post("/api/transactions", json={"kind": "sell", "account_id": b, "ticker": "FAKE", "shares": 5, "price": 250, "fee": 1, "date": "2026-03-01"})
    client.post("/api/transactions", json={"kind": "dividend", "account_id": b, "ticker": "FAKE", "amount": 12, "date": "2026-04-01"})
    client.post("/api/prices", json={"ticker": "FAKE", "price": 300})
    inv = client.get("/api/investments").json()
    h = inv["holdings"][0]
    assert h["ticker"] == "FAKE" and h["shares"] == 15
    assert h["cost"] == round(3001 * 15 / 20, 2)  # average cost basis
    assert h["market_value"] == 4500
    assert h["dividends"] == 12
    assert h["realized"] == round(1249 - 3001 / 20 * 5, 2)
    acct = bal(client, b)
    assert acct["cash_balance"] == round(5000 - 1001 - 2000 + 1249 + 12, 2)
    assert acct["balance"] == round(acct["cash_balance"] + 4500, 2)


def test_review_items_do_not_affect_balance(client, accounts):
    c = accounts["checking"]["id"]
    client.post("/api/transactions", json={"kind": "expense", "amount": 999, "account_id": c, "status": "review"})
    assert bal(client, c)["balance"] == 1000


def test_dashboard_totals(client, accounts):
    from datetime import date
    c = accounts["checking"]["id"]
    d = date.today().strftime("%Y-%m-05")
    client.post("/api/transactions", json={"kind": "income", "amount": 3000, "account_id": c, "date": d})
    client.post("/api/transactions", json={"kind": "expense", "amount": 200, "account_id": c, "date": d, "category_id": 2})
    client.post("/api/transactions", json={"kind": "refund", "amount": 50, "account_id": c, "date": d, "category_id": 2})
    client.post("/api/transactions", json={"kind": "transfer", "amount": 70, "account_id": c, "to_account_id": accounts["cash"]["id"], "date": d})
    dash = client.get("/api/dashboard").json()
    assert dash["totals"]["income"] == 3000 and dash["totals"]["expense"] == 150 and dash["totals"]["net"] == 2850
    assert dash["by_category"][0]["amount"] == 150
    assert dash["net_worth"]["net_worth"] == 1020 + 2850
