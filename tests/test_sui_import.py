"""Suishouji (随手记) import. Column/sheet names follow the formats confirmed from ezbookkeeping's
Feidee importers (see docs/sui_reference.md). All data below is FAKE."""
import io
from pathlib import Path

import openpyxl

from app.importers import parse_sui, guess_account_type

FIX = Path(__file__).parent / "fixtures"
HDR = ["交易类型", "日期", "分类", "子分类", "账户1", "账户2", "账户币种", "金额", "成员", "商家", "项目分类", "项目", "记账人", "备注"]


def make_xlsx(sheets: dict) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(rows[0])
        for r in rows[1:]:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def sample_workbook() -> bytes:
    R = lambda *a: list(a)
    return make_xlsx({
        "支出": [HDR,
               R("支出", "2026-09-01 12:30:00", "食品酒水", "早午晚餐", "测试现金", None, "CNY", 35.5, "本人", "测试面馆", None, "测试项目", "测试用户", "午饭"),
               R("支出", "2026-09-02 08:00:00", "行车交通", "打车租车", "测试信用卡", None, "CNY", 22, None, None, None, None, "测试用户", None),
               R("支出", "2026-09-03 08:00:00", "食品酒水", "早午晚餐", "测试现金", None, "CNY", -5, None, None, None, None, "测试用户", "负数支出")],
        "收入": [HDR, R("收入", "2026-09-05 09:00:00", "职业收入", "工资收入", "测试储蓄卡", None, "CNY", 8000, None, None, None, None, "测试用户", "九月工资")],
        "转账": [HDR, R("转账", "2026-09-06 10:00:00", None, None, "测试储蓄卡", "测试信用卡", "CNY", 1000, None, None, None, None, "测试用户", "还信用卡")],
        "余额变更": [HDR, R("余额变更", "2026-09-01 00:00:00", None, None, "测试储蓄卡", None, "CNY", 2000, None, None, None, None, "测试用户", None)],
        "负债变更": [HDR, R("负债变更", "2026-09-01 00:00:00", None, None, "测试信用卡", None, "CNY", 300, None, None, None, None, "测试用户", None)],
        "借入": [HDR, R("借入", "2026-09-07 00:00:00", None, None, "测试借款人", "测试储蓄卡", "CNY", 500, None, None, None, None, "测试用户", None)],
        "未知": [HDR, R("神秘类型", "2026-09-08 00:00:00", None, None, "测试现金", None, "CNY", 1, None, None, None, None, "测试用户", None)],
    })


def acct(client, name):
    return next((a for a in client.get("/api/accounts").json()["accounts"] if a["name"] == name), None)


def test_parse_sheets_and_types():
    p = parse_sui("x.xlsx", sample_workbook())
    names = [s["name"] for s in p["sheets"]]
    assert names == ["支出", "收入", "转账", "余额变更", "负债变更", "借入", "未知"]
    assert all(not s.get("missing") for s in p["sheets"])
    types = [r["type"] for r in p["records"]]
    assert types.count("expense") == 3 and "transfer" in types and "balance" in types and "liability_change" in types and "borrow" in types
    assert any(r["type"] is None for r in p["records"])


def test_guess_types():
    assert guess_account_type("招商银行信用卡") == "credit_card"
    assert guess_account_type("支付宝") == "ewallet"
    assert guess_account_type("微信零钱") == "ewallet"
    assert guess_account_type("现金") == "cash"
    assert guess_account_type("工商银行储蓄卡") == "checking"
    assert guess_account_type("某某股票账户") == "brokerage"
    assert guess_account_type("房贷") == "loan"
    assert guess_account_type("借出-小明") == "receivable"


def test_preview_and_commit(client):
    up = client.post("/api/import/sui/preview", files={"file": ("sui.xlsx", sample_workbook(), "application/octet-stream")}).json()
    assert up["row_count"] == 9 and up["duplicates"] == 0 and up["unknown_types"] == ["神秘类型"]
    acc = {a["name"]: a for a in up["accounts"]}
    assert acc["测试信用卡"]["guessed_type"] == "credit_card" and acc["测试现金"]["guessed_type"] == "cash"
    assert acc["测试借款人"]["guessed_type"] == "payable"
    assert acc["测试储蓄卡"]["currency"] == "CNY"
    cats = {(c["category"], c["subcategory"]): c for c in up["categories"]}
    assert cats[("食品酒水", "早午晚餐")]["existing_category_id"]  # maps onto the seeded bilingual category
    assert "测试面馆" in up["merchants"] and "测试项目" in up["projects"]

    r = client.post("/api/import/sui/commit", json={"token": up["token"], "options": {"skip_duplicates": True}}).json()
    assert r["imported"] == 8
    assert r["skipped_count"] == 1 and "神秘类型" in r["skipped"][0]["reason"]
    assert r["by_type"]["支出"] == 3 and r["by_type"]["转账"] == 1
    assert r["totals"]["支出"] == 62.5
    assert {a["name"] for a in r["created_accounts"]} == {"测试现金", "测试信用卡", "测试储蓄卡", "测试借款人"}

    # balances: savings 2000 (余额变更) + 8000 - 1000 transfer + 500 borrowed = 9500
    assert acct(client, "测试储蓄卡")["balance"] == 9500
    # credit card: -300 (负债变更 = more debt) - 22 spend + 1000 payment = 678
    assert acct(client, "测试信用卡")["balance"] == 678
    # cash: -35.5 lunch, +5 (negative expense -> refund)
    assert acct(client, "测试现金")["balance"] == -30.5
    assert acct(client, "测试借款人")["is_liability"] and acct(client, "测试借款人")["balance"] == -500
    tx = client.get("/api/transactions", params={"q": "午饭"}).json()["items"][0]
    assert tx["merchant_name"] == "测试面馆" and tx["member_name"] == "本人" and tx["project_name"] == "测试项目"
    assert tx["category_label"].startswith("Food & Dining")

    # re-import: everything detected as duplicate
    up2 = client.post("/api/import/sui/preview", files={"file": ("sui.xlsx", sample_workbook(), "application/octet-stream")}).json()
    assert up2["duplicates"] == 8
    r2 = client.post("/api/import/sui/commit", json={"token": up2["token"], "options": {"skip_duplicates": True}}).json()
    assert r2["imported"] == 0 and r2["duplicates"] == 8


def test_account_and_category_mapping(client, accounts):
    up = client.post("/api/import/sui/preview", files={"file": ("sui.xlsx", sample_workbook(), "application/octet-stream")}).json()
    food_other = next(c for c in client.get("/api/categories").json() if c["name"] == "Other")["children"][0]["id"]
    opts = {"account_map": {"测试现金": {"account_id": accounts["cash"]["id"]}, "测试信用卡": {"type": "credit_card", "currency": "USD"}},
            "category_map": {"expense|行车交通|打车租车": food_other}}
    r = client.post("/api/import/sui/commit", json={"token": up["token"], "options": opts}).json()
    assert r["imported"] == 8
    assert "测试现金" not in {a["name"] for a in r["created_accounts"]}
    assert acct(client, "测试信用卡")["currency"] == "USD"
    taxi = client.get("/api/transactions", params={"account_id": acct(client, "测试信用卡")["id"], "kind": "expense"}).json()["items"][0]
    assert taxi["category_id"] == food_other


def test_app_csv_transfer_pairs(client):
    csv = ("随手记导出文件(headers:v5;fake)\n"
           '"交易类型","日期","类别","子类别","账户","账户币种","金额","备注","关联Id","成员","项目","商家"\n'
           '"支出","2026-09-01 12:00:00","食品酒水","早午晚餐","测试现金","CNY","20","","","","",""\n'
           '"转出","2026-09-02 10:00:00","","","测试储蓄卡","CNY","100","","fake-rel-1","","",""\n'
           '"转入","2026-09-02 10:00:00","","","测试现金","CNY","100","","fake-rel-1","","",""\n'
           '"转入","2026-09-03 10:00:00","","","测试现金","CNY","50","","fake-rel-2","","",""\n').encode()
    up = client.post("/api/import/sui/preview", files={"file": ("sui.csv", csv, "text/csv")}).json()
    r = client.post("/api/import/sui/commit", json={"token": up["token"], "options": {}}).json()
    assert r["imported"] == 2 and r["skipped_count"] == 1 and "关联Id" in r["skipped"][0]["reason"]
    assert acct(client, "测试现金")["balance"] == 80


def test_column_override_for_unrecognised_header(client):
    data = make_xlsx({"支出": [["交易类型", "记账日期X", "分类", "子分类", "账户1", "金额"],
                              ["支出", "2026-09-01", "食品酒水", "早午晚餐", "测试现金", 10]]})
    up = client.post("/api/import/sui/preview", files={"file": ("x.xlsx", data, "application/octet-stream")}).json()
    assert up["sheets"][0]["missing"] == ["date"] and up["row_count"] == 0
    up2 = client.post("/api/import/sui/preview", data={"token": up["token"], "overrides": '{"支出": {"date": "记账日期X"}}'}).json()
    assert up2["row_count"] == 1
    r = client.post("/api/import/sui/commit", json={"token": up["token"], "options": {"column_overrides": {"支出": {"date": "记账日期X"}}}}).json()
    assert r["imported"] == 1


def test_real_web_xls_samples(client):
    """Tiny placeholder .xls files from ezbookkeeping's test data (real sui.com web export structure)."""
    p = parse_sui("a.xls", (FIX / "ezbookkeeping_feidee_web_sample.xls").read_bytes())
    assert [s["name"] for s in p["sheets"]] == ["支出", "收入", "余额变更", "负债变更", "债权变更", "转账"]
    tr = next(r for r in p["records"] if r["type"] == "transfer")
    assert (tr["account"], tr["account2"], tr["amount"]) == ("Test Account", "Test Account2", 0.01)
    legacy = parse_sui("b.xls", (FIX / "ezbookkeeping_feidee_legacy_sample.xls").read_bytes())
    assert {r["type"] for r in legacy["records"]} == {"balance", "income", "expense", "transfer"}
    up = client.post("/api/import/sui/preview", files={"file": ("web.xls", (FIX / "ezbookkeeping_feidee_web_sample.xls").read_bytes(), "application/vnd.ms-excel")}).json()
    r = client.post("/api/import/sui/commit", json={"token": up["token"], "options": {}}).json()
    assert r["imported"] == up["row_count"] == 8
