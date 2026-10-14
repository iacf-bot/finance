"""Receipt tax/tip/service parsing and multi-pass OCR on synthetic (FAKE) receipts."""
import pytest

from app import receipts as R
from tests.receipt_images import CN_TT_LINES, EN_LINES, TT_LINES, degrade, font, receipt

OCR = R.ocr_status()["available"]
needs_ocr = pytest.mark.skipif(not OCR, reason="tesseract not installed")
has_font = lambda k: font(k, 20) is not None  # noqa: E731


def acct(client):
    return client.post("/api/accounts", json={"name": "Test Visa", "type": "credit_card", "currency": "USD"}).json()


# ---------------------------------------------------------------- text parsing
def test_breakdown_cross_check_en():
    p = R.parse_receipt("\n".join(TT_LINES))
    assert (p["total"], p["subtotal"], p["tax"], p["tip"]) == (38.06, 30.0, 2.66, 5.4)
    assert p["checked"] and p["confidence"] >= 0.8


def test_breakdown_cn_service_charge():
    p = R.parse_receipt("\n".join(CN_TT_LINES))
    assert (p["total"], p["subtotal"], p["tax"], p["service"], p["tip"]) == (116.0, 100.0, 6.0, 10.0, None)
    assert p["checked"]


def test_tip_written_after_printed_total():
    p = R.parse_receipt("BISTRO\nSubtotal 50.00\nTax 4.38\nTotal 54.38\nTip 9.00")
    assert p["total"] == 63.38 and p["tip"] == 9.0 and p["checked"]


def test_never_invents_zero_and_ignores_suggestions():
    p = R.parse_receipt("SHOP\nItem 3.00\nTOTAL 3.00")
    assert p["tax"] is None and p["tip"] is None and p["service"] is None
    p = R.parse_receipt("BISTRO\nSubtotal 50.00\nTax 4.38\nTotal 54.38\nSuggested tip: 18% 9.00 20% 10.00")
    assert p["tip"] is None and p["total"] == 54.38
    p = R.parse_receipt("SHOP\nTotal incl. tax 10.00\nTax ID 12-345")
    assert p["tax"] is None and p["total"] == 10.0


def test_mismatch_lowers_confidence():
    good = R.parse_receipt("\n".join(TT_LINES))
    bad = R.parse_receipt("\n".join(TT_LINES).replace("Tip                 5.40", "Tip                 9.40"))
    assert not bad["checked"] and bad["confidence"] < good["confidence"]


def test_draft_carries_tax_tip(db):
    from app import ingest
    d = ingest.receipt_draft(db, R.parse_receipt("\n".join(TT_LINES)))["draft"]
    assert (d["tax_amount"], d["tip_amount"]) == (2.66, 5.4)
    d = ingest.receipt_draft(db, R.parse_receipt("\n".join(CN_TT_LINES)))["draft"]
    assert (d["tax_amount"], d["tip_amount"]) == (6.0, 10.0)      # service charge 服务费 counts as tip
    d = ingest.receipt_draft(db, R.parse_receipt("SHOP\nTOTAL 3.00"))["draft"]
    assert "tax_amount" not in d or d["tax_amount"] is None


# ---------------------------------------------------------------- real OCR on synthetic images
@needs_ocr
@pytest.mark.skipif(not has_font("en"), reason="no font")
def test_ocr_tax_tip_receipt(client):
    acct(client)
    r = client.post("/api/receipts", files={"file": ("r.jpg", receipt(TT_LINES, "en"), "image/jpeg")}).json()
    d = r["draft"]
    assert (d["amount"], d["tax_amount"], d["tip_amount"]) == (38.06, 2.66, 5.4)
    assert r["parsed"]["checked"] and r["parsed"].get("ocr_pass")


@needs_ocr
@pytest.mark.skipif(not has_font("cn"), reason="no CJK font")
def test_ocr_cn_tax_service(client):
    r = client.post("/api/receipts", files={"file": ("r.png", receipt(CN_TT_LINES, "cn", fmt="PNG"), "image/png")}).json()
    assert r["draft"]["amount"] == 116.0 and r["draft"]["tax_amount"] == 6.0


@needs_ocr
@pytest.mark.skipif(not has_font("en"), reason="no font")
def test_ocr_degraded_photo_multi_pass():
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(degrade(receipt(EN_LINES, "en"), angle=3, noise=4000, contrast=0.5)))
    text, conf, info = R.ocr_best(img)
    assert R.parse_receipt(text)["total"] == 11.10 and info["passes"] >= 1
