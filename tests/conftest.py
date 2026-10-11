import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["FINANCE_DISABLE_SCHEDULER"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    return create_app(tmp_path / "finance.db", start_background=False)


@pytest.fixture()
def db(app):
    return app.state.db


@pytest.fixture()
def client(app):
    c = TestClient(app)
    c.headers["X-API-Key"] = app.state.db.get_setting("api_key")
    return c


@pytest.fixture()
def accounts(client):
    """A small set of FAKE accounts used by several tests."""
    mk = lambda **kw: client.post("/api/accounts", json=kw).json()
    return {
        "checking": mk(name="Test Checking", type="checking", currency="USD", opening_balance=1000, aliases="checking"),
        "sapphire": mk(name="Chase Sapphire Test", type="credit_card", currency="USD", opening_balance=0,
                       credit_limit=5000, last4="9999", aliases="chase sapphire, sapphire"),
        "amex": mk(name="Amex Test", type="credit_card", currency="USD", opening_balance=-100, last4="12345", aliases="amex"),
        "alipay": mk(name="Alipay Test", type="ewallet", currency="CNY", opening_balance=500, aliases="支付宝, alipay"),
        "cash": mk(name="Cash Test", type="cash", currency="USD", opening_balance=50, aliases="cash, 现金"),
        "broker": mk(name="Broker Test", type="brokerage", currency="USD", opening_balance=0),
    }
