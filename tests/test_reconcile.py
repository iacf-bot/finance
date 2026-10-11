"""Account reconciliation 对账: balance anchoring, drift, multiple checkpoints, undo, transfers, net worth. FAKE data."""
from datetime import date

from app import ledger, returns


def mk(client, **kw):
    return client.post("/api/accounts", json=kw).json()


def tx(client, **kw):
    r = client.post("/api/transactions", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def bal(client, aid):
    return next(a for a in client.get("/api/accounts").json()["accounts"] if a["id"] == aid)


def rec(client, aid, balance, as_of, **kw):
    r = client.post(f"/api/accounts/{aid}/reconcile", json={"balance": balance, "as_of": as_of, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def hist(client, aid):
    return client.get(f"/api/accounts/{aid}/reconciliations").json()["items"]


def test_anchoring_and_edits_before_checkpoint(client):
    a = mk(client, name="Fake Checking", type="checking", opening_balance=1000, start_date="2026-01-01")
    t1 = tx(client, kind="expense", amount=100, account_id=a["id"], date="2026-01-10")
    p = client.get(f"/api/accounts/{a['id']}/reconcile", params={"as_of": "2026-01-31", "balance": 900}).json()
    assert p["computed"] == 900 and p["difference"] == 0 and len(p["transactions"]) == 2
    r = rec(client, a["id"], 900, "2026-01-31", cleared_ids=[t1["id"]])
    assert r["flagged"] == 2 and r["reconciliation"]["cleared_count"] == 1
    assert client.get(f"/api/transactions/{t1['id']}").json()["reconciled_id"] == r["reconciliation"]["id"]
    tx(client, kind="expense", amount=50, account_id=a["id"], date="2026-02-05")
    assert bal(client, a["id"])["balance"] == 850
    # edit / add / delete before the checkpoint: current balance unchanged, warning returned, drift shown
    assert "reconcile_warning" not in client.put(f"/api/transactions/{t1['id']}", json={"note": "just a note"}).json()
    e = client.put(f"/api/transactions/{t1['id']}", json={"amount": 130}).json()
    assert "reconciled on 2026-01-31" in e["reconcile_warning"]
    late = tx(client, kind="expense", amount=20, account_id=a["id"], date="2026-01-15")
    assert late["reconcile_warning"] and late.get("reconciled_id") is None
    assert bal(client, a["id"])["balance"] == 850
    h = hist(client, a["id"])[0]
    assert h["drift"] == -50 and h["history_changed"] == -50 and h["balance_shown"] == 900
    assert ledger.account_balance_at(client.app.state.db, a["id"], "2026-01-20 00:00") == 850  # before checkpoint: computed as today
    d = client.delete(f"/api/transactions/{late['id']}").json()
    assert d["reconcile_warning"] and bal(client, a["id"])["balance"] == 850
    # filter
    flagged = client.get("/api/transactions", params={"account_id": a["id"], "reconciled": 1}).json()["items"]
    assert {t["id"] for t in flagged} >= {t1["id"]}
    assert all(t["date"] > "2026-01-31" for t in client.get("/api/transactions", params={"account_id": a["id"], "reconciled": 0}).json()["items"])


def test_difference_recorded_as_adjustment_or_accepted(client):
    b = mk(client, name="Fake Broker", type="brokerage", opening_balance=5000, start_date="2026-01-01")
    r = rec(client, b["id"], 5300, "2026-03-31", adjust_reason="investment_gain")
    adj = client.get(f"/api/transactions/{r['adjustment_tx_id']}").json()
    assert adj["amount"] == 300 and adj["adj_reason"] == "investment_gain" and adj["reconciled_id"]
    assert hist(client, b["id"])[0]["drift"] == 0
    c = mk(client, name="Fake Cash", type="cash", opening_balance=100, start_date="2026-01-01")
    r = rec(client, c["id"], 90, "2026-03-31")  # finalize anyway: the statement becomes the anchor
    assert r["adjustment_tx_id"] is None and bal(client, c["id"])["balance"] == 90
    h = hist(client, c["id"])[0]
    assert h["drift"] == 10 and h["history_changed"] == 0
    # liability: amount owed
    cc = mk(client, name="Fake Card", type="credit_card", opening_balance=-200, start_date="2026-01-01")
    rec(client, cc["id"], 250, "2026-03-31", adjust_reason="unrecorded_spending")
    assert bal(client, cc["id"])["owed"] == 250
    assert client.get("/api/reports", params={"start": "2026-03-01", "end": "2026-03-31"}).json()["totals"]["expense"] == 50


def test_multiple_checkpoints_and_undo(client):
    a = mk(client, name="Fake Checking", type="checking", opening_balance=0, start_date="2026-01-01")
    tx(client, kind="income", amount=1000, account_id=a["id"], date="2026-01-05")
    rec(client, a["id"], 1000, "2026-01-31")
    t2 = tx(client, kind="expense", amount=200, account_id=a["id"], date="2026-02-10")
    rec(client, a["id"], 800, "2026-02-28")
    tx(client, kind="expense", amount=100, account_id=a["id"], date="2026-03-10")
    assert bal(client, a["id"])["balance"] == 700
    assert client.post(f"/api/accounts/{a['id']}/reconcile", json={"balance": 1, "as_of": "2026-02-15"}).status_code == 400
    # change between cp1 and cp2: only cp2 drifts; current balance stays anchored to cp2
    client.put(f"/api/transactions/{t2['id']}", json={"amount": 250})
    h = hist(client, a["id"])
    assert [x["drift"] for x in h] == [-50, 0] and h[0]["is_latest"]
    assert bal(client, a["id"])["balance"] == 700
    u = client.delete(f"/api/accounts/{a['id']}/reconciliations/latest").json()
    assert u["unflagged"] == 1  # only the Feb transaction belonged to cp2
    assert bal(client, a["id"])["balance"] == 650  # anchored to cp1 again: 1000 - 250 - 100
    assert client.get(f"/api/transactions/{t2['id']}").json()["reconciled_id"] is None
    assert len(hist(client, a["id"])) == 1


def test_transfers_follow_each_accounts_checkpoints(client):
    a = mk(client, name="Fake A", type="checking", opening_balance=1000, start_date="2026-01-01")
    b = mk(client, name="Fake B", type="savings", opening_balance=0, start_date="2026-01-01")
    t = tx(client, kind="transfer", amount=300, account_id=a["id"], to_account_id=b["id"], date="2026-02-10")
    rec(client, a["id"], 700, "2026-02-28")          # A reconciled after the transfer
    rec(client, b["id"], 0, "2026-02-01")            # B reconciled before it
    assert bal(client, a["id"])["balance"] == 700 and bal(client, b["id"])["balance"] == 300
    client.put(f"/api/transactions/{t['id']}", json={"amount": 400})
    assert bal(client, a["id"])["balance"] == 700   # before A's checkpoint: anchored
    assert bal(client, b["id"])["balance"] == 400   # after B's checkpoint: moves
    t_flags = client.get(f"/api/transactions/{t['id']}").json()
    assert t_flags["reconciled_id"]                  # flagged for A


def test_networth_history_and_set_balance_option(client):
    db = client.app.state.db
    a = mk(client, name="Fake Checking", type="checking", opening_balance=500, start_date="2025-01-01")
    tx(client, kind="expense", amount=100, account_id=a["id"], date="2025-06-01")
    rec(client, a["id"], 450, "2025-06-30")  # statement says 450 (app had 400)
    tx(client, kind="expense", amount=200, account_id=a["id"], date="2025-01-15")  # old edit: ignored after the checkpoint
    cash_may = ledger.cash_balances(db, until="2025-06-01 00:00")[a["id"]]
    cash_jul = ledger.cash_balances(db, until="2025-07-01 00:00")[a["id"]]
    assert cash_may == 300 and cash_jul == 450
    hist_rows = ledger.networth_history(db, 24)
    assert all(isinstance(r["net_worth"], float) for r in hist_rows)
    # Set balance + 'also mark as reconciled'
    r = client.post(f"/api/accounts/{a['id']}/set-balance", json={"balance": 1000, "date": "2025-08-01", "reconcile": True}).json()
    assert r["reconciliation"]["balance"] == 1000 and "reconciled" in r["message"]
    assert hist(client, a["id"])[0]["drift"] == 0


def test_returns_use_checkpoint_balance_and_lock_and_quick_add(client):
    db = client.app.state.db
    b = mk(client, name="Fake Broker", type="brokerage", opening_balance=1000, start_date="2026-01-01")
    rec(client, b["id"], 1100, "2026-03-31")  # finalize anyway: +100 counts as gain via the checkpoint
    r = returns.compute(db, [b["id"]], "2026-01-01", "2026-06-30", "USD", today=date(2026, 7, 1))
    assert r["start_value"] == 0 and r["contributions"] == 1000 and r["end_value"] == 1100 and r["gain"] == 100
    assert r["components"]["reconciliation"] == 100 and r["components_total"] == 100
    # lock: changing a reconciled-period transaction needs confirmation
    client.put("/api/settings", json={"lock_reconciled": "1"})
    t = tx(client, kind="expense", amount=5, account_id=b["id"], date="2026-04-02")
    assert client.post("/api/transactions", json={"kind": "expense", "amount": 5, "account_id": b["id"], "date": "2026-03-01"}).status_code == 409
    assert client.post("/api/transactions", json={"kind": "expense", "amount": 5, "account_id": b["id"], "date": "2026-03-01",
                                                  "confirm_reconciled": True}).status_code == 200
    assert client.put(f"/api/transactions/{t['id']}", json={"date": "2026-03-01"}).status_code == 409
    assert client.put(f"/api/transactions/{t['id']}", json={"amount": 6}).status_code == 200  # after the checkpoint: free
    client.put("/api/settings", json={"lock_reconciled": "0"})
    # quick add
    mk(client, name="Fake Wallet", type="cash", opening_balance=40, start_date="2026-01-01", aliases="fakewallet")
    ok = client.post("/api/quick-add", json={"text": "reconcile fakewallet 40"}).json()
    assert ok["action"] == "reconcile" and ok["status"] == "posted"
    mk(client, name="Fake Purse", type="cash", opening_balance=10, start_date="2026-01-01", aliases="fakepurse")
    bad = client.post("/api/quick-add", json={"text": "对账 fakepurse 55"})
    assert bad.status_code == 400 and "difference" in bad.json()["detail"]
