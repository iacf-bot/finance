"""Run the app with uvicorn bound to explicit addresses.

Configuration (environment variables or a .env file next to the app; see app/config.py):
  HOST  (default 127.0.0.1; comma-separated list allowed, e.g. "127.0.0.1,xxx.xxx.xxx.xxx")  - legacy: FINANCE_HOSTS
  PORT  (default 8088)                                                                     - legacy: FINANCE_PORT
  DATA_DIR (default ./data)
Addresses that fail to bind are skipped with a warning. Works on Linux, macOS and Windows.
"""
from __future__ import annotations

import atexit
import logging
import os
import socket
import sys

import uvicorn

from . import config

log = logging.getLogger("finance.serve")


def _utf8_console():
    """Windows consoles / redirected logs default to a legacy code page; Chinese text would raise UnicodeEncodeError."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _bind(h: str, port: int) -> socket.socket | None:
    fam = socket.AF_INET6 if ":" in h else socket.AF_INET
    s = socket.socket(fam, socket.SOCK_STREAM)
    if config.IS_WINDOWS:
        # On Windows SO_REUSEADDR would let a second process steal a port that is in use; ask for exclusive use instead.
        excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if excl is not None:
            s.setsockopt(socket.SOL_SOCKET, excl, 1)
    else:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((h, port))
        s.listen(128)
        s.set_inheritable(True)
        log.info("Listening on http://%s:%d", h, port)
        return s
    except OSError as e:
        log.warning("Could not bind %s:%d (%s) - skipping%s", h, port, e,
                    " (is the port already in use, or is this address not on this machine?)" if h != "0.0.0.0" else "")
        s.close()
        return None


def _pid_file():
    p = os.environ.get("FINANCE_PID_FILE")
    if not p:
        return
    try:
        with open(p, "w", encoding="ascii") as f:
            f.write(str(os.getpid()))
        atexit.register(lambda: os.path.exists(p) and open(p, encoding="ascii").read().strip() == str(os.getpid()) and os.remove(p))
    except OSError as e:
        log.warning("Could not write pid file %s: %s", p, e)


def main():
    _utf8_console()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    loaded = config.load_env()
    if loaded:
        log.info("Loaded %s from .env", ", ".join(sorted(loaded)))
    hosts = config.hosts()
    if "0.0.0.0" in hosts and os.environ.get("FINANCE_IN_CONTAINER") != "1":
        log.warning("Listening on ALL interfaces (0.0.0.0). Prefer a specific address (e.g. your Tailscale IP) and keep the "
                    "firewall rule limited to Tailscale (100.64.0.0/10).")
    port = config.port()
    os.environ.setdefault("FINANCE_DB", str(config.db_path()))
    log.info("Data folder: %s", config.data_dir())
    socks = [s for s in (_bind(h, port) for h in hosts) if s]
    if socks and len(socks) < len(hosts) and os.environ.get("FINANCE_REQUIRE_ALL_HOSTS") == "1":
        # service mode: e.g. the Tailscale address is not up yet at boot - exit so the task scheduler retries
        log.error("Not all addresses could be bound (FINANCE_REQUIRE_ALL_HOSTS=1); exiting so the service retries")
        for s in socks:
            s.close()
        sys.exit(2)
    if not socks:
        log.error("No address could be bound on port %d. Another program (or another copy of IACF Personal Finance) may be using it; "
                  "set PORT in .env to use a different port.", port)
        sys.exit(1)
    from .main import create_app
    app = create_app()
    _pid_file()
    config_ = uvicorn.Config(app, log_level="info", proxy_headers=False, access_log=True)
    uvicorn.Server(config_).run(sockets=socks)


if __name__ == "__main__":
    main()
