"""Receipt photos 小票: local OCR (Tesseract, optional) -> draft transaction, and image storage.

Files live under the receipts folder (setting `receipts_dir`; blank = <data>/receipts, may point at a NAS / synced
Google Drive folder) in sub-folders from `receipts_pattern` (default {yyyy}/{mm}/{dd}), named
YYYYMMDD_HHMMSS_<merchant-slug>_<amount>_<id>.<ext>. The DB stores only the path RELATIVE to the receipts folder, so
the folder can be moved (change the setting). Files follow their transaction: a date/merchant/amount change renames
or moves them. Nothing here talks to the network: OCR runs on this machine or not at all.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from .db import Database

MAX_BYTES = 15 * 1024 * 1024       # per image
MAX_FILES = 6
MAX_PIXELS = 60_000_000
DEFAULT_PATTERN = "{yyyy}/{mm}/{dd}"
PATTERN_TOKENS = ("{yyyy}", "{mm}", "{dd}")
EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "BMP": "bmp", "TIFF": "tif"}


class ReceiptError(ValueError):
    pass


# ---------------------------------------------------------------- OCR (optional)
def _tesseract_cmd() -> str | None:
    cmd = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if not cmd and os.name == "nt":
        for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
            if Path(p).exists():
                return p
    return cmd


def ocr_status() -> dict:
    """{available, version, languages, message}. OCR is optional: without it receipts are still stored and the
    form opens empty for manual entry."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return {"available": False, "languages": [], "message": "OCR not installed: pip install pytesseract pillow (and Tesseract itself). Photos are still saved; enter the details by hand."}
    cmd = _tesseract_cmd()
    if not cmd:
        return {"available": False, "languages": [], "message": "Tesseract OCR not found on this machine (Linux: apt install tesseract-ocr tesseract-ocr-chi-sim; Windows: winget install UB-Mannheim.TesseractOCR). Photos are still saved; enter the details by hand."}
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        ver = str(pytesseract.get_tesseract_version())
        langs = [l for l in pytesseract.get_languages(config="") if l != "osd"]
    except Exception as e:  # noqa: BLE001
        return {"available": False, "languages": [], "message": f"Tesseract failed to run: {e}"}
    msg = None if "chi_sim" in langs else "Chinese OCR data (chi_sim) missing: Chinese receipts will read poorly."
    return {"available": True, "version": ver, "languages": langs, "message": msg}


def ocr_image(img, denoise: bool = False) -> tuple[str, float | None]:
    """(text, mean word confidence 0..1) for a PIL image; ('', None) when OCR is unavailable."""
    st = ocr_status()
    if not st["available"]:
        return "", None
    import pytesseract
    from PIL import ImageFilter, ImageOps
    g = ImageOps.exif_transpose(img).convert("L")
    if denoise:  # second pass for noisy photos: median filter removes speckle (but blurs small clean text)
        g = g.filter(ImageFilter.MedianFilter(3))
    if max(g.size) < 1600:  # small photos: upscale, Tesseract likes ~30px glyphs
        f = 1600 / max(g.size)
        g = g.resize((int(g.width * f), int(g.height * f)))
    g = ImageOps.autocontrast(g)
    langs = "+".join(l for l in ("eng", "chi_sim") if l in st["languages"]) or "eng"
    data = pytesseract.image_to_data(g, lang=langs, config="--psm 4", output_type=pytesseract.Output.DICT)
    lines: dict = {}
    confs = []
    for i, w in enumerate(data["text"]):
        if not str(w).strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(str(w))
        try:
            c = float(data["conf"][i])
            if c >= 0:
                confs.append(c)
        except (TypeError, ValueError):
            pass
    text = "\n".join(_join_words(ws) for _, ws in sorted(lines.items()))
    return text, (round(sum(confs) / len(confs) / 100, 2) if confs else None)


def _join_words(ws: list[str]) -> str:
    out = ""
    for w in ws:  # no spaces between CJK characters
        if out and not (re.match(r"[\u3400-\u9fff]", out[-1]) and re.match(r"[\u3400-\u9fff]", w[0])):
            out += " "
        out += w
    return out


# ---------------------------------------------------------------- text -> fields
TOTAL_KW = re.compile(r"(grand\s*total|total\s*due|amount\s*due|balance\s*due|total\s*amount|\btotal\b|\bamount\b|"
                      r"实付金额|实付|实收|应付|合计|总计|总额|金额合计|付款金额|支付金额|小计金额)", re.I)
STRONG_KW = re.compile(r"(grand\s*total|total\s*due|amount\s*due|balance\s*due|实付|应付|合计|总计|付款金额|支付金额)", re.I)
SKIP_KW = re.compile(r"(sub\s*-?\s*total|小计|\btax\b|\bvat\b|\bgst\b|税|\btip\b|gratuity|小费|change|找零|cash\b|现金|"
                     r"discount|优惠|折扣|saving|you saved|points|积分|card\s*#|\bauth|balance\s*(?!due))", re.I)
AMT_RX = re.compile(r"(?<![\d.])(?:[$¥￥€£]\s?)?(\d{1,3}(?:[,，]\d{3})+|\d+)[.．·](\d{2})(?!\d)")
CUR_SYM = [("¥", "CNY"), ("￥", "CNY"), ("元", "CNY"), ("RMB", "CNY"), ("CNY", "CNY"), ("人民币", "CNY"),
           ("€", "EUR"), ("EUR", "EUR"), ("£", "GBP"), ("$", "USD"), ("USD", "USD")]
DATE_RXS = [
    (re.compile(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})"), "ymd"),
    (re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](20\d{2}|\d{2})(?!\d)"), "mdy"),
    (re.compile(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d{2})"), "mon"),
    (re.compile(r"(?i)\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(20\d{2})"), "dmon"),
]
TIME_RX = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?::[0-5]\d)?\s*(am|pm|AM|PM)?")
MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
NOT_MERCHANT = re.compile(r"(receipt|invoice|发票|小票|欢迎|welcome|thank|谢谢|tel|phone|电话|www\.|http|\d{3}[-\s]\d{3,4}|"
                          r"order|订单|date|日期|time|时间|cashier|收银|store\s*#|地址|address)", re.I)


def _amt(m: re.Match) -> float:
    return float(m.group(1).replace(",", "").replace("，", "") + "." + m.group(2))


def parse_receipt(text: str, today: datetime | None = None) -> dict:
    """Best-effort fields from OCR text: total, currency, date (+time), merchant, confidence 0..1 and reasons."""
    today = today or datetime.now()
    t = unicodedata.normalize("NFKC", text or "")
    lines = [re.sub(r"\s+", " ", l).strip() for l in t.splitlines()]
    lines = [l for l in lines if l]
    reasons, conf = [], 0.0
    # --- total: amount on (or right after) a TOTAL-like line, skipping subtotal/tax/tip/change lines
    cands = []
    for i, l in enumerate(lines):
        if TOTAL_KW.search(l) and not (SKIP_KW.search(l) and not STRONG_KW.search(l)):
            amts = [_amt(m) for m in AMT_RX.finditer(l)]
            if not amts and i + 1 < len(lines) and not TOTAL_KW.search(lines[i + 1]):
                amts = [_amt(m) for m in AMT_RX.finditer(lines[i + 1])]
            if amts:
                cands.append((2 if STRONG_KW.search(l) else 1, amts[-1], i, l))
    total = None
    if cands:
        best = max(cands, key=lambda c: (c[0], c[1], c[2]))  # strongest keyword, then the largest such amount
        total = best[1]; conf += 0.45 if best[0] == 2 else 0.35; reasons.append(f"total from '{best[3][:40]}'")
    else:
        allamts = [(_amt(m), l) for l in lines if not SKIP_KW.search(l) for m in AMT_RX.finditer(l)]
        if allamts:
            total = max(a for a, _ in allamts); conf += 0.15; reasons.append("total = largest amount (no TOTAL line)")
    # --- currency
    cur = None
    for sym, code in CUR_SYM:
        if sym in t:
            cur = code; break
    if not cur and re.search(r"[\u3400-\u9fff]", t):
        cur = "CNY"
    if cur:
        reasons.append(f"currency {cur}")
    # --- date / time
    date_s = None
    for rx, kind in DATE_RXS:
        m = rx.search(t)
        if not m:
            continue
        try:
            if kind == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif kind == "mdy":
                a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                y += 2000 if y < 100 else 0
                mo, d = (a, b) if a <= 12 else (b, a)
            elif kind == "mon":
                mo, d, y = MONTHS.index(m.group(1).lower()[:3]) + 1, int(m.group(2)), int(m.group(3))
            else:
                d, mo, y = int(m.group(1)), MONTHS.index(m.group(2).lower()[:3]) + 1, int(m.group(3))
            dt = datetime(y, mo, d)
        except (ValueError, IndexError):
            continue
        if dt.date() <= today.date() and (today - dt).days < 3650:
            date_s = dt.strftime("%Y-%m-%d"); conf += 0.15; reasons.append(f"date {date_s}")
            tm = TIME_RX.search(t[m.end():m.end() + 40]) or TIME_RX.search(t)
            if tm:
                h, mi = int(tm.group(1)), int(tm.group(2))
                ap = (tm.group(3) or "").lower()
                h = h % 12 + (12 if ap == "pm" else 0) if ap else h
                date_s += f" {h:02d}:{mi:02d}"
            break
    # --- merchant: first meaningful line near the top
    merchant = None
    for l in lines[:6]:
        clean = re.sub(r"[^\w\u3400-\u9fff&'\[\] .-]", "", l).strip(" .-")
        if len(re.sub(r"[\W\d_]", "", clean)) >= 2 and not NOT_MERCHANT.search(l) and not AMT_RX.search(l):
            merchant = clean[:60]; conf += 0.15; reasons.append(f"merchant '{merchant}' (top line)"); break
    return {"total": total, "currency": cur, "date": date_s, "merchant": merchant, "confidence": round(min(conf, 0.95), 2),
            "reasons": reasons, "lines": lines[:60]}


# ---------------------------------------------------------------- storage
def base_dir(db: Database) -> Path:
    s = (db.get_setting("receipts_dir") or "").strip()
    return Path(os.path.expanduser(s)) if s else Path(db.path).parent / "receipts"


def validate_pattern(p: str) -> str:
    p = (p or "").strip().strip("/\\") or DEFAULT_PATTERN
    rest = p
    for tok in PATTERN_TOKENS:
        rest = rest.replace(tok, "")
    if ".." in p or p.startswith(("/", "\\")) or re.search(r"[:*?\"<>|{}]", rest):
        raise ReceiptError("Folder pattern: use {yyyy}, {mm}, {dd}, letters and '/' only, e.g. {yyyy}/{mm}/{dd} or {yyyy}-{mm}")
    return p


def slug(s: str | None, n: int = 30) -> str:
    s = unicodedata.normalize("NFKC", s or "").strip()
    s = re.sub(r"[^\w\u3400-\u9fff]+", "-", s, flags=re.U).strip("-_").lower()
    return (s[:n].strip("-") or "receipt")


def rel_path_for(db: Database, rid: int, when: str | None, merchant: str | None, amount, ext: str) -> str:
    try:
        dt = datetime.strptime((when or "")[:16].replace("T", " "), "%Y-%m-%d %H:%M") if when and len(when) >= 16 else \
            datetime.strptime((when or "")[:10], "%Y-%m-%d")
    except ValueError:
        dt = datetime.now()
    pat = validate_pattern(db.get_setting("receipts_pattern") or DEFAULT_PATTERN)
    folder = pat.replace("{yyyy}", f"{dt:%Y}").replace("{mm}", f"{dt:%m}").replace("{dd}", f"{dt:%d}")
    amt = "" if amount in (None, "") else f"{abs(float(amount)):.2f}".replace(".", "-")
    name = f"{dt:%Y%m%d_%H%M%S}_{slug(merchant)}" + (f"_{amt}" if amt else "") + f"_{rid}.{ext}"
    return f"{folder}/{name}"


def abs_path(db: Database, rel: str) -> Path:
    base = base_dir(db).resolve()
    p = (base / rel).resolve()
    if base not in p.parents:
        raise ReceiptError("Bad receipt path")
    return p


def load_image(data: bytes, name: str = ""):
    """Validates & opens an upload (JPEG/PNG/WebP/GIF/BMP/TIFF; HEIC via pillow-heif if installed).
    Returns (PIL image, bytes to store, extension)."""
    if not data:
        raise ReceiptError("Empty file")
    if len(data) > MAX_BYTES:
        raise ReceiptError(f"Image too large (max {MAX_BYTES // 1024 // 1024} MB)")
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    heic = data[4:12] in (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1", b"ftypmsf1") or name.lower().endswith((".heic", ".heif"))
    if heic:
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            raise ReceiptError("HEIC photos need pillow-heif on the server: please use JPEG (iPhone: Settings → Camera → Formats → Most Compatible)")
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ReceiptError("Not a supported image (JPEG, PNG, WebP, HEIC)")
    if heic or img.format not in EXT:  # store as JPEG
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=88)
        return img, buf.getvalue(), "jpg"
    return img, data, EXT[img.format]


def decode_b64(s: str) -> bytes:
    s = re.sub(r"^data:[^,]*,", "", (s or "").strip())
    try:
        return base64.b64decode(s, validate=False)
    except Exception:
        raise ReceiptError("Invalid base64 image")


def _dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def save_upload(db: Database, data: bytes, name: str = "") -> dict:
    """Stores one image (dated today until linked), runs OCR. Returns the receipt row + parsed fields."""
    img, blob, ext = load_image(data, name)
    text, ocr_conf = ocr_image(img)
    if text is not None and ocr_conf is not None:
        first = parse_receipt(text)
        if first["confidence"] < 0.5 or first["total"] is None:  # poor read: retry denoised, keep the better one
            t2, c2 = ocr_image(img, denoise=True)
            if parse_receipt(t2)["confidence"] > first["confidence"]:
                text, ocr_conf = t2, c2
    parsed = parse_receipt(text) if text else {"total": None, "currency": None, "date": None, "merchant": None,
                                                 "confidence": 0.0, "reasons": [], "lines": []}
    with db.tx() as con:
        rid = con.execute("""INSERT INTO receipts(orig_name, mime, size, sha256, ocr_text, ocr_confidence, parsed, rel_path)
            VALUES(?,?,?,?,?,?,?,'')""", (name[:200], f"image/{'jpeg' if ext == 'jpg' else ext}", len(blob),
                                         hashlib.sha256(blob).hexdigest(), text[:20000], ocr_conf, _dumps(parsed))).lastrowid
    rel = rel_path_for(db, rid, parsed.get("date") or datetime.now().strftime("%Y-%m-%d %H:%M"), parsed.get("merchant"),
                       parsed.get("total"), ext)
    p = abs_path(db, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(blob)
    with db.tx() as con:
        con.execute("UPDATE receipts SET rel_path=? WHERE id=?", (rel, rid))
    return {**get(db, rid), "parsed": parsed}


def get(db: Database, rid: int) -> dict | None:
    r = db.one("SELECT * FROM receipts WHERE id=?", (rid,))
    if r:
        r = dict(r)
        r["parsed"] = json.loads(r["parsed"] or "{}")
        r["url"] = f"/api/receipts/{rid}/image"
        r["thumb_url"] = f"/api/receipts/{rid}/image?thumb=1"
    return r


def for_tx(db: Database, tid: int) -> list[dict]:
    return [get(db, r["id"]) for r in db.all("SELECT id FROM receipts WHERE transaction_id=? ORDER BY id", (tid,))]


def relocate(db: Database, rid: int) -> str | None:
    """Rename/move one receipt file to match its transaction (date, merchant, amount). Returns the new rel path."""
    r = db.one("""SELECT r.*, t.date tdate, t.amount tamount, t.orig_amount, m.name merchant FROM receipts r
        LEFT JOIN transactions t ON t.id=r.transaction_id LEFT JOIN merchants m ON m.id=t.merchant_id WHERE r.id=?""", (rid,))
    if not r or not r["rel_path"]:
        return None
    ext = r["rel_path"].rsplit(".", 1)[-1]
    if r["transaction_id"] and r["tdate"]:
        want = rel_path_for(db, rid, r["tdate"], r["merchant"] or json.loads(r["parsed"] or "{}").get("merchant"),
                            r["orig_amount"] if r["orig_amount"] is not None else r["tamount"], ext)
    else:
        return r["rel_path"]
    if want == r["rel_path"]:
        return want
    src, dst = abs_path(db, r["rel_path"]), abs_path(db, want)
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        _prune_empty(db, src.parent)
    with db.tx() as con:
        con.execute("UPDATE receipts SET rel_path=? WHERE id=?", (want, rid))
    return want


def relocate_for_tx(db: Database, tid: int) -> None:
    for r in db.all("SELECT id FROM receipts WHERE transaction_id=?", (tid,)):
        try:
            relocate(db, r["id"])
        except (OSError, ReceiptError):
            pass


def link(db: Database, rids: list[int], tid: int) -> list[dict]:
    if not db.one("SELECT 1 x FROM transactions WHERE id=?", (tid,)):
        raise ReceiptError("Transaction not found")
    with db.tx() as con:
        for rid in rids:
            con.execute("UPDATE receipts SET transaction_id=? WHERE id=?", (tid, int(rid)))
    relocate_for_tx(db, tid)
    return for_tx(db, tid)


def _prune_empty(db: Database, d: Path) -> None:
    base = base_dir(db).resolve()
    d = d.resolve()
    while d != base and base in d.parents:
        try:
            d.rmdir()
        except OSError:
            break
        d = d.parent


def delete(db: Database, rid: int) -> None:
    r = db.one("SELECT rel_path FROM receipts WHERE id=?", (rid,))
    if not r:
        return
    if r["rel_path"]:
        try:
            p = abs_path(db, r["rel_path"])
            p.unlink(missing_ok=True)
            _prune_empty(db, p.parent)
        except (OSError, ReceiptError):
            pass
    with db.tx() as con:
        con.execute("DELETE FROM receipts WHERE id=?", (rid,))


def on_tx_delete(db: Database, tids: list[int], keep: bool = False) -> int:
    """Called when transactions are deleted: delete their receipt images, or keep them (unlinked) if keep=True."""
    rows = db.all(f"SELECT id FROM receipts WHERE transaction_id IN ({','.join('?' * len(tids)) or 'NULL'})", tids)
    for r in rows:
        if keep:
            with db.tx() as con:
                con.execute("UPDATE receipts SET transaction_id=NULL WHERE id=?", (r["id"],))
        else:
            delete(db, r["id"])
    return len(rows)


def cleanup_drafts(db: Database, days: int = 14) -> int:
    """Unconfirmed uploads (never linked) older than `days` are removed."""
    rows = db.all("SELECT id FROM receipts WHERE transaction_id IS NULL AND kept=0 AND created_at < datetime('now','localtime',?)",
                  (f"-{int(days)} days",))
    for r in rows:
        delete(db, r["id"])
    return len(rows)


def thumbnail(path: Path, size: int = 360) -> bytes:
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(path))
    img.thumbnail((size, size))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue()


def backup_copy(db: Database, dest_root: Path) -> int:
    """Mirror receipt files into the backups folder (new/changed files only). Skipped when the receipts folder is
    outside the data folder (NAS / Drive folders have their own copies)."""
    base = base_dir(db)
    data = Path(db.path).parent.resolve()
    if not base.exists() or data not in base.resolve().parents and base.resolve() != data:
        return 0
    n = 0
    for p in base.rglob("*"):
        if p.is_file():
            q = dest_root / p.relative_to(base)
            if not q.exists() or q.stat().st_size != p.stat().st_size:
                q.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, q); n += 1
    return n
