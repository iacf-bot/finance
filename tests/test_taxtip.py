"""Optional tax & tips 税/小费: NULL means not recorded (never 0); reports only sum recorded values."""
import csv
import io


def acct(client, name="Card", type_="credit_card", currency="USD"):
    return client.post("/api/accounts", json={"name": name, "type": type_, "currency": currency}).json()["id"]


def add(client, **kw):
    r = client.post("/api/transactions", json={"kind": "expense", "date": "2026-10-05", **kw})
    assert r.status_code == 200, r.text
    return client.get(f"/api/transactions/{r.json()['id']}").json()


def test_blank_is_null_not_zero(client):
    a = acct(client)
    t = add(client, account_id=a, amount=20)
    assert t["tax_amount"] is None and t["tip_amount"] is None and t["tax_rate"] is None
    t = add(client, account_id=a, amount=20, tax_amount="", tip_amount=None)
    assert t["tax_amount"] is None and t["tip_amount"] is None
    t = add(client, account_id=a, amount=118, tax_amount=8.5, tip_amount="18", tax_rate="8.5%")
    assert (t["tax_amount"], t["tip_amount"], t["tax_rate"]) == (8.5, 18.0, 8.5)
    t0 = add(client, account_id=a, amount=10, tip_amount=0)  # explicit zero tip is kept as 0
    assert t0["tip_amount"] == 0


def test_validation(client):
    a = acct(client)
    r = client.post("/api/transactions", json={"kind": "expense", "date": "2026-10-05", "account_id": a, "amount": 10, "tax_amount": 6, "tip_amount": 5})
    assert r.status_code == 400 and "Tax + tip" in r.text
    r = client.post("/api/transactions", json={"kind": "expense", "date": "2026-10-05", "account_id": a, "amount": 10, "tip_amount": -1})
    assert r.status_code == 400
    b = acct(client, "Checking", "checking")
    r = client.post("/api/transactions", json={"kind": "transfer", "date": "2026-10-05", "account_id": a, "to_account_id": b, "amount": 10, "tax_amount": 1})
    t = client.get(f"/api/transactions/{r.json()['id']}").json()
    assert t["tax_amount"] is None  # only expense/income/refund carry tax/tip


def test_quick_add_tokens(client):
    acct(client, "Chase")
    p = client.post("/api/quick-add", json={"text": "dinner 118 tip 18 tax 8.5 chase", "dry_run": True}).json()
    d = p.get("prefill") or p.get("transaction") or p
    assert d["tip_amount"] == 18 and d["tax_amount"] == 8.5 and d["amount"] == 118
    p = client.post("/api/quick-add", json={"text": "午饭 100 小费 10 税 8 chase", "dry_run": True}).json()
    d = p.get("prefill") or p.get("transaction") or p
    assert (d["tax_amount"], d["tip_amount"]) == (8, 10)
    p = client.post("/api/quick-add", json={"text": "lunch 25 chase", "dry_run": True}).json()
    d = p.get("prefill") or p.get("transaction") or p
    assert d.get("tax_amount") is None and d.get("tip_amount") is None


def test_tip_percent_quick_add():
    from app.parser import split_tax_tip
    rest, found = split_tax_tip("dinner 118 tip 18% chase")
    assert "tip" not in rest and found["tip"] == (18.0, True)


def test_report_and_filters_and_csv(client):
    a = acct(client)
    add(client, account_id=a, amount=118, tax_amount=8, tip_amount=10)   # subtotal 100
    add(client, account_id=a, amount=55, tax_amount=5)                   # subtotal 50, no tip
    add(client, account_id=a, amount=999)                                # not recorded: excluded
    d = client.get("/api/reports/tax-tips?start=2026-10-01&end=2026-10-31").json()
    x = d["totals"]
    assert (x["tax"], x["tip"], x["tax_count"], x["tip_count"], x["count"]) == (13, 10, 2, 1, 2)
    assert x["tip_pct"] == 10.0            # tips ÷ subtotal of tipped transactions only
    assert x["tax_pct"] == round(13 / 150 * 100, 2)
    assert d["rows"][0]["key"] == "2026-10"
    assert client.get("/api/reports/tax-tips?group_by=nope").status_code == 400
    assert len(client.get("/api/transactions?has_tip=1").json()["items"]) == 1
    assert len(client.get("/api/transactions?has_tax=1").json()["items"]) == 2
    assert len(client.get("/api/transactions?has_tax=0").json()["items"]) == 1
    rows = list(csv.DictReader(io.StringIO(client.get("/api/transactions/export.csv").text)))
    r = next(r for r in rows if r["tip_amount"])
    assert float(r["subtotal"]) == 100
    assert next(r for r in rows if float(r["amount"]) == 999)["tax_amount"] == ""


def test_report_empty_when_nothing_recorded(client):
    a = acct(client)
    add(client, account_id=a, amount=50)
    x = client.get("/api/reports/tax-tips?start=2026-01-01&end=2026-12-31").json()["totals"]
    assert x["count"] == 0 and x["tip_pct"] is None and x["tax_pct"] is None
