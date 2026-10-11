from datetime import datetime

import pytest

from app.parser import parse_amount, parse_quick_add, normalize

NOW = datetime(2026, 10, 11, 9, 30)
ACCTS = [
    {"id": 1, "name": "Chase Sapphire Preferred", "aliases": "chase sapphire, csp", "last4": "4242", "type": "credit_card", "currency": "USD"},
    {"id": 2, "name": "Alipay", "aliases": "支付宝", "type": "ewallet", "currency": "CNY"},
    {"id": 3, "name": "Checking", "aliases": "chk", "type": "checking", "currency": "USD"},
    {"id": 4, "name": "Amex Gold", "aliases": "amex", "type": "credit_card", "currency": "USD"},
    {"id": 5, "name": "招商银行储蓄卡", "aliases": "招行", "type": "checking", "currency": "CNY"},
    {"id": 6, "name": "WeChat Pay", "aliases": "微信", "type": "ewallet", "currency": "CNY"},
]


@pytest.fixture(scope="module")
def ctx():
    from app.db import Database
    import tempfile, os
    d = Database(os.path.join(tempfile.mkdtemp(), "p.db"))
    d.init()
    return {"accounts": ACCTS, "categories": d.all("SELECT * FROM categories"), "merchants": [], "members": [],
            "projects": [], "default_account_id": None, "now": NOW, "_db": d}


def cat_name(ctx, cid):
    for c in ctx["categories"]:
        if c["id"] == cid:
            return c["name"]


def test_english_expense(ctx):
    r = parse_quick_add("lunch 25.5 chase sapphire", ctx)
    assert (r.kind, r.amount, r.account_id) == ("expense", 25.5, 1)
    assert cat_name(ctx, r.category_id) == "Meals"
    assert r.confidence >= 0.9


def test_chinese_expense(ctx):
    r = parse_quick_add("午饭 35 支付宝", ctx)
    assert (r.kind, r.amount, r.account_id) == ("expense", 35, 2)
    assert cat_name(ctx, r.category_id) == "Meals"
    assert r.confidence >= 0.9


def test_chinese_no_spaces(ctx):
    r = parse_quick_add("打车32元微信", ctx)
    assert (r.kind, r.amount, r.currency, r.account_id) == ("expense", 32, "CNY", 6)
    assert cat_name(ctx, r.category_id) == "Taxi & Rideshare"


def test_income(ctx):
    r = parse_quick_add("salary 5000 to checking", ctx)
    assert (r.kind, r.amount, r.account_id) == ("income", 5000, 3)
    assert cat_name(ctx, r.category_id) == "Salary"
    assert r.confidence >= 0.9


def test_chinese_income(ctx):
    r = parse_quick_add("发工资 12000 招行", ctx)
    assert (r.kind, r.amount, r.account_id) == ("income", 12000, 5)


def test_transfer_from_to(ctx):
    r = parse_quick_add("paid 300 from checking to amex", ctx)
    assert (r.kind, r.amount, r.account_id, r.to_account_id) == ("transfer", 300, 3, 4)
    assert r.confidence >= 0.9


def test_card_payment_without_from(ctx):
    r = parse_quick_add("pay amex 300 checking", ctx)
    assert (r.kind, r.account_id, r.to_account_id) == ("transfer", 3, 4)


def test_chinese_transfer(ctx):
    r = parse_quick_add("从招行转500到支付宝", ctx)
    assert (r.kind, r.amount, r.account_id, r.to_account_id) == ("transfer", 500, 5, 2)


def test_last4_and_currency_symbol(ctx):
    r = parse_quick_add("coffee $4.75 4242", ctx)
    assert (r.amount, r.currency, r.account_id) == (4.75, "USD", 1)
    assert cat_name(ctx, r.category_id) == "Coffee & Drinks"


def test_date_tags_merchant(ctx):
    r = parse_quick_add("dinner 42 amex yesterday #date-night at Nopa", ctx)
    assert r.date.startswith("2026-10-10")
    assert r.tags == ["date-night"]
    assert r.merchant == "Nopa"
    assert r.amount == 42


def test_explicit_date(ctx):
    r = parse_quick_add("groceries 2026-09-30 88.10 amex", ctx)
    assert r.date.startswith("2026-09-30") and r.amount == 88.1


def test_chinese_date(ctx):
    r = parse_quick_add("昨天 水果 18 微信", ctx)
    assert r.date.startswith("2026-10-10") and r.amount == 18


def test_refund(ctx):
    r = parse_quick_add("refund 20 amazon amex", ctx)
    assert (r.kind, r.amount, r.account_id) == ("refund", 20, 4)
    assert cat_name(ctx, r.category_id) == "Online Shopping"


def test_low_confidence_without_account(ctx):
    r = parse_quick_add("dinner 40", ctx)
    assert r.account_id is None and r.confidence < 0.75


def test_no_amount(ctx):
    r = parse_quick_add("lunch with chase sapphire", ctx)
    assert r.amount is None and r.confidence <= 0.3


def test_partial_name_match(ctx):
    r = parse_quick_add("taxi 18 sapphire", ctx)  # "sapphire" token of the account name
    assert r.account_id == 1


def test_amount_helpers():
    assert parse_amount("¥1,234.5")[0:2] == (1234.5, "CNY")
    assert parse_amount("2k rent")[0] == 2000
    assert parse_amount("房租 1.2万")[0] == 12000
    assert normalize("午饭　３５") == "午饭 35"


def test_merchant_does_not_swallow_account(ctx):
    r = parse_quick_add("groceries 86.20 at Whole Foods amex", ctx)
    assert r.merchant == "Whole Foods" and r.account_id == 4 and r.amount == 86.2
    r = parse_quick_add("lunch 12 at Sample Deli with csp #work", ctx)
    assert r.merchant == "Sample Deli" and r.account_id == 1


def test_member_name_brackets_ignored(ctx):
    c = dict(ctx, members=[{"id": 9, "name": "[DEMO] Partner"}])
    assert parse_quick_add("[DEMO] lunch 10 amex", c).member_id is None
    assert parse_quick_add("lunch 10 amex partner", c).member_id == 9
