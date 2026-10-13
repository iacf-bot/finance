"""Reimbursements 报销: flag, exclusion from stats, receipts (partial, multi-currency), write-off, unlink. FAKE data."""
from datetime import date

M = date.today().strftime("%Y-%m")
D = date.today().isoformat() + " 10:00"


def mk(client, **kw):
    return client.post("/api/accounts", json=kw).json()


def tx(client, **kw):
    kw.setdefault("date", D)
    r = client.post("/api/transactions", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def month(client):
    return client.get(f"/api/summary/month?month={M}").json()


def setup(client):
    c = mk(client, name="Test Checking", type="checking", currency="USD", opening_balance=1000)
    w = mk(client, name="Test Alipay", type="ewallet", currency="CNY", opening_balance=0, aliases="支付宝")
    cash = mk(client, name="Test Cash", type="cash", currency="USD", opening_balance=100, aliases="cash")
    return c["id"], w["id"], cash["id"]


def bal(client, aid):
    return next(a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == aid)["balance"]


def test_reimbursable_excluded_from_stats_balances_unchanged(client, db):
    C, W, CASH = setup(client)
    base = month(client)
    tx(client, kind="expense", account_id=C, amount=50, note="lunch")
    e = tx(client, kind="expense", account_id=C, amount=200, note="client dinner", reimbursable=True, reimb_payer="Acme")
    assert e["reimbursable"] == 1 and e["reimb_payer"] == "Acme"
    m = month(client)
    assert m["expense"] == base["expense"] + 50  # the 200 is not an expense
    assert bal(client, C) == 1000 - 250          # but the balance changes normally
    from app import ledger
    s_, e_ = ledger.month_range(M)
    cats = ledger.breakdown(db, s_, e_, "category", "expense")
    assert round(sum(r["amount"] for r in cats), 2) == 50          # category report
    client.post("/api/budgets", json={"month": M, "amount": 1000, "category_id": None, "ledger_id": 1})
    total = [b for b in ledger.budget_progress(db, M, 1) if b["category_id"] is None]
    assert not total or total[0]["spent"] == 50                   # budgets
    r = client.post("/api/reimbursements/receipts", json={"account_id": C, "items": [{"expense_id": e["id"]}]}).json()
    assert r["receipt"]["kind"] == "reimbursement" and r["receipt"]["amount"] == 200
    assert r["settled"][0]["reimb_status"] == "settled"
    m2 = month(client)
    assert m2["income"] == base["income"] and m2["expense"] == base["expense"] + 50  # receipt is not income
    assert bal(client, C) == 1000 - 50
    # the transactions list header uses the same rules
    s = client.get("/api/transactions?limit=5").json()["summary"]
    assert s["expense"] == 50 and s["income"] == 0


def test_partial_settlement_status_and_totals(client):
    C, W, CASH = setup(client)
    e1 = tx(client, kind="expense", account_id=C, amount=100, reimbursable=True, reimb_payer="Acme")
    e2 = tx(client, kind="expense", account_id=CASH, amount=40, reimbursable=True, reimb_payer="Mom", reimb_expected=30)
    lst = client.get("/api/reimbursements").json()
    assert lst["count"] == 2 and lst["outstanding"]["by_currency"] == [{"currency": "USD", "amount": 130}]
    assert {p["payer"] for p in lst["outstanding"]["by_payer"]} == {"Acme", "Mom"}
    r = client.post("/api/reimbursements/receipts", json={"account_id": C, "items": [{"expense_id": e1["id"], "amount": 60}]})
    assert r.status_code == 200 and r.json()["settled"][0]["reimb_status"] == "partial"
    assert r.json()["receipt"]["reimb_payer"] == "Acme"
    one = next(i for i in client.get("/api/reimbursements").json()["items"] if i["id"] == e1["id"])
    assert (one["reimb_settled"], one["reimb_outstanding"], one["reimb_status"]) == (60, 40, "partial")
    over = client.post("/api/reimbursements/receipts", json={"account_id": C, "items": [{"expense_id": e1["id"], "amount": 41}]})
    assert over.status_code == 400
    # settle the rest of both in one batch
    r = client.post("/api/reimbursements/receipts", json={"account_id": C, "items": [{"expense_id": e1["id"]}, {"expense_id": e2["id"]}]}).json()
    assert r["receipt"]["amount"] == 70 and all(x["reimb_status"] == "settled" for x in r["settled"])
    assert client.get("/api/reimbursements").json()["count"] == 0
    assert client.get("/api/reimbursements?status=settled").json()["count"] == 2
    hist = client.get("/api/reimbursements/receipts").json()["items"]
    assert len(hist) == 2 and len(hist[0]["links"]) == 2


def test_multi_currency_receipt(client):
    C, W, CASH = setup(client)
    e = tx(client, kind="expense", account_id=W, amount=700, reimbursable=True)  # CNY expense
    # reimbursed in USD into checking: amount converted by default, or given explicitly
    r = client.post("/api/reimbursements/receipts", json={"account_id": C, "amount": 98.5, "items": [{"expense_id": e["id"]}]}).json()
    assert r["receipt"]["currency"] == "USD" and r["receipt"]["amount"] == 98.5
    assert r["settled"][0]["reimb_settled"] == 700 and r["settled"][0]["currency"] == "CNY"
    e2 = tx(client, kind="expense", account_id=W, amount=100, reimbursable=True)
    r2 = client.post("/api/reimbursements/receipts", json={"account_id": C, "items": [{"expense_id": e2["id"]}]}).json()
    assert r2["receipt"]["currency"] == "USD" and 0 < r2["receipt"]["amount"] < 100  # converted CNY -> USD


def test_write_off_and_mark_unmark(client):
    C, W, CASH = setup(client)
    base = month(client)["expense"]
    e = tx(client, kind="expense", account_id=C, amount=80, reimbursable=True)
    client.post("/api/reimbursements/receipts", json={"account_id": C, "items": [{"expense_id": e["id"], "amount": 30}]})
    assert month(client)["expense"] == base
    w = client.post(f"/api/reimbursements/{e['id']}/write-off", json={}).json()
    assert w["reimb_status"] == "written_off" and w["reimb_outstanding"] == 0
    assert month(client)["expense"] == base + 50  # the unreimbursed part is a normal expense again
    assert client.post("/api/reimbursements/receipts", json={"account_id": C, "items": [{"expense_id": e["id"]}]}).status_code == 400
    client.post(f"/api/reimbursements/{e['id']}/write-off", json={"undo": True})
    assert month(client)["expense"] == base
    # unmark refused while linked; mark/unmark a fresh one
    assert client.post("/api/reimbursements/mark", json={"ids": [e["id"]], "reimbursable": False}).status_code == 400
    e2 = tx(client, kind="expense", account_id=C, amount=12)
    assert client.post("/api/reimbursements/mark", json={"ids": [e2["id"]], "reimbursable": True, "payer": "Acme"}).json()["updated"] == 1
    assert month(client)["expense"] == base
    assert client.post("/api/reimbursements/mark", json={"ids": [e2["id"]], "reimbursable": False}).json()["updated"] == 1
    assert month(client)["expense"] == base + 12
    # flag only applies to expenses
    i = tx(client, kind="income", account_id=C, amount=5, reimbursable=True)
    assert i["reimbursable"] == 0


def test_unlink_on_delete(client, db):
    C, W, CASH = setup(client)
    e = tx(client, kind="expense", account_id=C, amount=60, reimbursable=True)
    r = client.post("/api/reimbursements/receipts", json={"account_id": C, "items": [{"expense_id": e["id"]}]}).json()
    rid = r["receipt"]["id"]
    assert client.delete(f"/api/transactions/{rid}").status_code == 200
    assert db.one("SELECT COUNT(*) n FROM reimb_links")["n"] == 0
    one = next(i for i in client.get("/api/reimbursements").json()["items"] if i["id"] == e["id"])
    assert one["reimb_status"] == "pending" and one["reimb_outstanding"] == 60
    r = client.post("/api/reimbursements/receipts", json={"account_id": C, "items": [{"expense_id": e["id"]}]}).json()
    # editing a linked expense into a non-reimbursable one is refused; deleting the expense unlinks
    assert client.put(f"/api/transactions/{e['id']}", json={"reimbursable": False}).status_code == 400
    assert client.delete(f"/api/transactions/{e['id']}").status_code == 200
    assert db.one("SELECT COUNT(*) n FROM reimb_links")["n"] == 0
    assert client.get(f"/api/transactions/{r['receipt']['id']}").json()["kind"] == "reimbursement"  # receipt stays


def test_quick_add_flag(client):
    C, W, CASH = setup(client)
    r = client.post("/api/quick-add", json={"text": "报销 午饭 35 支付宝"}, headers={"Accept-Language": "zh-CN"}).json()
    assert r["transaction"]["reimbursable"] == 1 and r["message"].startswith("已记(可报销): 午饭")
    r = client.post("/api/quick-add", json={"text": "taxi 23 cash #reimb:Acme"}).json()
    assert r["transaction"]["reimbursable"] == 1 and r["transaction"]["reimb_payer"] == "Acme"
    assert r["message"].startswith("Saved (reimbursable): ")
    r = client.post("/api/quick-add", content="#报销 打车 20 cash".encode(), headers={"Content-Type": "text/plain"}).json()
    assert r["transaction"]["reimbursable"] == 1
    d = client.post("/api/quick-add", json={"text": "reimb hotel 200 cash", "dry_run": True}).json()
    assert d["prefill"]["reimbursable"] is True and d["message"].startswith("Preview (reimbursable): ")
    assert client.post("/api/quick-add", json={"text": "coffee 4 cash"}).json()["transaction"]["reimbursable"] == 0
