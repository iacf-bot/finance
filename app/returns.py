"""Investment returns 投资收益 per account / group / all investment accounts over a period.

Definitions (all amounts converted to one currency with the current FX rates):
* value V(t)            = cash balance + tracked holdings (shares x price; current quote for today, otherwise the
                          last trade price on or before t).
* external flows F      = money moving in (+) or out (-) of the account set: transfers from/to accounts outside the set,
                          income/refund (+) and expense (-) recorded on the account, and balance adjustments whose
                          reason is NOT a return (Correction/other, Opening balance, Unrecorded spending/income).
                          Transfers between two accounts of the same set are internal and not flows.
                          Buys/sells only move money between cash and holdings inside the account (not flows).
* net contributions     = sum(F) = contributions - withdrawals
* gain                  = V(end) - V(start) - net contributions
                        = investment gain/loss adjustments + interest + dividends - fees + market gains on tracked holdings
* simple return         = gain / (V(start) + net contributions)
* Modified Dietz return = gain / (V(start) + sum(w_i * F_i)), w_i = share of the period left after flow i
* annualized            = (1 + Modified Dietz)^(365 / days) - 1   (only shown for periods longer than a year)
* XIRR                  = annual rate r with  -V(start) - sum(F_i / (1+r)^(t_i/365)) + V(end)/(1+r)^(T/365) = 0
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from .db import ACCOUNT_TYPES, Database
from .ledger import GAIN_REASONS, REASON_SQL, convert, fx_rates, is_investment

PERIODS = ("month", "ytd", "1y", "all", "custom")
INVEST_TYPES = [k for k, v in ACCOUNT_TYPES.items() if v[3]]


def _d(s) -> date:
    if isinstance(s, date):
        return s
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def period_bounds(period: str, start=None, end=None, first_date=None, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    period = (period or "ytd").lower()
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}")
    e = _d(end) if (period == "custom" and end) else today
    if period == "month":
        s = e.replace(day=1)
    elif period == "ytd":
        s = e.replace(month=1, day=1)
    elif period == "1y":
        s = e - timedelta(days=364)
    elif period == "all":
        s = _d(first_date) if first_date else e.replace(month=1, day=1)
    else:
        if not start:
            raise ValueError("custom period needs start (and optionally end)")
        s = _d(start)
    if s > e:
        raise ValueError("start must be on or before end")
    return s, e


def xirr(flows: list[tuple[float, float]]) -> float | None:
    """flows: (years_from_start, amount) with investor sign (money in = negative). Returns the annual rate or None."""
    if not flows or not any(a > 0 for _, a in flows) or not any(a < 0 for _, a in flows):
        return None
    npv = lambda r: sum(a / (1 + r) ** t for t, a in flows)
    lo, hi = -0.9999, 1.0
    while npv(hi) > 0 and hi < 1e6:
        hi *= 2
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f = npv(mid)
        if abs(f) < 1e-9:
            break
        if (f > 0) == (f_lo > 0):
            lo, f_lo = mid, f
        else:
            hi = mid
    return mid


def modified_dietz(v0: float, v1: float, flows: list[tuple[float, float]]) -> float | None:
    """flows: (weight = fraction of the period remaining after the flow, amount (+ in / - out))."""
    gain = v1 - v0 - sum(a for _, a in flows)
    denom = v0 + sum(w * a for w, a in flows)
    return gain / denom if denom > 1e-9 else None


def first_activity(db: Database, ids: list[int]) -> str | None:
    if not ids:
        return None
    q = ",".join("?" * len(ids))
    r = db.one(f"SELECT MIN(date) d FROM transactions WHERE status='posted' AND (account_id IN ({q}) OR to_account_id IN ({q}))",
               ids + ids)
    return r["d"] if r else None


def compute(db: Database, ids: list[int], start, end, currency: str | None = None, series: bool = False,
            today: date | None = None, points: list[date] | None = None) -> dict:
    """Returns for the set of accounts `ids` between start and end dates (inclusive)."""
    today = today or date.today()
    s, e = _d(start), _d(end)
    currency = currency or db.get_setting("base_currency", "USD")
    rates = fx_rates(db)
    ids = [int(i) for i in ids]
    idset = set(ids)
    accts = {a["id"]: a for a in db.all(f"SELECT id, type, currency, opening_balance FROM accounts WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})", ids)}
    f = {a: convert(1.0, accts[a]["currency"], currency, rates) for a in accts}
    quotes = {p["ticker"]: p["price"] for p in db.all("SELECT ticker, price FROM prices")}
    start_cut, end_cut = s.strftime("%Y-%m-%d 00:00"), e.strftime("%Y-%m-%d 23:59")
    T = (e - s).days + 1  # days in the period (whole days, start 00:00 to end 24:00)
    q = ",".join("?" * len(ids)) or "NULL"
    rows = db.all(f"""SELECT t.id, t.kind, t.date, t.amount, t.to_amount, t.account_id, t.to_account_id, t.ticker, t.shares,
        t.price, {REASON_SQL} reason FROM transactions t LEFT JOIN categories c ON c.id=t.category_id
        WHERE t.status='posted' AND t.date <= ? AND (t.account_id IN ({q}) OR t.to_account_id IN ({q}))
        ORDER BY t.date, t.id""", [end_cut] + ids + ids)
    # reconciliation checkpoints anchor the account's cash at as_of (processed after that day's transactions)
    cps = db.all(f"SELECT account_id, as_of, cash_balance FROM reconciliations WHERE as_of <= ? AND account_id IN ({q}) ORDER BY as_of, id",
                 [end_cut] + ids)
    rows = sorted([dict(r, _o=0) for r in rows] + [{"kind": "_checkpoint", "date": c["as_of"], "account_id": c["account_id"],
                                                     "cash_balance": c["cash_balance"], "_o": 1, "id": 0} for c in cps],
                  key=lambda r: (r["date"], r["_o"], r["id"]))

    cash = {a: (accts[a]["opening_balance"] or 0.0) for a in accts}
    shares: dict[tuple, float] = defaultdict(float)
    last_px: dict[str, float] = {}

    def value(at: date) -> float:
        v = sum(cash[a] * f[a] for a in cash)
        for (a, tk), sh in shares.items():
            if abs(sh) < 1e-12:
                continue
            px = quotes.get(tk) if at >= today else last_px.get(tk, quotes.get(tk))
            v += sh * (px or 0) * f[a]
        return v

    def holdings_value(at: date) -> float:
        return value(at) - sum(cash[a] * f[a] for a in cash)

    comp = {"investment_gain": 0.0, "interest": 0.0, "fees": 0.0, "dividends": 0.0, "invest_fees": 0.0, "reconciliation": 0.0}
    contributions = withdrawals = trade_cash = internal_fx = 0.0
    flows: list[tuple[datetime, float]] = []
    checkpoints: list[date] = []
    if series and points:
        checkpoints = sorted(p for p in points if s <= p <= e)
    elif series:
        if T <= 62:
            checkpoints = [s + timedelta(days=i) for i in range(T)]
        else:
            d = s
            while d <= e:
                nxt = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
                checkpoints.append(min(nxt - timedelta(days=1), e))
                d = nxt
    pts: list[dict] = []
    v0 = None
    hv0 = 0.0
    ci = 0
    flow_sum = 0.0

    def emit_until(dstr: str | None):
        nonlocal ci
        while ci < len(checkpoints) and (dstr is None or checkpoints[ci].strftime("%Y-%m-%d 23:59") < dstr):
            c = checkpoints[ci]
            pts.append({"date": c.isoformat(), "value": round(value(c), 2), "holdings": round(holdings_value(c), 2),
                        "cumulative_gain": round(value(c) - v0 - flow_sum, 2), "net_contributions": round(flow_sum, 2)})
            ci += 1

    for r in rows:
        if v0 is None and r["date"] >= start_cut:
            v0 = value(s - timedelta(days=1)); hv0 = holdings_value(s - timedelta(days=1))
        in_period = v0 is not None
        if in_period:
            emit_until(r["date"])
        if r["kind"] == "_checkpoint":  # value at a checkpoint = the reconciled (statement) balance
            a = r["account_id"]
            if in_period:
                comp["reconciliation"] += (r["cash_balance"] - cash[a]) * f[a]
            cash[a] = r["cash_balance"]
            continue
        src, dst, k, amt = r["account_id"], r["to_account_id"], r["kind"], r["amount"] or 0.0
        src_in, dst_in = src in idset, dst in idset
        flow = 0.0
        if k == "transfer":
            recv = r["to_amount"] if r["to_amount"] is not None else amt
            if src_in:
                cash[src] -= amt
            if dst_in:
                cash[dst] += recv
            if src_in and dst_in:
                if in_period:
                    internal_fx += recv * f[dst] - amt * f[src]
            elif src_in:
                flow = -amt * f[src]
            elif dst_in:
                flow = recv * f[dst]
        elif src_in:
            if k in ("expense", "invest_fee"):
                cash[src] -= amt
            elif k in ("income", "refund", "dividend", "adjustment"):
                cash[src] += amt
            elif k == "buy":
                cash[src] -= amt
            elif k == "sell":
                cash[src] += amt
            if k in ("buy", "sell") and r["ticker"]:
                tk = r["ticker"].upper()
                shares[(src, tk)] += (r["shares"] or 0) * (1 if k == "buy" else -1)
                if r["price"] is not None:
                    last_px[tk] = r["price"]
            if in_period:
                v = amt * f[src]
                if k in ("income", "refund"):
                    flow = v
                elif k == "expense":
                    flow = -v
                elif k == "adjustment":
                    if r["reason"] in GAIN_REASONS:
                        comp[r["reason"]] += v
                    else:
                        flow = v
                elif k == "dividend":
                    comp["dividends"] += v
                elif k == "invest_fee":
                    comp["invest_fees"] -= v
                elif k == "buy":
                    trade_cash -= v
                elif k == "sell":
                    trade_cash += v
        if in_period and flow:
            flows.append((datetime.strptime(r["date"][:16], "%Y-%m-%d %H:%M"), flow))
            flow_sum += flow
            if flow > 0:
                contributions += flow
            else:
                withdrawals -= flow
    if v0 is None:
        v0 = value(s - timedelta(days=1)); hv0 = holdings_value(s - timedelta(days=1))
    emit_until(None)
    v1 = value(e)
    net = contributions - withdrawals
    gain = v1 - v0 - net
    market = holdings_value(e) - hv0 + trade_cash + internal_fx
    start_dt = datetime.combine(s, datetime.min.time())
    weights = [(max(0.0, min(1.0, (T - (t - start_dt).total_seconds() / 86400) / T)), a) for t, a in flows]
    md = modified_dietz(v0, v1, weights)
    simple_den = v0 + net
    x = xirr([(0.0, -v0)] + [((t - start_dt).total_seconds() / 86400 / 365, -a) for t, a in flows] + [(T / 365, v1)])
    ann = (1 + md) ** (365 / T) - 1 if (md is not None and T > 365 and md > -1) else None
    R = lambda v: round(v, 2) + 0.0
    pct = lambda v: round(v * 100, 2) if v is not None else None
    out = {"start": s.isoformat(), "end": e.isoformat(), "days": T, "currency": currency,
           "start_value": R(v0), "end_value": R(v1), "contributions": R(contributions), "withdrawals": R(withdrawals),
           "net_contributions": R(net), "gain": R(gain),
           "components": {"investment_gain": R(comp["investment_gain"]), "interest": R(comp["interest"]),
                          "dividends": R(comp["dividends"]), "fees": R(comp["fees"] + comp["invest_fees"]),
                          "market": R(market), "reconciliation": R(comp["reconciliation"])},
           "simple_return": pct(gain / simple_den) if simple_den > 1e-9 else None,
           "md_return": pct(md), "annualized": pct(ann), "xirr": pct(x), "flow_count": len(flows)}
    out["components_total"] = R(sum(out["components"].values()))
    if series:
        out["series"] = pts
    return out


def investment_accounts(db: Database) -> list[dict]:
    q = ",".join(f"'{t}'" for t in INVEST_TYPES)
    return db.all(f"""SELECT a.id, a.name, a.type, a.currency, a.archived, a.group_id, g.name group_name, g.sort gsort
        FROM accounts a LEFT JOIN account_groups g ON g.id=a.group_id WHERE a.type IN ({q}) ORDER BY g.sort, a.sort, a.name""")


def report(db: Database, period: str = "ytd", start=None, end=None, scope: str = "all", today: date | None = None) -> dict:
    """Table rows for every investment account, each group and 'All investments', plus the cumulative-gain series and
    per-year returns for `scope` ('all', 'group:<id>' or 'account:<id>')."""
    base = db.get_setting("base_currency", "USD")
    accts = investment_accounts(db)
    all_ids = [a["id"] for a in accts]
    first = first_activity(db, all_ids)
    s, e = period_bounds(period, start, end, first, today)
    rows = []
    groups: dict = defaultdict(list)
    for a in accts:
        groups[(a["group_id"], a["group_name"] or "Ungrouped 未分组")].append(a)
    for (gid, gname), members in groups.items():
        ids = [m["id"] for m in members]
        for m in members:
            r = compute(db, [m["id"]], s, e, base, today=today)
            if m["archived"] and not r["start_value"] and not r["end_value"] and not r["flow_count"] and not r["gain"]:
                continue
            rows.append({"level": "account", "id": m["id"], "name": m["name"], "type": m["type"], "group_id": gid,
                         "account_currency": m["currency"], "archived": m["archived"], **r})
        rows.append({"level": "group", "id": gid, "name": gname, "account_ids": ids, **compute(db, ids, s, e, base, today=today)})
    total = {"level": "total", "id": None, "name": "All investments 全部投资", "account_ids": all_ids,
             **compute(db, all_ids, s, e, base, today=today)}
    rows.append(total)
    # scope for chart + per-year table
    kind, _, sid = (scope or "all").partition(":")
    if kind == "account" and sid:
        sids, sname = [int(sid)], next((a["name"] for a in accts if a["id"] == int(sid)), "?")
    elif kind == "group":
        key = next((k for k in groups if str(k[0]) == sid), None)
        sids, sname = ([m["id"] for m in groups[key]], key[1]) if key else (all_ids, total["name"])
    else:
        sids, sname = all_ids, total["name"]
    chart = compute(db, sids, s, e, base, series=True, today=today)["series"] if sids else []
    years = []
    sfirst = first_activity(db, sids)
    if sids and sfirst:
        for y in range(max(_d(sfirst).year, 1900), e.year + 1):
            ys, ye = date(y, 1, 1), min(date(y, 12, 31), e)
            ys = max(ys, _d(sfirst)) if y == _d(sfirst).year else ys
            r = compute(db, sids, ys, ye, base, today=today)
            years.append({"year": y, **{k: r[k] for k in ("start", "end", "start_value", "end_value", "contributions",
                                                          "withdrawals", "net_contributions", "gain", "simple_return",
                                                          "md_return", "xirr")}})
    return {"period": {"name": period, "start": s.isoformat(), "end": e.isoformat(), "days": (e - s).days + 1},
            "currency": base, "rows": rows, "scope": {"key": scope or "all", "name": sname, "series": chart, "years": years},
            "first_date": first[:10] if first else None}


# ---------------------------------------------------------------- one-time reclassify tool (preview first)
def reclassify_preview(db: Database, include_savings: bool = False, account_ids: list[int] | None = None,
                       only: bool = False) -> dict:
    """Unclassified (Correction/other) balance adjustments on investment-type accounts — plus savings accounts when
    include_savings, plus any explicitly listed accounts — with a proposed reason. Read-only.
    The first record of an account (nothing earlier on it) is proposed as 'Opening balance': a 随手记 export has no
    opening balances, so an account's first 余额变更 usually is its starting balance, not a gain."""
    types = list(INVEST_TYPES) + (["savings"] if include_savings else [])
    tq = ",".join(f"'{t}'" for t in types)
    extra = [int(i) for i in (account_ids or [])]
    acond = f"a.type IN ({tq})" + (f" OR a.id IN ({','.join('?' * len(extra))})" if extra else "")
    if only and extra:  # just these accounts (account detail page hint)
        acond = f"a.id IN ({','.join('?' * len(extra))})"
    rows = db.all(f"""SELECT t.id, t.date, t.amount, t.currency, t.note, t.source, t.account_id, a.name account_name,
        a.type account_type, {REASON_SQL} reason FROM transactions t JOIN accounts a ON a.id=t.account_id
        LEFT JOIN categories c ON c.id=t.category_id
        WHERE t.kind='adjustment' AND t.status='posted' AND ({acond}) ORDER BY a.name, t.date, t.id""", extra)
    firsts = {r["aid"]: (r["d"], r["i"]) for r in db.all("""SELECT aid, MIN(d) d, MIN(i) i FROM (
        SELECT account_id aid, date d, id i FROM transactions WHERE status='posted' AND account_id IS NOT NULL
        UNION ALL SELECT to_account_id, date, id FROM transactions WHERE status='posted' AND to_account_id IS NOT NULL)
        GROUP BY aid""")}
    first_tx = {}
    for r in db.all("""SELECT account_id aid, id, date FROM transactions WHERE status='posted' AND kind='adjustment'
                       ORDER BY date, id"""):
        first_tx.setdefault(r["aid"], r)
    out = []
    for r in rows:
        if r["reason"] != "correction":
            continue  # already classified (or a real opening balance)
        f = firsts.get(r["account_id"])
        is_first = f is not None and first_tx.get(r["account_id"], {}).get("id") == r["id"] and r["date"] <= f[0]
        if is_first:
            proposed, why = "opening", "first record of this account (likely its starting balance)"
        elif is_investment(r["account_type"]):
            proposed, why = "investment_gain", "change in value of an investment account"
        elif r["account_type"] == "savings":
            proposed, why = "interest", "savings account: usually interest"
        else:
            proposed, why = "investment_gain", "selected account"
        out.append({**r, "note": (r["note"] or "")[:120], "current_reason": r.pop("reason"), "proposed_reason": proposed,
                    "why": why, "selected": True})
    by_reason: dict[str, int] = defaultdict(int)
    for r in out:
        by_reason[r["proposed_reason"]] += 1
    return {"rows": out, "count": len(out), "by_proposed_reason": dict(by_reason),
            "accounts": len({r["account_id"] for r in out}), "include_savings": include_savings}


def reclassify_apply(db: Database, items: list[dict]) -> dict:
    """Set the reason of the given adjustment transactions: items = [{"id": .., "reason": ..}]."""
    from .ledger import REASON_CODES, ValidationError, reason_category_id
    if not isinstance(items, list) or not items:
        raise ValidationError("Nothing selected")
    done = 0
    with db.tx() as con:
        for it in items:
            reason = str(it.get("reason") or "").lower()
            if reason not in REASON_CODES:
                raise ValidationError(f"reason must be one of {REASON_CODES}")
            cid = reason_category_id(con, reason)
            done += con.execute("UPDATE transactions SET category_id=?, updated_at=datetime('now','localtime') "
                                "WHERE id=? AND kind='adjustment'", (cid, int(it["id"]))).rowcount
    return {"ok": True, "updated": done}
