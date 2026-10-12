"""Principal 本金投入 vs returns 投资收益 for investment-type accounts.

For one account (amounts in the account's currency):
* principal  = opening balance (minus the part marked as prior returns, accounts.opening_returns)
               + transfers in − transfers out (also between two investment accounts: principal moves with the money)
               + income deposited (payroll contributions; employer match counts as a contribution, labelled)
               + refunds − expenses/withdrawals
               + balance adjustments with reason Opening balance / Unrecorded income / Unrecorded spending
* returns    = prior returns in the opening balance (accounts.opening_returns)
               + adjustments with reason Investment gain/loss, Interest, Fees (negative)
               + dividends − investment fees + income whose category is interest/dividend/investment income
               + trade gains on holdings (realized + unrealized) = holdings market value − buys + sells
* unexplained 未分类 = unclassified balance adjustments (Correction/other or no reason, e.g. imported 余额变更) and
               transactions overridden as 'unexplained'; plus `reconciliation_diff` (value gaps from reconciliation
               checkpoints / anything else not explained by the ledger) as a separate subfield
* value      = the account balance (cash anchored at reconciliation checkpoints + holdings at market value)
* value = principal + returns + unexplained, where unexplained = unclassified + reconciliation_diff
Any transaction can be overridden with transactions.pr_class = 'principal' | 'returns' | 'unexplained'
(NULL = automatic; 'other' is accepted as an older name for 'unexplained').
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta

from .db import Database
from .ledger import GAIN_REASONS, REASON_SQL, convert, fx_rates, is_investment, tx_effect_for

PR_CLASSES = ("principal", "returns", "unexplained", "other")
RETURN_INCOME_RX = re.compile(r"interest|dividend|利息|股息|分红|红利|投资收益|理财收益|capital gain", re.I)
MATCH_RX = re.compile(r"employer|match|雇主|匹配|公司配", re.I)
LABELS = {
    "contribution": "Contribution 投入", "employer_match": "Employer match 雇主匹配 (contribution)",
    "withdrawal": "Withdrawal 取出", "transfer_in": "Transfer in 转入", "transfer_out": "Transfer out 转出",
    "opening": "Opening balance 期初", "refund": "Refund 退款", "unrecorded": "Unrecorded flow 未记收支",
    "gain": "Gain/loss 投资收益", "interest": "Interest 利息", "fees": "Fees 费用", "dividend": "Dividend 股息",
    "invest_fee": "Investment fee 投资费用", "income_return": "Investment income 投资收入",
    "correction": "Unclassified balance adj. 未分类余额调整", "trade": "Trade 交易", "override": "Set manually 手动",
}
BADGE = {"principal": "本金", "returns": "收益", "unexplained": "未分类", "trade": "交易"}


def _rows(db: Database, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    return db.all(f"""SELECT t.id, t.date, t.kind, t.amount, t.to_amount, t.account_id, t.to_account_id, t.source,
        t.note, t.pr_class, {REASON_SQL} reason, c.name cat_name, c.name_zh cat_zh, m.name merchant
        FROM transactions t LEFT JOIN categories c ON c.id=t.category_id LEFT JOIN merchants m ON m.id=t.merchant_id
        WHERE t.status='posted' AND (t.account_id IN ({q}) OR t.to_account_id IN ({q})) ORDER BY t.date, t.id""", ids + ids)


def classify(r: dict, aid: int, inv_ids: set | None = None) -> tuple[str, str]:
    """(class, sub-code) of transaction r for account aid. class: principal | returns | other | trade."""
    k = r["kind"]
    if k in ("buy", "sell"):
        return "trade", "trade"
    if r.get("pr_class") in PR_CLASSES:
        return ("unexplained" if r["pr_class"] == "other" else r["pr_class"]), "override"
    if k == "transfer":
        return "principal", "transfer_in" if r.get("to_account_id") == aid and r.get("account_id") != aid else "transfer_out"
    if k == "dividend":
        return "returns", "dividend"
    if k == "invest_fee":
        return "returns", "invest_fee"
    if k == "income":
        text = " ".join(str(r.get(x) or "") for x in ("cat_name", "cat_zh", "note", "merchant"))
        if RETURN_INCOME_RX.search(text):
            return "returns", "income_return"
        return "principal", "employer_match" if MATCH_RX.search(text) else "contribution"
    if k in ("refund", "reimbursement"):
        return "principal", "refund"
    if k == "expense":
        return "principal", "withdrawal"
    if k == "adjustment":
        reason = r.get("reason")
        if reason in GAIN_REASONS:
            return "returns", {"investment_gain": "gain"}.get(reason, reason)
        if reason == "opening":
            return "principal", "opening"
        if reason in ("unrecorded_spending", "unrecorded_income"):
            return "principal", "unrecorded"
        return "unexplained", "correction"  # Correction/other or no reason (e.g. imported 余额变更): own category
    return "unexplained", "correction"


def walk(db: Database, acct: dict, rows: list[dict] | None = None) -> dict:
    """Cumulative principal/returns of one account. Returns totals plus per-transaction info
    {tx_id: (class, sub, effect, running_principal, running_returns)} (running = transaction-based, without the
    unrealized market change of holdings) and the ordered sequence for charts."""
    aid = acct["id"]
    rows = rows if rows is not None else _rows(db, [aid])
    legacy_open = float(acct.get("opening_balance") or 0)
    open_ret = float(acct.get("opening_returns") or 0)
    has_open_row = any(r["kind"] == "adjustment" and r.get("reason") == "opening" and r["account_id"] == aid
                       and r.get("pr_class") in (None, "", "principal") for r in rows)
    p, ret = legacy_open, 0.0
    if not has_open_row:  # the split applies to the legacy opening balance right away
        p, ret = p - open_ret, ret + open_ret
    shifted = not has_open_row
    p0, r0, shift_date = p, ret, None
    trade_net = other = 0.0
    info, seq = {}, []
    for r in rows:
        e = tx_effect_for(r, aid)
        cls, sub = classify(r, aid)
        if cls == "principal":
            p += e
            if sub == "opening" and not shifted:
                p, ret, shifted, shift_date = p - open_ret, ret + open_ret, True, r["date"]
        elif cls == "returns":
            ret += e
        elif cls == "trade":
            trade_net += e  # sells (+) − buys (−)
        else:
            other += e
        info[r["id"]] = (cls, sub, round(e, 2) + 0.0, round(p, 2) + 0.0, round(ret, 2) + 0.0, round(other, 2) + 0.0)
        seq.append((r["date"], p, ret, trade_net, other))
    return {"p0": p0, "r0": r0, "shift_date": shift_date, "principal_tx": p, "returns_tx": ret, "trade_net": trade_net, "other": other, "info": info, "seq": seq,
            "opening_total": legacy_open + sum(info[r["id"]][2] for r in rows if info[r["id"]][1] == "opening"),
            "opening_returns": open_ret}


def summary(acct: dict, w: dict) -> dict:
    """Current figures for an account (account currency). acct needs balance and holdings_value."""
    market = float(acct.get("holdings_value") or 0) + w["trade_net"]
    principal = w["principal_tx"]
    rets = w["returns_tx"] + market
    value = float(acct.get("balance") or 0)
    unexplained = value - principal - rets
    R = lambda v: round(v, 2) + 0.0
    recon = unexplained - w["other"]
    return {"currency": acct["currency"], "value": R(value), "principal": R(principal), "returns": R(rets),
            "returns_pct": round(rets / principal * 100, 2) if principal > 0.005 else None,
            "trade_gains": R(market), "unexplained": R(unexplained), "unclassified": R(w["other"]),
            "reconciliation_diff": R(recon) if abs(recon) >= 0.005 else 0.0,
            "opening_total": R(w["opening_total"]), "opening_returns": R(w["opening_returns"])}


def correction_counts(db: Database, ids: list[int]) -> dict[int, int]:
    """Unclassified (Correction/other) balance adjustments per account (counted as returns automatically)."""
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    return {r["account_id"]: r["n"] for r in db.all(f"""SELECT t.account_id, COUNT(*) n FROM transactions t
        LEFT JOIN categories c ON c.id=t.category_id WHERE t.kind='adjustment' AND t.status='posted'
        AND (t.pr_class IS NULL OR t.pr_class='') AND t.account_id IN ({q}) AND ({REASON_SQL})='correction'
        GROUP BY t.account_id""", ids)}


def annotate_accounts(db: Database, accts: list[dict]) -> None:
    """Adds `invest` = summary(...) (+ unclassified correction count) to each investment-type account, in place."""
    inv = [a for a in accts if is_investment(a["type"])]
    if not inv:
        return
    rows_by: dict[int, list] = defaultdict(list)
    ids = [a["id"] for a in inv]
    for r in _rows(db, ids):
        for x in {r["account_id"], r["to_account_id"]}:
            if x in rows_by or x in ids:
                rows_by[x].append(r)
    opening_ret = {r["id"]: r["opening_returns"] for r in db.all(
        f"SELECT id, opening_returns FROM accounts WHERE id IN ({','.join('?' * len(ids))})", ids)}
    cc = correction_counts(db, ids)
    for a in inv:
        a.setdefault("opening_returns", opening_ret.get(a["id"]))
        a["invest"] = {**summary(a, walk(db, a, rows_by.get(a["id"], []))), "correction_count": cc.get(a["id"], 0)}


def group_totals(members: list[dict], base: str, rates: dict) -> dict | None:
    inv = [a for a in members if a.get("invest")]
    if not inv:
        return None
    s = defaultdict(float)
    for a in inv:
        for k in ("value", "principal", "returns", "unexplained", "unclassified", "reconciliation_diff"):
            s[k] += convert(a["invest"][k], a["currency"], base, rates)
    p, r = s["principal"], s["returns"]
    return {"currency": base, **{k: round(v, 2) + 0.0 for k, v in s.items()},
            "returns_pct": round(r / p * 100, 2) if p > 0.005 else None, "accounts": len(inv),
            "correction_count": sum(a["invest"].get("correction_count", 0) for a in inv)}


def period_figures(db: Database, acct: dict, w: dict, start: str | None, end: str | None, today: date | None = None) -> dict:
    """Contributions / returns / Modified Dietz return for [start, end] (dates, inclusive; None = all / today)."""
    from . import returns as rt
    today = today or date.today()
    aid = acct["id"]
    first = rt.first_activity(db, [aid])
    s = rt._d(start) if start else (rt._d(first) if first else today)
    e = rt._d(end) if end else today
    if s > e:
        s = e
    c = rt.compute(db, [aid], s, e, acct["currency"], today=today)
    lo, hi = s.isoformat(), e.isoformat() + " 99"
    contrib = withdraw = ret_tx = other = 0.0
    flows = []
    T = (e - s).days + 1
    for (d, *_), (tid, inf) in zip(w["seq"], w["info"].items()):
        if not (lo <= d <= hi):
            continue
        cls, sub, eff = inf[0], inf[1], inf[2]
        if cls == "principal":
            if eff >= 0:
                contrib += eff
            else:
                withdraw -= eff
            dd = rt._d(d)
            flows.append((max(0.0, min(1.0, (T - (dd - s).days - 0.5) / T)), eff))
        elif cls == "returns":
            ret_tx += eff
        elif cls == "unexplained":
            other += eff
    if w["shift_date"] and lo <= w["shift_date"] <= hi and w["opening_returns"]:
        # the opening's prior-returns part is not a contribution: move it from principal flows to returns
        contrib -= w["opening_returns"]; ret_tx += w["opening_returns"]
        flows.append((1.0, -w["opening_returns"]))
    market = c["components"]["market"]
    period_returns = ret_tx + market
    v0, v1 = c["start_value"], c["end_value"]
    net = contrib - withdraw
    denom = v0 + sum(wt * a for wt, a in flows)
    md = period_returns / denom if denom > 1e-9 else None
    R = lambda v: round(v, 2) + 0.0
    return {"start": s.isoformat(), "end": e.isoformat(), "currency": acct["currency"], "start_value": R(v0),
            "end_value": R(v1), "contributions": R(contrib), "withdrawals": R(withdraw), "net_contributions": R(net),
            "returns": R(period_returns), "trade_gains": R(market), "unexplained": R(other + c["components"]["reconciliation"]),
            "unclassified": R(other), "reconciliation_diff": R(c["components"]["reconciliation"]),
            "md_return": round(md * 100, 2) if md is not None else None}


def history_extras(db: Database, acct: dict, page_items: list[dict], start, end, first_page: bool,
                   today: date | None = None) -> dict | None:
    """Adds pr_class / pr_badge / running_principal / running_returns to rows; on the first page returns the
    account-level `invest` block (current + period figures + chart series + hint)."""
    if not is_investment(acct["type"]):
        return None
    full = dict(acct)
    full.setdefault("opening_returns", (db.one("SELECT opening_returns FROM accounts WHERE id=?", (acct["id"],)) or {}).get("opening_returns"))
    w = walk(db, full)
    for t in page_items:
        inf = w["info"].get(t["id"])
        if not inf:
            continue
        cls, sub = inf[0], inf[1]
        t.update(pr_class=cls, pr_sub=sub, pr_badge=BADGE[cls], pr_label=LABELS.get(sub, sub),
                 running_principal=inf[3], running_returns=inf[4], running_unexplained=inf[5])
    if not first_page:
        return None
    cc = correction_counts(db, [acct["id"]]).get(acct["id"], 0)
    from .returns import reclassify_preview
    likely_opening = [r for r in reclassify_preview(db, account_ids=[acct["id"]], only=True)["rows"]
                      if r["proposed_reason"] == "opening" and r.get("pr_class") in (None, "")]
    return {**summary(full, w), "correction_count": cc, "likely_opening_count": len(likely_opening),
            "likely_opening_amount": round(sum(r["amount"] for r in likely_opening), 2) + 0.0,
            "period": period_figures(db, full, w, start, end, today),
            "series": investment_series(db, full, start, end, "auto", today, w),
            "running_note": "Running returns count recorded returns only; trade gains on holdings are in the header."}


INTERVALS = ("auto", "day", "week", "month")


def series_points(s: date, e: date, interval: str = "auto") -> tuple[str, list[date]]:
    """Chart dates between s and e (inclusive): daily up to ~2 months, weekly (week ends) up to ~18 months, else
    month ends; always includes s and e. Too many daily points fall back to weekly."""
    span = (e - s).days + 1
    iv = (interval or "auto").lower()
    if iv not in INTERVALS:
        raise ValueError(f"interval must be one of {INTERVALS}")
    if iv == "auto":
        iv = "day" if span <= 62 else "week" if span <= 550 else "month"
    if iv == "day" and span > 1100:
        iv = "week"
    pts = {s, e}
    d = s
    while d <= e:
        if iv == "day":
            pts.add(d); d += timedelta(days=1)
        elif iv == "week":
            we = d + timedelta(days=6 - d.weekday())  # Sunday
            if we <= e:
                pts.add(we)
            d = we + timedelta(days=1)
        else:
            nxt = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
            if nxt - timedelta(days=1) <= e:
                pts.add(nxt - timedelta(days=1))
            d = nxt
    return iv, sorted(pts)


def investment_series(db: Database, acct: dict, start=None, end=None, interval: str = "auto",
                      today: date | None = None, w: dict | None = None) -> dict:
    """Cumulative principal, cumulative returns and market value of one investment account at each chart date in
    [start, end] (account currency), plus a period summary. returns(t) = recorded returns + trade gains on holdings;
    unexplained(t) = value − principal − returns."""
    from . import returns as rt
    today = today or date.today()
    full = dict(acct)
    full.setdefault("opening_returns", (db.one("SELECT opening_returns FROM accounts WHERE id=?", (acct["id"],)) or {}).get("opening_returns"))
    w = w or walk(db, full)
    first = rt.first_activity(db, [acct["id"]])
    s = rt._d(start) if start else (rt._d(first) if first else today)
    e = rt._d(end) if end else today
    if s > e:
        raise ValueError("from must be on or before to")
    iv, pts = series_points(s, e, interval)
    c = rt.compute(db, [acct["id"]], s, e, acct["currency"], series=True, today=today, points=pts)
    seq, i = w["seq"], 0
    cur = (w["p0"], w["r0"], 0.0, 0.0)
    R = lambda v: round(v, 2) + 0.0
    out = []
    for pt in c["series"]:
        cut = pt["date"] + " 99"
        while i < len(seq) and seq[i][0] <= cut:
            cur = seq[i][1:]; i += 1
        p, r_tx, trade, _ = cur
        rets = r_tx + pt["holdings"] + trade
        out.append({"date": pt["date"], "principal": R(p), "returns": R(rets), "value": pt["value"],
                    "unexplained": R(pt["value"] - p - rets)})
    pf = period_figures(db, full, w, s.isoformat(), e.isoformat(), today)
    a, b = (out[0], out[-1]) if out else ({}, {})
    summ = {"start_principal": a.get("principal"), "end_principal": b.get("principal"),
            "start_returns": a.get("returns"), "end_returns": b.get("returns"),
            "start_value": a.get("value"), "end_value": b.get("value"),
            "principal_change": R((b.get("principal") or 0) - (a.get("principal") or 0)),
            "returns_change": R((b.get("returns") or 0) - (a.get("returns") or 0)),
            "start_unexplained": a.get("unexplained"), "end_unexplained": b.get("unexplained"),
            "unexplained_change": R((b.get("unexplained") or 0) - (a.get("unexplained") or 0)),
            "contributions": pf["contributions"], "withdrawals": pf["withdrawals"], "period_returns": pf["returns"],
            "return_pct": pf["md_return"]}
    return {"account_id": acct["id"], "currency": acct["currency"], "from": s.isoformat(), "to": e.isoformat(),
            "interval": iv, "points": out, "summary": summ}
