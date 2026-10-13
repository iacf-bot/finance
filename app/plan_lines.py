"""Project plan lines 预算明细: a project's budget as free-named lines (Flights, Hotel, Meals...), each with a planned
amount (own currency), optional note/sort, and an optional mapping to one or more expense categories.

Actuals per line (base currency), each project transaction counted once:
  1. explicitly assigned to a line (transactions.plan_line_id) - always wins;
  2. else the line mapped to its sub-category, else the line mapped to its top-level category (lowest sort wins
     when several lines map the same category);
  3. else the catch_all line ("Other"), if any;
  4. else Unplanned.
Same spending rules as Reports (reimbursable spending excluded, refunds negative, transfers ignored)."""
from __future__ import annotations

import re
from datetime import datetime

from . import ledger
from .db import Database


class PlanError(ValueError):
    pass


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _project(db: Database, pid: int) -> dict:
    p = db.one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        raise KeyError(pid)
    return dict(p)


def lines(db: Database, pid: int) -> list[dict]:
    rows = db.all("SELECT * FROM project_plan_lines WHERE project_id=? ORDER BY sort, id", (pid,))
    maps: dict[int, list[int]] = {}
    for r in db.all("SELECT m.line_id, m.category_id FROM project_plan_line_cats m JOIN project_plan_lines l ON l.id=m.line_id "
                    "WHERE l.project_id=? ORDER BY m.category_id", (pid,)):
        maps.setdefault(r["line_id"], []).append(r["category_id"])
    return [{**dict(r), "catch_all": bool(r["catch_all"]), "category_ids": maps.get(r["id"], [])} for r in rows]


def _get_line(db: Database, pid: int, lid: int) -> dict:
    r = db.one("SELECT * FROM project_plan_lines WHERE id=? AND project_id=?", (lid, pid))
    if not r:
        raise KeyError(lid)
    return dict(r)


def _clean(db: Database, body: dict, partial: bool = False) -> tuple[dict, list[int] | None]:
    out: dict = {}
    if not partial or "name" in body:
        name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()
        if not name:
            raise PlanError("A plan line needs a name")
        if len(name) > 80:
            raise PlanError("Plan line names are limited to 80 characters")
        out["name"] = name
    if not partial or "amount" in body:
        try:
            amt = 0.0 if body.get("amount") in (None, "") else round(float(body["amount"]), 2)
        except (TypeError, ValueError):
            raise PlanError("amount must be a number")
        if amt < 0:
            raise PlanError("planned amounts must be positive")
        out["amount"] = amt
    if "currency" in body:
        cur = str(body.get("currency") or "").strip().upper() or None
        if cur and not re.fullmatch(r"[A-Z]{3}", cur):
            raise PlanError("currency must be a 3-letter code like USD")
        out["currency"] = cur
    if "note" in body:
        out["note"] = str(body.get("note") or "").strip()[:500] or None
    if "sort" in body and body.get("sort") not in (None, ""):
        try:
            out["sort"] = int(body["sort"])
        except (TypeError, ValueError):
            raise PlanError("sort must be a whole number")
    if "catch_all" in body:
        out["catch_all"] = 1 if body.get("catch_all") else 0
    cats = None
    if "category_ids" in body:
        raw = body.get("category_ids") or []
        if not isinstance(raw, (list, tuple)):
            raise PlanError("category_ids must be a list")
        known = {r["id"]: r["kind"] for r in db.all("SELECT id, kind FROM categories")}
        cats = []
        for c in raw:
            try:
                c = int(c)
            except (TypeError, ValueError):
                raise PlanError("category_ids must be category ids")
            if known.get(c) != "expense":
                raise PlanError(f"category {c} is not an expense category")
            if c not in cats:
                cats.append(c)
    return out, cats


def _write_line(con, pid: int, lid: int | None, fields: dict, cats: list[int] | None) -> int:
    stamp = _now()
    if fields.get("catch_all"):
        con.execute("UPDATE project_plan_lines SET catch_all=0 WHERE project_id=? AND id IS NOT ?", (pid, lid))
    if lid is None:
        if "sort" not in fields:
            fields["sort"] = (con.execute("SELECT COALESCE(MAX(sort),0) FROM project_plan_lines WHERE project_id=?",
                                          (pid,)).fetchone()[0] or 0) + 10
        cols = ["project_id", *fields, "created_at", "updated_at"]
        lid = con.execute(f"INSERT INTO project_plan_lines({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                          [pid, *fields.values(), stamp, stamp]).lastrowid
    elif fields:
        con.execute(f"UPDATE project_plan_lines SET {', '.join(k + '=?' for k in fields)}, updated_at=? WHERE id=?",
                    [*fields.values(), stamp, lid])
    if cats is not None:
        con.execute("DELETE FROM project_plan_line_cats WHERE line_id=?", (lid,))
        con.executemany("INSERT INTO project_plan_line_cats(line_id, category_id) VALUES(?,?)", [(lid, c) for c in cats])
    return lid


def create(db: Database, pid: int, body: dict) -> dict:
    _project(db, pid)
    fields, cats = _clean(db, body)
    with db.tx() as con:
        lid = _write_line(con, pid, None, fields, cats)
    return next(l for l in lines(db, pid) if l["id"] == lid)


def update(db: Database, pid: int, lid: int, body: dict) -> dict:
    _get_line(db, pid, lid)
    fields, cats = _clean(db, body, partial=True)
    with db.tx() as con:
        _write_line(con, pid, lid, fields, cats)
    return next(l for l in lines(db, pid) if l["id"] == lid)


def _delete_lines(con, ids: list[int]) -> None:
    if not ids:
        return
    q = ",".join("?" * len(ids))
    con.execute(f"UPDATE transactions SET plan_line_id=NULL WHERE plan_line_id IN ({q})", ids)
    con.execute(f"DELETE FROM project_plan_line_cats WHERE line_id IN ({q})", ids)
    con.execute(f"DELETE FROM project_plan_lines WHERE id IN ({q})", ids)


def delete(db: Database, pid: int, lid: int) -> dict:
    _get_line(db, pid, lid)
    with db.tx() as con:
        n = con.execute("SELECT COUNT(*) FROM transactions WHERE plan_line_id=?", (lid,)).fetchone()[0]
        _delete_lines(con, [lid])
    return {"ok": True, "deleted": lid, "unassigned_transactions": n}


def reorder(db: Database, pid: int, ids: list) -> dict:
    _project(db, pid)
    have = [l["id"] for l in lines(db, pid)]
    try:
        ids = [int(i) for i in ids or []]
    except (TypeError, ValueError):
        raise PlanError("ids must be plan line ids")
    if sorted(ids) != sorted(have):
        raise PlanError("ids must list every plan line of the project exactly once")
    with db.tx() as con:
        for i, lid in enumerate(ids):
            con.execute("UPDATE project_plan_lines SET sort=? WHERE id=?", ((i + 1) * 10, lid))
    return {"project_id": pid, "lines": lines(db, pid)}


def bulk(db: Database, pid: int, items: list[dict], replace: bool = False) -> dict:
    """Create many lines at once (templates, copy). replace=True removes the existing lines first (their explicit
    transaction assignments are cleared)."""
    _project(db, pid)
    cleaned = [_clean(db, it) for it in items or []]
    if not cleaned:
        raise PlanError("No plan lines given")
    with db.tx() as con:
        if replace:
            _delete_lines(con, [r[0] for r in con.execute("SELECT id FROM project_plan_lines WHERE project_id=?", (pid,))])
        for fields, cats in cleaned:
            fields.pop("sort", None)
            _write_line(con, pid, None, fields, cats)
    return {"project_id": pid, "lines": lines(db, pid)}


def copy_from(db: Database, pid: int, from_pid: int, scale: float = 1.0, replace: bool = True) -> dict:
    _project(db, from_pid)
    src = lines(db, from_pid)
    if not src:
        raise PlanError("That project has no plan lines")
    try:
        scale = float(scale or 1)
    except (TypeError, ValueError):
        raise PlanError("scale must be a number")
    return bulk(db, pid, [{"name": l["name"], "amount": round(l["amount"] * scale, 2), "currency": l["currency"],
                           "note": l["note"], "catch_all": l["catch_all"], "category_ids": l["category_ids"]} for l in src],
                replace=replace)


# ---------------------------------------------------------------- templates
# keywords are matched against category names (English name and name_zh), whole words / substrings, case-insensitive
TEMPLATES: dict[str, dict] = {
    "vacation": {"name": "Vacation", "lines": [
        ("Flights", ["flight", "airfare", "air ticket", "机票"]),
        ("Hotel", ["hotel", "lodging", "accommodation", "住宿", "酒店"]),
        ("Transportation", ["transportation", "taxi", "rideshare", "train", "public transit", "交通", "打车"]),
        ("Meals", ["meals", "dining", "restaurant", "早午晚餐", "餐饮"]),
        ("Activities / Tickets", ["tickets", "activities", "entertainment", "attraction", "门票", "休闲娱乐"]),
        ("Shopping", ["shopping", "souvenir", "购物"]),
        ("Insurance", ["travel insurance", "insurance", "保险"]),
        ("Other", None)]},
    "renovation": {"name": "Home renovation", "lines": [
        ("Design / Permits", ["design", "permit", "设计"]),
        ("Materials", ["materials", "hardware", "home improvement", "建材"]),
        ("Labor", ["labor", "contractor", "装修", "repair", "维修"]),
        ("Appliances", ["appliance", "家电"]),
        ("Furniture", ["furniture", "家具"]),
        ("Cleanup / Disposal", ["cleaning", "清洁"]),
        ("Contingency", None)]},
    "event": {"name": "Event / Wedding", "lines": [
        ("Venue", ["venue", "场地"]),
        ("Catering", ["catering", "meals", "dining", "餐饮"]),
        ("Attire", ["clothing", "apparel", "服饰", "衣服"]),
        ("Photography", ["photo", "摄影"]),
        ("Decor / Flowers", ["flowers", "decor", "鲜花"]),
        ("Gifts / Favors", ["gifts", "礼"]),
        ("Travel", ["travel", "flight", "hotel", "旅行", "旅游"]),
        ("Other", None)]},
}


def _match_cats(cats: list[dict], words: list[str]) -> list[int]:
    """Best category for each keyword: exact name > word match > substring; sub-categories preferred over parents.
    One category per keyword, deduped; nothing when no category resembles the keyword."""
    out: list[int] = []
    for w in words:
        wl = w.lower()
        best, score = None, 0
        for c in cats:
            for nm in (c.get("name") or "", c.get("name_zh") or ""):
                n = nm.lower()
                if not n:
                    continue
                if n == wl:
                    s = 30
                elif re.search(r"(?<![a-z])" + re.escape(wl) + r"s?(?![a-z])", n) or (re.search(r"[\u3400-\u9fff]", wl) and wl in n):
                    s = 20
                else:
                    continue
                s += 1 if c.get("parent_id") else 0
                if s > score:
                    best, score = c["id"], s
        if best is not None and best not in out:
            out.append(best)
    # a matched parent is redundant (or too broad, e.g. all of Food & Dining for "Meals") when sub-categories of it matched
    par = {c["id"]: c.get("parent_id") for c in cats}
    return [c for c in out if not any(par.get(o) == c for o in out)]


def templates(db: Database) -> list[dict]:
    cats = [dict(c) for c in db.all("SELECT id, name, name_zh, parent_id FROM categories WHERE kind='expense'")]
    out = []
    for key, t in TEMPLATES.items():
        out.append({"key": key, "name": t["name"], "lines": [
            {"name": nm, "amount": 0, "catch_all": words is None, "category_ids": _match_cats(cats, words) if words else []}
            for nm, words in t["lines"]]})
    return out


def apply_template(db: Database, pid: int, key: str, replace: bool = False, amounts: dict | None = None,
                   names: dict | None = None) -> dict:
    """amounts / names: optional {template line name: amount / display name} (the UI passes names in its language)."""
    t = next((t for t in templates(db) if t["key"] == key), None)
    if not t:
        raise PlanError(f"Unknown template {key!r}")
    amounts, names = amounts or {}, names or {}
    if not isinstance(amounts, dict) or not isinstance(names, dict):
        raise PlanError("amounts and names must be objects keyed by template line name")
    items = [{**l, "amount": amounts.get(l["name"], 0), "name": str(names.get(l["name"]) or l["name"])} for l in t["lines"]]
    return bulk(db, pid, items, replace=replace)


# ---------------------------------------------------------------- legacy category plan → lines
def sync_category_plan(db: Database, pid: int, changes: dict, replace: bool) -> None:
    """Mirror PUT /api/projects/{id}/plan (category_id → amount, note) into plan lines. replace=True rebuilds the
    lines from the category plan; otherwise only the line mapped solely to that category (or the catch_all line
    for category None) is updated, created, or removed (amount 0)."""
    cats = {c["id"]: dict(c) for c in db.all("SELECT id, name, parent_id FROM categories")}

    def label(cid):
        c = cats[cid]
        return (cats[c["parent_id"]]["name"] + " › " + c["name"]) if c["parent_id"] in cats else c["name"]
    cur = lines(db, pid)
    with db.tx() as con:
        if replace:
            _delete_lines(con, [l["id"] for l in cur])
            cur = []
        for cid, (amt, note) in changes.items():
            hit = next((l for l in cur if (l["catch_all"] if cid is None else l["category_ids"] == [cid])), None)
            if amt <= 0:
                if hit:
                    _delete_lines(con, [hit["id"]])
                continue
            fields = {"amount": amt, "note": note}
            if hit:
                _write_line(con, pid, hit["id"], fields, None)
            else:
                _write_line(con, pid, None, {"name": "Other" if cid is None else label(cid), **fields,
                                             "catch_all": 1 if cid is None else 0}, None if cid is None else [cid])


# ---------------------------------------------------------------- plan vs actual
def _resolve(rows_lines: list[dict]) -> tuple[dict, int | None, set]:
    by_cat: dict[int, int] = {}
    for l in rows_lines:  # already sorted by sort, id: first line wins
        for c in l["category_ids"]:
            by_cat.setdefault(c, l["id"])
    catch = next((l["id"] for l in rows_lines if l["catch_all"]), None)
    return by_cat, catch, {l["id"] for l in rows_lines}


def assign_rows(db: Database, pid: int, s: str, e: str, ledger_id=None) -> tuple[list[dict], list[dict]]:
    """(lines, flow rows with 'line_id' (None = unplanned) and base-currency 'value')."""
    ls = lines(db, pid)
    by_cat, catch, ids = _resolve(ls)
    base = db.get_setting("base_currency", "USD"); rates = ledger.fx_rates(db)
    out = []
    for r in ledger._flow_rows(db, s, e, ledger_id, pid):
        if r["kind"] not in ("expense", "refund"):
            continue
        v = ledger.convert(r["amount"], r["currency"], base, rates) * (-1 if r["kind"] == "refund" else 1)
        lid = r.get("plan_line_id") if r.get("plan_line_id") in ids else None
        if lid is None:
            lid = by_cat.get(r["category_id"]) or by_cat.get(r.get("parent_id")) or catch
        out.append({**r, "line_id": lid, "value": v})
    return ls, out


def plan_vs_actual(db: Database, p: dict, s: str, e: str, ledger_id=None) -> dict | None:
    ls, rows = assign_rows(db, p["id"], s, e, ledger_id)
    if not ls:
        return None
    base = db.get_setting("base_currency", "USD"); rates = ledger.fx_rates(db)
    cat_rows = {c["id"]: c for c in db.all("SELECT * FROM categories")}
    act: dict = {}
    cnt: dict = {}
    for r in rows:
        act[r["line_id"]] = act.get(r["line_id"], 0.0) + r["value"]
        cnt[r["line_id"]] = cnt.get(r["line_id"], 0) + 1
    out_lines = []
    for l in ls:
        cur = l["currency"] or p.get("currency") or base
        planned = round(ledger.convert(l["amount"], cur, base, rates), 2)
        a = round(act.get(l["id"], 0.0), 2) + 0.0
        out_lines.append({**l, "currency": cur, "planned_base": planned, "actual": a, "remaining": round(planned - a, 2) + 0.0,
                          "pct": round(a / planned * 100, 1) if planned else None, "over": a > planned + 0.005,
                          "count": cnt.get(l["id"], 0),
                          "categories": [ledger.category_label(cat_rows[c]) for c in l["category_ids"] if c in cat_rows]})
    un = round(act.get(None, 0.0), 2) + 0.0
    by: dict = {}
    for r in rows:
        if r["line_id"] is None:
            by[r["category_id"]] = by.get(r["category_id"], 0.0) + r["value"]
    unplanned_by = sorted(({"category_id": k, "name": ledger.category_label(cat_rows[k]) if k in cat_rows else "Uncategorized",
                            "actual": round(v, 2)} for k, v in by.items() if abs(v) > 0.005), key=lambda x: -x["actual"])
    plan_total = round(sum(l["planned_base"] for l in out_lines), 2)
    actual = round(sum(r["value"] for r in rows), 2) + 0.0
    budget = None
    if p.get("budget") is not None:
        budget = round(ledger.convert(p["budget"], p.get("currency") or base, base, rates), 2)
    total = budget if budget is not None else plan_total
    return {"lines": out_lines, "unplanned": {"actual": un, "count": cnt.get(None, 0), "by_category": unplanned_by},
            "plan_total": plan_total, "actual": actual, "budget": total, "budget_source": "explicit" if budget is not None else "plan",
            "remaining": round(total - actual, 2) + 0.0, "pct": round(actual / total * 100, 1) if total else None,
            "over": actual > total + 0.005, "currency": base}


def line_transactions(db: Database, pid: int, line: str | int, s: str, e: str, ledger_id=None) -> dict:
    """Transactions counted in one line (line id) or in 'unplanned'."""
    _project(db, pid)
    want = None if str(line) == "unplanned" else int(line)
    if want is not None:
        _get_line(db, pid, want)
    _, rows = assign_rows(db, pid, s, e, ledger_id)
    ids = [r["id"] for r in rows if r["line_id"] == want and r.get("id")]
    txs = []
    if ids:
        txs = [t for t in (ledger.get_tx(db, i) for i in ids) if t]
        txs.sort(key=lambda t: (t["date"], t["id"]), reverse=True)
    expl = {r["id"] for r in rows if r.get("plan_line_id") == want and want is not None}
    for t in txs:
        t["plan_match"] = "explicit" if t["id"] in expl else ("category" if want is not None else None)
    return {"project_id": pid, "line": want if want is not None else "unplanned", "transactions": txs,
            "total": round(sum(r["value"] for r in rows if r["line_id"] == want), 2) + 0.0}


def parse_ctx(db: Database) -> list[dict]:
    return db.all("SELECT id, project_id, name, sort FROM project_plan_lines ORDER BY sort, id")
