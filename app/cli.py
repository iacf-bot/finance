"""Admin CLI: python -m app.cli reset-password | show-api-key | clear-demo | backup
                             | run-schedules [--date YYYY-MM-DD] [--dry-run]"""
import sys

from . import config
from .db import BASE_DIR, Database


def main():
    import os
    config.load_env()
    db = Database(str(config.db_path()))
    db.init()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "reset-password":
        with db.tx() as con:
            con.execute("DELETE FROM settings WHERE key='password_hash'")
        print("Password cleared. Open the app to set a new one on the first-run setup page.")
    elif cmd == "show-api-key":
        print(db.get_setting("api_key"))
    elif cmd == "clear-demo":
        from .demo import clear_demo
        print(clear_demo(db))
    elif cmd == "backup":
        from .backup import make_backup, upload_latest
        print(make_backup(db, "cli"), upload_latest(db))
    elif cmd == "run-schedules":
        # for cron on machines without the always-on server, e.g.  15 0 * * *  cd /path && .venv/bin/python -m app.cli run-schedules
        import argparse
        import json
        from .schedules import run
        ap = argparse.ArgumentParser(prog="python -m app.cli run-schedules")
        ap.add_argument("--date", help="generate occurrences due up to this date (default: today in the app's time zone)")
        ap.add_argument("--dry-run", action="store_true", help="only list what would be generated")
        a = ap.parse_args(sys.argv[2:])
        r = run(db, a.date, a.dry_run, trigger="cli")
        print(r["summary"])
        for it in r["items"]:
            print(f"  {it['date']}  #{it['schedule_id']} {it['name']}  -> {it['status']}")
        for e in r["errors"]:
            print("  ERROR", json.dumps(e, ensure_ascii=False))
        sys.exit(1 if r["errors"] else 0)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
