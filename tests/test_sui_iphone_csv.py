"""Regression tests for the 随手记 iPhone app CSV export (headers:v5). Every row here is SYNTHETIC/FAKE; the cases
mimic features seen in a real export: banner line, 项目 column before 账户, quoted multi-line notes, cross-currency
transfer pairs in either order, zero amounts, negative 支出, 负债变更 on store cards, CamelCase account names,
and accounts that need an opening balance."""
from app.importers import guess_account_type, guess_account_type_from_usage, parse_sui
from collections import Counter

HEAD = "随手记导出文件(headers:v5; iPhone; MyMoney; 0.0.0; 20990101)\n" \
       "交易类型,日期,类别,子类别,项目,账户,账户币种,金额,成员,商家,备注,关联Id\n"
ROWS = [
    "余额变更,2020-01-01 09:00:00,,,,FakeChecking,USD,500.00,,,,",
    "支出,2020-01-02 10:00:00,餐饮,午餐,,FakeStoreCard,USD,12.50,FakeMember,FakeDiner,\"line one\nline two, with comma\",",
    "支出,2020-01-03 10:00:00,餐饮,午餐,,FakeStoreCard,USD,-2.50,,,refund-ish,",
    "支出,2020-01-03 11:00:00,餐饮,午餐,,FakeStoreCard,USD,0.00,,,zero row,",
    "负债变更,2020-01-01 08:00:00,,,,FakeStoreCard,USD,100.00,,,,",
    # card payment, 转入 listed before 转出
    "转入,2020-01-05 09:00:00,,,,FakeStoreCard,USD,60.00,,,,fake-pair-1",
    "转出,2020-01-05 09:00:00,,,,FakeChecking,USD,60.00,,,,fake-pair-1",
    # cross-currency pair: 1000 CNY out, 140 USD in
    "转出,2020-01-06 09:00:00,,,,假的银行卡,CNY,1000.00,,,,fake-pair-2",
    "转入,2020-01-06 09:00:00,,,,FakeChecking,USD,140.00,,,,fake-pair-2",
    "收入,2020-01-07 09:00:00,职业收入,工资,FakeProject,FakeChecking,USD,\"1,000.00\",,,,",
    "支出,2020-01-08 09:00:00,家居,杂项,,FakeChecking,USD,2000.00,,,big spend,",
]


def csv_bytes(rows=ROWS, encoding="utf-8-sig"):
    return (HEAD + "\n".join(rows) + "\n").encode(encoding)


def acct(client, name):
    return next((a for a in client.get("/api/accounts").json()["accounts"] if a["name"] == name), None)


def test_parse_banner_multiline_and_pairs():
    p = parse_sui("export.csv", csv_bytes())
    recs = p["records"]
    assert p["sheets"][0]["rows"] == len(ROWS) and not p["sheets"][0]["unrecognized"]
    notes = [r["note"] for r in recs if r.get("note")]
    assert "line one\nline two, with comma" in notes
    transfers = [r for r in recs if r["type"] == "transfer"]
    assert len(transfers) == 2 and all(r["type_raw"] == "转账" for r in transfers)
    card = next(r for r in transfers if r["account2"] == "FakeStoreCard")
    assert card["account"] == "FakeChecking" and card["amount"] == 60
    fx = next(r for r in transfers if r["account"] == "假的银行卡")
    assert fx["account2"] == "FakeChecking" and fx["amount"] == 1000 and fx["to_amount"] == 140 and fx["currency2"] == "USD"
    assert next(r for r in recs if r["type"] == "income")["amount"] == 1000  # "1,000.00"


def test_gbk_encoded_csv_is_read():
    p = parse_sui("export.csv", csv_bytes(encoding="gb18030"))
    assert p["sheets"][0]["rows"] == len(ROWS)


def test_camelcase_and_store_card_guesses():
    assert guess_account_type("FakeChecking") == "checking"
    assert guess_account_type("XYChecking") == "checking"
    assert guess_account_type("XYZSaving") == "savings"
    assert guess_account_type("Fake2Save") == "savings"
    assert guess_account_type("FakeStoreRedCard") == "credit_card"
    assert guess_account_type("Aspirate Fake") == "other_asset"  # 'ira' inside a word is not an IRA
    assert guess_account_type("Fake IRA") == "retirement"
    assert guess_account_type("401k_Fake") == "retirement"
    assert guess_account_type("Fake Debit Card") == "checking"
    # 负债变更 + spending -> credit card even when the name says nothing; 负债变更 alone -> payable
    assert guess_account_type_from_usage("FakeStore", Counter(liability_change=3, expense=10)) == "credit_card"
    assert guess_account_type_from_usage("FakePerson", Counter(liability_change=1)) == "payable"
    assert guess_account_type_from_usage("Fake House Loan", Counter(liability_change=5)) == "loan"


def test_preview_flags_and_commit_with_opening_balances(client):
    up = client.post("/api/import/sui/preview", files={"file": ("export.csv", csv_bytes(), "text/csv")}).json()
    assert up["type_counts"]["转账"] == 2
    acc = {a["name"]: a for a in up["accounts"]}
    assert acc["FakeStoreCard"]["guessed_type"] == "credit_card"
    assert acc["假的银行卡"]["currency"] == "CNY"
    # checking: 500 - 60 + 140 + 1000 - 2000 = -420 -> flagged (opening balance missing)
    assert acc["FakeChecking"]["net_from_file"] == -420 and acc["FakeChecking"]["flags"]
    # store card: -100 - 12.5 + 2.5 + 60 = -50 (owes 50): fine
    assert acc["FakeStoreCard"]["net_from_file"] == -50 and not acc["FakeStoreCard"]["flags"]

    amap = {a["name"]: {"type": a["guessed_type"], "currency": a["currency"]} for a in up["accounts"]}
    amap["FakeChecking"]["balance"] = "1,234.56"      # what 随手记 shows today
    amap["FakeStoreCard"]["balance"] = "80"           # amount owed, entered as a positive number
    r = client.post("/api/import/sui/commit", json={"token": up["token"], "options": {"account_map": amap}}).json()
    assert r["imported"] == len(ROWS) - 2 - 1  # two pairs merged, one zero row skipped
    assert r["skipped_count"] == 1 and r["skipped"][0]["reason"] == "zero amount"
    assert r["by_type"]["转账"] == 2 and r["by_type"]["负债变更"] == 1
    bal = {a["name"]: a for a in r["accounts"]}
    assert bal["FakeChecking"]["balance"] == 1234.56 and bal["FakeChecking"]["opening_balance"] == 1654.56
    assert bal["FakeStoreCard"]["balance"] == -80 and acct(client, "FakeStoreCard")["owed"] == 80
    assert bal["假的银行卡"]["balance"] == -1000 and bal["假的银行卡"]["currency"] == "CNY"
    # cross-currency transfer keeps both sides' amounts
    tx = client.get("/api/transactions", params={"kind": "transfer", "account_id": acct(client, "假的银行卡")["id"]}).json()["items"][0]
    assert tx["amount"] == 1000 and tx["to_amount"] == 140


def test_points_account_and_missing_fx(client):
    rows = ["余额变更,2020-01-01 09:00:00,,,,Fake Rewards,USD,30.00,,,,",
            "支出,2020-01-02 09:00:00,餐饮,午餐,,Fake Rewards,USD,10.00,,,,",
            "支出,2020-01-03 09:00:00,旅行,交通,,FakeYen,JPY,1500,,,,"]
    up = client.post("/api/import/sui/preview", files={"file": ("e.csv", csv_bytes(rows), "text/csv")}).json()
    acc = {a["name"]: a for a in up["accounts"]}
    assert acc["Fake Rewards"]["guessed_type"] == "other_asset"  # 余额变更 => asset, despite "Rewards" in the name
    assert up["missing_fx"] == ["JPY"]
    r = client.post("/api/import/sui/commit", json={"token": up["token"], "options": {}}).json()
    assert r["missing_fx"] == ["JPY"]
