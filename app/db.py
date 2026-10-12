"""SQLite storage: schema, connection helper and default seed data."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

ACCOUNT_TYPES = {
    # type: (label, default group, is_liability, is_investment)
    "cash": ("Cash 现金", "Cash 现金账户", False, False),
    "checking": ("Checking 活期储蓄卡", "Bank 金融账户", False, False),
    "savings": ("Savings 储蓄/定期", "Bank 金融账户", False, False),
    "credit_card": ("Credit card 信用卡", "Credit Cards 信用卡", True, False),
    "ewallet": ("E-wallet 虚拟账户 (Alipay/WeChat/PayPal)", "E-Wallets 虚拟账户", False, False),
    "brokerage": ("Brokerage / stocks 股票账户", "Investments 投资账户", False, True),
    "retirement": ("Retirement 401k/IRA 养老金", "Investments 投资账户", False, True),
    "crypto": ("Crypto 加密货币", "Investments 投资账户", False, True),
    "loan": ("Loan / mortgage 贷款", "Liabilities 负债", True, False),
    "payable": ("Other payable 应付款项", "Liabilities 负债", True, False),
    "receivable": ("Receivable 应收款项/借出", "Receivables 债权", False, False),
    "other_asset": ("Other asset 其他资产", "Other 其他", False, False),
}
# Reason for difference 差额原因 on a balance adjustment: (code, english, chinese)
ADJ_REASONS = [
    ("investment_gain", "Investment gain/loss", "投资收益/亏损"),
    ("interest", "Interest", "利息"),
    ("fees", "Fees", "费用"),
    ("unrecorded_spending", "Unrecorded spending", "未记支出"),
    ("unrecorded_income", "Unrecorded income", "未记收入"),
    ("correction", "Correction/other", "校正/其他"),
    ("opening", "Opening balance", "期初余额"),
]
DEFAULT_GROUPS = ["Cash 现金账户", "Bank 金融账户", "Credit Cards 信用卡", "E-Wallets 虚拟账户",
                  "Investments 投资账户", "Liabilities 负债", "Receivables 债权", "Other 其他"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS ledgers (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, note TEXT DEFAULT '', is_demo INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS account_groups (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY, group_id INTEGER REFERENCES account_groups(id), name TEXT NOT NULL,
  type TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'USD', opening_balance REAL NOT NULL DEFAULT 0,
  credit_limit REAL, institution TEXT DEFAULT '', last4 TEXT DEFAULT '', aliases TEXT DEFAULT '',
  note TEXT DEFAULT '', archived INTEGER DEFAULT 0, sort INTEGER DEFAULT 0, is_demo INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, parent_id INTEGER REFERENCES categories(id),
  name TEXT NOT NULL, name_zh TEXT DEFAULT '', keywords TEXT DEFAULT '', sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS members (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, is_demo INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS merchants (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, is_demo INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, archived INTEGER DEFAULT 0, is_demo INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY, ledger_id INTEGER REFERENCES ledgers(id), kind TEXT NOT NULL,
  date TEXT NOT NULL, amount REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'USD',
  account_id INTEGER REFERENCES accounts(id), to_account_id INTEGER REFERENCES accounts(id), to_amount REAL,
  orig_amount REAL, orig_currency TEXT,
  category_id INTEGER REFERENCES categories(id), merchant_id INTEGER REFERENCES merchants(id),
  member_id INTEGER REFERENCES members(id), project_id INTEGER REFERENCES projects(id),
  tags TEXT DEFAULT '', note TEXT DEFAULT '', source TEXT DEFAULT 'web', attachment TEXT,
  status TEXT NOT NULL DEFAULT 'posted', confidence REAL, raw_text TEXT, external_id TEXT,
  ticker TEXT, shares REAL, price REAL, fee REAL,
  is_demo INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now','localtime')), updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS ix_tx_acct ON transactions(account_id);
CREATE INDEX IF NOT EXISTS ix_tx_status ON transactions(status);
CREATE INDEX IF NOT EXISTS ix_tx_ext ON transactions(external_id);
CREATE TABLE IF NOT EXISTS budgets (
  id INTEGER PRIMARY KEY, ledger_id INTEGER, category_id INTEGER REFERENCES categories(id),
  month TEXT NOT NULL, amount REAL NOT NULL, is_demo INTEGER DEFAULT 0,
  UNIQUE(ledger_id, category_id, month));
CREATE TABLE IF NOT EXISTS prices (
  ticker TEXT PRIMARY KEY, price REAL NOT NULL, currency TEXT DEFAULT 'USD', source TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS fx_rates (currency TEXT PRIMARY KEY, rate REAL NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS mailboxes (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, port INTEGER DEFAULT 993,
  use_ssl INTEGER DEFAULT 1, username TEXT NOT NULL, password_enc TEXT, folder TEXT DEFAULT 'INBOX',
  interval_minutes INTEGER DEFAULT 15, enabled INTEGER DEFAULT 1, sender_allowlist TEXT DEFAULT '',
  subject_keywords TEXT DEFAULT '', after_action TEXT DEFAULT 'none', move_folder TEXT DEFAULT '',
  default_account_id INTEGER, auto_post INTEGER DEFAULT 0, lookback_days INTEGER DEFAULT 7,
  last_checked_at TEXT, last_status TEXT, created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS email_seen (
  message_id TEXT PRIMARY KEY, mailbox_id INTEGER, seen_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS ingest_log (
  id INTEGER PRIMARY KEY, time TEXT DEFAULT (datetime('now','localtime')), mailbox_id INTEGER, source TEXT,
  sender TEXT, subject TEXT, message_id TEXT, result TEXT, detail TEXT, rule_name TEXT,
  confidence REAL, transaction_id INTEGER);
CREATE TABLE IF NOT EXISTS email_rules (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, sender_pattern TEXT DEFAULT '', subject_pattern TEXT DEFAULT '',
  amount_regex TEXT DEFAULT '', merchant_regex TEXT DEFAULT '', last4_regex TEXT DEFAULT '',
  kind TEXT DEFAULT 'expense', currency TEXT DEFAULT '', enabled INTEGER DEFAULT 1,
  priority INTEGER DEFAULT 100, is_preset INTEGER DEFAULT 0, note TEXT DEFAULT '');
-- scheduled transactions 周期记账 (AUTOINCREMENT: ids are never reused, so kept history can't collide)
CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
  template TEXT NOT NULL, freq TEXT NOT NULL DEFAULT 'monthly', interval INTEGER NOT NULL DEFAULT 1,
  weekdays TEXT DEFAULT '', month_mode TEXT DEFAULT 'day', month_day INTEGER, nth INTEGER, nth_weekday INTEGER,
  start_date TEXT NOT NULL, end_date TEXT, max_count INTEGER, time_of_day TEXT NOT NULL DEFAULT '09:00',
  post_mode TEXT NOT NULL DEFAULT 'post', variable_amount INTEGER NOT NULL DEFAULT 0, skip_before TEXT,
  is_demo INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now','localtime')), updated_at TEXT);
CREATE TABLE IF NOT EXISTS schedule_occurrences (
  schedule_id INTEGER NOT NULL, occurrence_date TEXT NOT NULL, transaction_id INTEGER,
  created_at TEXT DEFAULT (datetime('now','localtime')), PRIMARY KEY (schedule_id, occurrence_date));
CREATE TABLE IF NOT EXISTS schedule_runs (
  id INTEGER PRIMARY KEY, time TEXT DEFAULT (datetime('now','localtime')), as_of TEXT, trigger TEXT,
  dry_run INTEGER DEFAULT 0, generated INTEGER DEFAULT 0, to_review INTEGER DEFAULT 0, skipped_old INTEGER DEFAULT 0,
  errors TEXT DEFAULT '[]', summary TEXT DEFAULT '');
-- account reconciliation 对账: a checkpoint anchors the account's balance at as_of
CREATE TABLE IF NOT EXISTS reconciliations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL REFERENCES accounts(id), as_of TEXT NOT NULL,
  balance REAL NOT NULL, cash_balance REAL NOT NULL, computed_at_creation REAL NOT NULL, note TEXT DEFAULT '',
  adjustment_tx_id INTEGER, cleared_count INTEGER DEFAULT 0, tx_count INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE INDEX IF NOT EXISTS ix_recon_acct ON reconciliations(account_id, as_of);
CREATE TABLE IF NOT EXISTS reconciled_tx (
  reconciliation_id INTEGER NOT NULL, transaction_id INTEGER NOT NULL, account_id INTEGER NOT NULL,
  cleared INTEGER DEFAULT 0, PRIMARY KEY (reconciliation_id, transaction_id));
CREATE INDEX IF NOT EXISTS ix_recontx_tx ON reconciled_tx(transaction_id);
CREATE TRIGGER IF NOT EXISTS trg_tx_del_recon AFTER DELETE ON transactions
  BEGIN DELETE FROM reconciled_tx WHERE transaction_id = OLD.id; END;
CREATE TRIGGER IF NOT EXISTS trg_acct_del_recon AFTER DELETE ON accounts
  BEGIN DELETE FROM reconciled_tx WHERE account_id = OLD.id; DELETE FROM reconciliations WHERE account_id = OLD.id; END;
"""

# (english, chinese, [(sub_en, sub_zh, keywords)])
EXPENSE_CATEGORIES = [
    ("Food & Dining", "食品酒水", [
        ("Meals", "早午晚餐", "lunch,dinner,breakfast,brunch,meal,restaurant,eat out,午饭,午餐,晚饭,晚餐,早饭,早餐,早点,吃饭,夜宵,聚餐"),
        ("Groceries", "买菜超市", "grocery,groceries,supermarket,costco,whole foods,trader joe,safeway,kroger,h mart,99 ranch,买菜,超市,菜市场"),
        ("Coffee & Drinks", "烟酒茶饮", "coffee,starbucks,tea,boba,latte,beer,wine,drinks,咖啡,奶茶,喝茶,饮料,酒,烟"),
        ("Snacks & Fruit", "水果零食", "snack,snacks,fruit,dessert,ice cream,零食,水果,甜品"),
        ("Takeout & Delivery", "外卖", "doordash,uber eats,ubereats,grubhub,takeout,delivery,外卖,美团,饿了么"),
    ]),
    ("Shopping", "购物消费", [
        ("Clothing", "衣服鞋帽", "clothes,clothing,shoes,shirt,jacket,uniqlo,nike,衣服,裤子,鞋,帽子,外套"),
        ("Household Supplies", "日用品", "household,toiletries,target,walmart,detergent,日用品,日用,纸巾"),
        ("Online Shopping", "网购", "amazon,taobao,tmall,ebay,online order,淘宝,天猫,京东,拼多多,网购"),
        ("Electronics", "数码电器", "electronics,apple store,best buy,laptop,iphone,数码,电器,手机"),
        ("Beauty", "化妆护肤", "cosmetics,sephora,skincare,haircut,salon,化妆品,护肤,理发"),
    ]),
    ("Housing", "居家物业", [
        ("Rent", "房租", "rent,房租,租金"),
        ("Mortgage", "房贷", "mortgage,房贷,月供"),
        ("Utilities", "水电煤气", "utilities,utility,electric,electricity,water bill,gas bill,pg&e,水费,电费,燃气费,水电"),
        ("Internet & Phone", "话费网费", "internet,phone bill,comcast,xfinity,verizon,t-mobile,at&t,wifi,话费,网费,宽带,充话费"),
        ("Furniture & Repairs", "家具维修", "ikea,furniture,repair,plumber,家具,维修,装修"),
        ("Property Fees", "物业费", "hoa,property fee,物业,物业费"),
    ]),
    ("Transportation", "行车交通", [
        ("Public Transit", "公共交通", "bus,metro,subway,train,bart,caltrain,clipper,muni,地铁,公交,火车,高铁"),
        ("Taxi & Rideshare", "打车", "uber,lyft,taxi,cab,打车,滴滴,出租车"),
        ("Fuel", "加油", "gas,fuel,gasoline,chevron,shell,arco,加油,油费"),
        ("Parking & Tolls", "停车过路", "parking,toll,fastrak,停车,过路费"),
        ("Car Maintenance", "汽车保养", "car wash,oil change,car repair,tires,保养,洗车,修车"),
        ("Flights", "机票", "flight,airline,airfare,united airlines,delta,机票,飞机"),
    ]),
    ("Entertainment", "休闲娱乐", [
        ("Movies & Shows", "电影演出", "movie,movies,cinema,concert,theater,电影,演出,演唱会"),
        ("Subscriptions", "订阅会员", "netflix,spotify,subscription,youtube premium,icloud,disney+,hulu,chatgpt,会员,订阅"),
        ("Sports & Fitness", "运动健身", "gym,fitness,yoga,climbing,健身,运动,瑜伽"),
        ("Travel & Hotels", "旅游度假", "hotel,airbnb,travel,trip,vacation,酒店,旅游,住宿"),
        ("Games & Hobbies", "游戏爱好", "game,games,steam,nintendo,playstation,游戏,爱好"),
    ]),
    ("Health", "医疗保健", [
        ("Doctor & Hospital", "看病", "doctor,hospital,clinic,copay,urgent care,医院,看病,挂号"),
        ("Pharmacy", "药品", "pharmacy,cvs,walgreens,medicine,药,药店"),
        ("Dental & Vision", "牙科眼科", "dentist,dental,optometrist,glasses,牙医,眼镜"),
    ]),
    ("Education", "学习进修", [
        ("Tuition", "学费", "tuition,学费"),
        ("Books", "书报杂志", "book,books,kindle,书,杂志"),
        ("Courses", "培训课程", "course,class,udemy,coursera,课程,培训"),
    ]),
    ("Social & Gifts", "人情往来", [
        ("Gifts", "送礼", "gift,present,礼物,送礼"),
        ("Red Packets", "红包礼金", "红包,礼金,hongbao,red envelope"),
        ("Donations", "慈善捐助", "donation,charity,捐款,捐赠"),
        ("Treating Others", "请客", "请客,treat friends"),
    ]),
    ("Finance & Insurance", "金融保险", [
        ("Insurance", "保险费", "insurance,premium,geico,保险"),
        ("Bank Fees", "银行手续费", "bank fee,atm fee,service fee,annual fee,late fee,手续费,年费"),
        ("Interest Paid", "利息支出", "interest charge,finance charge,loan interest,利息支出"),
        ("Taxes", "税费", "tax,taxes,irs,税,个税"),
    ]),
    ("Family", "家庭", [
        ("Childcare & Kids", "育儿", "daycare,childcare,babysitter,diapers,toys,托儿,奶粉,尿布,玩具"),
        ("Pets", "宠物", "pet,vet,petco,dog food,cat food,宠物,猫粮,狗粮"),
        ("Parents", "孝敬长辈", "孝敬,父母,爸妈"),
    ]),
    ("Other", "其他杂项", [
        ("Miscellaneous", "其他支出", "misc,miscellaneous,其他"),
        ("Loss", "意外丢失", "lost,丢失,罚款,fine"),
    ]),
]
INCOME_CATEGORIES = [
    ("Work Income", "职业收入", [
        ("Salary", "工资收入", "salary,paycheck,payroll,wage,wages,direct deposit,工资,薪水,发工资"),
        ("Bonus", "奖金收入", "bonus,奖金,年终奖"),
        ("Side Income", "兼职收入", "freelance,side gig,consulting,兼职,外快"),
        ("Reimbursement", "报销", "reimbursement,reimburse,expense report,报销"),
    ]),
    ("Investment Income", "投资收入", [
        ("Interest", "利息收入", "interest,interest earned,利息"),
        ("Dividends", "股息红利", "dividend,dividends,分红,股息"),
        ("Capital Gains", "投资收益", "capital gain,investment gain,理财收益,收益"),
    ]),
    ("Other Income", "其他收入", [
        ("Gifts Received", "礼金收入", "gift received,收红包,收礼金"),
        ("Cashback & Rewards", "返现奖励", "cashback,cash back,rebate,rewards,返现"),
        ("Windfall", "意外来钱", "lottery,prize,中奖"),
        ("Other Income", "其他收入", "income,收入,进账"),
    ]),
]


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    """Thin wrapper. One connection per call (sqlite is cheap); a lock serialises writers."""

    def __init__(self, path: str | os.PathLike):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    @contextmanager
    def tx(self):
        with self.lock:
            con = self.connect()
            try:
                yield con
                con.commit()
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()

    # convenience helpers
    def all(self, sql, params=()):
        con = self.connect()
        try:
            return [dict(r) for r in con.execute(sql, params).fetchall()]
        finally:
            con.close()

    def one(self, sql, params=()):
        con = self.connect()
        try:
            r = con.execute(sql, params).fetchone()
            return dict(r) if r else None
        finally:
            con.close()

    def get_setting(self, key, default=None):
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return r["value"] if r else default

    def set_setting(self, key, value):
        with self.tx() as con:
            con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, None if value is None else str(value)))

    def init(self):
        with self.tx() as con:
            con.executescript(SCHEMA)
            seeded = con.execute("SELECT value FROM settings WHERE key='seeded'").fetchone()
            if not seeded:
                seed_defaults(con)
                con.execute("INSERT INTO settings VALUES('seeded','1')")
            if not con.execute("SELECT 1 FROM settings WHERE key='api_key'").fetchone():
                con.execute("INSERT INTO settings VALUES('api_key',?)", ("fin_" + secrets.token_urlsafe(32),))
            if not con.execute("SELECT 1 FROM settings WHERE key='session_secret'").fetchone():
                con.execute("INSERT INTO settings VALUES('session_secret',?)", (secrets.token_hex(32),))
            # additive migration: system category for balance adjustments (kind 'adjustment', never in income/expense reports)
            if not con.execute("SELECT 1 FROM categories WHERE kind='adjustment'").fetchone():
                con.execute("INSERT INTO categories(kind,parent_id,name,name_zh,keywords,sort) "
                            "VALUES('adjustment',NULL,'Balance adjustment','余额调整','',0)")
            # additive migration: reasons for a balance difference (差额原因) as sub-categories of 'Balance adjustment',
            # identified by a stable `code` (new nullable column; existing rows are untouched apart from tagging the parent)
            cols = {r[1] for r in con.execute("PRAGMA table_info(categories)")}
            if "code" not in cols:
                con.execute("ALTER TABLE categories ADD COLUMN code TEXT")
            parent = con.execute("SELECT id, code FROM categories WHERE kind='adjustment' AND parent_id IS NULL ORDER BY id LIMIT 1").fetchone()
            if parent and parent[1] is None:
                con.execute("UPDATE categories SET code='adjustment' WHERE id=?", (parent[0],))
            if parent:
                for i, (code, en, zh) in enumerate(ADJ_REASONS):
                    if not con.execute("SELECT 1 FROM categories WHERE code=?", (code,)).fetchone():
                        con.execute("INSERT INTO categories(kind,parent_id,name,name_zh,keywords,sort,code) VALUES('adjustment',?,?,?,'',?,?)",
                                    (parent[0], en, zh, i + 1, code))
            # additive migration: generated transactions link back to their schedule
            tcols = {r[1] for r in con.execute("PRAGMA table_info(transactions)")}
            if "schedule_id" not in tcols:
                con.execute("ALTER TABLE transactions ADD COLUMN schedule_id INTEGER")
            con.execute("CREATE INDEX IF NOT EXISTS ix_tx_sched ON transactions(schedule_id)")
            # additive migration: principal/returns override per transaction (NULL = automatic) and the part of an
            # account's opening balance that is prior returns (NULL/0 = all principal)
            if "pr_class" not in tcols:
                con.execute("ALTER TABLE transactions ADD COLUMN pr_class TEXT")
            acols = {r[1] for r in con.execute("PRAGMA table_info(accounts)")}
            if "opening_returns" not in acols:
                con.execute("ALTER TABLE accounts ADD COLUMN opening_returns REAL")
            # cursor pagination / account history (additive indexes)
            con.execute("CREATE INDEX IF NOT EXISTS ix_tx_date_id ON transactions(date, id)")
            con.execute("CREATE INDEX IF NOT EXISTS ix_tx_acct_date ON transactions(account_id, date, id)")
            con.execute("CREATE INDEX IF NOT EXISTS ix_tx_to_acct_date ON transactions(to_account_id, date, id)")
            from .email_rules import PRESET_RULES, PRESET_VERSION
            ver = con.execute("SELECT value FROM settings WHERE key='preset_rules_version'").fetchone()
            if not ver or int(ver[0]) < PRESET_VERSION:
                con.execute("DELETE FROM email_rules WHERE is_preset=1")
                con.execute("INSERT OR REPLACE INTO settings VALUES('preset_rules_version',?)", (str(PRESET_VERSION),))
                for r in PRESET_RULES:
                    con.execute("""INSERT INTO email_rules(name,sender_pattern,subject_pattern,amount_regex,merchant_regex,
                        last4_regex,kind,currency,enabled,priority,is_preset,note) VALUES(?,?,?,?,?,?,?,?,1,?,1,?)""",
                                (r["name"], r["sender_pattern"], r.get("subject_pattern", ""), r["amount_regex"],
                                 r.get("merchant_regex", ""), r.get("last4_regex", ""), r.get("kind", "expense"),
                                 r.get("currency", ""), r.get("priority", 50), r.get("note", "")))


def seed_defaults(con: sqlite3.Connection):
    defaults = {
        "base_currency": "USD", "review_threshold": "0.75", "backup_interval_hours": "24",
        "backup_keep": "30", "default_account_id": "", "default_ledger_id": "1",
    }
    for k, v in defaults.items():
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    con.execute("INSERT INTO ledgers(id,name,note) VALUES(1,'Personal 个人账本','Default ledger')")
    for i, g in enumerate(DEFAULT_GROUPS):
        con.execute("INSERT OR IGNORE INTO account_groups(name,sort) VALUES(?,?)", (g, i))
    for kind, cats in (("expense", EXPENSE_CATEGORIES), ("income", INCOME_CATEGORIES)):
        for i, (en, zh, subs) in enumerate(cats):
            cur = con.execute("INSERT INTO categories(kind,parent_id,name,name_zh,sort) VALUES(?,?,?,?,?)",
                              (kind, None, en, zh, i))
            pid = cur.lastrowid
            for j, (sen, szh, kw) in enumerate(subs):
                con.execute("INSERT INTO categories(kind,parent_id,name,name_zh,keywords,sort) VALUES(?,?,?,?,?,?)",
                            (kind, pid, sen, szh, kw, j))
    for m in ("Me 本人", "Household 家庭公用"):
        con.execute("INSERT OR IGNORE INTO members(name) VALUES(?)", (m,))
    ts = now_iso()
    # Approximate rates; the user should edit them in Settings > Currencies.
    for cur_, rate in (("USD", 1.0), ("CNY", 0.14)):
        con.execute("INSERT OR IGNORE INTO fx_rates(currency,rate,updated_at) VALUES(?,?,?)", (cur_, rate, ts))


def row_list_json(rows):
    return json.loads(json.dumps(rows, default=str))
