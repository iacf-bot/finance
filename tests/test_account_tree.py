"""Accounts tree: group totals in the base currency (FAKE data)."""


def node(tree, name):
    return next(n for n in tree if n["name"].startswith(name))


def test_group_totals_mixed_currency_and_liabilities(client, accounts):
    client.put("/api/fx", json={"currency": "CNY", "rate": 0.14})
    d = client.get("/api/accounts").json()
    tree = d["tree"]
    # every non-empty group appears once, every account exactly once
    ids = [i for n in tree for i in n["account_ids"]]
    assert sorted(ids) == sorted(a["id"] for a in d["accounts"])
    assert all(n["count"] > 0 and n["children"] == [] for n in tree)
    cards = node(tree, "Credit Cards")
    assert cards["liability_group"] and cards["count"] == 2
    assert cards["total_base"] == -100 and cards["owed_base"] == 100 and not cards["mixed_currency"]
    cash = node(tree, "Cash")
    assert cash["total_base"] == 50 and cash["owed_base"] is None
    wallets = node(tree, "E-Wallets")
    assert wallets["by_currency"] == [{"currency": "CNY", "total": 500.0, "total_base": 70.0}] and wallets["total_base"] == 70
    # groups add up to net worth
    assert round(sum(n["total_base"] for n in tree), 2) == d["net_worth"]["net_worth"]
    # zero-balance accounts are counted
    assert node(tree, "Investments")["zero_count"] == 1


def test_mixed_currency_group_and_ungrouped(client, accounts, db):
    # put a CNY wallet into the Bank group and an account with a dangling group id -> "Ungrouped"
    bank = next(g for g in client.get("/api/accounts").json()["groups"] if g["name"].startswith("Bank"))
    client.put(f"/api/accounts/{accounts['alipay']['id']}", json={"group_id": bank["id"]})
    with db.tx() as con:
        con.execute("UPDATE accounts SET group_id=NULL WHERE id=?", (accounts["cash"]["id"],))
    tree = client.get("/api/accounts").json()["tree"]
    b = node(tree, "Bank")
    assert b["mixed_currency"] and [c["currency"] for c in b["by_currency"]] == ["USD", "CNY"]  # base currency first
    assert b["total_base"] == round(1000 + 500 * 0.14, 2)
    u = node(tree, "Ungrouped")
    assert u["id"] is None and u["account_ids"] == [accounts["cash"]["id"]] and tree[-1] is u


def test_liability_group_mixed_with_credit(client, accounts):
    # a card in credit inside a liability group shows as negative owed for that card and nets in the group
    sid = accounts["sapphire"]["id"]
    client.post(f"/api/accounts/{sid}/set-balance", json={"balance": -40})  # overpaid by 40
    cards = node(client.get("/api/accounts").json()["tree"], "Credit Cards")
    assert cards["owed_base"] == 60 and cards["total_base"] == -60


def test_archived_counted_in_totals_but_not_active(client, accounts):
    client.put(f"/api/accounts/{accounts['cash']['id']}", json={"archived": 1})
    cash = node(client.get("/api/accounts").json()["tree"], "Cash")
    assert cash["archived_count"] == 1 and cash["active_count"] == 0 and cash["total_base"] == 50
