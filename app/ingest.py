"""Quick-add, email ingestion and IMAP mailbox polling."""
from __future__ import annotations

import email
import email.header
import email.utils
import hashlib
import imaplib
import logging
import os
import re
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import ledger
from .db import Database, now_iso
from .email_rules import parse_email
from .parser import parse_quick_add, find_accounts

log = logging.getLogger("finance.ingest")


# ---------------------------------------------------------------- secrets
class SecretBox:
    """Fernet encryption with a key stored in data/secret.key (chmod 600)."""

    def __init__(self, key_path: str | os.PathLike):
        self.key_path = Path(key_path)
        if not self.key_path.exists():
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            self.key_path.write_bytes(Fernet.generate_key())
            from .config import private_file
            private_file(self.key_path)
        self.f = Fernet(self.key_path.read_bytes().strip())

    def encrypt(self, s: str) -> str:
        return self.f.encrypt(s.encode()).decode()

    def decrypt(self, s: str) -> str:
        try:
            return self.f.decrypt(s.encode()).decode()
        except (InvalidToken, ValueError):
            raise ValueError("Stored password cannot be decrypted (secret.key changed?). Re-enter the password.")


# ---------------------------------------------------------------- quick add
BALANCE_RX = re.compile(r"^\s*(?:调整余额|设置余额|余额(?!宝)|set\s+balance\b|balance\b|bal\b)[\s:：=]*", re.I)


class QuickAddError(ValueError):
    pass


REASON_WORDS = [
    ("unrecorded_spending", r"(?:unrecorded\s+spending|未记支出)"),
    ("unrecorded_income", r"(?:unrecorded\s+income|未记收入)"),
    ("investment_gain", r"(?<![A-Za-z])(?:gain|loss|returns?|投资收益|收益|亏损)(?![A-Za-z])"),
    ("interest", r"(?<![A-Za-z])(?:interest|利息)(?![A-Za-z])"),
    ("fees", r"(?<![A-Za-z])(?:fees?|费用|手续费)(?![A-Za-z])"),
    ("correction", r"(?<![A-Za-z])(?:correction|fix|校正|其他)(?![A-Za-z])"),
]


RECONCILE_RX = re.compile(r"^\s*(?:对账|reconcile\b|recon\b)[\s:：=]*", re.I)


def quick_reconcile(db: Database, text: str, ledger_id: int | None = None, dry_run: bool = False) -> dict:
    """'reconcile chase 1234.56' / '对账 支付宝 1000': mark the account reconciled at that balance (now, or a named date).
    Only when the app agrees with the statement; otherwise use the Reconcile dialog (or 'balance …') first."""
    from . import reconcile
    m = RECONCILE_RX.match(text)
    rest = text[m.end():]
    res = parse_quick_add(rest, ledger.parse_ctx(db))
    if not res.account_id or not any("account matched" in r for r in res.reasons):
        raise QuickAddError("Reconcile: name the account, e.g. 'reconcile chase 1234.56' or '对账 支付宝 1000'")
    if res.amount is None:
        raise QuickAddError("Reconcile: no statement balance found, e.g. 'reconcile chase 1234.56'")
    amount = -res.amount if re.search(r"(?<![\w.])-\s*[$¥￥]?\s*\d", rest) else res.amount
    from .parser import parse_date
    when = parse_date(rest, datetime.now())[0]
    try:
        p = reconcile.preview(db, res.account_id, when, amount)
    except reconcile.ReconcileError as e:
        raise QuickAddError(str(e))
    out = {"parsed": {**res.to_dict(), "kind": "reconcile", "amount": amount}, "confidence": 1.0, "action": "reconcile",
           "difference": p["difference_shown"]}
    if abs(p["difference"]) >= 0.005:
        raise QuickAddError(f"Reconcile: {p['account_name']} is {p['computed']:,.2f} in the app vs {p['statement']:,.2f} on the "
                            f"statement (difference {p['difference_shown']:+,.2f}). Open Accounts → Reconcile to record the "
                            f"difference, or use 'balance …' first.")
    if dry_run:
        out.update(status="preview", message=f"Would mark {p['account_name']} reconciled at {p['statement']:,.2f} {p['currency']} (matches)")
        return out
    r = reconcile.create(db, res.account_id, amount, when)
    out.update(status="posted", message=r["message"], reconciliation=r["reconciliation"])
    return out


def quick_set_balance(db: Database, text: str, ledger_id: int | None = None, dry_run: bool = False) -> dict:
    """'余额 支付宝 1234.5' / 'balance checking 5230' / 'balance amex 820 yesterday' (liabilities: amount owed)."""
    m = BALANCE_RX.match(text)
    rest = text[m.end():]
    reason = None  # optional reason word: 'balance brokerage 52000 gain' / '余额 余额宝 1000 利息'
    for code, rx in REASON_WORDS:
        mm = re.search(rx, rest, re.I)
        if mm:
            reason = code
            rest = (rest[:mm.start()] + " " + rest[mm.end():]).strip()
            break
    res = parse_quick_add(rest, ledger.parse_ctx(db))
    explicit = any("account matched" in r for r in res.reasons)
    if not res.account_id or not explicit:
        raise QuickAddError("Set balance: name the account, e.g. '余额 支付宝 1234.5' or 'balance checking 5230'")
    if res.amount is None:
        raise QuickAddError("Set balance: no amount found, e.g. 'balance checking 5230'")
    amount = -res.amount if re.search(r"(?<![\w.])-\s*[$¥￥]?\s*\d", rest) else res.amount
    from .parser import parse_date
    when = parse_date(rest, datetime.now())[0]  # None unless the text names a date ('yesterday', '10/3', '2026-10-01')
    acct = db.one("SELECT name, currency FROM accounts WHERE id=?", (res.account_id,))
    a_type = (db.one("SELECT type FROM accounts WHERE id=?", (res.account_id,)) or {}).get("type")
    reason = reason or ledger.default_reason(a_type)
    out = {"parsed": {**res.to_dict(), "kind": "adjustment", "amount": amount, "adj_reason": reason},
           "confidence": 1.0, "action": "set_balance", "reason": reason, "reason_label": ledger.reason_label(reason)}
    if dry_run:
        before = ledger.account_balance_at(db, res.account_id, when)
        out.update(status="preview", message=f"Would set the balance of {acct['name']} to {amount:,.2f} {acct['currency']}"
                                             f" · reason: {ledger.reason_label(reason)}")
        out["previous_signed"] = before
        return out
    r = ledger.set_balance(db, res.account_id, amount, when, None, ledger_id=ledger_id, reason=reason)
    out.update(status="posted" if r["created"] else "unchanged", balance_set={k: v for k, v in r.items() if k != "transaction"},
               transaction=r["transaction"], message=r["message"])
    return out


def prefill_from_parse(db: Database, res, text: str) -> dict:
    """Form pre-fill for 'Open in form': every field the parser found, plus the fields it was unsure about."""
    acct = db.one("SELECT currency FROM accounts WHERE id=?", (res.account_id,)) if res.account_id else None
    reasons = " | ".join(res.reasons)
    unsure = []
    if res.amount is None or "several numbers" in reasons:
        unsure.append("amount")
    if not res.account_id or "used default account" in reasons or "several accounts" in reasons:
        unsure.append("account_id")
    if res.kind == "transfer" and not res.to_account_id:
        unsure.append("to_account_id")
    if res.kind in ("expense", "income", "refund") and not res.category_id:
        unsure.append("category_id")
    if res.kind not in ("expense",) and res.confidence < 0.75:
        unsure.append("kind")
    prefill = {"kind": res.kind, "amount": res.amount,
               "currency": res.currency or (acct["currency"] if acct else None) or db.get_setting("base_currency", "USD"),
               "account_id": res.account_id, "to_account_id": res.to_account_id, "category_id": res.category_id,
               "merchant": res.merchant, "member_id": res.member_id, "project_id": res.project_id,
               "tags": ",".join(res.tags), "date": res.date, "note": (text or "").strip(), "raw_text": text}
    return {"prefill": prefill, "uncertain": unsure}


def quick_add(db: Database, text: str, source: str = "api", ledger_id: int | None = None,
              dry_run: bool = False, force_review: bool = False, threshold: float | None = None,
              external_id: str | None = None, allow_balance: bool = True) -> dict:
    if allow_balance and RECONCILE_RX.match(text or ""):
        return quick_reconcile(db, text, ledger_id, dry_run)
    if allow_balance and BALANCE_RX.match(text or ""):
        return quick_set_balance(db, text, ledger_id, dry_run)
    res = parse_quick_add(text, ledger.parse_ctx(db))
    th = float(threshold if threshold is not None else db.get_setting("review_threshold", "0.75"))
    status = "posted" if (res.confidence >= th and not force_review and res.amount and res.account_id) else "review"
    out = {"parsed": res.to_dict(), "confidence": res.confidence, "status": status, "threshold": th}
    if dry_run:
        out.update(prefill_from_parse(db, res, text))
        return out
    data = {"kind": res.kind, "date": res.date, "amount": res.amount or 0, "currency": res.currency,
            "account_id": res.account_id, "to_account_id": res.to_account_id, "category_id": res.category_id,
            "merchant": res.merchant, "member_id": res.member_id, "project_id": res.project_id, "tags": res.tags,
            "note": text.strip(), "source": source, "status": status, "confidence": res.confidence,
            "raw_text": text, "ledger_id": ledger_id or int(db.get_setting("default_ledger_id", "1") or 1),
            "external_id": external_id}
    try:
        out["transaction"] = ledger.create_tx(db, data)
    except ledger.ValidationError as e:
        data["status"] = "review"
        out["status"] = "review"
        out["warning"] = str(e)
        out["transaction"] = ledger.create_tx(db, data)
    return out


# ---------------------------------------------------------------- email ingest
def _dedupe_id(msg: dict) -> str:
    mid = (msg.get("message_id") or "").strip()
    if mid:
        return mid
    h = hashlib.sha1(f"{msg.get('from')}|{msg.get('subject')}|{msg.get('received_at')}|{(msg.get('body') or '')[:500]}".encode())
    return "sha1:" + h.hexdigest()


def _log(db: Database, **kw):
    kw.setdefault("time", now_iso())
    with db.tx() as con:
        cols = list(kw)
        con.execute(f"INSERT INTO ingest_log({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [kw[c] for c in cols])
        # keep the log bounded
        con.execute("DELETE FROM ingest_log WHERE id NOT IN (SELECT id FROM ingest_log ORDER BY id DESC LIMIT 2000)")


def ingest_email(db: Database, msg: dict, mailbox: dict | None = None, auto_post: bool | None = None,
                 source: str = "email") -> dict:
    """msg: {from, subject, body, received_at, message_id?}. Returns result dict."""
    sender, subject, body = msg.get("from") or "", msg.get("subject") or "", msg.get("body") or ""
    mid = _dedupe_id(msg)
    mbox_id = mailbox["id"] if mailbox else None
    logbase = dict(mailbox_id=mbox_id, source="imap" if mailbox else "api", sender=sender[:200], subject=subject[:300], message_id=mid[:300])
    if db.one("SELECT 1 x FROM email_seen WHERE message_id=?", (mid,)):
        _log(db, **logbase, result="duplicate", detail="Message already processed")
        return {"created": False, "result": "duplicate", "message_id": mid}
    with db.tx() as con:
        con.execute("INSERT OR IGNORE INTO email_seen(message_id, mailbox_id) VALUES(?,?)", (mid, mbox_id))

    rules = db.all("SELECT * FROM email_rules WHERE enabled=1")
    ex = parse_email(sender, subject, body, rules)
    text = ex["text"]
    if ex.get("amount") is None:
        # maybe a plain "lunch 25 alipay" note mailed to the inbox
        first = (subject + " " + body.strip().split("\n")[0]) if body.strip() else subject
        qa = quick_add(db, first[:300], dry_run=True, allow_balance=False)
        if qa["parsed"]["amount"]:
            res = quick_add(db, first[:300], allow_balance=False, source=source, force_review=not auto_post, external_id=mid)
            tid = res["transaction"]["id"]
            _log(db, **logbase, result=res["status"], detail="parsed as quick-add text", confidence=res["confidence"], transaction_id=tid)
            return {"created": True, "result": res["status"], "transaction": res["transaction"], "extracted": ex | {"text": None}}
        _log(db, **logbase, result="skipped", detail="No amount found", rule_name=ex.get("rule_name"))
        return {"created": False, "result": "skipped", "reason": "No amount found", "message_id": mid}

    conf = 0.4
    reasons = [f"amount {ex['amount']}"]
    if ex.get("rule_name"):
        conf += 0.1; reasons.append(f"rule {ex['rule_name']}")
    accounts = db.all("SELECT * FROM accounts WHERE archived=0")
    account_id = None
    if ex.get("last4"):
        l4 = str(ex["last4"])
        for a in accounts:
            if a.get("last4") and (l4.endswith(str(a["last4"])) or str(a["last4"]).endswith(l4)):
                account_id = a["id"]; conf += 0.3; reasons.append(f"account by last digits {l4}"); break
    if not account_id:
        hits = find_accounts(text[:1000], accounts)
        if len({h["account_id"] for h in hits}) == 1:
            account_id = hits[0]["account_id"]; conf += 0.2; reasons.append("account by name")
    if not account_id:
        dflt = (mailbox or {}).get("default_account_id") or db.get_setting("default_account_id")
        if dflt:
            account_id = int(dflt); conf += 0.1; reasons.append("default account")
    kind = ex.get("kind") or "expense"
    to_account_id = None
    if kind == "payment":  # credit card payment received -> transfer needs a source; leave for review
        kind = "transfer"
        to_account_id, account_id = account_id, None
        conf = min(conf, 0.5)
        reasons.append("card payment: choose the paying account")
    cats = db.all("SELECT * FROM categories")
    cat_id = None
    if kind in ("expense", "refund", "income"):
        from .parser import categorize
        cat_id, ckind, kw = categorize(f"{ex.get('merchant') or ''} {subject}", cats,
                                       kind="income" if kind == "income" else "expense")
        if cat_id:
            conf += 0.2; reasons.append(f"category by '{kw}'")
    conf = round(min(conf, 1.0), 2)
    th = float(db.get_setting("review_threshold", "0.75"))
    if auto_post is None:
        auto_post = bool((mailbox or {}).get("auto_post"))
    status = "posted" if (auto_post and conf >= th and account_id and kind != "transfer") else "review"
    date_s = None
    if msg.get("received_at"):
        try:
            date_s = ledger._norm_date(msg["received_at"])
        except ledger.ValidationError:
            try:
                date_s = email.utils.parsedate_to_datetime(msg["received_at"]).astimezone().strftime("%Y-%m-%d %H:%M")
            except Exception:
                date_s = None
    data = {"kind": kind, "date": date_s, "amount": ex["amount"], "currency": ex.get("currency"),
            "account_id": account_id, "to_account_id": to_account_id, "category_id": cat_id,
            "merchant": ex.get("merchant"), "note": f"{subject}".strip()[:200], "source": source,
            "status": status, "confidence": conf, "raw_text": text[:4000], "external_id": mid,
            "ledger_id": int(db.get_setting("default_ledger_id", "1") or 1)}
    try:
        tx = ledger.create_tx(db, data)
    except ledger.ValidationError as e:
        data["status"] = status = "review"
        tx = ledger.create_tx(db, data)
        reasons.append(str(e))
    _log(db, **logbase, result=status, detail="; ".join(reasons), rule_name=ex.get("rule_name"), confidence=conf,
         transaction_id=tx["id"])
    ex.pop("text", None)
    return {"created": True, "result": status, "confidence": conf, "reasons": reasons, "extracted": ex, "transaction": tx}


# ---------------------------------------------------------------- IMAP
def _decode(h: str | None) -> str:
    if not h:
        return ""
    parts = []
    for txt, enc in email.header.decode_header(h):
        if isinstance(txt, bytes):
            try:
                parts.append(txt.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                parts.append(txt.decode("utf-8", errors="replace"))
        else:
            parts.append(txt)
    return "".join(parts).strip()


def message_body(m: Message) -> str:
    plain, html = [], []
    for part in (m.walk() if m.is_multipart() else [m]):
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            txt = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, AttributeError):
            continue
        (html if part.get_content_type() == "text/html" else plain).append(txt)
    return "\n".join(plain) if plain else "\n".join(html)


def _split(s: str | None) -> list[str]:
    return [x.strip().lower() for x in re.split(r"[,;\n]", s or "") if x.strip()]


def imap_connect(mb: dict, password: str) -> imaplib.IMAP4:
    if mb.get("use_ssl", 1):
        conn = imaplib.IMAP4_SSL(mb["host"], int(mb.get("port") or 993), timeout=30)
    else:
        conn = imaplib.IMAP4(mb["host"], int(mb.get("port") or 143), timeout=30)
        try:
            conn.starttls()
        except Exception:
            pass
    conn.login(mb["username"], password)
    return conn


def _quote_folder(f: str) -> str:
    f = f or "INBOX"
    return f'"{f}"' if (" " in f or "[" in f) and not f.startswith('"') else f


def test_mailbox(mb: dict, password: str) -> dict:
    try:
        conn = imap_connect(mb, password)
        typ, data = conn.select(_quote_folder(mb.get("folder")), readonly=True)
        if typ != "OK":
            conn.logout()
            return {"ok": False, "error": f"Login OK but folder '{mb.get('folder')}' not found"}
        n = int(data[0] or 0)
        typ, folders = conn.list()
        conn.logout()
        names = []
        for f in folders or []:
            mm = re.search(rb'"([^"]+)"\s*$|(\S+)\s*$', f)
            if mm:
                names.append((mm.group(1) or mm.group(2)).decode(errors="replace"))
        return {"ok": True, "messages_in_folder": n, "folders": names[:100]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def poll_mailbox(db: Database, mb: dict, secret: SecretBox, max_messages: int = 200) -> dict:
    stats = {"checked": 0, "matched": 0, "created": 0, "duplicates": 0, "skipped": 0, "errors": 0}
    status = "ok"
    try:
        password = secret.decrypt(mb["password_enc"] or "")
        conn = imap_connect(mb, password)
        typ, _ = conn.select(_quote_folder(mb.get("folder")), readonly=False)
        if typ != "OK":
            raise RuntimeError(f"Folder {mb.get('folder')} not found")
        since = (datetime.now() - timedelta(days=int(mb.get("lookback_days") or 7))).strftime("%d-%b-%Y")
        typ, data = conn.uid("SEARCH", None, "SINCE", since)
        uids = (data[0] or b"").split()[-max_messages:]
        allow, keywords = _split(mb.get("sender_allowlist")), _split(mb.get("subject_keywords"))
        for uid in uids:
            stats["checked"] += 1
            typ, hd = conn.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT DATE)])")
            if typ != "OK" or not hd or not isinstance(hd[0], tuple):
                continue
            h = email.message_from_bytes(hd[0][1])
            mid = (h.get("Message-ID") or "").strip() or f"uid:{mb['id']}:{uid.decode()}"
            sender, subject = _decode(h.get("From")), _decode(h.get("Subject"))
            if db.one("SELECT 1 x FROM email_seen WHERE message_id=?", (mid,)):
                stats["duplicates"] += 1
                continue
            if allow and not any(a in sender.lower() for a in allow):
                continue
            if keywords and not any(k in subject.lower() for k in keywords):
                continue
            stats["matched"] += 1
            typ, full = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not full or not isinstance(full[0], tuple):
                stats["errors"] += 1
                continue
            m = email.message_from_bytes(full[0][1])
            res = ingest_email(db, {"from": sender, "subject": subject, "body": message_body(m),
                                    "received_at": h.get("Date"), "message_id": mid}, mailbox=mb)
            if res.get("created"):
                stats["created"] += 1
            else:
                stats["skipped"] += 1
            action = mb.get("after_action") or "none"
            if action == "mark_read":
                conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
            elif action == "move" and mb.get("move_folder"):
                dest = _quote_folder(mb["move_folder"])
                try:
                    typ, _ = conn.uid("MOVE", uid, dest)
                except imaplib.IMAP4.error:
                    typ = "NO"
                if typ != "OK":
                    if conn.uid("COPY", uid, dest)[0] == "OK":
                        conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                        conn.expunge()
        conn.logout()
    except Exception as e:  # noqa: BLE001
        status = f"error: {type(e).__name__}: {e}"
        stats["errors"] += 1
        _log(db, mailbox_id=mb["id"], source="imap", sender="", subject="", result="error", detail=status[:500])
    summary = f"{status} | checked {stats['checked']}, matched {stats['matched']}, created {stats['created']}, dup {stats['duplicates']}"
    with db.tx() as con:
        con.execute("UPDATE mailboxes SET last_checked_at=?, last_status=? WHERE id=?", (now_iso(), summary[:500], mb["id"]))
    return {"status": status, **stats}


def due_mailboxes(db: Database) -> list[dict]:
    out = []
    now = datetime.now()
    for mb in db.all("SELECT * FROM mailboxes WHERE enabled=1"):
        last = mb.get("last_checked_at")
        if not last:
            out.append(mb); continue
        try:
            if now - datetime.strptime(last, "%Y-%m-%d %H:%M:%S") >= timedelta(minutes=max(1, int(mb.get("interval_minutes") or 15))):
                out.append(mb)
        except ValueError:
            out.append(mb)
    return out
