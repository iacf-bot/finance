"""Account reconciliation 对账.

A checkpoint (account, as_of, statement balance) anchors the account: from then on
    balance(t) = latest checkpoint with as_of <= t  +  that account's transactions after it up to t
(see ledger.cash_balances). Editing/adding/deleting transactions dated on or before the latest checkpoint does not
change the balance at/after it; the effect shows up as *drift* in the reconciliation history instead.
Before the first checkpoint balances are computed as before. Income/expense reports are unaffected.

For accounts with tracked holdings the anchor is stored as cash = statement balance - holdings value at as_of.
"""
from __future__ import annotations

from datetime import datetime

from . import ledger
from .db import Database, now_iso


class ReconcileError(ValueError):
    pass


def norm_as_of(as_of) -> str:
    if not as_of:
        return datetime.now().strftime("%Y-%m-%d %H:%M")
    s = str(as_of).replace("T", " ").strip()
    if len(s) == 10:
        s += " 23:59"
    try:
        return datetime.strptime(s[:16], "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M")
    except ValueError:
        raise ReconcileError(f"Invalid date: {as_of}")


def _acct(db: Database, aid: int) -> dict:
    a = db.one("SELECT * FROM accounts WHERE id=?", (aid,))
    if not a:
        raise KeyError(aid)
    a["liability"] = ledger.is_liability(a["type"])
    return a


def latest(db: Database, aid: int) -> dict | None:
    return db.one("SELECT * FROM reconciliations WHERE account_id=? ORDER BY as_of DESC, id DESC LIMIT 1", (aid,))


def _holdings_value(db: Database, aid: int, as_of: str) -> float:
    return round(sum(h.get("market_value") or 0 for h in ledger.holdings(db, aid, until=as_of)), 2)


def _signed(a: dict, balance, as_owed=None) -> float:
    try:
        v = float(str(balance).replace(",", ""))
    except (TypeError, ValueError):
        raise ReconcileError("balance must be a number")
    owed = a["liability"] if as_owed is None else bool(as_owed)
    return -v if owed else v


def _effect(t: dict, aid: int) -> float:
    k, amt = t["kind"], t["amount"] or 0
    if k == "transfer":
        if t["to_account_id"] == aid and t["account_id"] != aid:
            return t["to_amount"] if t["to_amount"] is not None else amt
        return -amt
    return {"expense": -amt, "income": amt, "refund": amt, "adjustment": amt, "buy": -amt, "sell": amt,
            "dividend": amt, "invest_fee": -amt}.get(k, 0)


def preview(db: Database, aid: int, as_of=None, balance=None, as_owed=None) -> dict:
    """What the Reconcile dialog shows: transactions since the previous checkpoint, computed vs statement balance."""
    a = _acct(db, aid)
    when = norm_as_of(as_of)
    prev = latest(db, aid)
    if prev and when <= prev["as_of"]:
        raise ReconcileError(f"{a['name']} was already reconciled on {prev['as_of']}; pick a later date or undo that one first")
    computed = ledger.account_balance_at(db, aid, when)
    cond, params = "t.status='posted' AND (t.account_id=? OR t.to_account_id=?) AND t.date <= ?", [aid, aid, when]
    if prev:
        cond += " AND t.date > ?"; params.append(prev["as_of"])
    rows = db.all(ledger.TX_SELECT + f" WHERE {cond} ORDER BY t.date, t.id", params)
    txs = []
    for r in map(ledger.decorate, rows):
        e = _effect(r, aid)
        txs.append({"id": r["id"], "date": r["date"], "kind": r["kind"], "effect": round(e, 2) + 0.0,
                    "label": r.get("merchant_name") or r.get("category_label") or r.get("note") or r["kind"],
                    "note": (r.get("note") or "")[:80], "other_account": r["to_account_name"] if r["account_id"] == aid else r["account_name"],
                    "reconciled_id": r.get("reconciled_id")})
    late = db.one("""SELECT COUNT(*) n FROM transactions t WHERE t.status='posted' AND (t.account_id=? OR t.to_account_id=?)
        AND t.date <= ? AND NOT EXISTS (SELECT 1 FROM reconciled_tx rc WHERE rc.transaction_id=t.id AND rc.account_id=?)""",
                  (aid, aid, prev["as_of"], aid))["n"] if prev else 0
    out = {"account_id": aid, "account_name": a["name"], "currency": a["currency"], "liability": a["liability"],
           "as_of": when, "previous": prev, "computed_signed": computed,
           "computed": round(-computed if a["liability"] else computed, 2) + 0.0, "transactions": txs,
           "added_before_previous": late}
    if balance not in (None, ""):
        st = _signed(a, balance, as_owed)
        diff = round(st - computed, 2) + 0.0
        out.update(statement_signed=st, statement=round(-st if a["liability"] else st, 2) + 0.0, difference=diff,
                   difference_shown=round(-diff if a["liability"] else diff, 2) + 0.0)
    return out


def create(db: Database, aid: int, balance, as_of=None, note: str = "", cleared_ids=None, adjust_reason: str | None = None,
           as_owed=None) -> dict:
    """Finalize a reconciliation. With adjust_reason, a difference is first recorded as a balance adjustment
    (Set balance reasons); otherwise the checkpoint is created anyway and the difference becomes the anchor."""
    a = _acct(db, aid)
    p = preview(db, aid, as_of, balance, as_owed)
    when, st = p["as_of"], p["statement_signed"]
    adj_id = None
    if adjust_reason and abs(p["difference"]) >= 0.005:
        r = ledger.set_balance(db, aid, st, when, (note or "").strip() or "Reconciliation 对账", source="reconcile",
                               as_owed=False, reason=adjust_reason)
        adj_id = (r.get("transaction") or {}).get("id")
    hv = _holdings_value(db, aid, when)
    computed_cash = ledger.cash_balances(db, until=when).get(aid, 0.0)
    cleared = {int(x) for x in (cleared_ids or [])}
    with db.tx() as con:
        rid = con.execute("""INSERT INTO reconciliations(account_id, as_of, balance, cash_balance, computed_at_creation, note,
            adjustment_tx_id, created_at) VALUES(?,?,?,?,?,?,?,?)""",
                          (aid, when, round(st, 2), round(st - hv, 2), round(computed_cash, 2), (note or "").strip(), adj_id, now_iso())).lastrowid
        ids = [r[0] for r in con.execute("""SELECT id FROM transactions t WHERE status='posted' AND (account_id=? OR to_account_id=?)
            AND date <= ? AND NOT EXISTS (SELECT 1 FROM reconciled_tx rc WHERE rc.transaction_id=t.id AND rc.account_id=?)""",
                                         (aid, aid, when, aid)).fetchall()]
        con.executemany("INSERT OR IGNORE INTO reconciled_tx(reconciliation_id, transaction_id, account_id, cleared) VALUES(?,?,?,?)",
                        [(rid, t, aid, 1 if t in cleared else 0) for t in ids])
        n_cleared = sum(1 for t in ids if t in cleared)
        con.execute("UPDATE reconciliations SET tx_count=?, cleared_count=? WHERE id=?", (len(ids), n_cleared, rid))
    word = "Amount owed" if a["liability"] else "Balance"
    shown = -st if a["liability"] else st
    return {"ok": True, "reconciliation": history(db, aid)[0], "adjustment_tx_id": adj_id, "flagged": len(ids),
            "message": f"Reconciled {a['name']} on {when[:10]}: {word.lower()} {shown:,.2f} {a['currency']}"
                       + (f" (difference {p['difference_shown']:+,.2f} recorded as {ledger.reason_label(adjust_reason, p['difference'])})" if adj_id else
                          (f" (difference {p['difference_shown']:+,.2f} accepted as the new anchor)" if abs(p["difference"]) >= 0.005 else ""))}


def history(db: Database, aid: int) -> list[dict]:
    """Checkpoints newest first, with drift = what the transactions now sum to − the checkpoint balance
    (each checkpoint measured from the previous one) and history_changed = change since the checkpoint was made."""
    a = _acct(db, aid)
    cps = db.all("SELECT * FROM reconciliations WHERE account_id=? ORDER BY as_of, id", (aid,))
    raw = None
    out, prev = [], None
    for cp in cps:
        if prev is None:
            if raw is None:
                raw = ledger.cash_balances(db, until=cp["as_of"], anchored=False).get(aid, 0.0)
            now_sum = raw
        else:
            now_sum = prev["cash_balance"] + ledger.effects_between(db, aid, prev["as_of"], cp["as_of"])
        sign = -1 if a["liability"] else 1
        cp = dict(cp)
        cp["computed_now"] = round(now_sum, 2) + 0.0
        cp["drift"] = round((now_sum - cp["cash_balance"]) * sign, 2) + 0.0
        cp["history_changed"] = round((now_sum - cp["computed_at_creation"]) * sign, 2) + 0.0
        cp["balance_shown"] = round(cp["balance"] * sign, 2) + 0.0
        cp["currency"] = a["currency"]
        out.append(cp)
        prev = cp
        raw = None
    for i, cp in enumerate(out):
        cp["is_latest"] = i == len(out) - 1
    return list(reversed(out))


def undo_latest(db: Database, aid: int, delete_adjustment: bool = False) -> dict:
    cp = latest(db, aid)
    if not cp:
        raise ReconcileError("This account has no reconciliation to undo")
    with db.tx() as con:
        n = con.execute("DELETE FROM reconciled_tx WHERE reconciliation_id=?", (cp["id"],)).rowcount
        con.execute("DELETE FROM reconciliations WHERE id=?", (cp["id"],))
        if delete_adjustment and cp["adjustment_tx_id"]:
            con.execute("DELETE FROM transactions WHERE id=?", (cp["adjustment_tx_id"],))
    return {"ok": True, "undone": cp["id"], "unflagged": n, "as_of": cp["as_of"]}


def edit_warning(db: Database, tx_dates_accounts: list[tuple[str, int | None]]) -> str | None:
    """Warning when a change touches a transaction dated on/before an affected account's latest checkpoint."""
    msgs = []
    for d, aid in tx_dates_accounts:
        if not aid or not d:
            continue
        cp = latest(db, int(aid))
        if cp and str(d)[:16] <= cp["as_of"]:
            name = db.one("SELECT name FROM accounts WHERE id=?", (aid,))["name"]
            msgs.append(f"{name} was reconciled on {cp['as_of'][:10]}; this change won't affect its current balance "
                        f"(it shows as drift in the reconciliation history)")
    return "; ".join(dict.fromkeys(msgs)) or None
