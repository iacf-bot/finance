"""Transaction form upgrades: remembered defaults (prefs API) and the quick-add → form pre-fill payload. FAKE data."""


def test_prefs_defaults_and_meta(client):
    p = client.get("/api/prefs").json()
    assert p == {"tx_last": {}, "quickadd_enter": "add"}
    assert client.get("/api/meta").json()["prefs"] == p


def test_prefs_put_and_validation(client, accounts):
    r = client.put("/api/prefs", json={"quickadd_enter": "form"})
    assert r.status_code == 200 and r.json()["quickadd_enter"] == "form"
    assert client.put("/api/prefs", json={"quickadd_enter": "maybe"}).status_code == 400
    assert client.put("/api/prefs", json={"colour": "red"}).status_code == 400
    assert client.put("/api/prefs", json={"tx_last": {"bogus_kind": {}}}).status_code == 400
    aid = accounts["alipay"]["id"]
    r = client.put("/api/prefs", json={"tx_last": {"expense": {"account_id": aid, "currency": "cny"}}}).json()
    assert r["tx_last"]["expense"] == {"account_id": aid, "currency": "CNY"}
    assert r["quickadd_enter"] == "form"  # other prefs untouched
    # unknown account id rejected
    assert client.post("/api/prefs/tx-last", json={"kind": "expense", "account_id": 999999}).status_code == 400
    r = client.post("/api/prefs/tx-last", json={"kind": "income", "account_id": accounts["checking"]["id"]}).json()
    assert r["tx_last"]["income"]["account_id"] == accounts["checking"]["id"]
    assert r["tx_last"]["expense"]["account_id"] == aid  # per type


def test_remember_on_create(client, accounts):
    cats = client.get("/api/meta").json()["categories"]
    food = next(c for c in cats if c["kind"] == "expense")
    body = {"kind": "expense", "amount": 12, "currency": "USD", "account_id": accounts["sapphire"]["id"],
            "category_id": food["id"], "merchant": "Fake Cafe"}
    client.post("/api/transactions", json=body)  # no remember flag -> nothing stored
    assert client.get("/api/prefs").json()["tx_last"] == {}
    t = client.post("/api/transactions", json={**body, "remember": True}).json()
    assert t["id"] and "remember" not in t
    last = client.get("/api/prefs").json()["tx_last"]["expense"]
    assert last == {"account_id": accounts["sapphire"]["id"], "category_id": food["id"], "currency": "USD"}
    client.post("/api/transactions", json={"kind": "transfer", "amount": 100, "account_id": accounts["checking"]["id"],
                                           "to_account_id": accounts["alipay"]["id"], "to_amount": 700, "remember": True})
    p = client.get("/api/prefs").json()["tx_last"]
    assert p["transfer"]["account_id"] == accounts["checking"]["id"]
    assert p["transfer"]["to_account_id"] == accounts["alipay"]["id"]
    assert p["expense"]["account_id"] == accounts["sapphire"]["id"]  # untouched


def test_dry_run_prefill_payload(client, accounts):
    n = len(client.get("/api/transactions", params={"limit": 500}).json()["items"])
    r = client.post("/api/quick-add", json={"text": "lunch 25.5 chase sapphire #work", "dry_run": True}).json()
    p = r["prefill"]
    assert p["kind"] == "expense" and p["amount"] == 25.5 and p["currency"] == "USD"
    assert p["account_id"] == accounts["sapphire"]["id"] and p["note"] == "lunch 25.5 chase sapphire #work"
    assert p["category_id"] and "work" in p["tags"]
    assert set(p) >= {"to_account_id", "merchant", "member_id", "date", "raw_text"}
    assert "amount" not in r["uncertain"] and "account_id" not in r["uncertain"]
    # transfer: from/to filled
    p = client.post("/api/quick-add", json={"text": "paid 300 from checking to amex", "dry_run": True}).json()["prefill"]
    assert p["kind"] == "transfer" and p["account_id"] == accounts["checking"]["id"] and p["to_account_id"] == accounts["amex"]["id"]
    # currency from the account when the text names none
    p = client.post("/api/quick-add", json={"text": "午饭 35 支付宝", "dry_run": True}).json()["prefill"]
    assert p["account_id"] == accounts["alipay"]["id"] and p["currency"] == "CNY"
    # vague text -> uncertain fields flagged, nothing saved
    r = client.post("/api/quick-add", json={"text": "something", "dry_run": True}).json()
    assert "amount" in r["uncertain"] and "account_id" in r["uncertain"]
    assert len(client.get("/api/transactions", params={"limit": 500}).json()["items"]) == n


def test_plain_add_unchanged(client, accounts):
    r = client.post("/api/quick-add", json={"text": "lunch 25.5 chase sapphire"}).json()
    assert r["transaction"]["amount"] == 25.5 and "prefill" not in r
