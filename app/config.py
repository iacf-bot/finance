"""Runtime configuration from environment variables and an optional .env file (works the same on Linux and Windows).

Variables (all optional):
  HOST       address(es) to listen on, comma separated. Default 127.0.0.1.   (legacy name: FINANCE_HOSTS)
  PORT       TCP port. Default 8088.                                           (legacy name: FINANCE_PORT)
  DATA_DIR   folder for finance.db, backups, logs, attachments, secret.key.
             Default: the "data" folder next to the app (.\\data on Windows). Relative paths are relative to the app folder.
  FINANCE_DB full path of the database file (overrides DATA_DIR/finance.db; used by tests).
Values already set in the environment win over .env.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
IS_WINDOWS = os.name == "nt"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8088


def load_env(path: str | os.PathLike | None = None) -> dict:
    """Minimal .env reader (KEY=VALUE, # comments, optional quotes, optional 'export '). Never overrides the environment."""
    p = Path(path) if path else BASE_DIR / ".env"
    loaded = {}
    if not p.is_file():
        return loaded
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if not key.replace("_", "").isalnum():
            continue
        if val[:1] in ("'", '"') and val[-1:] == val[:1] and len(val) >= 2:
            val = val[1:-1]
        elif " #" in val or "\t#" in val:
            val = val.split("#", 1)[0].rstrip()
        if key not in os.environ:
            os.environ[key] = val
            loaded[key] = val
    return loaded


def _first(*names: str, default: str | None = None) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v is not None and v.strip() != "":
            return v.strip()
    return default


def hosts() -> list[str]:
    return [h.strip() for h in (_first("FINANCE_HOSTS", "HOST", default=DEFAULT_HOST) or "").split(",") if h.strip()]


def port() -> int:
    return int(_first("FINANCE_PORT", "PORT", default=str(DEFAULT_PORT)))


def data_dir() -> Path:
    db = _first("FINANCE_DB")
    if db:
        return Path(db).expanduser().resolve().parent
    d = Path(_first("DATA_DIR", "FINANCE_DATA_DIR", default="data")).expanduser()
    return (d if d.is_absolute() else BASE_DIR / d).resolve()


def db_path() -> Path:
    db = _first("FINANCE_DB")
    return Path(db).expanduser().resolve() if db else data_dir() / "finance.db"


def private_file(path: str | os.PathLike) -> None:
    """Restrict a secret file to the owner. POSIX: chmod 600. Windows: NTFS permissions already limit the user profile /
    app folder; os.chmod there only toggles read-only, so it is skipped."""
    if IS_WINDOWS:
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
