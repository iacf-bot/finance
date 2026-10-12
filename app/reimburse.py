"""Reimbursements 报销.

* An expense can be flagged reimbursable 可报销 (transactions.reimbursable=1), with optional payer (reimb_payer: who
  pays it back), expected amount (reimb_expected, default = the expense amount, in the expense's currency) and note.
* A reimbursement receipt is a transaction of kind 'reimbursement' (money in to an account; balances change
  normally). reimb_links(receipt_id, expense_id, amount) says how much of each expense it settles, in the expense's
  currency, so one receipt can settle several expenses and an expense can be settled by several receipts (partial).
* Reports: a reimbursable expense is not an expense, a receipt is not income (see ledger.REPORT_AMOUNT_SQL).
  Write-off 核销 (reimb_writeoff=1): it won't be reimbursed, so the unreimbursed part counts as a normal expense again.
* Status: pending 待报销 (nothing received) / partial 部分报销 / settled 已报销 / written_off 已核销.
"""
from __future__ import annotations

from collections import defaultdict

from .db import Database, now_iso
from .ledger import TX_FIELDS, TX_SELECT, ValidationError, convert, decorate, fx_rates, normalize_tx

STATUSES = {"pending": "待报销 Pending", "partial": "部分报销 Partial", "settled": "已报销 Settled",
            "written_off": "已核销 Written off"}
EPS = 0.005


def _settled(db: Database, ids: list[int]) -> dict[int, float]:
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    return {r["expense_id"]: r["s"] for r in db.all(
        f"SELECT expense_id, SUM(amount) s FROM reimb_links WHERE expense_id IN ({q}) GROUP BY expense_id", ids)}


def _decorate(t: dict, settled: float, links: list[dict]) -> dict:
    expected = t.get("reimb_expected") if t.get("reimb_expected") is not None else t["amount"]
    out = 0.0 if t.get("reimb_writeoff") else max(expected - settled, 0.0)
    if t.get("reimb_writeoff"):
        st = "written_off"
    elif out <= EPS:
        st = "settled"
    elif settled > EPS:
        st = "partial"
    else:
        st = "pending"
    t.update(reimb_expected_amount=round(expected, 2), reimb_settled=round(settled, 2) + 0.0,
             reimb_outstanding=round(out, 2) + 0.0, reimb_status=st, reimb_status_label=STATUSES[st], reimb_links=links)
    return t


def expense_status(db: Database, tid: int) -> dict:
    t = db.one(TX_SELECT + " WHERE t.id=?", (tid,))
    if not t or t["kind"] != "expense" or not t["reimbursable"]:
        raise ValidationError("Not a reimbursable expense")
    return _decorate(decorate(t), _settled(db, [tid]).get(tid, 0.0), _links_for(db, [tid]).get(tid, []))


def _links_for(db: Database, ids: list[int]) -> dict[int, list[dict]]:
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    out: dict[int, list] = defaultdict(list)
    for r in db.all(f"""SELECT l.expense_id, l.receipt_id, l.amount, r.date, r.amount receipt_amount, r.currency receipt_currency,
        a.name account_name FROM reimb_links l JOIN transactions r ON r.id=l.receipt_id LEFT JOIN accounts a ON a.id=r.account_id
        WHERE l.expense_id IN ({q}) ORDER BY r.date, l.id""", ids):
        out[r.pop("expense_id")].append(r)
    return out


def list_items(db: Database, f: dict) -> dict:
    """Reimbursable expenses (newest first) with status and outstanding amount, plus totals by currency / payer."""
    where, params = ["t.kind='expense'", "COALESCE(t.reimbursable,0)=1", "t.status='posted'"], []
    if f.get("start"):
        where.append("t.date >= ?"); params.append(f["start"])
    if f.get("end"):
        where.append("t.date <= ?"); params.append(str(f["end"]) + " 99")
    if f.get("account_id"):
        where.append("t.account_id=?"); params.append(int(f["account_id"]))
    if f.get("payer"):
        if f["payer"] == "(none)":
            where.append("COALESCE(t.reimb_payer,'')=''")
        else:
            where.append("t.reimb_payer=?"); params.append(f["payer"])
    rows = [decorate(r) for r in db.all(TX_SELECT + " WHERE " + " AND ".join(where) + " ORDER BY t.date DESC, t.id DESC", params)]
    ids = [r["id"] for r in rows]
    st, links = _settled(db, ids), _links_for(db, ids)
    rows = [_decorate(r, st.get(r["id"], 0.0), links.get(r["id"], [])) for r in rows]
    statuses = [s for s in str(f.get("status") or "").split(",") if s]
    if not statuses:
        statuses = ["pending", "partial"]  # default: still open
    if "all" not in statuses:
        rows = [r for r in rows if r["reimb_status"] in statuses]
    by_cur, by_payer = defaultdict(float), defaultdict(lambda: defaultdict(float))
    counts = defaultdict(int)
    for r in rows:
        counts[r["reimb_status"]] += 1
        if r["reimb_outstanding"] > EPS:
            by_cur[r["currency"]] += r["reimb_outstanding"]
            by_payer[r.get("reimb_payer") or "(none)"][r["currency"]] += r["reimb_outstanding"]
    base = db.get_setting("base_currency", "USD"); rates = fx_rates(db)
    payers = [p["p"] for p in db.all("""SELECT DISTINCT reimb_payer p FROM transactions WHERE reimbursable=1
                                        AND COALESCE(reimb_payer,'')!='' ORDER BY 1""")]
    return {"items": rows, "count": len(rows), "counts": dict(counts), "statuses": STATUSES, "payers": payers,
            "outstanding": {"by_currency": [{"currency": c, "amount": round(v, 2)} for c, v in sorted(by_cur.items())],
                            "total_base": round(sum(convert(v, c, base, rates) for c, v in by_cur.items()), 2),
                            "base_currency": base,
                            "by_payer": [{"payer": p, "by_currency": [{"currency": c, "amount": round(v, 2)} for c, v in sorted(d.items())]}
                                         for p, d in sorted(by_payer.items())]}}


def record_receipt(db: Database, body: dict) -> dict:
    """Record a reimbursement received 报销到账 into account_id that settles `items` = [{expense_id, amount?}]
    (amount in the expense's currency; default = its outstanding amount; partial allowed). The receipt amount
    (account currency) defaults to the sum of the item amounts converted with the current rates."""
    items = body.get("items") or []
    if not isinstance(items, list) or not items:
        raise ValidationError("Select at least one reimbursable expense")
    if not body.get("account_id"):
        raise ValidationError("account_id (where the money arrived) is required")
    rates = fx_rates(db)
    with db.tx() as con:
        acct = con.execute("SELECT * FROM accounts WHERE id=?", (int(body["account_id"]),)).fetchone()
        if not acct:
            raise ValidationError("Account not found")
        links, seen, total_acct, payers = [], set(), 0.0, set()
        for it in items:
            eid = int(it.get("expense_id") or it.get("id") or 0)
            if eid in seen:
                raise ValidationError(f"Expense {eid} listed twice")
            seen.add(eid)
            e = con.execute("SELECT * FROM transactions WHERE id=?", (eid,)).fetchone()
            if not e or e["kind"] != "expense" or not e["reimbursable"]:
                raise ValidationError(f"Transaction {eid} is not a reimbursable expense")
            if e["reimb_writeoff"]:
                raise ValidationError(f"Expense {eid} was written off; undo the write-off first")
            settled = con.execute("SELECT COALESCE(SUM(amount),0) FROM reimb_links WHERE expense_id=?", (eid,)).fetchone()[0]
            expected = e["reimb_expected"] if e["reimb_expected"] is not None else e["amount"]
            outstanding = round(expected - settled, 2)
            amt = it.get("amount")
            amt = outstanding if amt in (None, "") else round(float(amt), 2)
            if amt <= 0:
                raise ValidationError(f"Expense {eid}: nothing left to reimburse" if outstanding <= EPS else "Amounts must be > 0")
            if amt > outstanding + EPS:
                raise ValidationError(f"Expense {eid}: {amt:.2f} is more than the outstanding {outstanding:.2f} {e['currency']}")
            links.append((eid, amt))
            total_acct += convert(amt, e["currency"], acct["currency"], rates)
            if e["reimb_payer"]:
                payers.add(e["reimb_payer"])
        amount = body.get("amount")
        amount = round(total_acct, 2) if amount in (None, "") else round(float(amount), 2)
        if amount <= 0:
            raise ValidationError("Receipt amount must be > 0")
        payer = body.get("payer") or (payers.pop() if len(payers) == 1 else None)
        data = {"kind": "reimbursement", "account_id": acct["id"], "amount": amount,
                "currency": body.get("currency") or acct["currency"], "date": body.get("date"),
                "note": body.get("note") or ("报销 Reimbursement" + (f" · {payer}" if payer else "")),
                "reimb_payer": payer, "source": body.get("source") or "web", "status": "posted",
                "ledger_id": body.get("ledger_id"), "tags": body.get("tags") or ""}
        tx = normalize_tx(con, data, rates)
        tx["updated_at"] = tx["created_at"] = now_iso()
        cols = [k for k in TX_FIELDS + ["updated_at", "created_at"] if k in tx]
        rid = con.execute(f"INSERT INTO transactions({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                          [tx[k] for k in cols]).lastrowid
        for eid, amt in links:
            con.execute("INSERT INTO reimb_links(receipt_id, expense_id, amount) VALUES(?,?,?)", (rid, eid, amt))
    return {"receipt": receipt(db, rid), "settled": [expense_status(db, eid) for eid, _ in links]}


def receipt(db: Database, rid: int) -> dict:
    r = db.one(TX_SELECT + " WHERE t.id=? AND t.kind='reimbursement'", (rid,))
    if not r:
        raise KeyError(rid)
    r = decorate(r)
    r["links"] = db.all("""SELECT l.expense_id, l.amount, e.date, e.amount expense_amount, e.currency, e.note, e.reimb_payer,
        m.name merchant_name, c.name category_name, c.name_zh category_name_zh FROM reimb_links l
        JOIN transactions e ON e.id=l.expense_id LEFT JOIN merchants m ON m.id=e.merchant_id
        LEFT JOIN categories c ON c.id=e.category_id WHERE l.receipt_id=? ORDER BY e.date, e.id""", (rid,))
    return r


def receipts(db: Database, limit: int = 100) -> dict:
    ids = [r["id"] for r in db.all("SELECT id FROM transactions WHERE kind='reimbursement' ORDER BY date DESC, id DESC LIMIT ?",
                                   (max(1, min(int(limit), 500)),))]
    return {"items": [receipt(db, i) for i in ids]}


def write_off(db: Database, tid: int, undo: bool = False) -> dict:
    e = db.one("SELECT kind, reimbursable FROM transactions WHERE id=?", (tid,))
    if not e or e["kind"] != "expense" or not e["reimbursable"]:
        raise ValidationError("Not a reimbursable expense")
    with db.tx() as con:
        con.execute("UPDATE transactions SET reimb_writeoff=?, updated_at=? WHERE id=?", (0 if undo else 1, now_iso(), tid))
    return expense_status(db, tid)


def mark(db: Database, ids: list[int], flag: bool, payer: str | None = None) -> dict:
    """Flag / unflag expenses as reimbursable 可报销 (unflagging is refused while receipts are linked)."""
    ids = [int(i) for i in ids or []]
    if not ids:
        raise ValidationError("Nothing selected")
    done = 0
    with db.tx() as con:
        for i in ids:
            e = con.execute("SELECT kind FROM transactions WHERE id=?", (i,)).fetchone()
            if not e or e["kind"] != "expense":
                raise ValidationError(f"Transaction {i} is not an expense")
            if not flag and con.execute("SELECT 1 FROM reimb_links WHERE expense_id=?", (i,)).fetchone():
                raise ValidationError(f"Expense {i} already has reimbursement receipts: delete those receipts or write it off instead")
            if flag:
                sets, vals = "reimbursable=1", []
                if payer is not None:
                    sets += ", reimb_payer=?"; vals.append(payer.strip() or None)
            else:
                sets, vals = "reimbursable=0, reimb_writeoff=0", []
            done += con.execute(f"UPDATE transactions SET {sets}, updated_at=? WHERE id=?", vals + [now_iso(), i]).rowcount
    return {"ok": True, "updated": done}
