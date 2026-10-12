"""Email rules with kind 'balance': the extracted amount is the new account balance (statement value). FAKE data only."""
from tests.test_principal_returns import inv, mk, tx

TPL = {"name": "Test statement", "sender_pattern": r"statements@example\.test",
       "amount_regex": r"Total Account Value\s*\$?([\d,]+\.\d{2})", "last4_regex": r"Account:\s*X+(\d{4})",
       "kind": "balance", "adjust_reason": "investment_gain", "enabled": 1, "priority": 5}


def mail(value, last4="0000", mid="m1", date="2026-03-31 18:00"):
    return {"from": "Test Broker <statements@example.test>", "subject": "Your monthly statement",
            "body": f"Account: XXXXX{last4}\nTotal Account Value ${value}\n", "received_at": date, "message_id": mid,
            "auto_post": True}


def setup(client):
    a = mk(client, name="Test Brokerage", type="brokerage", currency="USD", opening_balance=1000,
           start_date="2026-01-01", last4="0000")
    r = client.post("/api/email-rules", json=TPL)
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "balance" and r.json()["adjust_reason"] == "investment_gain"
    return a["id"]


def bal(client, aid):
    return next(a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == aid)["balance"]


def test_positive_and_negative_difference_flow_into_returns(client):
    A = setup(client)
    r = client.post("/api/ingest/email", json=mail("1,250.50")).json()
    assert r["result"] == "posted" and r["balance"]["difference"] == 250.5
    t = r["transaction"]
    assert t["kind"] == "adjustment" and t["amount"] == 250.5 and t["date"].startswith("2026-03-31")
    assert bal(client, A) == 1250.5
    x = inv(client, A)
    assert (x["principal"], x["returns"], x["unexplained"]) == (1000, 250.5, 0)   # gain/loss -> returns
    r = client.post("/api/ingest/email", json=mail("1,100.00", mid="m2", date="2026-04-30 18:00")).json()
    assert r["balance"]["difference"] == -150.5 and r["transaction"]["amount"] == -150.5
    assert bal(client, A) == 1100 and inv(client, A)["returns"] == 100


def test_zero_difference_unknown_account_and_dedupe(client):
    A = setup(client)
    r = client.post("/api/ingest/email", json=mail("1,000.00")).json()
    assert r["result"] == "unchanged" and not r["created"]
    r = client.post("/api/ingest/email", json=mail("5,000.00", last4="9999", mid="m3")).json()
    assert r["result"] == "skipped" and "9999" in r["reason"]
    r1 = client.post("/api/ingest/email", json=mail("1,300.00", mid="m4")).json()
    r2 = client.post("/api/ingest/email", json=mail("1,300.00", mid="m4")).json()
    assert r1["result"] == "posted" and r2["result"] == "duplicate"
    assert bal(client, A) == 1300
    log = client.get("/api/ingest-log").json()
    assert {"unchanged", "skipped", "posted", "duplicate"} <= {l["result"] for l in log}


def test_review_mode_reason_default_and_correction_is_unexplained(client):
    A = setup(client)
    m = mail("1,200.00"); m["auto_post"] = False
    r = client.post("/api/ingest/email", json=m).json()
    assert r["result"] == "review" and r["transaction"]["status"] == "review"
    assert bal(client, A) == 1000                                                     # not posted yet
    rid = client.get("/api/email-rules").json()
    rid = next(x["id"] for x in rid if x["name"] == "Test statement")
    client.put(f"/api/email-rules/{rid}", json={"adjust_reason": "correction"})
    r = client.post("/api/ingest/email", json=mail("1,040.00", mid="m9", date="2026-05-01 10:00")).json()
    assert r["result"] == "posted" and inv(client, A)["unexplained"] == 40
    client.put(f"/api/email-rules/{rid}", json={"adjust_reason": ""})                 # blank -> account default
    pv = client.post("/api/email-rules/test", json={"from": "statements@example.test", "body": mail("1,100.00")["body"]}).json()
    assert pv["kind"] == "balance" and pv["balance_preview"]["reason"] == "investment_gain"
    assert pv["balance_preview"]["difference"] == 60 and pv["balance_preview"]["ok"]
    assert bal(client, A) == 1040                                                     # tester never writes
    assert client.put(f"/api/email-rules/{rid}", json={"adjust_reason": "bogus"}).status_code == 400


def test_reconciliation_respected(client):
    A = setup(client)
    assert client.post(f"/api/accounts/{A}/reconcile", json={"balance": 1000, "as_of": "2026-04-15"}).status_code == 200
    r = client.post("/api/ingest/email", json=mail("1,500.00", mid="m5", date="2026-03-31 18:00")).json()
    assert r["result"] == "review" and r["balance"]["before_reconciliation"]


def test_template_offered_not_inserted(client):
    t = client.get("/api/email-rules/templates").json()
    s = next(x for x in t if x["name"] == "Statement balance (Total Account Value)")
    assert s["kind"] == "balance" and s["enabled"] == 0 and s["adjust_reason"] == "investment_gain"
    assert not any(r["name"] == s["name"] for r in client.get("/api/email-rules").json())
