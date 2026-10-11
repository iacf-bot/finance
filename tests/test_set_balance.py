"""Set balance 调整余额 (like 随手记 余额变更): adjustment transactions instead of silent opening-balance edits. FAKE data."""
from datetime import datetime, timedelta


def acct(client, aid):
    return next(a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == aid)


def tx_list(client, **params):
    return client.get("/api/transactions", params={"limit": 500, **params}).json()["items"]


def test_new_account_opening_balance_is_a_transaction(client):
    a = client.post("/api/accounts", json={"name": "Fake Bank", "type": "checking", "opening_balance": 250,
                                            "start_date": "2026-01-15"}).json()
    assert a["balance"] == 250 and a["opening_balance"] == 0  # no double counting via the legacy field
    t = tx_list(client, account_id=a["id"])
    assert len(t) == 1 and t[0]["kind"] == "adjustment" and t[0]["source"] == "opening_balance"
    assert t[0]["date"] == "2026-01-15 00:00" and t[0]["amount"] == 250
    # liability opening (signed, as the UI sends it): owes 300
    c = client.post("/api/accounts", json={"name": "Fake Card", "type": "credit_card", "opening_balance": -300}).json()
    assert c["owed"] == 300


def test_positive_negative_and_zero(client, accounts):
    aid = accounts["checking"]["id"]  # balance 1000
    r = client.post(f"/api/accounts/{aid}/set-balance", json={"balance": 1234.5, "note": "bank app"}).json()
    assert r["created"] and r["difference"] == 234.5 and r["previous"] == 1000
    t = r["transaction"]
    assert t["kind"] == "adjustment" and t["amount"] == 234.5 and t["source"] == "balance_set"
    assert "Balance set to 1,234.50 USD (was 1,000.00 USD)" in t["note"] and t["note"].startswith("bank app")
    assert t["category_label"].startswith("Balance adjustment") and "余额调整" in t["category_label"]
    assert acct(client, aid)["balance"] == 1234.5
    r = client.post(f"/api/accounts/{aid}/set-balance", json={"balance": "1,000"}).json()
    assert r["difference"] == -234.5 and r["transaction"]["amount"] == -234.5
    assert acct(client, aid)["balance"] == 1000
    n = len(tx_list(client, account_id=aid))
    r = client.post(f"/api/accounts/{aid}/set-balance", json={"balance": 1000}).json()
    assert r["created"] is False and r["transaction"] is None and "nothing recorded" in r["message"]
    assert len(tx_list(client, account_id=aid)) == n


def test_liability_amount_owed(client, accounts):
    aid = accounts["amex"]["id"]  # owes 100
    assert acct(client, aid)["owed"] == 100
    r = client.post(f"/api/accounts/{aid}/set-balance", json={"balance": 450}).json()
    assert r["liability"] and r["previous"] == 100 and r["difference"] == -350
    assert "Amount owed set to 450.00 USD (was 100.00 USD)" in r["transaction"]["note"]
    a = acct(client, aid)
    assert a["owed"] == 450 and a["balance"] == -450
    # paid off and in credit
    client.post(f"/api/accounts/{aid}/set-balance", json={"balance": -20})
    assert acct(client, aid)["owed"] == -20


def test_backdated_adjustment_compares_with_balance_on_that_date(client, accounts):
    aid = accounts["cash"]["id"]  # opening tx today 00:00 for 50
    client.post("/api/transactions", json={"kind": "income", "amount": 100, "account_id": aid, "date": "2026-03-10 12:00"})
    client.post("/api/transactions", json={"kind": "expense", "amount": 30, "account_id": aid, "date": "2026-03-20 12:00"})
    # on 2026-03-15 the app thinks the cash balance is 100 (opening is dated later); the real balance was 80
    r = client.post(f"/api/accounts/{aid}/set-balance", json={"balance": 80, "date": "2026-03-15T09:30"}).json()
    assert r["previous"] == 100 and r["difference"] == -20 and r["transaction"]["date"] == "2026-03-15 09:30"
    # date-only means end of that day
    r = client.post(f"/api/accounts/{aid}/set-balance", json={"balance": 80, "date": "2026-03-15"}).json()
    assert r["created"] is False and r["date"] == "2026-03-15 23:59"
    # current balance shifts by the same difference: 50 + 100 - 30 - 20
    assert acct(client, aid)["balance"] == 100


def test_edit_account_balance_field_creates_adjustment(client, accounts):
    aid = accounts["checking"]["id"]
    r = client.put(f"/api/accounts/{aid}", json={"name": "Test Checking", "balance": 1500}).json()
    assert r["balance"] == 1500 and r["opening_balance"] == 0 and r["balance_set"]["difference"] == 500
    assert any(t["source"] == "balance_set" and t["amount"] == 500 for t in tx_list(client, account_id=aid))
    # unchanged balance -> no transaction
    n = len(tx_list(client, account_id=aid))
    r = client.put(f"/api/accounts/{aid}", json={"balance": 1500}).json()
    assert r["balance_set"]["created"] is False and len(tx_list(client, account_id=aid)) == n


def test_adjustments_editable_deletable_and_excluded_from_reports(client, accounts):
    aid = accounts["checking"]["id"]
    month = datetime.now().strftime("%Y-%m")
    before = client.get("/api/dashboard", params={"month": month}).json()
    nw0 = client.get("/api/accounts").json()["net_worth"]["net_worth"]
    t = client.post(f"/api/accounts/{aid}/set-balance", json={"balance": 6000}).json()["transaction"]
    after = client.get("/api/dashboard", params={"month": month}).json()
    assert after["totals"] == before["totals"] and after["trend"] == before["trend"]
    assert after["by_category"] == before["by_category"] and after["budgets"] == before["budgets"]
    assert after["net_worth"]["net_worth"] == before["net_worth"]["net_worth"] + 5000
    rep = client.get("/api/reports", params={"group_by": "category", "kind": "income"}).json()
    assert all("余额调整" not in (r["name"] or "") for r in rep["rows"])
    assert rep["totals"] == client.get("/api/reports", params={"group_by": "category"}).json()["totals"]
    assert client.get("/api/accounts").json()["net_worth"]["net_worth"] == nw0 + 5000
    # editable
    e = client.put(f"/api/transactions/{t['id']}", json={**t, "amount": 4000}).json()
    assert e["amount"] == 4000 and acct(client, aid)["balance"] == 5000
    # deletable
    assert client.delete(f"/api/transactions/{t['id']}").status_code == 200
    assert acct(client, aid)["balance"] == 1000
    # filter by source
    assert all(x["source"] == "balance_set" for x in tx_list(client, source="balance_set"))


def test_api_errors_and_legacy_adjust(client, accounts):
    aid = accounts["checking"]["id"]
    assert client.post(f"/api/accounts/{aid}/set-balance", json={}).status_code == 400
    assert client.post(f"/api/accounts/{aid}/set-balance", json={"balance": "abc"}).status_code == 400
    assert client.post("/api/accounts/99999/set-balance", json={"balance": 1}).status_code == 404
    r = client.post(f"/api/accounts/{aid}/adjust", json={"target_balance": 1100}).json()
    assert r["kind"] == "adjustment" and r["amount"] == 100


def test_requires_auth(app):
    from fastapi.testclient import TestClient
    c = TestClient(app)
    assert c.post("/api/accounts/1/set-balance", json={"balance": 1}, follow_redirects=False).status_code in (401, 303, 307)


def test_quick_add_set_balance(client, accounts):
    r = client.post("/api/quick-add", json={"text": "余额 支付宝 1234.5"}).json()
    assert r["action"] == "set_balance" and r["status"] == "posted"
    assert r["balance_set"]["difference"] == 734.5 and r["transaction"]["currency"] == "CNY"
    assert acct(client, accounts["alipay"]["id"])["balance"] == 1234.5
    r = client.post("/api/quick-add", json={"text": "balance checking 5230"}).json()
    assert acct(client, accounts["checking"]["id"])["balance"] == 5230
    # liabilities: amount owed
    client.post("/api/quick-add", json={"text": "balance amex 820"})
    assert acct(client, accounts["amex"]["id"])["owed"] == 820
    # unchanged
    r = client.post("/api/quick-add", json={"text": "bal checking 5,230"}).json()
    assert r["status"] == "unchanged" and r["transaction"] is None
    # dry run doesn't write
    n = len(tx_list(client))
    r = client.post("/api/quick-add", json={"text": "balance cash 10", "dry_run": True}).json()
    assert r["status"] == "preview" and len(tx_list(client)) == n
    # back-dated
    r = client.post("/api/quick-add", json={"text": "balance cash 10 yesterday"}).json()
    assert r["transaction"]["date"].startswith((datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))
    # errors: no account / no amount
    assert client.post("/api/quick-add", json={"text": "balance 500"}).status_code == 400
    assert client.post("/api/quick-add", json={"text": "balance checking"}).status_code == 400
    # 余额宝 is an account-ish word, not the balance command; normal quick-add still works
    r = client.post("/api/quick-add", json={"text": "lunch 12 amex"}).json()
    assert r["transaction"]["kind"] == "expense"


def test_email_never_sets_balances(client, accounts):
    r = client.post("/api/ingest/email", json={"from": "x@fake.example", "subject": "Balance checking 99999",
                                                "body": "balance checking 99999"}).json()
    assert acct(client, accounts["checking"]["id"])["balance"] == 1000
    assert not tx_list(client, source="balance_set")
