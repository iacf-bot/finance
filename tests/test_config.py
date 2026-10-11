"""Configuration via env vars / .env (cross-platform)."""
import os
from pathlib import Path

import pytest

from app import config
from app import serve


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("HOST", "PORT", "DATA_DIR", "FINANCE_DATA_DIR", "FINANCE_HOSTS", "FINANCE_PORT", "FINANCE_DB", "X_TEST_A", "X_TEST_B", "X_TEST_C", "X_TEST_D"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_defaults(clean_env):
    assert config.hosts() == ["127.0.0.1"] and config.port() == 8088
    assert config.data_dir() == (config.BASE_DIR / "data").resolve()
    assert config.db_path() == config.data_dir() / "finance.db"


def test_env_overrides_and_legacy_names(clean_env, tmp_path):
    clean_env.setenv("HOST", "127.0.0.1, xxx.xxx.xxx.xxx")
    clean_env.setenv("PORT", "9000")
    clean_env.setenv("DATA_DIR", str(tmp_path / "d"))
    assert config.hosts() == ["127.0.0.1", "xxx.xxx.xxx.xxx"] and config.port() == 9000
    assert config.db_path() == (tmp_path / "d" / "finance.db").resolve()
    clean_env.setenv("FINANCE_HOSTS", "127.0.0.1")  # legacy name wins (Linux start.sh / docker set it)
    clean_env.setenv("FINANCE_DB", str(tmp_path / "x" / "f.db"))
    assert config.hosts() == ["127.0.0.1"] and config.db_path() == (tmp_path / "x" / "f.db").resolve()
    clean_env.setenv("DATA_DIR", "relative-data")
    clean_env.delenv("FINANCE_DB")
    assert config.data_dir() == (config.BASE_DIR / "relative-data").resolve()


def test_dotenv_parsing_never_overrides(clean_env, tmp_path):
    p = tmp_path / ".env"
    p.write_text('\ufeff# comment\nX_TEST_A=1  # inline\nexport X_TEST_B="two words"\nX_TEST_C=already\nbad line\nX_TEST_D=C:\\Data\\fin\n', encoding="utf-8")
    clean_env.setenv("X_TEST_C", "from-env")
    loaded = config.load_env(p)
    assert os.environ["X_TEST_A"] == "1" and os.environ["X_TEST_B"] == "two words" and os.environ["X_TEST_C"] == "from-env"
    assert os.environ["X_TEST_D"] == "C:\\Data\\fin" and "X_TEST_C" not in loaded
    assert config.load_env(tmp_path / "missing.env") == {}


def test_bind_reports_port_in_use(clean_env):
    a = serve._bind("127.0.0.1", 0)
    port = a.getsockname()[1]
    try:
        b = serve._bind("127.0.0.1", port)  # already listening -> skipped, not stolen
        assert b is None
    finally:
        a.close()


def test_private_file_noop_on_windows(clean_env, tmp_path):
    f = tmp_path / "k"
    f.write_text("x")
    clean_env.setattr(config, "IS_WINDOWS", True)
    config.private_file(f)  # must not raise
    if os.name != "nt":  # real chmod semantics exist only on POSIX
        clean_env.setattr(config, "IS_WINDOWS", False)
        config.private_file(f)
        assert (f.stat().st_mode & 0o777) == 0o600
