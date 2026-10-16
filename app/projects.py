"""Projects 项目: group transactions across categories/accounts (trips, renovations, events...).

Totals use the same income/expense rules as Reports (ledger._flow_rows): reimbursable expenses are excluded and
reimbursement receipts are not income; reimbursable spending is shown separately. Amounts are in the base currency;
the project budget is converted from the project's currency for the comparison.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from . import ledger, plan_lines
from .db import Database, now_iso

FIELDS = ("name", "description", "start_date", "end_date", "budget", "currency", "color", "icon", "archived", "note", "sort")
FAR_PAST, FAR_FUTURE = "0000-01-01", "9999-12-31 99"


class ProjectError(ValueError):
    pass


def clean(db: Database, body: dict, partial: bool = False) -> dict:
    d = {k: body[k] for k in FIELDS if k in body}
    if "name" in d or not partial:
        d["name"] = str(d.get("name") or "").strip()
        if not d["name"]:
            raise ProjectError("name required")
    for k in ("start_date", "end_date"):
        if k in d:
            v = str(d[k] or "").strip()[:10]
            if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                raise ProjectError(f"{k} must be YYYY-MM-DD")
            d[k] = v or None
    if d.get("start_date") and d.get("end_date") and d["end_date"] < d["start_date"]:
        raise ProjectError("end_date is before start_date")
    if "budget" in d:
        try:
            d["budget"] = None if d["budget"] in ("", None) else round(float(d["budget"]), 2)
        except (TypeError, ValueError):
            raise ProjectError("budget must be a number")
        if d["budget"] is not None and d["budget"] < 0:
            raise ProjectError("budget must be positive")
    if "currency" in d:
        d["currency"] = (str(d["currency"] or "").strip().upper()) or None
        if d["currency"] and d["currency"] != db.get_setting("base_currency", "USD") and d["currency"] not in ledger.fx_rates(db):
            raise ProjectError(f"Unknown currency {d['currency']}: add it under Settings > Currencies")
    if "color" in d and d["color"] and not re.fullmatch(r"#[0-9a-fA-F]{3,8}", str(d["color"])):
        raise ProjectError("color must be like #3b82f6")
    if "icon" in d:
        d["icon"] = (str(d["icon"] or "").strip())[:8] or None
    if "archived" in d:
        d["archived"] = 1 if d["archived"] in (1, True, "1", "true", "on") else 0
    for k in ("description", "note"):
        if k in d:
            d[k] = (str(d[k] or "").strip()) or None
    return d


def create(db: Database, body: dict) -> dict:
    d = clean(db, body)
    if db.one("SELECT 1 x FROM projects WHERE name=?", (d["name"],)):
        raise ProjectError("A project with that name already exists")
    d["created_at"] = d["updated_at"] = now_iso()
    with db.tx() as con:
        pid = con.execute(f"INSERT INTO projects({','.join(d)}) VALUES({','.join('?' * len(d))})", list(d.values())).lastrowid
    return get(db, pid)


def update(db: Database, pid: int, body: dict) -> dict:
    if not db.one("SELECT 1 x FROM projects WHERE id=?", (pid,)):
        raise KeyError(pid)
    d = clean(db, body, partial=True)
    if d.get("name") and db.one("SELECT 1 x FROM projects WHERE name=? AND id<>?", (d["name"], pid)):
        raise ProjectError("A project with that name already exists")
    if d:
        d["updated_at"] = now_iso()
        with db.tx() as con:
            con.execute(f"UPDATE projects SET {','.join(k + '=?' for k in d)} WHERE id=?", list(d.values()) + [pid])
    return get(db, pid)


def delete(db: Database, pid: int) -> dict:
    with db.tx() as con:
        con.execute("DELETE FROM project_budgets WHERE project_id=?", (pid,))
        con.execute("DELETE FROM project_plan_line_cats WHERE line_id IN (SELECT id FROM project_plan_lines WHERE project_id=?)", (pid,))
        con.execute("DELETE FROM project_plan_lines WHERE project_id=?", (pid,))
        n = con.execute("UPDATE transactions SET project_id=NULL, plan_line_id=NULL WHERE project_id=?", (pid,)).rowcount
        con.execute("DELETE FROM projects WHERE id=?", (pid,))
    return {"ok": True, "unassigned": n}


def assign(db: Database, ids: list[int], project_id: int | None) -> dict:
    """Batch assign (project_id=None clears). Archived projects can't receive new transactions."""
    ids = [int(i) for i in ids or []]
    if not ids:
        raise ProjectError("No transactions selected")
    if project_id:
        p = db.one("SELECT archived FROM projects WHERE id=?", (project_id,))
        if not p:
            raise ProjectError("Unknown project")
        if p["archived"]:
            raise ProjectError("That project is archived: unarchive it first")
    with db.tx() as con:
        n = con.execute(f"UPDATE transactions SET project_id=?, updated_at=? WHERE id IN ({','.join('?' * len(ids))})",
                        [project_id, now_iso()] + ids).rowcount
        # a plan line only makes sense inside its project
        con.execute(f"""UPDATE transactions SET plan_line_id=NULL WHERE id IN ({','.join('?' * len(ids))}) AND plan_line_id IS NOT NULL
            AND plan_line_id NOT IN (SELECT id FROM project_plan_lines WHERE project_id IS ?)""", ids + [project_id])
    return {"ok": True, "updated": n, "project_id": project_id}


def _range(p: dict, start: str | None, end: str | None) -> tuple[str, str]:
    return (start or FAR_PAST), ((end + " 99") if end else FAR_FUTURE)


def plan_rows(db: Database, pid: int) -> list[dict]:
    return db.all("SELECT category_id, amount, note FROM project_budgets WHERE project_id=? ORDER BY category_id IS NULL, category_id", (pid,))


def set_plan(db: Database, pid: int, lines: list[dict], replace: bool = True) -> dict:
    """lines: [{category_id (parent, sub, or null = other/uncategorized), amount, note?}] in the project currency.
    amount 0/blank removes the line. replace=True replaces the whole plan."""
    get(db, pid)
    cats = {c["id"]: c for c in db.all("SELECT id, kind, parent_id FROM categories")}
    clean_lines = {}
    for ln in lines or []:
        cid = ln.get("category_id")
        cid = int(cid) if cid not in (None, "", 0, "0") else None
        if cid is not None and (cid not in cats or cats[cid]["kind"] != "expense"):
            raise ProjectError(f"category {cid} is not an expense category")
        try:
            amt = 0.0 if ln.get("amount") in (None, "") else round(float(ln["amount"]), 2)
        except (TypeError, ValueError):
            raise ProjectError("amount must be a number")
        if amt < 0:
            raise ProjectError("planned amounts must be positive")
        clean_lines[cid] = (amt, (str(ln.get("note") or "").strip() or None))
    with db.tx() as con:
        if replace:
            con.execute("DELETE FROM project_budgets WHERE project_id=?", (pid,))
        for cid, (amt, note) in clean_lines.items():
            con.execute("DELETE FROM project_budgets WHERE project_id=? AND COALESCE(category_id,0)=?", (pid, cid or 0))
            if amt > 0:
                con.execute("INSERT INTO project_budgets(project_id, category_id, amount, note) VALUES(?,?,?,?)", (pid, cid, amt, note))
    plan_lines.sync_category_plan(db, pid, clean_lines, replace)
    return {"project_id": pid, "lines": plan_rows(db, pid)}


def copy_plan(db: Database, pid: int, from_pid: int, scale: float = 1.0) -> dict:
    get(db, from_pid)
    src = plan_rows(db, from_pid)
    if not src:
        raise ProjectError("That project has no budget plan")
    return set_plan(db, pid, [{**r, "amount": round(r["amount"] * float(scale or 1), 2)} for r in src])


def plan_vs_actual(db: Database, p: dict, s: str, e: str, ledger_id=None) -> dict | None:
    """Planned vs actual per category (base currency). Parent rows roll up their sub-category plans (an explicit
    parent amount wins if larger); spending in categories without a plan goes to the Other line if one exists,
    and is listed under `unplanned` either way."""
    plan = plan_rows(db, p["id"])
    if not plan:
        return None
    base = db.get_setting("base_currency", "USD"); rates = ledger.fx_rates(db)
    conv = lambda v: round(ledger.convert(v, p.get("currency") or base, base, rates), 2)
    cats = {c["id"]: c for c in db.all("SELECT * FROM categories")}
    act_parent = {r["key"]: r["amount"] for r in ledger.breakdown(db, s, e, "category", "expense", ledger_id, p["id"])}
    act_sub = {r["key"]: r["amount"] for r in ledger.breakdown(db, s, e, "subcategory", "expense", ledger_id, p["id"])}
    planned = {r["category_id"]: conv(r["amount"]) for r in plan}
    notes = {r["category_id"]: r["note"] for r in plan}
    other_plan = planned.pop(None, None)
    parents: dict[int, dict] = {}
    for cid, amt in planned.items():
        c = cats[cid]
        pid_ = c["parent_id"] or cid
        par = parents.setdefault(pid_, {"own": None, "children": {}})
        if c["parent_id"]:
            par["children"][cid] = amt
        else:
            par["own"] = amt

    def line(cid, name, plan_amt, actual, level, note=None):
        rem = round(plan_amt - actual, 2)
        return {"category_id": cid, "name": name, "level": level, "planned": round(plan_amt, 2), "actual": round(actual, 2),
                "remaining": rem, "pct": round(actual / plan_amt * 100, 1) if plan_amt else None, "over": actual > plan_amt + 0.005,
                "note": note}
    rows, covered = [], set()
    for pid_, par in sorted(parents.items(), key=lambda kv: -(kv[1]["own"] or sum(kv[1]["children"].values()))):
        c = cats[pid_]
        kids_sum = sum(par["children"].values())
        tot = max(par["own"] or 0, kids_sum)
        rows.append({**line(pid_, ledger.category_label(c), tot, act_parent.get(pid_, 0), 0, notes.get(pid_)),
                     "rollup": bool(par["children"]) and (par["own"] or 0) < kids_sum})
        covered.add(pid_)
        for cid, amt in sorted(par["children"].items(), key=lambda kv: -kv[1]):
            rows.append(line(cid, ledger.category_label(cats[cid]), amt, act_sub.get(cid, 0), 1, notes.get(cid)))
        if par["children"] and par["own"] is None:  # spending in unplanned sub-categories of a planned parent
            rest = act_parent.get(pid_, 0) - sum(act_sub.get(cid, 0) for cid in par["children"])
            if abs(rest) > 0.005:
                rows.append({**line(None, "  other " + ledger.category_label(c) + " 其他", 0, rest, 1), "pct": None, "unplanned": True})
    unplanned = [{"category_id": k, "name": (ledger.category_label(cats[k]) if k in cats else "Uncategorized 未分类"),
                  "actual": round(v, 2)} for k, v in act_parent.items() if k not in covered and abs(v) > 0.005]
    unplanned.sort(key=lambda r: -r["actual"])
    un_total = round(sum(r["actual"] for r in unplanned), 2)
    if other_plan is not None:
        rows.append({**line(None, "Other / uncategorized 其他/未分类", other_plan, un_total, 0, notes.get(None)), "other": True})
    plan_total = round(sum(r["planned"] for r in rows if r["level"] == 0), 2)
    actual_total = round(sum(act_parent.values()), 2)
    budget = _budget_base(db, p, plan=False)
    total = budget if budget is not None else plan_total
    return {"rows": rows, "unplanned": unplanned, "unplanned_total": un_total, "plan_total": plan_total,
            "budget": total, "budget_source": "explicit" if budget is not None else "plan", "actual": actual_total,
            "remaining": round(total - actual_total, 2), "pct": round(actual_total / total * 100, 1) if total else None,
            "over": actual_total > total + 0.005, "currency": base}


def _budget_base(db: Database, p: dict, plan: bool = True) -> float | None:
    """Overall budget in base currency: the explicit project budget, else (plan=True) the sum of its plan lines."""
    if p.get("budget") is None:
        if plan:
            tot = db.one("SELECT SUM(amount) v FROM project_budgets WHERE project_id=?", (p["id"],))["v"]
            if tot:
                base = db.get_setting("base_currency", "USD")
                return round(ledger.convert(tot, p.get("currency") or base, base, ledger.fx_rates(db)), 2)
        return None
    base = db.get_setting("base_currency", "USD")
    return round(ledger.convert(p["budget"], p.get("currency") or base, base, ledger.fx_rates(db)), 2)


def _reimbursable(db: Database, pid: int, s: str, e: str, ledger_id=None) -> dict:
    """Reimbursable spending 可报销 in the project that is not closed yet (excluded from expense totals), base currency.
    outstanding = expected − reimbursed. Closed items count in the totals with their unreimbursed part (Reports rule)."""
    base = db.get_setting("base_currency", "USD"); rates = ledger.fx_rates(db)
    cond, params = "t.project_id=? AND t.status='posted' AND t.kind='expense' AND COALESCE(t.reimbursable,0)=1 " \
                   f"AND NOT {ledger.REIMB_CLOSED_SQL} AND t.date >= ? AND t.date < ?", [pid, s, e]
    if ledger_id:
        cond += " AND t.ledger_id=?"; params.append(ledger_id)
    tot = out = 0.0
    n = 0
    for r in db.all(f"SELECT t.amount, t.currency, {ledger.REIMB_EXPECTED_SQL} expected, {ledger.REIMB_SETTLED_SQL} settled "
                    f"FROM transactions t WHERE {cond}", params):
        tot += ledger.convert(r["amount"], r["currency"], base, rates)
        out += ledger.convert(max(r["expected"] - r["settled"], 0), r["currency"], base, rates)
        n += 1
    return {"amount": round(tot, 2) + 0.0, "outstanding": round(out, 2) + 0.0, "count": n}


def _summary(db: Database, p: dict, s: str, e: str, ledger_id=None) -> dict:
    t = ledger.totals(db, s, e, ledger_id, p["id"])
    b = _budget_base(db, p)
    stats = db.one("SELECT COUNT(*) n, MIN(date) first, MAX(date) last FROM transactions WHERE project_id=? AND status='posted' "
                   "AND date >= ? AND date < ?" + (" AND ledger_id=?" if ledger_id else ""),
                   [p["id"], s, e] + ([ledger_id] if ledger_id else []))
    return {"expense": t["expense"], "income": t["income"], "net": t["net"], "currency": t["currency"],
            "budget": b, "budget_remaining": None if b is None else round(b - t["expense"], 2),
            "budget_pct": None if not b else round(t["expense"] / b * 100, 1),
            "reimbursable": _reimbursable(db, p["id"], s, e, ledger_id),
            "count": stats["n"], "first_date": (stats["first"] or "")[:10] or None, "last_date": (stats["last"] or "")[:10] or None}


def get(db: Database, pid: int) -> dict:
    p = db.one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        raise KeyError(pid)
    return dict(p)


def listing(db: Database, include_archived: bool = False, ledger_id: int | None = None) -> dict:
    rows = db.all("SELECT * FROM projects" + ("" if include_archived else " WHERE COALESCE(archived,0)=0")
                  + " ORDER BY COALESCE(archived,0), COALESCE(sort,0), COALESCE(start_date,'9999') DESC, name")
    items = [{**p, "summary": _summary(db, p, FAR_PAST, FAR_FUTURE, ledger_id)} for p in rows]
    n_arch = db.one("SELECT COUNT(*) n FROM projects WHERE COALESCE(archived,0)=1")["n"]
    return {"items": items, "archived_count": n_arch, "currency": db.get_setting("base_currency", "USD")}


def detail(db: Database, pid: int, start: str | None = None, end: str | None = None, ledger_id: int | None = None,
           parent_id: int | None = None) -> dict:
    p = get(db, pid)
    s, e = _range(p, start, end)
    bd = lambda g, k="expense": ledger.breakdown(db, s, e, g, k, ledger_id, pid)
    months = [r for r in bd("month")]
    inc_m = {r["key"]: r["amount"] for r in bd("month", "income")}
    exp_m = {r["key"]: r["amount"] for r in months}
    keys = sorted(set(exp_m) | set(inc_m))
    if keys:  # continuous month axis
        y, m = map(int, keys[0].split("-")); ly, lm = map(int, keys[-1].split("-"))
        keys = []
        while (y, m) <= (ly, lm):
            keys.append(f"{y}-{m:02d}"); y, m = (y + (m == 12), m % 12 + 1)
    out = {"project": p, "start": start, "end": end, "summary": _summary(db, p, s, e, ledger_id),
           "by_category": bd("category"), "by_account": bd("account"), "by_member": bd("member"),
           "by_merchant": bd("merchant")[:30], "income_by_category": bd("category", "income"),
           "monthly": [{"month": k, "expense": round(exp_m.get(k, 0), 2), "income": round(inc_m.get(k, 0), 2)} for k in keys]}
    out["plan"] = plan_vs_actual(db, p, s, e, ledger_id)  # legacy category view
    out["plan_lines"] = plan_lines.plan_vs_actual(db, p, s, e, ledger_id)
    if parent_id:
        out["drill"] = {"parent_id": parent_id, "rows": ledger.breakdown(db, s, e, "subcategory", "expense", ledger_id, pid, parent_id)}
    return out


def parse_ctx_projects(db: Database) -> list[dict]:
    return db.all("SELECT id, name, archived FROM projects")
