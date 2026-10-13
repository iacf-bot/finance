"""Project plan lines 预算明细. FAKE data only."""
from tests.test_principal_returns import mk, tx


def _cat(client, name):
    for c in client.get("/api/meta").json()["categories"]:
        if c["name"] == name:
            return c
        for k in c.get("children") or []:
            if k["name"] == name:
                return k
    raise AssertionError(name)


def _setup(client, name="Test Vacation"):
    acc = getattr(client, "_test_chk", None)
    if acc is None:
        acc = client._test_chk = mk(client, name="Test Checking", type="checking", currency="USD", opening_balance=5000,
                                    start_date="2026-01-01", aliases="checking")["id"]
    chk = {"id": acc}
    p = client.post("/api/projects", json={"name": name, "currency": "USD"}).json()
    return chk["id"], p["id"]


def _lines(client, pid):
    return client.get(f"/api/projects/{pid}").json()["plan_lines"]


def test_line_crud_validation_reorder(client):
    _, pid = _setup(client)
    fl = _cat(client, "Flights")
    a = client.post(f"/api/projects/{pid}/plan-lines", json={"name": "Flights", "amount": 900, "category_ids": [fl["id"]],
                                                             "note": "2 seats"}).json()
    assert a["name"] == "Flights" and a["category_ids"] == [fl["id"]] and a["sort"] == 10
    b = client.post(f"/api/projects/{pid}/plan-lines", json={"name": "Hotel", "amount": 600, "currency": "eur"}).json()
    assert b["currency"] == "EUR" and b["sort"] == 20
    for bad in ({"name": "", "amount": 1}, {"name": "X", "amount": -1}, {"name": "X", "amount": "abc"},
                {"name": "X", "currency": "EURO"}, {"name": "X", "category_ids": [999999]}, {"name": "X", "category_ids": "1"}):
        assert client.post(f"/api/projects/{pid}/plan-lines", json=bad).status_code == 400, bad
    salary = next(c for c in client.get("/api/meta").json()["categories"] if c["kind"] == "income")
    assert client.post(f"/api/projects/{pid}/plan-lines", json={"name": "X", "category_ids": [salary["id"]]}).status_code == 400
    u = client.put(f"/api/projects/{pid}/plan-lines/{b['id']}", json={"amount": 650, "note": "4 nights"}).json()
    assert u["amount"] == 650 and u["name"] == "Hotel" and u["currency"] == "EUR"
    r = client.post(f"/api/projects/{pid}/plan-lines/reorder", json={"ids": [b["id"], a["id"]]}).json()
    assert [l["name"] for l in r["lines"]] == ["Hotel", "Flights"]
    assert client.post(f"/api/projects/{pid}/plan-lines/reorder", json={"ids": [a["id"]]}).status_code == 400
    assert client.put(f"/api/projects/{pid}/plan-lines/99999", json={"amount": 1}).status_code == 404
    assert client.delete(f"/api/projects/{pid}/plan-lines/{b['id']}").json()["ok"]
    assert [l["name"] for l in client.get(f"/api/projects/{pid}/plan-lines").json()["lines"]] == ["Flights"]


def test_actuals_explicit_wins_no_double_count_unplanned(client):
    chk, pid = _setup(client)
    fl, taxi, meals, hotel = (_cat(client, n) for n in ("Flights", "Taxi & Rideshare", "Meals", "Travel & Hotels"))
    trans = client.post(f"/api/projects/{pid}/plan-lines", json={"name": "Transportation", "amount": 1000,
                                                                 "category_ids": [fl["id"], taxi["id"]]}).json()
    food = client.post(f"/api/projects/{pid}/plan-lines", json={"name": "Meals", "amount": 100, "category_ids": [meals["id"]]}).json()
    extra = client.post(f"/api/projects/{pid}/plan-lines", json={"name": "Souvenirs", "amount": 50}).json()  # no mapping
    tx(client, kind="expense", account_id=chk, amount=800, project_id=pid, category_id=fl["id"])
    tx(client, kind="expense", account_id=chk, amount=30, project_id=pid, category_id=taxi["id"])
    tx(client, kind="expense", account_id=chk, amount=120, project_id=pid, category_id=meals["id"])          # over plan
    t4 = tx(client, kind="expense", account_id=chk, amount=40, project_id=pid, category_id=meals["id"], plan_line_id=extra["id"])
    tx(client, kind="expense", account_id=chk, amount=200, project_id=pid, category_id=hotel["id"])          # unplanned
    tx(client, kind="refund", account_id=chk, amount=10, project_id=pid, category_id=taxi["id"])
    tx(client, kind="expense", account_id=chk, amount=999, category_id=fl["id"])                             # other project/no project
    assert t4["plan_line_id"] == extra["id"]
    pl = _lines(client, pid)
    by = {l["name"]: l for l in pl["lines"]}
    assert by["Transportation"]["actual"] == 820 and not by["Transportation"]["over"] and by["Transportation"]["remaining"] == 180
    assert by["Meals"]["actual"] == 120 and by["Meals"]["over"] and by["Meals"]["pct"] == 120.0
    assert by["Souvenirs"]["actual"] == 40   # explicit assignment wins over the Meals category mapping
    assert pl["unplanned"]["actual"] == 200 and pl["unplanned"]["by_category"][0]["category_id"] == hotel["id"]
    assert pl["actual"] == 820 + 120 + 40 + 200 == sum(l["actual"] for l in pl["lines"]) + pl["unplanned"]["actual"]
    assert pl["plan_total"] == 1150 and pl["budget_source"] == "plan" and pl["over"]
    # expand a line / unplanned
    r = client.get(f"/api/projects/{pid}/plan-lines/{extra['id']}/transactions").json()
    assert [t["id"] for t in r["transactions"]] == [t4["id"]] and r["transactions"][0]["plan_match"] == "explicit"
    assert r["transactions"][0]["plan_line_name"] == "Souvenirs"
    u = client.get(f"/api/projects/{pid}/plan-lines/unplanned/transactions").json()
    assert u["total"] == 200 and len(u["transactions"]) == 1
    assert client.get(f"/api/projects/{pid}/plan-lines/abc/transactions").status_code == 400
    # catch-all absorbs what nothing else matched
    client.put(f"/api/projects/{pid}/plan-lines/{extra['id']}", json={"catch_all": True})
    pl = _lines(client, pid)
    assert pl["unplanned"]["actual"] == 0 and {l["name"]: l for l in pl["lines"]}["Souvenirs"]["actual"] == 240
    # a line of another project is rejected; moving the tx to another project drops its line
    _, other = _setup(client, "Test Other")
    assert client.put(f"/api/transactions/{t4['id']}", json={"plan_line_id": food["id"] + 1000}).status_code == 400
    client.post("/api/projects/assign", json={"ids": [t4["id"]], "project_id": other})
    assert client.get(f"/api/transactions/{t4['id']}").json()["plan_line_id"] is None
    # deleting a line clears explicit assignments
    t5 = tx(client, kind="expense", account_id=chk, amount=5, project_id=pid, category_id=meals["id"], plan_line_id=trans["id"])
    assert client.delete(f"/api/projects/{pid}/plan-lines/{trans['id']}").json()["unassigned_transactions"] == 1
    assert client.get(f"/api/transactions/{t5['id']}").json()["plan_line_id"] is None


def test_templates_suggest_categories_and_copy(client):
    _, pid = _setup(client)
    t = {x["key"]: x for x in client.get("/api/plan-templates").json()["templates"]}
    assert {"vacation", "renovation", "event"} <= set(t)
    vac = {l["name"]: l for l in t["vacation"]["lines"]}
    assert list(vac)[:3] == ["Flights", "Hotel", "Transportation"] and "Insurance" in vac and vac["Other"]["catch_all"]
    assert _cat(client, "Flights")["id"] in vac["Flights"]["category_ids"]
    assert _cat(client, "Travel & Hotels")["id"] in vac["Hotel"]["category_ids"]
    r = client.post(f"/api/projects/{pid}/plan-lines/template", json={"template": "vacation", "amounts": {"Flights": 700}}).json()
    assert len(r["lines"]) == 8 and r["lines"][0]["amount"] == 700
    assert client.post(f"/api/projects/{pid}/plan-lines/template", json={"template": "nope"}).status_code == 400
    _, z = _setup(client, "Test Zh")
    r2 = client.post(f"/api/projects/{z}/plan-lines/template", json={"template": "event", "names": {"Venue": "场地"}}).json()
    assert r2["lines"][0]["name"] == "场地" and r2["lines"][1]["name"] == "Catering"
    _, q = _setup(client, "Test Copy")
    c = client.post(f"/api/projects/{q}/plan-lines/copy", json={"from_project_id": pid, "scale": 2}).json()
    assert [l["name"] for l in c["lines"]] == [l["name"] for l in r["lines"]] and c["lines"][0]["amount"] == 1400
    assert c["lines"][0]["category_ids"] == r["lines"][0]["category_ids"]
    _, e = _setup(client, "Test Empty")
    assert client.post(f"/api/projects/{q}/plan-lines/copy", json={"from_project_id": e}).status_code == 400
    assert _lines(client, e) is None


def test_legacy_category_plan_mirrors_into_lines(client):
    _, pid = _setup(client)
    fl, meals = _cat(client, "Flights"), _cat(client, "Meals")
    client.put(f"/api/projects/{pid}/plan", json={"lines": [{"category_id": fl["id"], "amount": 300},
                                                          {"category_id": None, "amount": 50, "note": "misc"}]})
    ls = client.get(f"/api/projects/{pid}/plan-lines").json()["lines"]
    assert [(l["amount"], l["category_ids"], l["catch_all"]) for l in ls] == [(300, [fl["id"]], False), (50, [], True)]
    assert ls[0]["name"].endswith("Flights") and ls[1]["note"] == "misc"
    client.put(f"/api/projects/{pid}/plan", json={"replace": False, "lines": [{"category_id": meals["id"], "amount": 80},
                                                                            {"category_id": fl["id"], "amount": 0}]})
    ls = client.get(f"/api/projects/{pid}/plan-lines").json()["lines"]
    assert sorted(l["amount"] for l in ls) == [50, 80]
    assert client.get(f"/api/projects/{pid}").json()["plan"] is not None  # legacy view still served


def test_migration_copies_category_plans_once(tmp_path):
    from app.main import create_app
    reopen = lambda: create_app(tmp_path / "m.db", start_background=False).state.db
    d = reopen()
    with d.tx() as con:
        con.execute("DELETE FROM settings WHERE key='migr_plan_lines'")
        pid = con.execute("INSERT INTO projects(name) VALUES('Test Legacy')").lastrowid
        cid = con.execute("SELECT id FROM categories WHERE name='Meals'").fetchone()[0]
        con.execute("INSERT INTO project_budgets(project_id, category_id, amount) VALUES(?,?,?)", (pid, cid, 120))
        con.execute("INSERT INTO project_budgets(project_id, category_id, amount, note) VALUES(?,NULL,30,'x')", (pid,))
    d2 = reopen()  # re-open runs the additive migration
    rows = d2.all("SELECT name, amount, catch_all FROM project_plan_lines WHERE project_id=? ORDER BY sort", (pid,))
    assert [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in rows] == [("Food & Dining › Meals", 120, 0), ("Other", 30, 1)]
    assert d2.one("SELECT COUNT(*) n FROM project_budgets WHERE project_id=?", (pid,))["n"] == 2  # source kept
    d3 = reopen()
    assert d3.one("SELECT COUNT(*) n FROM project_plan_lines WHERE project_id=?", (pid,))["n"] == 2  # runs once


def test_quick_add_plan_line_syntax(client):
    chk, pid = _setup(client, "日本旅行")
    fl = client.post(f"/api/projects/{pid}/plan-lines", json={"name": "Flights", "amount": 900}).json()
    client.post(f"/api/projects/{pid}/plan-lines", json={"name": "Hotel & food", "amount": 500})
    r = client.post("/api/quick-add", json={"text": "jal ticket 450 checking #p:日本旅行/Flights"}).json()
    assert r["transaction"]["project_id"] == pid and r["transaction"]["plan_line_id"] == fl["id"]
    r = client.post("/api/quick-add", json={"text": "ryokan 200 checking #p:日本旅行/\"Hotel & food\""}).json()
    assert r["transaction"]["plan_line_id"] != fl["id"] and r["transaction"]["plan_line_id"], r
    r = client.post("/api/quick-add", json={"text": "taxi 20 checking #p:日本/fl"}).json()   # prefixes
    assert r["transaction"]["plan_line_id"] == fl["id"]
    r = client.post("/api/quick-add", json={"text": "snack 3 checking #p:日本旅行/Nope"}).json()
    assert r["transaction"]["project_id"] == pid and r["transaction"]["plan_line_id"] is None and "plan line" in r["message"]
    r = client.post("/api/quick-add", json={"text": "gum 1 checking #p:日本旅行"}).json()
    assert r["transaction"]["project_id"] == pid and r["transaction"]["plan_line_id"] is None
