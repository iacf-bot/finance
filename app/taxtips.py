"""Tax & tips 税 / 小费: optional breakouts of a transaction's charged total.

Only transactions where tax or tip was actually recorded are counted (NULL = not recorded, never treated as 0).
Tip % = tips ÷ subtotal of the transactions that have a tip; effective tax % = tax ÷ subtotal of those with tax,
where subtotal = amount − tax − tip. Amounts in the base currency; reimbursable spending is excluded like Reports."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import ledger
from .db import Database

GROUPS = ("month", "category", "subcategory", "merchant", "project", "account")


def _rows(db: Database, start: str, end: str, kind: str = "expense", ledger_id=None, project_id=None) -> list[dict]:
    cond = ["t.status='posted'", "t.date >= ?", "t.date < ?", "(t.tax_amount IS NOT NULL OR t.tip_amount IS NOT NULL)"]
    params: list[Any] = [start, end]
    if kind == "expense":
        cond.append("t.kind='expense' AND COALESCE(t.reimbursable,0)=0")
    elif kind == "income":
        cond.append("t.kind='income'")
    if ledger_id:
        cond.append("t.ledger_id=?"); params.append(ledger_id)
    if project_id:
        cond.append("t.project_id=?"); params.append(project_id)
    return db.all(f"""SELECT t.id, t.date, t.amount, t.currency, t.tax_amount, t.tip_amount, t.tax_rate, t.category_id,
        c.parent_id, t.merchant_id, t.project_id, t.account_id FROM transactions t
        LEFT JOIN categories c ON c.id=t.category_id WHERE {' AND '.join(cond)}""", params)


def _acc() -> dict:
    return {"tax": 0.0, "tip": 0.0, "tax_count": 0, "tip_count": 0, "tax_base": 0.0, "tip_base": 0.0, "amount": 0.0, "count": 0}


def _finish(a: dict) -> dict:
    out = {k: (round(v, 2) + 0.0 if isinstance(v, float) else v) for k, v in a.items()}
    out["tip_pct"] = round(a["tip"] / a["tip_base"] * 100, 1) if a["tip_count"] and a["tip_base"] > 0 else None
    out["tax_pct"] = round(a["tax"] / a["tax_base"] * 100, 2) if a["tax_count"] and a["tax_base"] > 0 else None
    out.pop("tax_base"); out.pop("tip_base")
    return out


def summary(db: Database, start: str, end: str, group_by: str = "month", kind: str = "expense",
            ledger_id=None, project_id=None) -> dict:
    if group_by not in GROUPS:
        raise ValueError(f"group_by must be one of {', '.join(GROUPS)}")
    base = db.get_setting("base_currency", "USD"); rates = ledger.fx_rates(db)
    tot = _acc()
    groups: dict = defaultdict(_acc)
    for r in _rows(db, start, end, kind, ledger_id, project_id):
        cv = lambda v: ledger.convert(v, r["currency"], base, rates)
        amt = cv(r["amount"] or 0)
        tax = cv(r["tax_amount"]) if r["tax_amount"] is not None else None
        tip = cv(r["tip_amount"]) if r["tip_amount"] is not None else None
        sub = amt - (tax or 0) - (tip or 0)
        key = {"month": (r["date"] or "")[:7], "category": r["parent_id"] or r["category_id"], "subcategory": r["category_id"],
               "merchant": r["merchant_id"], "project": r["project_id"], "account": r["account_id"]}[group_by]
        for a in (tot, groups[key]):
            a["count"] += 1; a["amount"] += amt
            if tax is not None:
                a["tax"] += tax; a["tax_count"] += 1; a["tax_base"] += sub
            if tip is not None:
                a["tip"] += tip; a["tip_count"] += 1; a["tip_base"] += sub
    names = _names(db, group_by, [k for k in groups if k is not None and group_by != "month"])
    rows = [{"key": k, "name": (k if group_by == "month" else names.get(k)) or None, **_finish(a)} for k, a in groups.items()]
    rows.sort(key=lambda x: x["key"] or "") if group_by == "month" else rows.sort(key=lambda x: -(x["tax"] + x["tip"]))
    return {"start": start, "end": end, "group_by": group_by, "kind": kind, "currency": base, "totals": _finish(tot), "rows": rows}


def _names(db: Database, group_by: str, ids: list) -> dict:
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    if group_by in ("category", "subcategory"):
        cats = {c["id"]: c for c in db.all("SELECT * FROM categories")}
        out = {}
        for i in ids:
            c = cats.get(i)
            if c:
                p = cats.get(c["parent_id"]) if c["parent_id"] else None
                out[i] = (ledger.category_label(p) + " › " if p and group_by == "subcategory" else "") + ledger.category_label(c)
        return out
    tbl = {"merchant": "merchants", "project": "projects", "account": "accounts"}[group_by]
    return {r["id"]: r["name"] for r in db.all(f"SELECT id, name FROM {tbl} WHERE id IN ({q})", ids)}
