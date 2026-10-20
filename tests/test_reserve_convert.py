"""Transaction → reserve: "Convert to reserve" (deposit / card hold) keeps the transaction id and can be undone exactly;
"Link to reserve" from the transaction side (partial amounts, review items); the email "looks like a deposit / hold"
hint (never auto-converted); the reserve_conversions migration. FAKE data only."""
import sqlite3
from datetime import timedelta

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_reserves import M, T, TD, accts, bal, mk, nw_total, res, setup, stats, tx

FIELDS = ("kind", "date", "amount", "currency", "account_id", "to_account_id", "to_amount", "category_id", "merchant_name",
          "note", "status", "tax_amount", "tip_amount", "external_id", "source", "raw_text", "project_id")


def snap(t):
    return {k: t.get(k) for k in FIELDS}


def cat_id(c):
    cats = c.get("/api/categories").json()
    cats = cats.get("categories", cats) if isinstance(cats, dict) else cats
    return next(x["id"] for x in cats if x.get("kind") == "expense")


def conv(c, tid, **kw):
    r = c.post(f"/api/transactions/{tid}/to-reserve", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def test_convert_expense_to_deposit_and_back(client):
    a = setup(client)
    cid = cat_id(client)
    tx(client, kind="expense", account_id=a["chk"], amount=40, note="groceries", category_id=cid)
    s_before, nw_before, chk_before = stats(client), nw_total(client), bal(client, a["chk"])
    t = tx(client, kind="expense", account_id=a["chk"], amount=500, merchant="Test Lakeside Rentals",
           note="Security deposit", category_id=cid, tax_amount=0, tip_amount=None)
    orig = snap(client.get(f"/api/transactions/{t['id']}").json())
    s_spent, nw_spent = stats(client), nw_total(client)
    assert s_spent != s_before  # the mistaken expense counts as spending
    r = conv(client, t["id"], method="deposit", end_date=(T + timedelta(days=3)).isoformat(), title="Lake cabin")
    assert r["method"] == "deposit" and r["amount"] == 500 and r["currency"] == "USD" and r["outstanding"] == 500
    assert r["merchant_name"] == "Test Lakeside Rentals" and r["start_date"] == TD and r["title"] == "Lake cabin"
    assert r["category_id"] == cid and r["converted"][0]["transaction_id"] == t["id"]
    assert [l["transaction_id"] for l in r["links"]] == [t["id"]] and r["links"][0]["role"] == "deposit"
    # same id, now a transfer into Deposits: no longer spending, net worth back to before the "expense"
    now = client.get(f"/api/transactions/{t['id']}").json()
    assert now["kind"] == "transfer" and now["to_account_id"] == r["deposit_account_id"] and now["category_id"] is None
    assert now["note"] == "Security deposit" and now["reserves"][0]["id"] == r["id"]
    assert stats(client) == s_before and nw_total(client) == nw_before
    assert bal(client, a["chk"]) == round(chk_before - 500, 2) and bal(client, r["deposit_account_id"]) == 500
    assert client.get("/api/reserves").json()["items"][0]["is_converted"]
    # its deposit link can't be removed on its own (Undo conversion restores it instead)
    assert client.delete(f"/api/reserves/links/{r['links'][0]['id']}").status_code == 400
    # can't edit the type while it belongs to the reserve
    assert client.put(f"/api/transactions/{t['id']}", json={"kind": "expense"}).status_code == 400
    # undo: everything back exactly, reserve gone, stats as before the conversion
    u = client.post(f"/api/reserves/{r['id']}/unconvert")
    assert u.status_code == 200 and u.json()["restored"] == [t["id"]]
    back = client.get(f"/api/transactions/{t['id']}").json()
    assert snap(back) == orig and not back.get("reserves")
    assert stats(client) == s_spent and nw_total(client) == nw_spent
    assert client.get(f"/api/reserves/{r['id']}").status_code == 404
    assert bal(client, r["deposit_account_id"]) == 0


def test_convert_to_hold_archives_and_undo_restores(client):
    a = setup(client)
    s0, nw0, card0 = stats(client), nw_total(client), bal(client, a["card"])
    t = tx(client, kind="expense", account_id=a["card"], amount=200, merchant="Test Grand Hotel",
           note="Authorization hold placed")
    orig = snap(client.get(f"/api/transactions/{t['id']}").json())
    s1, nw1 = stats(client), nw_total(client)
    r = conv(client, t["id"], method="hold")
    assert r["method"] == "hold" and r["amount"] == 200 and r["on_hold"] == 200 and r["link_count"] == 0
    assert r["converted"][0]["orig_status"] == "posted"
    kept = client.get(f"/api/transactions/{t['id']}").json()
    assert kept["status"] == "reserve" and kept["amount"] == 200 and kept["reserves"][0]["role"] == "converted"
    # archived: out of balances, stats, net worth and the default list; the card shows it as on hold
    assert stats(client) == s0 and nw_total(client) == nw0 and bal(client, a["card"]) == card0
    assert accts(client)[a["card"]]["on_hold"] == 200
    assert t["id"] not in [x["id"] for x in client.get("/api/transactions?limit=50").json()["items"]]
    assert t["id"] in [x["id"] for x in client.get("/api/transactions?limit=50&status=all").json()["items"]]
    # an archived hold transaction can't be edited or linked; it is not lost
    assert client.put(f"/api/transactions/{t['id']}", json={"amount": 1}).status_code == 400
    h2 = res(client, method="hold", account_id=a["card"], amount=10)
    assert client.post(f"/api/reserves/{h2['id']}/links", json={"transaction_id": t["id"]}).status_code == 400
    # undo
    assert client.post(f"/api/reserves/{r['id']}/unconvert").status_code == 200
    assert snap(client.get(f"/api/transactions/{t['id']}").json()) == orig
    assert stats(client) == s1 and nw_total(client) == nw1 and accts(client)[a["card"]]["on_hold"] == 10


def test_delete_converted_reserve_restores_and_deleted_tx_is_reinserted(client):
    a = setup(client)
    t = tx(client, kind="expense", account_id=a["chk"], amount=300, merchant="Test Car Hire", note="deposit")
    orig = snap(client.get(f"/api/transactions/{t['id']}").json())
    r = conv(client, t["id"], method="deposit")
    # an extra charge linked → delete needs unlink=1, and the original still comes back
    extra = tx(client, kind="expense", account_id=a["chk"], amount=25, merchant="Test Car Hire")
    assert client.post(f"/api/reserves/{r['id']}/links", json={"transaction_id": extra["id"], "role": "charge"}).status_code == 200
    assert client.post(f"/api/reserves/{r['id']}/unconvert").status_code == 400  # other links first
    assert client.delete(f"/api/reserves/{r['id']}").status_code == 400
    d = client.delete(f"/api/reserves/{r['id']}?unlink=1").json()
    assert d["restored"] == [t["id"]] and d["unlinked"] == 1
    assert snap(client.get(f"/api/transactions/{t['id']}").json()) == orig
    assert client.get(f"/api/transactions/{extra['id']}").status_code == 200
    # the converted hold's transaction was deleted meanwhile: undo re-inserts it with its id
    t2 = tx(client, kind="expense", account_id=a["card"], amount=90, merchant="Test Inn", note="pre-auth")
    o2 = snap(client.get(f"/api/transactions/{t2['id']}").json())
    r2 = conv(client, t2["id"], method="hold")
    assert client.delete(f"/api/transactions/{t2['id']}").status_code == 200
    assert client.post(f"/api/reserves/{r2['id']}/unconvert").status_code == 200
    assert snap(client.get(f"/api/transactions/{t2['id']}").json()) == o2
    # deleted, and SQLite then reused its id for a NEW transaction: the new one is untouched, the original comes back
    t3 = tx(client, kind="expense", account_id=a["card"], amount=70, note="hold")
    o3 = snap(client.get(f"/api/transactions/{t3['id']}").json())
    r3 = conv(client, t3["id"], method="hold")
    client.delete(f"/api/transactions/{t3['id']}")
    newer = tx(client, kind="expense", account_id=a["card"], amount=5, note="coffee")
    u = client.post(f"/api/reserves/{r3['id']}/unconvert").json()
    assert client.get(f"/api/transactions/{newer['id']}").json()["amount"] == 5
    assert snap(client.get(f"/api/transactions/{u['restored'][0]}").json()) == o3


def test_convert_review_item_keeps_id_and_undo_returns_it_to_review(client):
    a = setup(client)
    n0 = client.get("/api/review").json()["total"]
    t = tx(client, kind="expense", account_id=a["card"], amount=150, merchant="Test Resort", status="review",
           note="Incidentals hold", source="email")
    assert client.get("/api/review").json()["total"] == n0 + 1
    s0 = stats(client)
    r = client.post(f"/api/review/{t['id']}/to-reserve", json={"method": "deposit"})
    assert r.status_code == 200, r.text
    r = r.json()
    after = client.get(f"/api/transactions/{t['id']}").json()
    assert after["status"] == "posted" and after["kind"] == "transfer" and client.get("/api/review").json()["total"] == n0
    assert stats(client) == s0  # a deposit transfer: not spending
    client.post(f"/api/reserves/{r['id']}/unconvert")
    assert client.get(f"/api/transactions/{t['id']}").json()["status"] == "review"
    assert client.get("/api/review").json()["total"] == n0 + 1


def test_convert_validation(client):
    a = setup(client)
    inc = tx(client, kind="income", account_id=a["chk"], amount=10)
    assert client.post(f"/api/transactions/{inc['id']}/to-reserve", json={"method": "deposit"}).status_code == 400
    assert client.post("/api/transactions/987654/to-reserve", json={"method": "hold"}).status_code == 404
    e = tx(client, kind="expense", account_id=a["chk"], amount=10)
    assert client.post(f"/api/transactions/{e['id']}/to-reserve", json={"method": "loan"}).status_code == 400
    conv(client, e["id"], method="deposit")
    assert client.post(f"/api/transactions/{e['id']}/to-reserve", json={"method": "hold"}).status_code == 400  # already
    h = res(client, method="hold", account_id=a["card"], amount=5)
    assert client.post(f"/api/reserves/{h['id']}/unconvert").status_code == 400  # not converted
    # "It's a real charge" on a converted hold gives the original transaction back (no duplicate)
    t = tx(client, kind="expense", account_id=a["card"], amount=33, note="hold")
    r = conv(client, t["id"], method="hold")
    back = client.post(f"/api/reserves/{r['id']}/to-transaction").json()
    assert back["id"] == t["id"] and back["status"] == "posted"


def test_link_from_transaction_partial_and_review(client):
    a = setup(client)
    d = res(client, method="deposit", account_id=a["chk"], amount=400, merchant="Test Hostel", start_date=TD)
    s0 = stats(client)
    bill = tx(client, kind="expense", account_id=a["chk"], amount=120, merchant="Test Hostel")
    s1 = stats(client)
    # partial: only 50 of the 120 belongs to the stay
    r = client.post(f"/api/transactions/{bill['id']}/link-reserve", json={"reserve_id": d["id"], "role": "charge", "amount": 50})
    assert r.status_code == 200, r.text
    lk = [l for l in r.json()["links"] if l["transaction_id"] == bill["id"]][0]
    assert lk["role"] == "charge" and lk["amount"] == 50
    assert stats(client) == s1  # linking never changes stats
    assert client.get(f"/api/transactions/{bill['id']}").json()["reserves"][0]["id"] == d["id"]
    # a review item is approved, then linked
    rv = tx(client, kind="refund", account_id=a["chk"], amount=400, merchant="Test Hostel", status="review")
    r = client.post(f"/api/transactions/{rv['id']}/link-reserve", json={"reserve_id": d["id"], "role": "refund"})
    assert r.status_code == 200, r.text
    assert r.json()["outstanding"] == 0 and r.json()["state"] == "settled"
    assert client.get(f"/api/transactions/{rv['id']}").json()["status"] == "posted"
    # refused link → the review item goes back to review
    h = res(client, method="hold", account_id=a["card"], amount=20)
    rv2 = tx(client, kind="income", account_id=a["chk"], amount=5, status="review")
    assert client.post(f"/api/transactions/{rv2['id']}/link-reserve", json={"reserve_id": h["id"], "role": "charge"}).status_code == 400
    assert client.get(f"/api/transactions/{rv2['id']}").json()["status"] == "review"
    assert client.post(f"/api/transactions/{bill['id']}/link-reserve", json={}).status_code == 400
    assert stats(client) == s1 and s1 != s0  # the refund became a transfer from Deposits: not income


def test_hint_for_deposit_and_hold_wording(client):
    from app.reserves import hint_for
    ex = lambda **k: {"kind": "expense", "status": "posted", **k}
    assert hint_for(ex(note="Security deposit for 12 Main St")) == "deposit"
    assert hint_for(ex(raw_text="An authorization hold of $200 was placed")) == "hold"
    assert hint_for(ex(note="酒店押金 500元")) == "deposit"
    assert hint_for(ex(note="信用卡预授权 人民币800元")) == "hold"
    assert hint_for(ex(note="Incidentals")) == "deposit"
    assert hint_for(ex(note="Lunch at cafe")) is None
    assert hint_for(ex(note="Direct deposit from employer")) is None
    assert hint_for({"kind": "income", "status": "posted", "note": "deposit"}) is None
    assert hint_for(ex(note="deposit", reserves=[{"id": 1}])) is None
    a = setup(client)
    t = tx(client, kind="expense", account_id=a["card"], amount=80, note="Hotel deposit")
    assert client.get(f"/api/transactions/{t['id']}").json()["reserve_hint"] == "deposit"


def test_email_deposit_goes_to_review_with_hint_never_converted(client):
    a = setup(client)
    n_res = client.get("/api/reserves?state=all").json()["count"]
    rule = {"name": "Test hotel charge", "sender_pattern": "stay\\.example", "amount_regex": r"\$([\d.]+)", "kind": "expense",
            "last4_regex": r"ending (\d{4})", "enabled": 1, "priority": 1}
    assert client.post("/api/email-rules", json=rule).status_code == 200
    r = client.post("/api/ingest/email", json={"from": "desk@stay.example", "subject": "Security deposit received",
                                                "body": "We charged a $250.00 security deposit to your card ending 4321",
                                                "message_id": "<dep-1@x>"}).json()
    assert r["created"] and r["result"] == "review" and r["reserve_hint"] == "deposit"
    assert any("Convert to reserve" in x for x in r["reasons"])
    t = r["transaction"]
    assert t["kind"] == "expense" and t["status"] == "review"
    assert client.get("/api/reserves?state=all").json()["count"] == n_res  # not converted automatically
    items = client.get("/api/review").json()["items"]
    assert [x for x in items if x["id"] == t["id"]][0]["reserve_hint"] == "deposit"
    # an ordinary charge from the same rule is unaffected
    r2 = client.post("/api/ingest/email", json={"from": "desk@stay.example", "subject": "Your receipt",
                                                 "body": "We charged $30.00 to your card ending 4321", "message_id": "<dep-2@x>"}).json()
    assert not r2.get("reserve_hint")


def test_migration_adds_reserve_conversions_with_backup(tmp_path):
    path = tmp_path / "finance.db"
    app = create_app(path, start_background=False)
    db = app.state.db
    c = TestClient(app); c.headers["X-API-Key"] = db.get_setting("api_key")
    a = setup(c)
    tx(c, kind="expense", account_id=a["card"], amount=12)
    con = sqlite3.connect(path)
    con.executescript("DROP TABLE reserve_conversions;")
    con.commit(); con.close()
    assert db.pending_migrations() == ["reserve_conversions"]
    app2 = create_app(path, start_background=False)
    db2 = app2.state.db
    bk = db2.last_migration_backup
    assert bk and bk.exists() and "before-migrate" in bk.name
    old = sqlite3.connect(bk)
    assert "reserve_conversions" not in {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    old.close()
    assert db2.pending_migrations() == [] and db2.one("SELECT COUNT(*) n FROM reserve_conversions")["n"] == 0
    assert create_app(path, start_background=False).state.db.last_migration_backup is None
