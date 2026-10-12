"""Receipt photos: parsing, local OCR (skipped if Tesseract is missing), API, storage layout. FAKE data only."""
import base64
import io
from pathlib import Path

import pytest

from app import receipts as R
from tests.receipt_images import CN_LINES, EN_LINES, has_font, receipt

OCR = R.ocr_status()["available"]
needs_ocr = pytest.mark.skipif(not OCR, reason="tesseract not installed")


def acct(client, **kw):
    kw = {"name": "Test Card", "type": "credit_card", "currency": "USD", "opening_balance": 0, **kw}
    return client.post("/api/accounts", json=kw).json()


# ---------------------------------------------------------------- text parsing (no OCR needed)
def test_parse_english_text_skips_subtotal_tax_tip():
    p = R.parse_receipt("\n".join(["CAFE TEST", "Date 2026-10-01 08:15", "Latte 4.50", "Subtotal 9.00", "Tax 0.72",
                                   "Tip 2.00", "Amount Due $11.72", "Cash 20.00", "Change 8.28"]))
    assert (p["total"], p["currency"], p["date"], p["merchant"]) == (11.72, "USD", "2026-10-01 08:15", "CAFE TEST")
    assert p["confidence"] >= 0.75


def test_parse_chinese_text_and_formats():
    p = R.parse_receipt("测试饭馆\n2026年9月28日 19:30\n小计 120.00\n优惠 20.00\n实付金额 ￥100.00")
    assert (p["total"], p["currency"], p["date"], p["merchant"]) == (100.0, "CNY", "2026-09-28 19:30", "测试饭馆")
    p = R.parse_receipt("SHOP\nOct 3, 2026\nGRAND TOTAL €1,234.50")
    assert (p["total"], p["currency"], p["date"]) == (1234.5, "EUR", "2026-10-03")
    p = R.parse_receipt("SHOP\n12.00\n7.50")  # no TOTAL line: largest amount, low confidence
    assert p["total"] == 12.0 and p["confidence"] < 0.5


# ---------------------------------------------------------------- OCR on synthetic images
@needs_ocr
@pytest.mark.skipif(not has_font("en"), reason="no font")
def test_ocr_english_receipt(client):
    acct(client, name="Test Visa", last4="0000")
    r = client.post("/api/receipts", files={"file": ("r.jpg", receipt(EN_LINES, "en"), "image/jpeg")}).json()
    assert r["status"] == "draft" and r["draft"]["amount"] == 11.10 and r["draft"]["merchant"] == "TEST MART GROCERY"
    assert r["draft"]["date"] == "2026-10-03 14:25" and r["message"].startswith("Read:")
    assert client.get("/api/transactions").json()["total"] == 0          # nothing saved without confirm


@needs_ocr
@pytest.mark.skipif(not has_font("cn"), reason="no CJK font")
def test_ocr_chinese_receipt_base64(client):
    b = base64.b64encode(receipt(CN_LINES, "cn", fmt="PNG")).decode()
    r = client.post("/api/receipts", json={"image": "data:image/png;base64," + b}).json()
    assert r["draft"]["amount"] == 10.0 and r["parsed"]["currency"] == "CNY" and r["draft"]["merchant"] == "测试便利店"
    assert r["draft"]["date"].startswith("2026-09-28")


@needs_ocr
@pytest.mark.skipif(not has_font("en"), reason="no font")
def test_auto_post_and_review(client):
    a = acct(client, name="Test Visa")
    client.put("/api/settings", json={"default_account_id": a["id"]})
    r = client.post("/api/receipts?auto_post=true", files={"file": ("r.jpg", receipt(EN_LINES, "en"), "image/jpeg")}).json()
    t = r["transaction"]
    assert t["amount"] == 11.10 and t["source"] == "receipt" and r["status"] in ("posted", "review")
    assert [x["id"] for x in client.get(f"/api/transactions/{t['id']}/receipts").json()] == r["receipt_ids"]
    blurry = receipt(["SOMETHING", "12.00", "7.50"], "en")              # no total line -> low confidence -> review
    r2 = client.post("/api/receipts", data={"auto_post": "true"}, files={"file": ("b.jpg", blurry, "image/jpeg")}).json()
    assert r2["transaction"]["status"] == "review"


# ---------------------------------------------------------------- no tesseract: graceful
def test_no_tesseract_fallback(client, monkeypatch):
    monkeypatch.setattr(R, "_tesseract_cmd", lambda: None)
    st = client.get("/api/receipts/status").json()["ocr"]
    assert st["available"] is False and "Tesseract" in st["message"]
    r = client.post("/api/receipts", files={"file": ("r.png", receipt(["X"], "en", fmt="PNG"), "image/png")}).json()
    assert r["status"] == "draft" and r["draft"]["amount"] is None and "no OCR" in r["message"]
    assert client.get(r["receipts"][0]["url"]).status_code == 200


def test_validation(client):
    assert client.post("/api/receipts", files={"file": ("x.jpg", b"not an image", "image/jpeg")}).status_code == 400
    assert client.post("/api/receipts", json={}).status_code == 400
    big = b"\xff\xd8" + b"0" * (R.MAX_BYTES + 10)
    r = client.post("/api/receipts", files={"file": ("big.jpg", big, "image/jpeg")})
    assert r.status_code == 400 and "too large" in r.json()["detail"]


# ---------------------------------------------------------------- storage layout + moves
def _upload_and_link(client, monkeypatch, date="2026-10-03 14:25"):
    monkeypatch.setattr(R, "_tesseract_cmd", lambda: None)  # fast: layout tests don't need OCR
    a = acct(client)
    up = client.post("/api/receipts", files={"file": ("r.png", receipt(["X"], "en", fmt="PNG"), "image/png")}).json()
    t = client.post("/api/transactions", json={"kind": "expense", "amount": 11.1, "account_id": a["id"], "date": date,
                                               "merchant": "Test Mart", "receipt_ids": up["receipt_ids"]}).json()
    return up["receipt_ids"][0], t


def test_path_layout_and_move_on_date_change(client, db, monkeypatch):
    rid, t = _upload_and_link(client, monkeypatch)
    r = db.one("SELECT rel_path FROM receipts WHERE id=?", (rid,))
    assert r["rel_path"] == f"2026/10/03/20261003_142500_test-mart_11-10_{rid}.png"
    base = Path(db.path).parent / "receipts"
    assert (base / r["rel_path"]).exists()
    client.put(f"/api/transactions/{t['id']}", json={"date": "2026-11-05 09:00"})
    r2 = db.one("SELECT rel_path FROM receipts WHERE id=?", (rid,))["rel_path"]
    assert r2 == f"2026/11/05/20261105_090000_test-mart_11-10_{rid}.png"
    assert (base / r2).exists() and not (base / r["rel_path"]).exists() and not (base / "2026/10/03").exists()
    tx = client.get(f"/api/transactions/{t['id']}/receipts").json()
    assert client.get(tx[0]["thumb_url"]).headers["content-type"] == "image/jpeg"


def test_configurable_folder_and_pattern(client, db, monkeypatch, tmp_path):
    nas = tmp_path / "nas" / "Receipts"
    assert client.put("/api/settings", json={"receipts_dir": str(nas), "receipts_pattern": "{yyyy}-{mm}"}).status_code == 200
    assert client.put("/api/settings", json={"receipts_pattern": "../x"}).status_code == 400
    assert client.put("/api/settings", json={"receipts_dir": "relative/dir"}).status_code == 400
    rid, t = _upload_and_link(client, monkeypatch)
    rel = db.one("SELECT rel_path FROM receipts WHERE id=?", (rid,))["rel_path"]
    assert rel.startswith("2026-10/") and not rel.startswith("/") and (nas / rel).exists()


def test_delete_with_transaction_or_keep(client, db, monkeypatch):
    rid, t = _upload_and_link(client, monkeypatch)
    rid2, t2 = _upload_and_link(client, monkeypatch, "2026-10-04 10:00")
    base = Path(db.path).parent / "receipts"
    p2 = base / db.one("SELECT rel_path FROM receipts WHERE id=?", (rid2,))["rel_path"]
    p1 = base / db.one("SELECT rel_path FROM receipts WHERE id=?", (rid,))["rel_path"]
    client.delete(f"/api/transactions/{t['id']}")
    assert not p1.exists() and not db.one("SELECT 1 x FROM receipts WHERE id=?", (rid,))
    client.delete(f"/api/transactions/{t2['id']}?keep_receipts=true")
    assert p2.exists() and db.one("SELECT transaction_id FROM receipts WHERE id=?", (rid2,))["transaction_id"] is None


def test_backup_includes_receipts(client, db, monkeypatch):
    rid, t = _upload_and_link(client, monkeypatch)
    from app import backup
    backup.make_backup(db, "test")
    rel = db.one("SELECT rel_path FROM receipts WHERE id=?", (rid,))["rel_path"]
    assert (Path(db.path).parent / "backups" / "receipts" / rel).exists()


# ---- upload shapes iPhone Shortcuts / browsers actually send (any field name, raw body, no filename, base64 text)
def _shapes(img):
    b64 = base64.b64encode(img).decode()
    B = "ShortcutBoundary"
    nofn = (f"--{B}\r\nContent-Disposition: form-data; name=\"file\"\r\n\r\n").encode() + img + \
           f"\r\n--{B}\r\nContent-Disposition: form-data; name=\"auto_post\"\r\n\r\nfalse\r\n--{B}--\r\n".encode()
    return [
        ("any field name, octet-stream", dict(files={"Photo 1": ("IMG_0001", img, "application/octet-stream")})),
        ("part without filename", dict(content=nofn, headers={"Content-Type": f"multipart/form-data; boundary={B}"})),
        ("raw image/jpeg body", dict(content=img, headers={"Content-Type": "image/jpeg"})),
        ("raw octet-stream body", dict(content=img, headers={"Content-Type": "application/octet-stream"})),
        ("base64 text form field", dict(data={"file": b64})),
        ("json any key", dict(json={"photo": "data:image/jpeg;base64," + b64})),
        ("text/plain base64 body", dict(content=b64.encode(), headers={"Content-Type": "text/plain"})),
    ]


def test_upload_accepts_every_shape(client):
    img = receipt(EN_LINES)
    for label, kw in _shapes(img):
        r = client.post("/api/receipts", **kw)
        assert r.status_code == 200, (label, r.text)
        j = r.json()
        assert j["status"] == "draft" and j["receipt_ids"] and j["message"], label


def test_upload_errors_have_message(client):
    r = client.post("/api/receipts", content=b"%PDF-1.4 not an image" * 100, headers={"Content-Type": "application/pdf"})
    assert r.status_code == 400 and r.json()["message"].startswith("Receipt not saved") and "supported image" in r.json()["message"]
    r = client.post("/api/receipts")
    assert r.status_code == 400 and "No image received" in r.json()["message"]
    r = client.post("/api/receipts", content=b"{bad json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["message"]
    r = client.post("/api/receipts", json={"image": "!!!not-base64!!!" * 10})
    assert r.status_code == 400 and "base64" in r.json()["message"]


def test_heic_raw_body(client):
    pytest.importorskip("pillow_heif")
    from PIL import Image
    import pillow_heif
    pillow_heif.register_heif_opener()
    buf = io.BytesIO()
    Image.open(io.BytesIO(receipt(EN_LINES))).save(buf, "HEIF")
    r = client.post("/api/receipts", content=buf.getvalue())  # no content type at all
    assert r.status_code == 200, r.text
    assert r.json()["receipts"][0]["rel_path"].endswith(".jpg")


def test_without_pillow_photo_is_still_saved(client, monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name.split(".")[0] in ("PIL", "pytesseract", "pillow_heif"):
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    img = receipt(EN_LINES)
    r = client.post("/api/receipts?auto_post=true", files={"file": ("r.jpg", img, "image/jpeg")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "review" and j["receipt_ids"] and "OCR off" in j["message"]
    rid = j["receipt_ids"][0]
    assert client.get(f"/api/receipts/{rid}/image").content == img
    assert client.get(f"/api/receipts/{rid}/image?thumb=1").status_code == 200
    r = client.post("/api/receipts", content=b"x" * 5000, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 400 and "supported image" in r.json()["message"]
