"""Projects 项目. FAKE data only."""
from tests.test_principal_returns import mk, tx


def setup(client):
    chk = mk(client, name="Test Checking", type="checking", currency="USD", opening_balance=5000, start_date="2026-01-01")
    card = mk(client, name="Test Card", type="credit_card", currency="USD", opening_balance=0, start_date="2026-01-01")
    r = client.post("/api/projects", json={"name": "Test Trip", "description": "fake trip", "start_date": "2026-03-01",
                                           "end_date": "2026-03-20", "budget": 1000, "currency": "USD", "color": "#3b82f6", "icon": "✈️"})
    assert r.status_code == 200, r.text
    return chk["id"], card["id"], r.json()["id"]


def test_crud_validation_archive(client):
    _, _, pid = setup(client)
    assert client.post("/api/projects", json={"name": "Test Trip"}).status_code == 400          # duplicate
    assert client.post("/api/projects", json={"name": ""}).status_code == 400
    assert client.post("/api/projects", json={"name": "X", "start_date": "2026-05-01", "end_date": "2026-04-01"}).status_code == 400
    assert client.post("/api/projects", json={"name": "Y", "currency": "ZZZ"}).status_code == 400
    assert client.post("/api/projects", json={"name": "Z", "color": "red"}).status_code == 400
    p = client.put(f"/api/projects/{pid}", json={"note": "n1", "budget": ""}).json()
    assert p["note"] == "n1" and p["budget"] is None
    assert client.post(f"/api/projects/{pid}/archive").json()["archived"] == 1
    lst = client.get("/api/projects").json()
    assert all(x["id"] != pid for x in lst["items"]) and lst["archived_count"] == 1
    assert any(x["id"] == pid for x in client.get("/api/projects?include_archived=true").json()["items"])
    assert client.post(f"/api/projects/{pid}/archive", json={"undo": True}).json()["archived"] == 0
    assert client.get("/api/projects/9999").status_code == 404


def test_totals_breakdowns_reimbursable_and_reports_filter(client):
    chk, card, pid = setup(client)
    cats = client.get("/api/meta").json()["categories"]
    leaf = next(ch for c in cats if c["kind"] == "expense" for ch in c["children"])
    other = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    a = tx(client, kind="expense", account_id=card, amount=300, date="2026-03-02", project_id=pid, merchant="Test Hotel",
           category_id=leaf["id"])
    tx(client, kind="expense", account_id=chk, amount=50, date="2026-04-05", project_id=pid, merchant="Test Cafe")
    tx(client, kind="income", account_id=chk, amount=80, date="2026-03-10", project_id=pid)
    tx(client, kind="expense", account_id=chk, amount=200, date="2026-03-03", project_id=pid, reimbursable=True)
    tx(client, kind="expense", account_id=chk, amount=999, date="2026-03-03", project_id=other)
    tx(client, kind="transfer", account_id=chk, to_account_id=card, amount=300, date="2026-03-15", project_id=pid)
    d = client.get(f"/api/projects/{pid}").json()
    s = d["summary"]
    assert (s["expense"], s["income"], s["net"]) == (350, 80, -270)            # reimbursable + transfer excluded
    assert s["reimbursable"] == {"amount": 200, "outstanding": 200, "count": 1}
    assert s["budget"] == 1000 and s["budget_remaining"] == 650 and s["budget_pct"] == 35
    assert [m["month"] for m in d["monthly"]] == ["2026-03", "2026-04"] and d["monthly"][0]["expense"] == 300
    assert {r["name"] for r in d["by_account"]} == {"Test Card", "Test Checking"}
    assert d["by_merchant"][0]["name"] == "Test Hotel"
    cat = leaf["parent_id"]
    assert d["by_category"][0]["key"] == cat
    dd = client.get(f"/api/projects/{pid}?parent_id={cat}").json()
    assert dd["drill"]["rows"] and sum(r["amount"] for r in dd["drill"]["rows"]) == d["by_category"][0]["amount"]
    r = client.get(f"/api/projects/{pid}?start=2026-04-01&end=2026-04-30").json()["summary"]
    assert (r["expense"], r["income"]) == (50, 0)
    rep = client.get(f"/api/reports?start=2026-01-01&end=2026-12-31&project_id={pid}").json()
    assert rep["totals"]["expense"] == 350 and rep["totals"]["income"] == 80
    assert client.get("/api/reports?start=2026-01-01&end=2026-12-31").json()["totals"]["expense"] == 1349
    lst = {x["id"]: x for x in client.get("/api/projects").json()["items"]}
    assert lst[pid]["summary"]["expense"] == 350 and lst[pid]["summary"]["count"] == 5
    items = client.get(f"/api/transactions?project_id={pid}&limit=50").json()
    rows = items.get("items", items)
    assert len(rows) == 5 and a["id"] in {t["id"] for t in rows}


def test_batch_assign_archived_and_delete_keeps_tx(client):
    chk, _, pid = setup(client)
    ids = [tx(client, kind="expense", account_id=chk, amount=10 + i, date="2026-03-05")["id"] for i in range(3)]
    assert client.post("/api/projects/assign", json={"ids": ids, "project_id": pid}).json()["updated"] == 3
    client.post(f"/api/projects/{pid}/archive")
    assert client.post("/api/projects/assign", json={"ids": ids[:1], "project_id": pid}).status_code == 400
    assert client.get(f"/api/projects/{pid}").json()["summary"]["count"] == 3               # kept while archived
    assert client.post("/api/projects/assign", json={"ids": ids[:1], "project_id": None}).json()["updated"] == 1
    assert client.post("/api/projects/assign", json={"ids": [], "project_id": pid}).status_code == 400
    assert client.delete(f"/api/projects/{pid}").json()["unassigned"] == 2
    assert client.get(f"/api/transactions/{ids[1]}").json()["project_id"] is None


def test_quick_add_project_syntax(client):
    chk, _, pid = setup(client)
    client.post("/api/projects", json={"name": "Kitchen reno 厨房装修"})
    r = client.post("/api/quick-add", json={"text": "ramen 15 checking #p:trip"}).json()
    assert r["transaction"]["project_id"] == pid
    r = client.post("/api/quick-add", json={"text": "瓷砖 200 checking #项目:厨房"}).json()
    assert r["transaction"]["project_name"].startswith("Kitchen")
    r = client.post("/api/quick-add", json={"text": 'paint 40 checking #project:"Test Trip" #home'}).json()
    assert r["transaction"]["project_id"] == pid and "home" in r["transaction"]["tags"]
    r = client.post("/api/quick-add", json={"text": "x 5 checking #p:nope"}).json()
    assert r["transaction"]["project_id"] is None and "not found" in r["message"]
    client.post(f"/api/projects/{pid}/archive")
    r = client.post("/api/quick-add", json={"text": "y 6 checking #p:trip"}).json()
    assert r["transaction"]["project_id"] is None and "not found" in r["message"]


def _cats(client):
    cats = [c for c in client.get("/api/meta").json()["categories"] if c["kind"] == "expense" and len(c["children"]) >= 2]
    return cats[0], cats[1]


def test_budget_plan_vs_actual_rollup_other_unplanned_and_copy(client):
    chk, _, pid = setup(client)
    client.put(f"/api/projects/{pid}", json={"budget": None})
    A, B = _cats(client)
    a1, a2 = A["children"][0]["id"], A["children"][1]["id"]
    r = client.put(f"/api/projects/{pid}/plan", json={"lines": [
        {"category_id": a1, "amount": 300}, {"category_id": a2, "amount": 100}, {"category_id": None, "amount": 50, "note": "misc"}]})
    assert r.status_code == 200 and len(r.json()["lines"]) == 3
    assert client.put(f"/api/projects/{pid}/plan", json={"lines": [{"category_id": a1, "amount": -5}]}).status_code == 400
    tx(client, kind="expense", account_id=chk, amount=350, date="2026-03-02", project_id=pid, category_id=a1)   # over
    tx(client, kind="expense", account_id=chk, amount=40, date="2026-03-02", project_id=pid, category_id=a2)
    tx(client, kind="expense", account_id=chk, amount=70, date="2026-03-02", project_id=pid, category_id=B["children"][0]["id"])  # unplanned
    tx(client, kind="expense", account_id=chk, amount=30, date="2026-03-02", project_id=pid)                    # uncategorized
    pl = client.get(f"/api/projects/{pid}").json()["plan"]
    rows = {(r["category_id"], r["level"]): r for r in pl["rows"]}
    par = rows[(A["id"], 0)]
    assert par["planned"] == 400 and par["actual"] == 390 and par["rollup"] and not par["over"]
    assert rows[(a1, 1)]["over"] and rows[(a1, 1)]["remaining"] == -50 and rows[(a2, 1)]["pct"] == 40
    other = next(r for r in pl["rows"] if r.get("other"))
    assert other["planned"] == 50 and other["actual"] == 100 and other["over"]
    assert {u["category_id"] for u in pl["unplanned"]} == {B["id"], None} and pl["unplanned_total"] == 100
    assert (pl["plan_total"], pl["budget"], pl["budget_source"], pl["actual"], pl["over"]) == (450, 450, "plan", 490, True)
    s = client.get(f"/api/projects/{pid}").json()["summary"]
    assert s["budget"] == 450                                                          # total = sum of plan
    client.put(f"/api/projects/{pid}", json={"budget": 600})
    pl = client.get(f"/api/projects/{pid}").json()["plan"]
    assert pl["budget"] == 600 and pl["budget_source"] == "explicit" and not pl["over"]
    # explicit parent amount larger than its children wins
    client.put(f"/api/projects/{pid}/plan", json={"replace": False, "lines": [{"category_id": A["id"], "amount": 500}]})
    pl = client.get(f"/api/projects/{pid}").json()["plan"]
    assert next(r for r in pl["rows"] if r["category_id"] == A["id"])["planned"] == 500
    # copy to another project (scaled)
    q = client.post("/api/projects", json={"name": "Copy target"}).json()["id"]
    c = client.post(f"/api/projects/{q}/plan/copy", json={"from_project_id": pid, "scale": 2}).json()
    assert sorted(l["amount"] for l in c["lines"]) == [100, 200, 600, 1000]
    e = client.post("/api/projects", json={"name": "Empty"}).json()["id"]
    assert client.post(f"/api/projects/{q}/plan/copy", json={"from_project_id": e}).status_code == 400
    assert client.get(f"/api/projects/{e}").json()["plan"] is None
