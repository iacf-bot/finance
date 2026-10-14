"""POST /api/agent/transactions: JSON (+ base64 receipt) and multipart (+ file). FAKE data only."""
import base64

import pytest

from app import receipts as R
from tests.receipt_images import TT_LINES, font, receipt

OCR = R.ocr_status()["available"] and font("en", 20) is not None


@pytest.fixture
def setup(client):
    a = client.post("/api/accounts", json={"name": "Test Sapphire", "type": "credit_card", "currency": "USD", "last4": "4321"}).json()
    b = client.post("/api/accounts", json={"name": "Test Checking", "type": "checking", "currency": "USD"}).json()
    p = client.post("/api/projects", json={"name": "Kitchen Remodel"}).json()
    return {"card": a["id"], "chk": b["id"], "project": p.get("id")}


def post(client, **kw):
    return client.post("/api/agent/transactions", **kw)


def test_json_minimal_and_names(client, setup):
    r = post(client, json={"account": "4321", "amount": 42.5, "merchant": "Fake Diner", "category": "Dining",
                           "tip": "18%", "tax": 2.5, "note": "team lunch", "person": "Alex"})
    assert r.status_code == 200, r.text
    d = r.json()
    t = client.get(f"/api/transactions/{d['id']}").json()
    assert t["account_id"] == setup["card"] and t["amount"] == 42.5 and t["source"] == "agent"
    assert t["tax_amount"] == 2.5 and t["tip_amount"] == round((42.5 - 2.5) / 1.18 * 0.18, 2)
    assert d["ok"] and "Recorded" in d["message"] and f"#{d['id']}" in d["message"] and "tip" in d["message"]


def test_json_no_tax_tip_stays_null(client, setup):
    d = post(client, json={"account": "Test Sapphire", "amount": 10, "date": "2026-10-01"}).json()
    t = client.get(f"/api/transactions/{d['id']}").json()
    assert t["tax_amount"] is None and t["tip_amount"] is None and t["date"].startswith("2026-10-01")


def test_project_reimbursable_kind_review(client, setup):
    d = post(client, json={"account_id": setup["chk"], "amount": -120, "project": "Kitchen Remodel", "reimbursable": True,
                           "review": True}).json()
    t = client.get(f"/api/transactions/{d['id']}").json()
    assert t["amount"] == 120 and t["project_id"] and t["reimbursable"] and t["status"] == "review"
    d = post(client, json={"account": "checking", "amount": 3000, "kind": "income", "note": "salary"}).json()
    assert client.get(f"/api/transactions/{d['id']}").json()["kind"] == "income"
    d = post(client, json={"account": "Test Checking", "to_account": "4321", "amount": 200, "kind": "transfer"}).json()
    assert client.get(f"/api/transactions/{d['id']}").json()["to_account_id"] == setup["card"]


def test_errors_have_message(client, setup):
    r = post(client, json={"account": "nope", "amount": 5})
    assert r.status_code == 400 and "not found" in r.json()["message"]
    r = post(client, json={"account": "Test", "amount": 5})
    assert r.status_code == 400 and "ambiguous" in r.json()["message"]
    r = post(client, json={"account": "4321"})
    assert r.status_code == 400 and "amount" in r.json()["message"]
    r = post(client, content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 400
    r = post(client, json={"account": "4321", "amount": 5, "tax": 4, "tip": 3})
    assert r.status_code == 400 and "Tax + tip" in r.json()["message"]
    r = post(client, json={"account": "4321", "amount": 5, "category": "Zzqx Nonexistent"})
    assert r.status_code == 200 and r.json()["warnings"]


def test_chinese_message(client, setup):
    d = post(client, json={"account": "4321", "amount": 30, "tip": 3}, headers={"accept-language": "zh-CN"}).json()
    assert d["message"].startswith("已记") and "小费" in d["message"]


def test_multipart_with_file(client, setup):
    img = receipt(["FAKE STORE", "TOTAL 9.99"], "en", fmt="PNG") if font("en", 20) else b"\x89PNG\r\n\x1a\n" + b"0" * 64
    r = post(client, data={"account": "4321", "amount": "12.34", "tip": "2", "category": "Dining"},
             files={"receipt": ("r.png", img, "image/png")})
    assert r.status_code == 200, r.text
    d = r.json()
    t = client.get(f"/api/transactions/{d['id']}").json()
    assert t["amount"] == 12.34 and t["tip_amount"] == 2 and t["tax_amount"] is None
    assert len(client.get(f"/api/transactions/{d['id']}/receipts").json()) == 1


@pytest.mark.skipif(not OCR, reason="tesseract/font missing")
def test_json_base64_receipt_fills_amount_tax_tip(client, setup):
    b = base64.b64encode(receipt(TT_LINES, "en")).decode()
    d = post(client, json={"account": "4321", "receipt": "data:image/jpeg;base64," + b}).json()
    t = client.get(f"/api/transactions/{d['id']}").json()
    assert (t["amount"], t["tax_amount"], t["tip_amount"]) == (38.06, 2.66, 5.4)
    assert "amount read from the receipt" in d["warnings"]


def test_category_fuzzy(client, db, setup):
    from app import agent_api
    food = db.one("SELECT id FROM categories WHERE name='Food & Dining'")
    if food:
        assert agent_api.resolve_category(db, "dining", "expense") in {food["id"]} | {r["id"] for r in db.all("SELECT id FROM categories WHERE parent_id=?", (food["id"],))}
