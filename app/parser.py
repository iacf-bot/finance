"""Rule-based parser for quick-add text (English + 中文).

Examples:
    "lunch 25.5 chase sapphire"        -> expense 25.50, account Chase Sapphire, Food > Meals
    "午饭 35 支付宝"                     -> expense 35, account Alipay, 食品酒水 > 早午晚餐
    "salary 5000 to checking"          -> income 5000 into Checking, Work Income > Salary
    "paid 300 from checking to amex"   -> transfer 300 Checking -> Amex
    "从招行转500到支付宝"                 -> transfer
    "refund 20 amazon chase"           -> refund (money back into Chase)
    "coffee $4.5 yesterday #work"      -> expense dated yesterday with tag "work"
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any

CJK = re.compile(r"[\u3400-\u9fff]")

CURRENCY_WORDS = {
    "$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD", "bucks": "USD", "美元": "USD", "刀": "USD",
    "¥": "CNY", "￥": "CNY", "cny": "CNY", "rmb": "CNY", "元": "CNY", "块": "CNY", "块钱": "CNY", "人民币": "CNY",
    "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "欧元": "EUR",
    "£": "GBP", "gbp": "GBP", "hkd": "HKD", "港币": "HKD", "jpy": "JPY", "日元": "JPY",
}

TRANSFER_WORDS = ["transfer", "transferred", "move", "moved", "paid", "pay", "payment", "pay off", "payoff",
                  "top up", "topup", "withdraw", "withdrew", "deposit", "转账", "转", "还款", "还", "充值", "提现",
                  "存入", "取现", "取钱", "存钱"]
REFUND_WORDS = ["refund", "refunded", "return", "returned", "chargeback", "退款", "退货", "退钱", "退回"]
FROM_MARKERS = r"(?:from|out of|从|由)"
TO_MARKERS = r"(?:to|into|->|→|到|至|转入|进)"
GENERIC_TOKENS = {"card", "account", "bank", "credit", "debit", "the", "my", "a", "of", "and", "visa",
                  "mastercard", "卡", "账户", "银行", "信用卡"}


@dataclass
class ParseResult:
    text: str
    kind: str = "expense"
    amount: float | None = None
    currency: str | None = None
    account_id: int | None = None
    to_account_id: int | None = None
    category_id: int | None = None
    merchant: str | None = None
    member_id: int | None = None
    project_id: int | None = None
    project_unknown: str | None = None  # "#项目:X" naming no (active) project
    tags: list[str] = field(default_factory=list)
    date: str | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PROJECT_TAG_RX = re.compile(r'(?<!\S)#(?:项目|project|proj|p):(?:"([^"]+)"|([^\s#]+))', re.I)


def match_project(projects: list[dict], name: str) -> dict | None:
    """Active project by exact name, else a unique prefix, else a unique substring (case-insensitive; a leading
    "[DEMO]" is ignored). Ambiguous or unknown -> None."""
    n = (name or "").strip().lower()
    if not n:
        return None
    act = [(re.sub(r"^\[demo\]\s*", "", p["name"].lower()), p) for p in projects if not p.get("archived")]
    for ok in (lambda k, p: n in (k, p["name"].lower()), lambda k, p: k.startswith(n), lambda k, p: n in k):
        hits = [p for k, p in act if ok(k, p)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            return None  # ambiguous
    return None


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = t.replace("，", ",").replace("。", " ").replace("：", ":")
    return re.sub(r"\s+", " ", t).strip()


def _blank(text: str, start: int, end: int) -> str:
    return text[:start] + " " * (end - start) + text[end:]


def _term_regex(term: str) -> re.Pattern:
    term = term.strip().lower()
    esc = re.escape(term)
    if CJK.search(term):
        return re.compile(esc)
    return re.compile(r"(?<![a-z0-9])" + esc + r"(?![a-z0-9])")


def account_terms(acct: dict) -> list[tuple[str, float]]:
    """Return (term, strength) pairs used to recognise an account in text."""
    terms: list[tuple[str, float]] = []
    if acct.get("name"):
        terms.append((acct["name"], 1.0))
    for a in re.split(r"[,;，；\n]", acct.get("aliases") or ""):
        if a.strip():
            terms.append((a.strip(), 1.0))
    if acct.get("last4"):
        terms.append((str(acct["last4"]), 0.9))
    return terms


def find_accounts(text: str, accounts: list[dict]) -> list[dict]:
    """Find account mentions. Returns list of {account_id, start, end, strength} (non-overlapping)."""
    low = text.lower()
    cands = []
    for a in accounts:
        if a.get("archived"):
            continue
        for term, strength in account_terms(a):
            if len(term.strip()) < 2:
                continue
            for m in _term_regex(term).finditer(low):
                cands.append({"account_id": a["id"], "start": m.start(), "end": m.end(), "strength": strength,
                              "len": m.end() - m.start()})
    # longest-first, non-overlapping
    cands.sort(key=lambda c: (-c["len"], c["start"]))
    chosen: list[dict] = []
    for c in cands:
        if any(not (c["end"] <= o["start"] or c["start"] >= o["end"]) for o in chosen):
            continue
        if any(o["account_id"] == c["account_id"] for o in chosen):
            continue
        chosen.append(c)
    if not chosen:
        # fallback: significant tokens of account names (e.g. "sapphire" for "Chase Sapphire Preferred")
        words = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", low))
        best, best_score = [], 0
        for a in accounts:
            if a.get("archived"):
                continue
            toks = [t for t in re.findall(r"[a-z0-9]+", (a.get("name") or "").lower())
                    if t not in GENERIC_TOKENS and len(t) > 2]
            score = sum(1 for t in toks if t in words)
            if score > best_score:
                best, best_score = [a], score
            elif score == best_score and score > 0:
                best.append(a)
        if best_score > 0 and len(best) == 1:
            a = best[0]
            toks = [t for t in re.findall(r"[a-z0-9]+", (a.get("name") or "").lower())
                    if t not in GENERIC_TOKENS and len(t) > 2]
            for t in toks:
                m = _term_regex(t).search(low)
                if m:
                    chosen.append({"account_id": a["id"], "start": m.start(), "end": m.end(), "strength": 0.6,
                                   "len": m.end() - m.start()})
                    break
    chosen.sort(key=lambda c: c["start"])
    return chosen


DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b"), "ymd"),
    (re.compile(r"(\d{1,2})月(\d{1,2})[日号]?"), "md"),
    (re.compile(r"(?<![\d.$¥])\b(\d{1,2})/(\d{1,2})\b(?![./]\d)"), "md"),
]
REL_DATES = [("day before yesterday", -2), ("前天", -2), ("yesterday", -1), ("昨天", -1), ("昨晚", -1),
             ("today", 0), ("今天", 0), ("tonight", 0), ("今晚", 0)]


def parse_date(text: str, now: datetime) -> tuple[str | None, str]:
    low = text.lower()
    for word, delta in REL_DATES:
        i = low.find(word)
        if i >= 0:
            d = now + timedelta(days=delta)
            return d.strftime("%Y-%m-%d %H:%M") if delta == 0 else d.strftime("%Y-%m-%d 12:00"), _blank(text, i, i + len(word))
    for rx, kind in DATE_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        try:
            if kind == "ymd":
                d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, 0)
            else:
                d = datetime(now.year, int(m.group(1)), int(m.group(2)), 12, 0)
                if d > now + timedelta(days=1):
                    d = d.replace(year=now.year - 1)
        except ValueError:
            continue
        return d.strftime("%Y-%m-%d %H:%M"), _blank(text, m.start(), m.end())
    return None, text


AMOUNT_RX = re.compile(
    r"(?P<pre>[$¥￥€£]|usd|cny|rmb|eur|hkd)?\s*"
    r"(?P<num>(?<![A-Za-z0-9.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![A-Za-z0-9.])\d+(?:\.\d+)?)"
    r"\s*(?P<mult>k|w|万|千)?(?![A-Za-z0-9])\s*"
    r"(?P<post>块钱|块|元|人民币|美元|刀|欧元|港币|日元|usd|cny|rmb|eur|hkd|jpy|dollars?|bucks|euros?)?",
    re.IGNORECASE)


def parse_amount(text: str) -> tuple[float | None, str | None, tuple[int, int] | None, int]:
    matches = [m for m in AMOUNT_RX.finditer(text) if m.group("num")]
    if not matches:
        return None, None, None, 0
    # prefer a match with an explicit currency marker or a decimal point
    def score(m):
        return (1 if (m.group("pre") or m.group("post")) else 0) + (0.5 if "." in m.group("num") else 0)
    best = max(matches, key=lambda m: (score(m), -m.start()))
    val = float(best.group("num").replace(",", ""))
    mult = (best.group("mult") or "").lower()
    val *= {"k": 1000, "千": 1000, "w": 10000, "万": 10000}.get(mult, 1)
    cur = None
    for tok in (best.group("pre"), best.group("post")):
        if tok:
            cur = CURRENCY_WORDS.get(tok.lower()) or cur
    return round(val, 2), cur, (best.start(), best.end()), len(matches)


def _keywords(cat: dict) -> list[str]:
    kws = [k.strip().lower() for k in re.split(r"[,;，；\n]", cat.get("keywords") or "") if k.strip()]
    for nm in (cat.get("name"), cat.get("name_zh")):
        if nm and len(nm) >= 2:
            kws.append(nm.lower())
    return kws


def categorize(text: str, categories: list[dict], kind: str | None = None) -> tuple[int | None, str | None, str | None]:
    """Find best category by longest keyword match. Returns (category_id, category_kind, keyword)."""
    low = text.lower()
    best = (None, None, None, 0)
    for c in categories:
        if c.get("parent_id") is None:
            continue  # match leaf (second-level) categories only
        if kind and c["kind"] != kind:
            continue
        for kw in _keywords(c):
            if len(kw) > best[3] and _term_regex(kw).search(low):
                best = (c["id"], c["kind"], kw, len(kw))
    return best[0], best[1], best[2]


def _has_word(low: str, words: list[str]) -> str | None:
    for w in sorted(words, key=len, reverse=True):
        if _term_regex(w).search(low):
            return w
    return None


def parse_quick_add(text: str, ctx: dict) -> ParseResult:
    """ctx keys: accounts, categories, merchants, members, projects, default_account_id, now."""
    now: datetime = ctx.get("now") or datetime.now()
    accounts = ctx.get("accounts") or []
    t = normalize(text)
    res = ParseResult(text=t)

    # project: "#项目:Name" / "#project:Name" / "#p:Name" (quotes allow spaces: #p:"Japan trip")
    pm = PROJECT_TAG_RX.search(t)
    if pm:
        want = (pm.group(1) or pm.group(2) or "").strip()
        t = (t[:pm.start()] + t[pm.end():]).strip()
        res.text = t
        hit = match_project(ctx.get("projects") or [], want)
        if hit:
            res.project_id = hit["id"]
            res.reasons.append(f"project #{hit['name']}")
        else:
            res.project_unknown = want
    # tags: #word
    for m in re.finditer(r"#([\w\u3400-\u9fff-]+)", t):
        res.tags.append(m.group(1))
    work = re.sub(r"#[\w\u3400-\u9fff-]+", lambda m: " " * len(m.group(0)), t)

    # date
    res.date, work = parse_date(work, now)
    if not res.date:
        res.date = now.strftime("%Y-%m-%d %H:%M")

    # accounts (blank them out so digits in names/last4 aren't taken as amounts)
    acct_hits = find_accounts(work, accounts)
    low_full = work.lower()
    for h in acct_hits:
        work = _blank(work, h["start"], h["end"])

    # amount
    amount, cur, span, n_amounts = parse_amount(work)
    if amount is not None:
        res.amount, res.currency = amount, cur
        work = _blank(work, *span)
        res.confidence += 0.4
        res.reasons.append(f"amount {amount}" + (f" {cur}" if cur else ""))
        if n_amounts > 1:
            res.confidence -= 0.1
            res.reasons.append("several numbers found; picked the most likely amount")
    else:
        res.reasons.append("no amount found")

    low = work.lower()
    # roles of accounts from/to markers
    src = dst = None
    for h in acct_hits:
        before = low_full[max(0, h["start"] - 8):h["start"]]
        if re.search(TO_MARKERS + r"\s*$", before):
            dst = dst or h
        elif re.search(FROM_MARKERS + r"\s*$", before):
            src = src or h
    transfer_word = _has_word(low_full, TRANSFER_WORDS)
    refund_word = _has_word(low_full, REFUND_WORDS)
    distinct = list({h["account_id"]: h for h in acct_hits}.values())

    if len(distinct) >= 2 and (transfer_word or src or dst):
        res.kind = "transfer"
        if src and dst:
            a, b = src, dst
        elif dst:
            a = next(h for h in distinct if h is not dst); b = dst
        elif src:
            a = src; b = next(h for h in distinct if h is not src)
        else:
            a, b = distinct[0], distinct[1]
            # "pay amex from checking" -> handled above; "pay amex 300 checking": paying the card
            if transfer_word in ("pay", "paid", "payment", "pay off", "payoff", "还款", "还"):
                a_acc = next((x for x in accounts if x["id"] == a["account_id"]), {})
                if a_acc.get("type") in ("credit_card", "loan", "payable"):
                    a, b = b, a
        res.account_id, res.to_account_id = a["account_id"], b["account_id"]
        res.confidence += 0.3 * min(a["strength"], b["strength"]) + 0.25
        res.reasons.append("transfer between two accounts")
    else:
        if refund_word:
            res.kind = "refund"
        cat_id, cat_kind, kw = categorize(low, ctx.get("categories") or [],
                                          kind="expense" if res.kind == "refund" else None)
        if cat_id:
            res.category_id = cat_id
            if res.kind != "refund":
                res.kind = "income" if cat_kind == "income" else "expense"
            res.confidence += 0.25
            res.reasons.append(f"category by keyword '{kw}'")
        else:
            res.reasons.append("no category keyword matched")
        if distinct:
            h = dst or src or distinct[0]
            res.account_id = h["account_id"]
            res.confidence += 0.3 * h["strength"]
            res.reasons.append("account matched by name/alias")
            if len(distinct) > 1:
                res.confidence -= 0.1
                res.reasons.append("several accounts mentioned")
        elif ctx.get("default_account_id"):
            res.account_id = int(ctx["default_account_id"])
            res.confidence += 0.1
            res.reasons.append("no account mentioned; used default account")
        else:
            res.reasons.append("no account found")

    if res.amount is not None and n_amounts == 1:
        res.confidence += 0.05

    # merchant: "at X" / "@X" / known merchant names
    m = re.search(r"(?:\bat\b|@|在)\s*([A-Za-z0-9&'\u3400-\u9fff][\w&' .\u3400-\u9fff-]{1,40}?)(?=\s+(?:with|on|from|to|using|for|via)\b|\s{2,}|[,#]|\s*$)", work, re.I)
    if m and not re.fullmatch(r"\d+(?:\.\d+)?", m.group(1).strip()):
        # `work` has accounts/amount/date blanked out (same length), so slice the original text for its casing
        res.merchant = t[m.start(1):m.end(1)].strip() if len(work) == len(t) else m.group(1).strip()
    else:
        for mer in sorted(ctx.get("merchants") or [], key=lambda x: -len(x["name"])):
            if len(mer["name"]) >= 2 and _term_regex(mer["name"]).search(t.lower()):
                res.merchant = mer["name"]
                break
    for mem in ctx.get("members") or []:
        for nm in re.split(r"\s+", re.sub(r"\[[^\]]*\]", " ", mem["name"])):
            if len(nm) >= 2 and nm.lower() not in ("me", "household") and _term_regex(nm).search(t.lower()):
                res.member_id = mem["id"]
    if res.project_id is None and not pm:
        for p in ctx.get("projects") or []:
            if not p.get("archived") and len(p["name"]) >= 2 and _term_regex(p["name"]).search(t.lower()):
                res.project_id = p["id"]

    if res.amount is None:
        res.confidence = min(res.confidence, 0.3)
    if res.account_id is None:
        res.confidence = min(res.confidence, 0.5)
    res.confidence = round(max(0.0, min(1.0, res.confidence)), 2)
    return res
