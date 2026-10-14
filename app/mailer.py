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

TIMEOUT = 15  # seconds; setting smtp_timeout (5–60)
KEYS = ("smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_from", "smtp_to", "smtp_timeout")
DEFAULTS = {"smtp_host": "", "smtp_port": "587", "smtp_security": "starttls", "smtp_user": "", "smtp_from": "", "smtp_to": "",
            "smtp_timeout": str(TIMEOUT)}
EMAIL_RX = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


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
    if "smtp_timeout" in body:
        t = str(body["smtp_timeout"] or "").strip() or str(TIMEOUT)
        if not t.isdigit() or not 5 <= int(t) <= 60:
            raise MailError("SMTP timeout must be 5–60 seconds")
        out["smtp_timeout"] = t
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
    timeout = int(c.get("smtp_timeout") or TIMEOUT)
    try:
        if sec == "ssl":
            s = smtplib.SMTP_SSL(c["smtp_host"], port, timeout=timeout, context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(c["smtp_host"], port, timeout=timeout)
        with s:
            s.ehlo()
            if sec == "starttls":
                s.starttls(context=ssl.create_default_context()); s.ehlo()
            if c["smtp_user"] and pw:
                s.login(c["smtp_user"], pw)
            refused = s.send_message(msg)
    except Exception as e:  # noqa: BLE001  every failure becomes a readable MailError (HTTP 400, never 500)
        raise MailError(explain(e, c["smtp_host"], port, sec, timeout)) from None
    return {"ok": True, "to": rcpt, "refused": list(refused or {})}


def _zh() -> bool:
    from .ingest import msg_zh
    return msg_zh()


def explain(e: Exception, host: str, port: int, sec: str, timeout: int) -> str:
    """Short reason + the likely fixes, in the request language."""
    import socket
    zh = _zh()
    gmail = "gmail" in (host or "").lower() or "google" in (host or "").lower()
    where = f"{host}:{port} ({sec.upper()})"
    mismatch = (port == 465 and sec != "ssl") or (port == 587 and sec == "ssl")
    if isinstance(e, smtplib.SMTPAuthenticationError):
        why = ("登录被拒绝:用户名或密码不对。" + ("Gmail 需要“应用专用密码”(Google 帐号 → 安全性 → 两步验证 → 应用专用密码),不能用普通密码。" if gmail else "很多邮箱要求使用应用专用密码。")) if zh else \
              ("Login refused: wrong user name or password. " + ("Gmail needs an app password (Google Account → Security → 2-Step Verification → App passwords), not your normal password." if gmail else "Many providers require an app password."))
    elif isinstance(e, (socket.timeout, TimeoutError)) or "timed out" in str(e).lower():
        tips_zh = ["主机或端口不对", "端口与加密方式不匹配:465 用 SSL/TLS,587 用 STARTTLS", "防火墙/网络或运营商屏蔽了 SMTP 端口(可换 587/465 试试,或换网络)"]
        tips_en = ["wrong host or port", "port and security don't match: 465 needs SSL/TLS, 587 needs STARTTLS",
                   "a firewall, VPN or your ISP blocks outgoing SMTP (try the other port, or another network)"]
        why = (f"连接 {where} 超时({timeout} 秒)。可能原因:" + ";".join(tips_zh) + "。") if zh else \
              (f"Connection to {where} timed out after {timeout}s. Likely: " + "; ".join(tips_en) + ".")
    elif isinstance(e, socket.gaierror):
        why = f"找不到主机 {host}:请检查主机名(例如 smtp.gmail.com)。" if zh else f"Host {host} not found: check the host name (e.g. smtp.gmail.com)."
    elif isinstance(e, ConnectionRefusedError):
        why = f"{where} 拒绝连接:端口不对或服务器未开放。" if zh else f"{where} refused the connection: wrong port, or the server doesn't accept SMTP there."
    elif isinstance(e, (ssl.SSLError, smtplib.SMTPServerDisconnected)) or "wrong version number" in str(e).lower():
        why = (f"与 {where} 的加密握手失败:465 端口请选 SSL/TLS,587 端口请选 STARTTLS。" if zh else
               f"TLS handshake with {where} failed: use SSL/TLS on port 465 and STARTTLS on port 587.")
    elif isinstance(e, smtplib.SMTPNotSupportedError):
        why = "服务器不支持 STARTTLS:试试 465 + SSL/TLS。" if zh else "Server doesn't support STARTTLS: try port 465 with SSL/TLS."
    elif isinstance(e, smtplib.SMTPRecipientsRefused):
        why = "收件人被服务器拒绝:请检查收件地址。" if zh else "The server refused the recipient address(es): check the To list."
    elif isinstance(e, smtplib.SMTPSenderRefused):
        why = "发件地址被拒绝:发件地址通常需要和登录账号一致。" if zh else "Sender refused: the From address usually has to match the login account."
    else:
        why = (f"SMTP 错误:{e}" if zh else f"SMTP error: {e}")
    if mismatch and "465" not in why:
        why += (" 注意:465 端口应选 SSL/TLS,587 端口应选 STARTTLS。" if zh else " Note: port 465 needs SSL/TLS; port 587 needs STARTTLS.")
    if gmail and not zh and "Gmail" not in why:
        why += " Gmail: smtp.gmail.com, 587 + STARTTLS (or 465 + SSL/TLS), with an app password."
    if gmail and zh and "Gmail" not in why:
        why += " Gmail:smtp.gmail.com,587 + STARTTLS(或 465 + SSL/TLS),使用应用专用密码。"
    return why
