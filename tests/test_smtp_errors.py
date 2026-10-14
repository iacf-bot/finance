"""SMTP test-send failures come back as HTTP 400 with a helpful (localized) message, never a 500. No network."""
import smtplib
import socket

import pytest

from app import mailer


def cfg(client, **kw):
    body = {"smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_security": "starttls", "smtp_user": "me@example.com",
            "smtp_password": "fixture-pw", "smtp_from": "me@example.com", "smtp_to": "me@example.com", **kw}
    assert client.put("/api/email/settings", json=body).status_code == 200


def boom(exc):
    class S:
        def __init__(self, *a, **k):
            raise exc
    return S


@pytest.mark.parametrize("exc,needle", [
    (socket.timeout("timed out"), "timed out after 15s"),
    (socket.gaierror(-2, "Name or service not known"), "not found"),
    (ConnectionRefusedError(111, "refused"), "refused the connection"),
    (RuntimeError("weird"), "SMTP error: weird"),
])
def test_test_send_errors_are_400_with_hints(client, monkeypatch, exc, needle):
    cfg(client)
    monkeypatch.setattr(mailer.smtplib, "SMTP", boom(exc))
    r = client.post("/api/email/test", json={})
    assert r.status_code == 400 and needle in r.json()["message"]
    assert "app password" in r.json()["message"]          # Gmail host: always hints the Gmail settings


def test_timeout_message_lists_likely_causes(client, monkeypatch):
    cfg(client, smtp_host="mail.example.com", smtp_port=465, smtp_security="starttls", smtp_timeout=10)
    monkeypatch.setattr(mailer.smtplib, "SMTP", boom(socket.timeout("timed out")))
    m = client.post("/api/email/test", json={}).json()["message"]
    assert "after 10s" in m and "465 needs SSL/TLS" in m and "firewall" in m and "wrong host or port" in m


def test_auth_error_chinese(client, monkeypatch):
    cfg(client)

    class S:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self, **k): pass
        def login(self, u, p): raise smtplib.SMTPAuthenticationError(535, b"bad")
    monkeypatch.setattr(mailer.smtplib, "SMTP", S)
    r = client.post("/api/email/test", json={}, headers={"accept-language": "zh-CN"})
    assert r.status_code == 400 and "应用专用密码" in r.json()["message"]


def test_timeout_setting_validated_and_used(client, monkeypatch):
    assert client.put("/api/email/settings", json={"smtp_timeout": 2}).status_code == 400
    cfg(client, smtp_timeout=20)
    seen = {}

    class S:
        def __init__(self, host, port, timeout=None, **k):
            seen["timeout"] = timeout
            raise socket.timeout("timed out")
    monkeypatch.setattr(mailer.smtplib, "SMTP", S)
    client.post("/api/email/test", json={})
    assert seen["timeout"] == 20
    assert client.get("/api/email/settings").json()["smtp"]["smtp_timeout"] == "20"


def test_returns_report_on_empty_book(client):
    """Investments page on a new book (no investment accounts): 200 with an empty table, not a 500."""
    r = client.get("/api/investments/returns?period=ytd&scope=all")
    assert r.status_code == 200 and r.json()["rows"][-1]["level"] == "total"
