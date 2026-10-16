"""Posting logic: project value journals, go-live reclassification, amortisation.

Each journal is one posting per (entity, period, type, asset). Amounts are
rounded to cents here and nowhere earlier. Every journal must balance; a
rounding difference of at most 0.05 is put on the largest credit line, anything
larger raises. Zero lines are dropped. A negative credit (the example's ESOP)
is kept as a negative credit, not flipped to a debit, so the line still maps to
its allocation component. Output is a proposal; nothing is sent to an ERP.
"""
import re
from decimal import Decimal

from src.money import cents

WIP = "1001"
IN_SERVICE = "1002"
ACCUMULATED = "1009"
EXPENSE = "5001"
TOLERANCE = Decimal("0.05")

COMPONENT_ORDER = ["salary", "social_security", "esop", "contractor",
                   "general_overhead", "department_overhead"]


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def anchor_project(project_id, period):
    return "proj-%s-%s" % (project_id, period)


def anchor_component(entity, period, component):
    return "ent-%s-%s-%s" % (slug(entity), period, component)


class PostingError(Exception):
    pass


def _insert(conn, run_id, entity, period, ptype, asset_id, lines):
    debit = sum((Decimal(str(l["debit"])) for l in lines), Decimal("0"))
    credit = sum((Decimal(str(l["credit"])) for l in lines), Decimal("0"))
    diff = debit - credit
    if diff != 0:
        if abs(diff) > TOLERANCE:
            raise PostingError("journal %s %s %s out of balance by %s"
                               % (entity, period, ptype, diff))
        credits = [l for l in lines if l["credit"] != 0]
        largest = max(credits, key=lambda l: abs(l["credit"]))
        largest["credit"] = float(Decimal(str(largest["credit"])) + diff)
        largest["description"] += " (includes rounding %s)" % diff
    cur = conn.execute(
        "INSERT INTO posting (run_id, entity_code, period, posting_type, asset_id) VALUES (?,?,?,?,?)",
        (run_id, entity, period, ptype, asset_id))
    conn.executemany(
        """INSERT INTO posting_line (posting_id, account_code, project_id, debit, credit,
           description, source_ref) VALUES (?,?,?,?,?,?,?)""",
        [(cur.lastrowid, l["account"], l.get("project"), l["debit"], l["credit"],
          l["description"], l["source_ref"]) for l in lines])


def post(conn):
    """Build all postings for the latest calculation run. Safe to call repeatedly."""
    run_id = conn.execute("SELECT MAX(run_id) FROM calc_run").fetchone()[0]
    if run_id is None:
        raise PostingError("run calc first")
    conn.execute("DELETE FROM posting_line")
    conn.execute("DELETE FROM posting")
    account_for = {r["cost_component"]: r["account_code"]
                   for r in conn.execute("SELECT * FROM posting_map")}
    names = {r["account_code"]: r["account_name"] for r in conn.execute("SELECT * FROM gl_account")}

    # 1. Project value: debit WIP per project, credit capitalised-cost accounts per component.
    groups = conn.execute(
        "SELECT DISTINCT entity_code, period FROM capitalised_line ORDER BY entity_code, period")
    for g in groups.fetchall():
        entity, period = g["entity_code"], g["period"]
        lines = []
        for p in conn.execute(
                """SELECT project_id, SUM(amount) AS v FROM capitalised_line
                   WHERE entity_code=? AND period=? GROUP BY project_id ORDER BY project_id""",
                (entity, period)):
            amount = cents(p["v"])
            if amount != 0:
                lines.append({"account": WIP, "project": p["project_id"], "debit": float(amount),
                              "credit": 0.0,
                              "description": "%s capitalised cost %s" % (p["project_id"], period),
                              "source_ref": anchor_project(p["project_id"], period)})
        by_component = {r["cost_component"]: r["v"] for r in conn.execute(
            """SELECT cost_component, SUM(amount) AS v FROM capitalised_line
               WHERE entity_code=? AND period=? GROUP BY cost_component""", (entity, period))}
        for comp in COMPONENT_ORDER:
            if comp not in by_component:
                continue
            amount = cents(by_component[comp])
            if amount != 0:
                code = account_for[comp]
                lines.append({"account": code, "project": None, "debit": 0.0,
                              "credit": float(amount),
                              "description": names[code],
                              "source_ref": anchor_component(entity, period, comp)})
        _insert(conn, run_id, entity, period, "project_value", "", lines)

    # 2 and 3. Asset reclassification at go-live, then monthly amortisation.
    for a in conn.execute("SELECT * FROM intangible_asset ORDER BY asset_id").fetchall():
        for m in conn.execute(
                "SELECT * FROM asset_movement WHERE asset_id=? ORDER BY period_index",
                (a["asset_id"],)).fetchall():
            amount = float(cents(m["amount"]))
            if m["movement_type"] == "reclassification":
                lines = [
                    {"account": IN_SERVICE, "project": a["project_id"], "debit": amount,
                     "credit": 0.0, "description": "Transfer %s into service" % a["project_id"],
                     "source_ref": "asset-" + a["asset_id"]},
                    {"account": WIP, "project": a["project_id"], "debit": 0.0, "credit": amount,
                     "description": "Clear WIP for %s" % a["project_id"],
                     "source_ref": "asset-" + a["asset_id"]}]
                _insert(conn, run_id, a["entity_code"], m["period"], "reclassification",
                        a["asset_id"], lines)
            else:
                text = "Month %d of %d straight-line" % (m["period_index"], a["useful_life_months"])
                lines = [
                    {"account": EXPENSE, "project": a["project_id"], "debit": amount,
                     "credit": 0.0, "description": text,
                     "source_ref": "asset-" + a["asset_id"]},
                    {"account": ACCUMULATED, "project": a["project_id"], "debit": 0.0,
                     "credit": amount, "description": text,
                     "source_ref": "asset-" + a["asset_id"]}]
                _insert(conn, run_id, a["entity_code"], m["period"], "amortisation",
                        a["asset_id"], lines)
    conn.commit()
    return run_id
