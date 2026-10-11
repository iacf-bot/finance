"""Email alert parsing. All sample alerts below are FAKE, written for these tests
(fake merchants, fake card endings, example.test style senders where possible)."""
import pytest

from app.email_rules import PRESET_RULES, parse_email, strip_html

RULES = [dict(r, id=i, enabled=1) for i, r in enumerate(PRESET_RULES, start=1)]

SAMPLES = [
    # (from, subject, body, amount, merchant, last4, kind, rule)
    ("Chase <no.reply.alerts@chase.com>", "Your $23.45 transaction with FAKE BAKERY",
     "You made a $23.45 transaction with FAKE BAKERY on your Chase Freedom card ending in 1111.", 23.45, "FAKE BAKERY", "1111", "expense", "Chase"),
    ("American Express <AmericanExpress@welcome.americanexpress.com>", "Large Purchase Approved",
     "Merchant: FAKE ELECTRONICS STORE\nAmount: $1,299.00\nCard Ending: 21005\nDate: Oct 10, 2026", 1299.0, "FAKE ELECTRONICS STORE", "21005", "expense", "American Express"),
    ("Bank of America <onlinebanking@ealerts.bankofamerica.com>", "Credit card transaction exceeds alert limit",
     "<html><body><p>Credit card ending in 3333</p><p>Amount: $64.10</p><p>Where: FAKE GAS STATION 42</p></body></html>", 64.10, "FAKE GAS STATION 42", "3333", "expense", "Bank of America"),
    ("Citi Alerts <alerts@info6.citi.com>", "A purchase was made on your account",
     "A $15.00 transaction was made at FAKE CINEMA on card ending in 4444.", 15.0, "FAKE CINEMA", "4444", "expense", "Citi"),
    ("Capital One <capitalone@notification.capitalone.com>", "A new transaction was charged to your account",
     "As requested, we're notifying you that on October 10, 2026, at FAKE TACO SHOP, a pending authorization or purchase in the amount of $9.87 was placed or charged on your Capital One Quicksilver card ending in 5555.",
     9.87, "FAKE TACO SHOP", "5555", "expense", "Capital One"),
    ("Discover Card <discover@services.discover.com>", "Transaction Alert",
     "Transaction Date: October 10, 2026\nMerchant: FAKE BOOKSHOP\nAmount: $31.20\nLast 4 #: 6666", 31.20, "FAKE BOOKSHOP", "6666", "expense", "Discover"),
    ("支付宝 <service@mail.alipay.com>", "付款成功提醒",
     "您于2026年10月10日成功付款。付款金额：￥45.50 商户名称：测试咖啡店 交易号：0000FAKE", 45.50, "测试咖啡店", None, "expense", "Alipay 支付宝"),
    ("招商银行信用卡 <ccsvc@message.cmbchina.com>", "交易提醒",
     "您尾号7777的信用卡10月10日12:30在【测试超市】消费人民币128.60元。", 128.60, "测试超市", "7777", "expense", "China bank card 银行卡动账 (招行/工行/建行等)"),
    ("Chase <no.reply.alerts@chase.com>", "You have a direct deposit",
     "A direct deposit of $2,500.00 was credited to your account ending in 8888.", 2500.0, None, "8888", "income", "Chase"),
    ("American Express <AmericanExpress@welcome.americanexpress.com>", "Refund processed",
     "A refund of $42.99 from FAKE RETURNS INC was credited to your Card Ending: 21005", 42.99, None, "21005", "refund", "American Express"),
]


@pytest.mark.parametrize("sender,subject,body,amount,merchant,last4,kind,rule", SAMPLES)
def test_preset_rules(sender, subject, body, amount, merchant, last4, kind, rule):
    r = parse_email(sender, subject, body, RULES)
    assert r["rule_name"] == rule
    assert r["amount"] == amount
    if merchant:
        assert r["merchant"] == merchant
    if last4:
        assert r["last4"] == last4
    assert r["kind"] == kind


def test_generic_fallback_unknown_sender():
    r = parse_email("alerts@unknown-bank.example", "Purchase alert",
                    "A purchase of $12.00 at FAKE DELI was made with your card ending in 2468.", RULES)
    assert r["rule_name"] is None and r["amount"] == 12.0 and r["last4"] == "2468"


def test_custom_rule_wins_by_priority():
    custom = {"id": 99, "name": "My credit union", "sender_pattern": r"fakecu\.example", "amount_regex": r"Total:\s*USD\s*([\d.]+)",
              "merchant_regex": r"Vendor=(\w+)", "last4_regex": r"acct \*(\d{4})", "kind": "expense", "priority": 1, "enabled": 1}
    r = parse_email("alerts@fakecu.example", "x", "Vendor=FAKEMART Total: USD 77.70 acct *1357", RULES + [custom])
    assert (r["rule_name"], r["amount"], r["merchant"], r["last4"]) == ("My credit union", 77.7, "FAKEMART", "1357")


def test_card_payment_detected():
    r = parse_email("no.reply.alerts@chase.com", "Thank you for your payment",
                    "Thank you for your payment of $500.00 to your card ending in 1111.", RULES)
    assert r["kind"] == "payment" and r["amount"] == 500


def test_strip_html():
    assert strip_html("<p>A&amp;B</p><br>C") == "A&B\nC"


def test_rule_test_endpoint(client):
    r = client.post("/api/email-rules/test", json={"from": "no.reply.alerts@chase.com", "subject": "",
                                                   "body": "You made a $5.00 transaction with FAKE CAFE on card ending in 0000."}).json()
    assert r["amount"] == 5 and r["rule_name"] == "Chase"


def test_ingest_cn_alert_matches_last4(client, accounts):
    client.put(f"/api/accounts/{accounts['alipay']['id']}", json={"last4": "7777"})
    r = client.post("/api/ingest/email", json={"from": "ccsvc@message.cmbchina.com", "subject": "交易提醒",
                                               "body": "您尾号7777的信用卡10月10日在【测试超市】消费人民币128.60元。"}).json()
    assert r["created"] and r["transaction"]["account_id"] == accounts["alipay"]["id"]
    assert r["transaction"]["amount"] == 128.6 and r["transaction"]["currency"] == "CNY"
    assert r["transaction"]["category_label"].startswith("Food & Dining")  # 超市 keyword
