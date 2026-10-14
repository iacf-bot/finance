"""Backups (folder, receipts), off-site Drive backup, SMTP (fake server only) and emailed reports/reminders."""
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app import email_reports as ER
from app import jobsched, offsite
from tests.fake_smtp import FakeSMTP


@pytest.fixture
def smtp():
    s = FakeSMTP(creds=("bot@example.com", "app-pass-123"))
    yield s
    s.close()


def setup_smtp(client, smtp, **kw):
    body = {"smtp_host": "127.0.0.1", "smtp_port": smtp.port, "smtp_security": "none", "smtp_user": "bot@example.com",
            "smtp_password": "app-pass-123", "smtp_from": "IACF <bot@example.com>", "smtp_to": "a@example.com, b@example.com", **kw}
    r = client.put("/api/email/settings", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- defaults
def test_everything_off_by_default(client, db):
    e = client.get("/api/email/settings").json()
    assert all(not j["enabled"] for j in e["jobs"].values()) and not e["smtp"]["configured"]
    o = client.get("/api/offsite").json()
    assert o["enabled"] is False and o["next_run"] is None
    assert offsite.due(db) is None and ER.run_due(db, None) == []


# ---------------------------------------------------------------- local backups
def test_backup_folder_and_external_receipts(client, db, tmp_path):
    bdir = tmp_path / "nas" / "finance-backups"
    assert client.put("/api/settings", json={"backup_dir": "relative/path"}).status_code == 400
    assert client.put("/api/settings", json={"backup_dir": str(bdir)}).status_code == 200
    ext = tmp_path / "drive-receipts"
    client.put("/api/settings", json={"receipts_dir": str(ext)})
    (ext / "2026" / "10").mkdir(parents=True)
    (ext / "2026" / "10" / "r1.jpg").write_bytes(b"fake-jpeg")
    r = client.post("/api/backups").json()
    assert (bdir / r["name"]).exists()
    assert (bdir / "receipts" / "2026" / "10" / "r1.jpg").read_bytes() == b"fake-jpeg"   # external folder included
    assert client.get("/api/backups").json()["dir"] == str(bdir)
    client.put("/api/settings", json={"backup_receipts": "0"})
    (ext / "2026" / "10" / "r2.jpg").write_bytes(b"x")
    client.post("/api/backups")
    assert not (bdir / "receipts" / "2026" / "10" / "r2.jpg").exists()


# ---------------------------------------------------------------- off-site
def test_offsite_folder_incremental_and_retention(client, db, tmp_path):
    drive = tmp_path / "GoogleDrive" / "FinanceBackups"
    drive.parent.mkdir()
    assert client.put("/api/offsite", json={"offsite_enabled": True}).status_code == 400   # no folder yet
    assert client.put("/api/offsite", json={"offsite_folder": "not/absolute"}).status_code == 400
    st = client.put("/api/offsite", json={"offsite_folder": str(drive), "offsite_keep": 2, "offsite_enabled": True,
                                          "offsite_times": "02:30", "offsite_weekdays": "0,3"}).json()
    assert st["enabled"] and st["next_run"] and "Mon" in st["schedule"]
    from app.receipts import base_dir
    rd = base_dir(db); (rd / "2026").mkdir(parents=True, exist_ok=True); (rd / "2026" / "a.jpg").write_bytes(b"A")
    r1 = client.post("/api/offsite/run").json()
    assert r1["ok"] and "1 receipt file(s) copied" in r1["detail"]
    r2 = client.post("/api/offsite/run").json()
    assert "0 receipt file(s) copied" in r2["detail"]                      # incremental: unchanged files skipped
    import time; time.sleep(1.1)
    client.post("/api/offsite/run")
    assert len(list((drive / "db").glob("finance-*.db"))) == 2              # retention
    assert (drive / "receipts" / "2026" / "a.jpg").exists()
    st = client.get("/api/offsite").json()
    assert st["last_status"] == "ok" and st["last_run"]


def test_offsite_rclone_uses_copy_and_prunes(client, db, monkeypatch):
    calls = []

    class R:
        def __init__(self, out=""):
            self.returncode, self.stdout, self.stderr = 0, out, ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "lsf":
            return R("\n".join(f"finance-2026100{i}-000000-offsite.db" for i in range(1, 5)))
        return R()
    monkeypatch.setattr(offsite, "_run", fake_run)
    monkeypatch.setattr(offsite.shutil, "which", lambda n: "/usr/bin/rclone")
    client.put("/api/offsite", json={"offsite_method": "rclone", "offsite_rclone_target": "gdrive:FinanceAppBackups", "offsite_keep": 2})
    r = client.post("/api/offsite/run").json()
    assert r["ok"], r
    assert calls[0][1] == "copy" and calls[0][3] == "gdrive:FinanceAppBackups/db"
    assert [c[2] for c in calls if c[1] == "deletefile"] == ["gdrive:FinanceAppBackups/db/finance-20261002-000000-offsite.db",
                                                             "gdrive:FinanceAppBackups/db/finance-20261001-000000-offsite.db"]


def test_offsite_failure_is_reported_not_raised(client, db, tmp_path):
    db.set_setting("offsite_method", "folder"); db.set_setting("offsite_folder", str(tmp_path / "missing" / "x"))
    r = client.post("/api/offsite/run").json()
    assert r["ok"] is False and client.get("/api/offsite").json()["last_status"] == "FAILED"


# ---------------------------------------------------------------- schedules
def test_fixed_schedule_runs_once_per_slot(db):
    cfg = {"enabled": True, "mode": "fixed", "times": "02:30", "weekdays": "", "since": "2026-10-01 00:00"}
    now = datetime(2026, 10, 13, 9, 0)
    due, key = jobsched.is_due(db, cfg, now)
    assert due and key == "2026-10-13 02:30"
    assert jobsched.claim(db, "x_slot", key) and not jobsched.claim(db, "x_slot", key)
    cfg["last_slot"] = key
    assert not jobsched.is_due(db, cfg, now)[0]
    assert jobsched.next_run(db, cfg, now) == "2026-10-14 02:30"
    cfg = {"enabled": True, "mode": "fixed", "times": "07:30", "weekdays": "0", "since": "2026-10-01 00:00", "last_slot": "2026-10-12 07:30"}  # Mondays
    assert jobsched.next_run(db, cfg, datetime(2026, 10, 13, 9, 0)) == "2026-10-19 07:30"
    cfg = {"enabled": True, "mode": "fixed", "times": "07:30", "monthday": "31", "since": "2026-01-01 00:00", "last_slot": "2026-10-31 07:30"}
    assert jobsched.next_run(db, cfg, datetime(2026, 11, 2, 9, 0)) == "2026-11-30 07:30"      # short month → last day
    cfg = {"enabled": True, "mode": "interval", "interval_hours": 6, "last_run": "2026-10-13 05:00"}
    assert not jobsched.is_due(db, cfg, datetime(2026, 10, 13, 9, 0))[0] and jobsched.is_due(db, cfg, datetime(2026, 10, 13, 11, 1))[0]


# ---------------------------------------------------------------- SMTP (fake server)
def test_smtp_settings_encrypted_and_test_send(client, db, smtp):
    st = setup_smtp(client, smtp)
    assert st["smtp"]["has_password"] and "smtp_password" not in st["smtp"] and st["smtp"]["configured"]
    assert db.get_setting("smtp_password_enc") not in ("", "app-pass-123")
    assert "app-pass-123" not in client.get("/api/email/settings").text
    r = client.post("/api/email/test", json={})
    assert r.status_code == 200 and r.json()["ok"]
    m = smtp.messages[-1]
    assert m["to"] == ["a@example.com", "b@example.com"] and smtp.logins == ["bot@example.com"]
    assert "test" in m["msg"]["Subject"].lower()
    client.put("/api/email/settings", json={"smtp_host": "127.0.0.1"})          # blank password keeps the stored one
    assert client.post("/api/email/test", json={}).status_code == 200


def test_smtp_errors(client, smtp):
    assert client.post("/api/email/test", json={}).status_code == 400           # not configured
    assert client.put("/api/email/settings", json={"smtp_to": "not-an-email"}).status_code == 400
    assert client.put("/api/email/settings", json={"email_daily_enabled": True}).status_code == 400   # SMTP first
    setup_smtp(client, smtp, smtp_password="wrong")
    r = client.post("/api/email/test", json={})
    assert r.status_code == 400 and "login failed" in r.json()["message"]


# ---------------------------------------------------------------- reports
def _seed(client):
    a = client.post("/api/accounts", json={"name": "Test Card", "type": "credit_card", "currency": "USD"}).json()["id"]
    y = (date.today() - timedelta(days=1)).isoformat()
    client.post("/api/transactions", json={"kind": "expense", "account_id": a, "amount": 118, "tax_amount": 8, "tip_amount": 10, "date": y})
    client.post("/api/transactions", json={"kind": "income", "account_id": a, "amount": 500, "date": y})
    p = client.post("/api/projects", json={"name": "Fake Project", "budget": 1000}).json()
    return a, p


def test_report_content_and_languages(client, db):
    _seed(client)
    m = ER.build(db, "daily", date.today())
    assert "Daily report" in m["subject"] and "Income" in m["html"] and "$118" in m["html"] and "Tax & tips" in m["text"]
    assert "$8 (1)" in m["text"] and "tip %" in m["text"]
    zh = ER.build(db, "daily", date.today(), "zh-CN")
    assert "每日报表" in zh["subject"] and "收入" in zh["html"] and "小费" in zh["html"]
    tw = ER.build(db, "weekly", date.today(), "zh-TW")
    assert "每週報表" in tw["subject"] and "結餘" in tw["html"]
    mo = ER.build(db, "monthly", date(2026, 11, 1))
    assert "2026-10-01 – 2026-10-31" in mo["subject"]
    r = client.get("/api/email/preview?kind=weekly&lang=zh-CN")
    assert r.status_code == 200 and "收入" in r.text


def test_scheduled_report_sent_once(client, db, smtp):
    _seed(client)
    setup_smtp(client, smtp, email_daily_enabled=True, email_daily_times="00:00", email_lang="zh-CN")
    db.set_setting("email_daily_since", "2000-01-01 00:00")   # pretend it was switched on long ago
    out = ER.run_due(db, client.app.state.secret if hasattr(client.app.state, "secret") else _secret(db))
    assert [o["job"] for o in out] == ["daily"] and out[0]["ok"]
    assert "每日报表" in smtp.messages[-1]["msg"]["Subject"]
    assert ER.run_due(db, _secret(db)) == []                 # same slot: not sent twice
    st = client.get("/api/email/settings").json()["jobs"]["daily"]
    assert st["last_status"] == "ok" and st["next_run"]


def test_reminder_email_skips_when_nothing_due(client, db, smtp):
    setup_smtp(client, smtp)
    n = len(smtp.messages)
    r = client.post("/api/email/send?kind=reminders").json()
    assert r["skipped"] and len(smtp.messages) == n


def _secret(db):
    from app.ingest import SecretBox
    return SecretBox(Path(db.path).parent / "secret.key")
