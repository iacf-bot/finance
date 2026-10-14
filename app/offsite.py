"""Incremental off-site backup 异地备份 to Google Drive (OFF by default).

Two ways, both incremental:
- folder: a Google Drive for desktop (or Dropbox/OneDrive/NAS) folder on this computer. Each run writes one new
  database snapshot to <folder>/db/ and mirrors only new/changed receipt photos into <folder>/receipts/.
- rclone: `rclone copy` of the snapshot to <target>/db and of the receipts folder to <target>/receipts (rclone only
  transfers what changed). Needs a configured rclone remote, e.g. `gdrive:`.
Retention: the newest N snapshots are kept in the destination (receipts are never deleted there).
Schedule: every N hours or at fixed times on chosen weekdays (like the email-check schedule)."""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from . import backup as bk
from . import jobsched
from .db import Database

DEFAULTS = {"offsite_enabled": "0", "offsite_method": "folder", "offsite_folder": "", "offsite_rclone_target": "gdrive:FinanceAppBackups",
            "offsite_mode": "fixed", "offsite_interval_hours": "24", "offsite_times": "02:30", "offsite_weekdays": "",
            "offsite_keep": "14", "offsite_receipts": "1"}
STATE = ("offsite_last_run", "offsite_last_status", "offsite_last_detail", "offsite_last_slot", "offsite_since")
_run = subprocess.run  # tests replace this (no real rclone calls)


def config(db: Database) -> dict:
    return {k: db.get_setting(k, v) if db.get_setting(k, None) is not None else v for k, v in DEFAULTS.items()}


def _cfg(db: Database) -> dict:
    c = config(db)
    return {"enabled": c["offsite_enabled"] == "1", "mode": c["offsite_mode"], "interval_hours": c["offsite_interval_hours"],
            "times": c["offsite_times"], "weekdays": c["offsite_weekdays"], "last_run": db.get_setting("offsite_last_run"),
            "last_slot": db.get_setting("offsite_last_slot"), "since": db.get_setting("offsite_since")}


def update(db: Database, body: dict) -> dict:
    out = jobsched.validate(body, "offsite_")
    if "offsite_method" in body:
        if body["offsite_method"] not in ("folder", "rclone"):
            raise ValueError("method must be folder or rclone")
        out["offsite_method"] = body["offsite_method"]
    if "offsite_folder" in body:
        f = str(body["offsite_folder"] or "").strip()
        if f:
            p = Path(f).expanduser()
            if not p.is_absolute():
                raise ValueError("Drive folder must be an absolute path, e.g. G:\\\\My Drive\\\\FinanceBackups")
            if not p.parent.exists():
                raise ValueError(f"Folder {p.parent} doesn't exist (is Google Drive for desktop running?)")
        out["offsite_folder"] = f
    if "offsite_rclone_target" in body:
        t = str(body["offsite_rclone_target"] or "").strip()
        if t and ":" not in t:
            raise ValueError("rclone target looks like remote:folder, e.g. gdrive:FinanceAppBackups")
        out["offsite_rclone_target"] = t
    if "offsite_keep" in body:
        v = str(body["offsite_keep"]).strip()
        if not v.isdigit() or not 1 <= int(v) <= 1000:
            raise ValueError("keep must be 1–1000 snapshots")
        out["offsite_keep"] = v
    if "offsite_receipts" in body:
        out["offsite_receipts"] = "1" if str(body["offsite_receipts"]).lower() in ("1", "true", "on", "yes") else "0"
    if "offsite_enabled" in body:
        on = str(body["offsite_enabled"]).lower() in ("1", "true", "on", "yes")
        merged = {**config(db), **out}
        if on and merged["offsite_method"] == "folder" and not merged["offsite_folder"]:
            raise ValueError("Set the Drive folder first")
        if on and merged["offsite_method"] == "rclone" and not merged["offsite_rclone_target"]:
            raise ValueError("Set the rclone target first")
        if on and config(db)["offsite_enabled"] != "1":
            db.set_setting("offsite_since", datetime.now().strftime("%Y-%m-%d %H:%M"))
        out["offsite_enabled"] = "1" if on else "0"
    if any(k in out for k in ("offsite_mode", "offsite_times", "offsite_weekdays")):
        db.set_setting("offsite_since", datetime.now().strftime("%Y-%m-%d %H:%M"))
    for k, v in out.items():
        db.set_setting(k, v)
    return status(db)


def status(db: Database) -> dict:
    c, cfg = config(db), _cfg(db)
    return {**c, "enabled": cfg["enabled"], "schedule": jobsched.describe(cfg, _zh()), "next_run": jobsched.next_run(db, cfg),
            "last_run": db.get_setting("offsite_last_run"), "last_status": db.get_setting("offsite_last_status"),
            "last_detail": db.get_setting("offsite_last_detail"), "rclone": bk.rclone_status() if c["offsite_method"] == "rclone" else None}


def due(db: Database) -> str | None:
    ok, key = jobsched.is_due(db, _cfg(db))
    return key if ok else None


def _mirror(src: Path, dest: Path) -> int:
    """Copy new/changed files only (size or mtime differs). Nothing is deleted at the destination."""
    n = 0
    if not src.exists():
        return 0
    for p in src.rglob("*"):
        if p.is_file():
            q = dest / p.relative_to(src)
            if not q.exists() or q.stat().st_size != p.stat().st_size or q.stat().st_mtime < p.stat().st_mtime - 1:
                q.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, q); n += 1
    return n


def run(db: Database, trigger: str = "manual", slot: str | None = None) -> dict:
    """One incremental off-site backup. Never raises (the result says what failed)."""
    if slot and not jobsched.claim(db, "offsite_last_slot", slot):
        return {"ok": False, "skipped": True, "reason": "slot already done"}
    c = config(db)
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        from .receipts import base_dir
        snap = bk.make_backup(db, "offsite")
        keep = int(c["offsite_keep"] or 14)
        rc_dir = base_dir(db)
        if c["offsite_method"] == "folder":
            root = Path(c["offsite_folder"]).expanduser()
            if not c["offsite_folder"] or not root.parent.exists():
                raise RuntimeError("Drive folder not set or not found")
            (root / "db").mkdir(parents=True, exist_ok=True)
            shutil.copy2(snap, root / "db" / snap.name)
            files = sorted((root / "db").glob("finance-*.db"), reverse=True)
            for p in files[keep:]:
                p.unlink(missing_ok=True)
            n = _mirror(rc_dir, root / "receipts") if c["offsite_receipts"] == "1" else 0
            detail = f"{snap.name} → {root}; {n} receipt file(s) copied; {min(len(files), keep)} snapshot(s) kept"
        else:
            exe = shutil.which("rclone")
            target = c["offsite_rclone_target"].rstrip("/")
            if not exe:
                raise RuntimeError("rclone is not installed")
            r = _run([exe, "copy", str(snap), f"{target}/db"], capture_output=True, text=True, timeout=1800)
            if r.returncode != 0:
                raise RuntimeError("rclone copy failed: " + (r.stderr or "")[-300:])
            if c["offsite_receipts"] == "1" and rc_dir.exists():
                r = _run([exe, "copy", str(rc_dir), f"{target}/receipts"], capture_output=True, text=True, timeout=3600)
                if r.returncode != 0:
                    raise RuntimeError("rclone receipts copy failed: " + (r.stderr or "")[-300:])
            ls = _run([exe, "lsf", f"{target}/db", "--files-only"], capture_output=True, text=True, timeout=300)
            names = sorted((x.strip() for x in (ls.stdout or "").splitlines() if x.strip().startswith("finance-")), reverse=True)
            for old in names[keep:]:
                _run([exe, "deletefile", f"{target}/db/{old}"], capture_output=True, text=True, timeout=300)
            detail = f"{snap.name} → {target}; {max(0, len(names) - keep)} old snapshot(s) removed"
        db.set_setting("offsite_last_status", "ok")
        res = {"ok": True, "detail": detail}
    except Exception as e:  # noqa: BLE001
        detail = str(e)[:500]
        db.set_setting("offsite_last_status", "FAILED")
        res = {"ok": False, "detail": detail}
    db.set_setting("offsite_last_run", started[:16])
    db.set_setting("offsite_last_detail", f"{trigger}: {detail}")
    return {**res, "trigger": trigger, "at": started}


def _zh() -> bool:
    from .ingest import msg_zh
    return msg_zh()
