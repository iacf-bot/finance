"""Local SQLite backups (+ optional rclone upload to gdrive:FinanceAppBackups)."""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from .db import Database

REMOTE = "gdrive"
REMOTE_DIR = "FinanceAppBackups"


def backup_dir(db: Database) -> Path:
    """Setting `backup_dir` (absolute path, e.g. a NAS / external disk) or data/backups by default."""
    custom = (db.get_setting("backup_dir", "") or "").strip()
    d = Path(custom).expanduser() if custom else Path(db.path).parent / "backups"
    if custom and not d.is_absolute():
        d = Path(db.path).parent / d
    d.mkdir(parents=True, exist_ok=True)
    return d


def check_dir(path: str) -> str:
    """Validates a backup folder setting: '' (default) or an absolute, writable folder (created if missing)."""
    path = (path or "").strip()
    if not path:
        return ""
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError("Backup folder must be an absolute path (e.g. D:\\Backups\\Finance or /mnt/nas/finance)")
    try:
        p.mkdir(parents=True, exist_ok=True)
        t = p / ".write-test"; t.write_text("ok"); t.unlink()
    except OSError as e:
        raise ValueError(f"Backup folder is not writable: {e}")
    return str(p)


def make_backup(db: Database, tag: str = "auto") -> Path:
    d = backup_dir(db)
    dest = d / f"finance-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{tag}.db"
    src = sqlite3.connect(db.path)
    dst = sqlite3.connect(dest)
    with db.lock:
        src.backup(dst)
    dst.close(); src.close()
    if (db.get_setting("backup_receipts", "1") or "1") != "0":
        try:  # receipt photos: mirrored into <backups>/receipts (new/changed files only), wherever they're stored
            from .receipts import backup_copy
            backup_copy(db, d / "receipts", include_external=True)
        except Exception:  # noqa: BLE001
            pass
    prune(db)
    db.set_setting("last_backup_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return dest


def list_backups(db: Database) -> list[dict]:
    out = []
    for p in sorted(backup_dir(db).glob("finance-*.db"), reverse=True):
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size, "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
    return out


def prune(db: Database):
    keep = int(db.get_setting("backup_keep", "30") or 30)
    files = sorted(backup_dir(db).glob("finance-*.db"), reverse=True)
    for p in files[keep:]:
        p.unlink(missing_ok=True)


def validate_db_file(path: Path) -> None:
    con = sqlite3.connect(path)
    try:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Integrity check failed")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("settings", "accounts", "transactions"):
            if t not in tables:
                raise ValueError(f"Not a finance-app backup (missing table {t})")
    except sqlite3.DatabaseError as e:
        raise ValueError(f"Not a valid SQLite file: {e}")
    finally:
        con.close()


def restore(db: Database, uploaded: Path) -> Path:
    validate_db_file(uploaded)
    safety = make_backup(db, tag="before-restore")
    src = sqlite3.connect(uploaded)
    dst = sqlite3.connect(db.path)
    with db.lock:
        src.backup(dst)
    dst.close(); src.close()
    db.init()  # apply any newer schema to the restored copy
    return safety


def rclone_status() -> dict:
    exe = shutil.which("rclone")
    if not exe:
        return {"installed": False, "remote": False}
    try:
        out = subprocess.run([exe, "listremotes"], capture_output=True, text=True, timeout=20).stdout
    except Exception as e:  # noqa: BLE001
        return {"installed": True, "remote": False, "error": str(e)}
    return {"installed": True, "remote": f"{REMOTE}:" in out.split(), "target": f"{REMOTE}:{REMOTE_DIR}"}


def upload_latest(db: Database) -> dict:
    st = rclone_status()
    if not st.get("remote"):
        return {"ok": False, "skipped": True, "reason": "rclone remote 'gdrive' not configured", **st}
    files = sorted(backup_dir(db).glob("finance-*.db"), reverse=True)
    if not files:
        return {"ok": False, "reason": "no backups yet"}
    r = subprocess.run([shutil.which("rclone"), "copy", str(files[0]), f"{REMOTE}:{REMOTE_DIR}"],
                       capture_output=True, text=True, timeout=600)
    ok = r.returncode == 0
    db.set_setting("last_gdrive_sync", f"{datetime.now():%Y-%m-%d %H:%M:%S} {'ok' if ok else 'FAILED'} {files[0].name}")
    return {"ok": ok, "file": files[0].name, "stderr": r.stderr[-500:]}


def backup_due(db: Database) -> bool:
    hours = float(db.get_setting("backup_interval_hours", "24") or 0)
    if hours <= 0:
        return False
    last = db.get_setting("last_backup_at")
    if not last:
        return True
    try:
        return (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() >= hours * 3600
    except ValueError:
        return True
