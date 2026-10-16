"""Reimbursement linking 报销关联: many-to-many links with partial amounts between reimbursable expenses and
reimbursement transactions, over-allocation checks, auto-close, manual close (write-off) / reopen, unlink, edit,
delete cascade, the stats rule, suggestions, list badges, CSV / agent export and the backup-before-migrate. FAKE data."""
import csv
import io
import sqlite3
from datetime import date

from app.main import create_app

M = date.today().strftime("%Y-%m")
D = date.today().isoformat() + " 10:00"


def acct(client, **kw):
    return client.post("/api/accounts", json=kw).json()["id"]


def tx(client, **kw):
    kw.setdefault("date", D)
    r = client.post("/api/transactions", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def exp(client, a, amount, **kw):
    return tx(client, kind="expense", account_id=a, amount=amount, reimbursable=True, **kw)


def reimb(client, a, amount, **kw):
    return tx(client, kind="reimbursement", account_id=a, amount=amount, **kw)


def link(client, **body):
    return client.post("/api/reimbursements/links", json=body)


def st(client, eid):
    return client.get(f"/api/transactions/{eid}").json()


def month(client):
    return client.get(f"/api/summary/month?month={M}").json()


def setup(client):
    return acct(client, name="Test Checking", type="checking", currency="USD", opening_balance=1000)


def test_many_to_many_default_amounts_and_close(client):
    C = setup(client)
    e1, e2 = exp(client, C, 100, note="flight"), exp(client, C, 60, note="hotel")
    r1, r2 = reimb(client, C, 120, note="expense report A"), reimb(client, C, 40)
    assert r1["reimb_unallocated"] == 120 and r1["reimb_allocated"] == 0
    # one reimbursement -> several expenses (default = min(outstanding, unallocated))
    res = link(client, reimbursement_id=r1["id"], items=[{"expense_id": e1["id"]}, {"expense_id": e2["id"]}])
    assert res.status_code == 200, res.text
    amounts = {l["expense_id"]: l["amount"] for l in res.json()["links"]}
    assert amounts == {e1["id"]: 100, e2["id"]: 20}
    assert res.json()["reimbursements"][0]["reimb_unallocated"] == 0
    # one expense <- several reimbursements
    r = link(client, expense_id=e2["id"], reimbursement_id=r2["id"]).json()
    assert r["links"][0]["amount"] == 40
    s1, s2 = st(client, e1["id"]), st(client, e2["id"])
    assert (s1["reimb_state"], s1["reimb_closed_by"], s1["reimb_outstanding"]) == ("closed", "reimbursed", 0)
    assert s2["reimb_state"] == "closed" and s2["reimb_settled"] == 60 and len(s2["reimb_links"]) == 2
    rr = client.get(f"/api/reimbursements/receipts/{r1['id']}").json()
    assert rr["reimb_allocated"] == 120 and len(rr["links"]) == 2
    assert len(client.get(f"/api/reimbursements/links?expense_id={e2['id']}").json()["items"]) == 2
    assert len(client.get(f"/api/reimbursements/links?reimbursement_id={r1['id']}").json()["items"]) == 2
    # closed ones are hidden by default, shown with status=closed
    lst = client.get("/api/reimbursements").json()
    assert lst["count"] == 0 and lst["state_counts"] == {"open": 0, "partial": 0, "closed": 2}
    assert client.get("/api/reimbursements?status=closed").json()["count"] == 2


def test_partial_amounts_and_over_allocation_rejected(client):
    C = setup(client)
    e = exp(client, C, 100)
    r = reimb(client, C, 70)
    assert link(client, expense_id=e["id"], reimbursement_id=r["id"], amount=80).status_code == 400  # > unallocated 70
    ok = link(client, expense_id=e["id"], reimbursement_id=r["id"], amount=30)
    assert ok.status_code == 200
    s = st(client, e["id"])
    assert (s["reimb_state"], s["reimb_settled"], s["reimb_outstanding"]) == ("partial", 30, 70)
    assert link(client, expense_id=e["id"], reimbursement_id=r["id"], amount=10).status_code == 400  # pair already linked
    r2 = reimb(client, C, 500)
    over = link(client, expense_id=e["id"], reimbursement_id=r2["id"], amount=70.01)               # > outstanding 70
    assert over.status_code == 400 and "outstanding" in over.json()["detail"]
    assert link(client, expense_id=e["id"], reimbursement_id=r2["id"], amount=0).status_code == 400
    assert link(client, expense_id=e["id"], reimbursement_id=r2["id"], amount=-5).status_code == 400
    plain = tx(client, kind="expense", account_id=C, amount=10)                                  # not reimbursable
    assert link(client, expense_id=plain["id"], reimbursement_id=r2["id"]).status_code == 400
    inc = tx(client, kind="income", account_id=C, amount=10)                                     # not a reimbursement
    assert link(client, expense_id=e["id"], reimbursement_id=inc["id"]).status_code == 400
    # a batch is all or nothing
    e3 = exp(client, C, 20)
    bad = link(client, reimbursement_id=r2["id"], items=[{"expense_id": e3["id"]}, {"expense_id": plain["id"]}])
    assert bad.status_code == 400 and st(client, e3["id"])["reimb_state"] == "open"
    # the default fills what is left: min(outstanding 70, unallocated 500)
    assert link(client, expense_id=e["id"], reimbursement_id=r2["id"]).json()["links"][0]["amount"] == 70
    assert st(client, e["id"])["reimb_state"] == "closed"
    assert st(client, r2["id"])["reimb_unallocated"] == 430
    # nothing outstanding any more
    assert link(client, expense_id=e["id"], reimbursement_id=reimb(client, C, 5)["id"]).status_code == 400


def test_stats_rule_auto_close_write_off_reopen(client, db):
    C = setup(client)
    base = month(client)
    e = exp(client, C, 80, note="client dinner")
    r = reimb(client, C, 50)
    m = month(client)
    assert m["expense"] == base["expense"] and m["income"] == base["income"]  # open: not an expense, receipt not income
    link(client, expense_id=e["id"], reimbursement_id=r["id"])
    assert month(client)["expense"] == base["expense"]                       # partial: still 0
    w = client.post(f"/api/reimbursements/{e['id']}/close").json()           # manual close = write off the rest
    assert (w["reimb_state"], w["reimb_closed_by"], w["reimb_writeoff_amount"], w["reimb_counted"]) == ("closed", "written_off", 30, 30)
    m = month(client)
    assert m["expense"] == base["expense"] + 30 and m["income"] == base["income"]
    # every report uses the same rule: transactions header, category breakdown, project totals, budgets
    assert client.get("/api/transactions?limit=5").json()["summary"]["expense"] == 30
    from app import ledger
    s_, e_ = ledger.month_range(M)
    assert round(sum(x["amount"] for x in ledger.breakdown(db, s_, e_, "category", "expense")), 2) == 30
    # closed by write-off: can't link more until reopened
    r2 = reimb(client, C, 30)
    assert link(client, expense_id=e["id"], reimbursement_id=r2["id"]).status_code == 400
    o = client.post(f"/api/reimbursements/{e['id']}/reopen").json()
    assert o["reimb_state"] == "partial" and month(client)["expense"] == base["expense"]
    link(client, expense_id=e["id"], reimbursement_id=r2["id"])              # auto-close: outstanding reaches 0
    s = st(client, e["id"])
    assert (s["reimb_state"], s["reimb_closed_by"], s["reimb_counted"]) == ("closed", "reimbursed", 0)
    assert month(client)["expense"] == base["expense"] and month(client)["income"] == base["income"]


def test_expected_less_than_paid_counts_the_rest_when_closed(client):
    C = setup(client)
    base = month(client)["expense"]
    e = exp(client, C, 40, reimb_expected=30)  # paid 40, only 30 will come back
    link(client, expense_id=e["id"], reimbursement_id=reimb(client, C, 30)["id"])
    s = st(client, e["id"])
    assert s["reimb_state"] == "closed" and s["reimb_counted"] == 10
    assert month(client)["expense"] == base + 10


def test_unlink_and_edit_amount(client):
    C = setup(client)
    base = month(client)["expense"]
    e = exp(client, C, 100)
    r1, r2 = reimb(client, C, 100), reimb(client, C, 50)
    lid = link(client, expense_id=e["id"], reimbursement_id=r1["id"]).json()["links"][0]["id"]
    assert st(client, e["id"])["reimb_state"] == "closed"
    # edit: lower it (partial again), over-limit refused
    up = client.put(f"/api/reimbursements/links/{lid}", json={"amount": 60}).json()
    assert up["expenses"][0]["reimb_state"] == "partial" and up["reimbursements"][0]["reimb_unallocated"] == 40
    assert client.put(f"/api/reimbursements/links/{lid}", json={"amount": 100.5}).status_code == 400
    l2 = link(client, expense_id=e["id"], reimbursement_id=r2["id"]).json()["links"][0]
    assert l2["amount"] == 40  # min(outstanding 40, unallocated 50)
    assert client.put(f"/api/reimbursements/links/{lid}", json={"amount": 61}).status_code == 400  # outstanding (minus itself) is 60
    client.post(f"/api/reimbursements/{e['id']}/close")
    assert month(client)["expense"] == base  # nothing outstanding: closing writes off 0
    # unlink -> outstanding again; a written-off expense now counts its unreimbursed part
    un = client.delete(f"/api/reimbursements/links/{lid}").json()
    assert un["reimbursements"][0]["reimb_unallocated"] == 100
    assert month(client)["expense"] == base + 60
    client.post(f"/api/reimbursements/{e['id']}/reopen")
    assert st(client, e["id"])["reimb_state"] == "partial" and month(client)["expense"] == base
    assert client.delete(f"/api/reimbursements/links/{lid}").status_code == 404
    # unflagging is refused while linked
    assert client.put(f"/api/transactions/{e['id']}", json={"reimbursable": False}).status_code == 400


def test_delete_cascade_and_reimbursement_edits(client, db):
    C = setup(client)
    e1, e2 = exp(client, C, 30), exp(client, C, 20)
    r = reimb(client, C, 50)
    link(client, reimbursement_id=r["id"], items=[{"expense_id": e1["id"]}, {"expense_id": e2["id"]}])
    # the reimbursement amount can't go below what is allocated; the date/note can change
    assert client.put(f"/api/transactions/{r['id']}", json={"amount": 40}).status_code == 400
    assert client.put(f"/api/transactions/{r['id']}", json={"amount": 55, "note": "more"}).json()["reimb_unallocated"] == 5
    other = acct(client, name="Test Alipay", type="ewallet", currency="CNY", opening_balance=0)
    assert client.put(f"/api/transactions/{r['id']}", json={"account_id": other}).status_code == 400  # currency change
    # deleting an expense removes its links; the reimbursement's money becomes unallocated
    assert client.delete(f"/api/transactions/{e1['id']}").status_code == 200
    assert st(client, r["id"])["reimb_unallocated"] == 35
    assert db.one("SELECT COUNT(*) n FROM reimb_links")["n"] == 1
    # deleting the reimbursement removes the rest; the expense is open again
    assert client.delete(f"/api/transactions/{r['id']}").status_code == 200
    assert db.one("SELECT COUNT(*) n FROM reimb_links")["n"] == 0
    assert st(client, e2["id"])["reimb_state"] == "open"
    # bulk delete too
    r3 = reimb(client, C, 20)
    link(client, expense_id=e2["id"], reimbursement_id=r3["id"])
    client.post("/api/transactions/bulk-delete", json={"ids": [r3["id"]]})
    assert db.one("SELECT COUNT(*) n FROM reimb_links")["n"] == 0
    # turning a reimbursement into another kind unlinks it
    r4 = reimb(client, C, 20)
    link(client, expense_id=e2["id"], reimbursement_id=r4["id"])
    client.put(f"/api/transactions/{r4['id']}", json={"kind": "income"})
    assert db.one("SELECT COUNT(*) n FROM reimb_links")["n"] == 0


def test_cross_currency_link(client):
    C = setup(client)
    W = acct(client, name="Test Alipay", type="ewallet", currency="CNY", opening_balance=0)
    e = exp(client, W, 700)                        # CNY expense
    r = reimb(client, C, 98.5)                     # paid back in USD
    l = link(client, expense_id=e["id"], reimbursement_id=r["id"]).json()["links"][0]
    assert l["amount"] == 700 and l["alloc_amount"] == 98.5  # settles the expense, uses the whole reimbursement
    assert st(client, e["id"])["reimb_state"] == "closed" and st(client, r["id"])["reimb_unallocated"] == 0
    # explicit parts on both sides; the reimbursement side can't exceed its unallocated money
    e2 = exp(client, W, 100)
    r2 = reimb(client, C, 10)
    assert link(client, expense_id=e2["id"], reimbursement_id=r2["id"], amount=100, alloc_amount=11).status_code == 400
    ok = link(client, expense_id=e2["id"], reimbursement_id=r2["id"], amount=50, alloc_amount=7).json()["links"][0]
    assert (ok["amount"], ok["alloc_amount"]) == (50, 7)


def test_suggestions_and_list_badges_and_filter(client):
    C = setup(client)
    e_far = exp(client, C, 33, date="2026-01-05 10:00", reimb_payer="Other Co")
    e_hit = exp(client, C, 120, date=D, reimb_payer="Acme Test")
    r = reimb(client, C, 120, reimb_payer="Acme Test")
    sg = client.get(f"/api/reimbursements/suggest?reimbursement_id={r['id']}").json()
    assert sg["items"][0]["id"] == e_hit["id"] and {"amount", "payer"} <= set(sg["items"][0]["match"])
    assert sg["items"][0]["default_amount"] == 120 and any(x["id"] == e_far["id"] for x in sg["items"])
    se = client.get(f"/api/reimbursements/suggest?expense_id={e_hit['id']}").json()
    assert se["items"][0]["id"] == r["id"] and se["items"][0]["default_amount"] == 120
    link(client, expense_id=e_hit["id"], reimbursement_id=r["id"], amount=100)
    rows = {t["id"]: t for t in client.get("/api/transactions?limit=20").json()["items"]}
    assert rows[e_hit["id"]]["reimb_state"] == "partial" and rows[e_far["id"]]["reimb_state"] == "open"
    assert rows[r["id"]]["reimb_unallocated"] == 20
    ids = lambda q: {t["id"] for t in client.get("/api/transactions?limit=50&" + q).json()["items"]}  # noqa: E731
    assert ids("reimb=partial") == {e_hit["id"]} and ids("reimb=open") == {e_far["id"]}
    assert ids("reimb=unallocated") == {r["id"]}
    un = client.get("/api/reimbursements/receipts?unallocated=1").json()["items"]
    assert [x["id"] for x in un] == [r["id"]]
    assert client.get("/api/reimbursements").json()["unallocated"]["by_currency"] == [{"currency": "USD", "amount": 20}]
    assert client.get("/api/reimbursements/suggest").status_code == 400


def test_csv_and_agent_export_links(client):
    C = setup(client)
    e = exp(client, C, 45)
    a = client.post("/api/agent/transactions", json={"kind": "reimbursement", "amount": 45, "account": "Test Checking",
                                                       "payer": "Acme Test", "expense_ids": [e["id"]]}).json()
    assert a["ok"] and a["transaction"]["kind"] == "reimbursement" and a["transaction"]["reimb_allocated"] == 45
    assert a["transaction"]["reimb_links"][0]["expense_id"] == e["id"] and not a["warnings"]
    assert st(client, e["id"])["reimb_state"] == "closed"
    rows = {int(r["id"]): r for r in csv.DictReader(io.StringIO(client.get("/api/transactions/export.csv").text.lstrip("\ufeff")))}
    er, rr = rows[e["id"]], rows[a["id"]]
    assert er["reimb_state"] == "closed" and er["reimb_links"] == f"#{a['id']}=45.00" and er["reimb_outstanding"] in ("0", "0.0")
    assert rr["reimb_links"] == f"#{e['id']}=45.00" and rr["reimb_unallocated"] in ("0", "0.0")
    bad = client.post("/api/agent/transactions", json={"kind": "reimbursement", "amount": 5, "account": "Test Checking",
                                                         "expense_ids": "999999"}).json()
    assert bad["ok"] and bad["warnings"]


def test_record_receipt_sets_allocation(client):
    C = setup(client)
    e1, e2 = exp(client, C, 30), exp(client, C, 20)
    r = client.post("/api/reimbursements/receipts", json={"account_id": C, "items": [{"expense_id": e1["id"]}, {"expense_id": e2["id"], "amount": 5}]}).json()
    assert r["receipt"]["reimb_allocated"] == 35 and r["receipt"]["reimb_unallocated"] == 0
    # received more than settled: the extra stays unallocated and can be linked later
    r2 = client.post("/api/reimbursements/receipts", json={"account_id": C, "amount": 40, "items": [{"expense_id": e2["id"], "amount": 5}]}).json()
    assert r2["receipt"]["reimb_unallocated"] == 35
    assert link(client, expense_id=e2["id"], reimbursement_id=r2["receipt"]["id"]).status_code == 400  # already linked
    e4 = exp(client, C, 12)
    assert link(client, expense_id=e4["id"], reimbursement_id=r2["receipt"]["id"]).json()["links"][0]["amount"] == 12
    # received less than settled (same currency) is refused
    e3 = exp(client, C, 50)
    assert client.post("/api/reimbursements/receipts", json={"account_id": C, "amount": 10, "items": [{"expense_id": e3["id"]}]}).status_code == 400


def test_backup_before_migrate_and_backfill(tmp_path):
    path = tmp_path / "finance.db"
    app = create_app(path, start_background=False)
    db = app.state.db
    from fastapi.testclient import TestClient
    c = TestClient(app); c.headers["X-API-Key"] = db.get_setting("api_key")
    C = setup(c)
    W = acct(c, name="Test Alipay", type="ewallet", currency="CNY", opening_balance=0)
    e1, e2 = exp(c, C, 30), exp(c, W, 700)
    rid = c.post("/api/reimbursements/receipts", json={"account_id": C, "amount": 128.5,
                                                       "items": [{"expense_id": e1["id"]}, {"expense_id": e2["id"]}]}).json()["receipt"]["id"]
    # turn the file into the previous schema (reimb_links without alloc_amount)
    con = sqlite3.connect(path)
    con.executescript("""DROP TRIGGER trg_tx_delete_reimb; CREATE TABLE rl_old (id INTEGER PRIMARY KEY, receipt_id INTEGER NOT NULL, expense_id INTEGER NOT NULL,
        amount REAL NOT NULL, created_at TEXT); INSERT INTO rl_old SELECT id, receipt_id, expense_id, amount, created_at FROM reimb_links;
        DROP TABLE reimb_links; ALTER TABLE rl_old RENAME TO reimb_links;""")
    con.commit(); con.close()
    assert db.pending_migrations() == ["reimb_links.alloc_amount"]
    app2 = create_app(path, start_background=False)
    db2 = app2.state.db
    bk = db2.last_migration_backup
    assert bk and bk.exists() and "before-migrate" in bk.name
    old = sqlite3.connect(bk)
    assert "alloc_amount" not in {r[1] for r in old.execute("PRAGMA table_info(reimb_links)")}  # the copy is pre-migration
    assert old.execute("SELECT COUNT(*) FROM reimb_links").fetchone()[0] == 2
    old.close()
    allocs = {r["expense_id"]: r["alloc_amount"] for r in db2.all("SELECT expense_id, alloc_amount FROM reimb_links")}
    assert allocs == {e1["id"]: 30, e2["id"]: 98.5}  # USD link exact, the CNY link gets the rest of the 128.50
    assert db2.one("SELECT 1 x FROM sqlite_master WHERE name='trg_tx_delete_reimb'")
    assert db2.pending_migrations() == [] and create_app(path, start_background=False).state.db.last_migration_backup is None
    c2 = TestClient(app2); c2.headers["X-API-Key"] = db2.get_setting("api_key")
    assert c2.get(f"/api/transactions/{rid}").json()["reimb_unallocated"] == 0
