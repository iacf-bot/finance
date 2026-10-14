"""Outgoing email (SMTP), OFF until configured. Password is stored encrypted (same SecretBox as IMAP passwords).
Settings: smtp_host, smtp_port, smtp_security (starttls | ssl | none), smtp_user, smtp_password_enc, smtp_from,
smtp_to (comma-separated recipients)."""
from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .db import Database

KEYS = ("smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_from", "smtp_to")
DEFAULTS = {"smtp_host": "", "smtp_port": "587", "smtp_security": "starttls", "smtp_user": "", "smtp_from": "", "smtp_to": ""}
EMAIL_RX = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
TIMEOUT = 20


class MailError(ValueError):
    pass


def recipients(s) -> list[str]:
    items = s if isinstance(s, (list, tuple)) else re.split(r"[,;\s]+", str(s or ""))
    out = []
    for x in items:
        x = str(x).strip()
        if not x:
            continue
        if not EMAIL_RX.match(x):
            raise MailError(f"Not an email address: {x}")
        if x.lower() not in [o.lower() for o in out]:
            out.append(x)
    return out


def config(db: Database) -> dict:
    c = {k: db.get_setting(k, None) if db.get_setting(k, None) is not None else v for k, v in DEFAULTS.items()}
    c["has_password"] = bool(db.get_setting("smtp_password_enc", ""))
    c["configured"] = bool(c["smtp_host"] and (c["smtp_from"] or c["smtp_user"]) and c["smtp_to"])
    return c


def update(db: Database, secret, body: dict) -> dict:
    out = {}
    if "smtp_host" in body:
        h = str(body["smtp_host"] or "").strip()
        if h and not re.match(r"^[A-Za-z0-9.-]+$", h):
            raise MailError("SMTP host: a host name like smtp.gmail.com")
        out["smtp_host"] = h
    if "smtp_port" in body:
        p = str(body["smtp_port"] or "").strip() or "587"
        if not p.isdigit() or not 1 <= int(p) <= 65535:
            raise MailError("SMTP port must be 1–65535 (587 STARTTLS, 465 SSL)")
        out["smtp_port"] = p
    if "smtp_security" in body:
        s = str(body["smtp_security"] or "starttls").lower()
        if s not in ("starttls", "ssl", "none"):
            raise MailError("security must be starttls, ssl or none")
        out["smtp_security"] = s
    if "smtp_user" in body:
        out["smtp_user"] = str(body["smtp_user"] or "").strip()
    if "smtp_from" in body:
        f = str(body["smtp_from"] or "").strip()
        addr = re.search(r"<([^>]+)>", f).group(1) if "<" in f and re.search(r"<([^>]+)>", f) else f
        if f and not EMAIL_RX.match(addr):
            raise MailError("From must be an email address (optionally Name <addr>)")
        out["smtp_from"] = f
    if "smtp_to" in body:
        out["smtp_to"] = ", ".join(recipients(body["smtp_to"]))
    for k, v in out.items():
        db.set_setting(k, v)
    if body.get("smtp_password"):  # blank = keep the stored one
        db.set_setting("smtp_password_enc", secret.encrypt(str(body["smtp_password"])))
    if body.get("clear_password"):
        db.set_setting("smtp_password_enc", "")
    return config(db)


def send(db: Database, secret, subject: str, html: str, text: str, to: list[str] | None = None) -> dict:
    """Sends one message to `to` (default: smtp_to). Raises MailError with a readable reason."""
    c = config(db)
    if not c["smtp_host"]:
        raise MailError("SMTP is not configured (Settings → Email)")
    rcpt = to or recipients(c["smtp_to"])
    if not rcpt:
        raise MailError("No recipients (Settings → Email → To)")
    sender = c["smtp_from"] or c["smtp_user"]
    if not sender:
        raise MailError("Set a From address")
    pw = ""
    if db.get_setting("smtp_password_enc", ""):
        pw = secret.decrypt(db.get_setting("smtp_password_enc"))
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(rcpt)
    msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid(domain="iacf-finance.local")
    msg.set_content(text or " ")
    if html:
        msg.add_alternative(html, subtype="html")
    port, sec = int(c["smtp_port"] or 587), c["smtp_security"]
    try:
        if sec == "ssl":
            s = smtplib.SMTP_SSL(c["smtp_host"], port, timeout=TIMEOUT, context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(c["smtp_host"], port, timeout=TIMEOUT)
        with s:
            s.ehlo()
            if sec == "starttls":
                s.starttls(context=ssl.create_default_context()); s.ehlo()
            if c["smtp_user"] and pw:
                s.login(c["smtp_user"], pw)
            refused = s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise MailError("SMTP login failed: check the user name and app password")
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        raise MailError(f"SMTP error: {e}")
    return {"ok": True, "to": rcpt, "refused": list(refused or {})}
