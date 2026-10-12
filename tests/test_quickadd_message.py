"""Quick-add responses carry a short, Siri-friendly 'message' (iPhone / Apple Watch Shortcuts). FAKE data only."""


def mk(client, **kw):
    return client.post("/api/accounts", json=kw).json()


def qa(client, text=None, **kw):
    if text is not None:
        kw.setdefault("json", {"text": text})
    return client.post("/api/quick-add", **kw)


def tx_count(db):
    return db.one("SELECT COUNT(*) AS n FROM transactions")["n"]


def setup(client):
    mk(client, name="Checking", type="checking", currency="USD", opening_balance=1000, aliases="checking")
    mk(client, name="Amex", type="credit_card", currency="USD", opening_balance=-100, aliases="amex")
    mk(client, name="Alipay", type="ewallet", currency="CNY", opening_balance=500, aliases="支付宝, alipay")
    mk(client, name="Cash", type="cash", currency="USD", opening_balance=50, aliases="cash, 现金")


def test_posted_messages_english_and_chinese(client):
    setup(client)
    r = qa(client, "coffee 4.50 cash").json()
    assert r["status"] == "posted" and r["message"] == "Saved: Coffee $4.50 · Cash"
    assert {"parsed", "confidence", "status", "threshold", "transaction"} <= set(r)  # existing keys kept
    assert qa(client, "午饭 35 支付宝").json()["message"] == "已记: 午饭 ¥35 · Alipay"
    assert qa(client, "salary 3000 checking").json()["message"] == "Saved: Salary +$3,000 · Checking"


def test_review_transfer_and_preview(client, db):
    setup(client)
    r = qa(client, "lunch 25").json()
    assert r["status"] == "review" and r["message"] == "Needs review: lunch $25 (account not found)"
    assert qa(client, "transfer 300 checking to amex").json()["message"] == "Transferred $300 Checking → Amex"
    n = tx_count(db)
    r = qa(client, json={"text": "coffee 4.50 cash", "dry_run": True}).json()
    assert r["message"] == "Preview: Coffee $4.50 · Cash" and "prefill" in r
    assert qa(client, json={"text": "lunch 25", "dry_run": "true"}).json()["message"].startswith("Preview (needs review): lunch $25")
    assert tx_count(db) == n  # dry runs never write


def test_balance_and_reconcile_messages_keep_long_detail(client, db):
    setup(client)
    r = qa(client, json={"text": "balance amex 250", "dry_run": True}).json()
    assert r["message"] == "Preview: balance Amex → owed $250" and r["status"] == "preview"
    r = qa(client, "balance alipay 1234.5").json()
    assert r["message"] == "Balance set: Alipay ¥1,234.50" and len(r["detail"]) > len(r["message"])
    assert qa(client, "balance alipay 1234.5").json()["message"] == "Balance unchanged: Alipay ¥1,234.50"
    assert qa(client, "余额 支付宝 1234.5").json()["message"] == "余额无变化: Alipay ¥1,234.50"
    r = qa(client, "reconcile alipay 1234.5").json()
    assert r["action"] == "reconcile" and r["message"].startswith("Reconciled: Alipay ¥1,234.50")


def test_errors_are_json_with_message(client):
    setup(client)
    r = qa(client, "")
    assert r.status_code == 400 and r.json()["message"].startswith("Nothing to add") and r.json()["detail"]
    r = qa(client, "reconcile cash 54")  # mismatch -> 400
    assert r.status_code == 400 and r.json()["message"].startswith("Couldn't save: ")
    r = qa(client, "对账 现金 54")
    assert r.status_code == 400 and r.json()["message"].startswith("没记上: ")
    r = client.post("/api/quick-add", json={"text": "x 1"}, headers={"X-API-Key": "wrong"})
    assert r.status_code == 401 and "message" in r.json()


def test_body_formats_from_shortcuts(client, db):
    setup(client)
    plain = qa(client, content="tea 3 cash".encode(), headers={"Content-Type": "text/plain"}).json()
    assert plain["message"] == "Saved: Tea $3 · Cash"
    n = tx_count(db)
    r = qa(client, content='{"Text": "taxi 12 amex", "dry_run": true}'.encode(),
           headers={"Content-Type": "application/json; charset=utf-8"}).json()
    assert r["message"] == "Preview: Taxi $12 · Amex" and tx_count(db) == n
    # Shortcuts sometimes sends text with a JSON content type, or a bare JSON string
    assert qa(client, content=b"bus 2 cash", headers={"Content-Type": "application/json"}).json()["message"] == "Saved: Bus $2 · Cash"
    assert qa(client, content=b'"snack 1 cash"', headers={"Content-Type": "application/json"}).json()["message"] == "Saved: Snack $1 · Cash"
    assert qa(client, data={"text": "gum 1 cash"}).json()["message"] == "Saved: Gum $1 · Cash"
    assert qa(client, content="午饭 20 支付宝".encode(), headers={"Content-Type": ""}).json()["message"] == "已记: 午饭 ¥20 · Alipay"
