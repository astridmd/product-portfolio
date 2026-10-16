"""Cost allocation engine and amortisation schedule.

Implements the capitalisation rules as parameterised rules:

  contribution      = weeks logged / (working weeks - holiday weeks)
  internal salary   = monthly salary / net weeks * weeks logged
  social security   = monthly amount * contribution   (ESOP the same, sign kept)
  contractor        = weeks logged * hours per week * hourly rate
  overhead          = rate * contribution, rate = pool / headcount

overhead_mode:
  derived         rates are computed from overhead_balance and headcount_input
  typed           rates are per-person amounts typed in by hand (rounded);
                  exists only so the reference test can reproduce a spreadsheet

Nothing here decides whether a project qualifies under IAS 38.57. The
capitalisation_status on the project is read as a recorded human decision.
"""
import json
from datetime import datetime, timezone

from decimal import Decimal

from src import load
from src.money import cents

GENERAL = "general"
DEPARTMENT = "department"
FALLBACK_FLAG = "rate_fallback"
BEFORE_START_FLAG = "before_start_date"


def _clear(conn):
    for table in ("posting_line", "posting", "asset_movement", "intangible_asset",
                  "capitalised_line", "overhead_rate", "calc_run"):
        conn.execute("DELETE FROM " + table)


def _compute_rates(conn, run_id):
    """Pool / headcount per entity, period and scope. Returns lookup dicts."""
    pools = {}
    for row in conn.execute(
            """SELECT entity_code, period, pool_type, department, SUM(amount) AS pool
               FROM overhead_balance GROUP BY entity_code, period, pool_type, department"""):
        pools[(row["entity_code"], row["period"], row["pool_type"], row["department"])] = row["pool"]
    heads = {}
    for row in conn.execute("SELECT * FROM headcount_input"):
        key = GENERAL if row["scope"] == "entity" else DEPARTMENT
        heads[(row["entity_code"], row["period"], key, row["department"])] = row["headcount"]
    rates = {}
    for key, pool in pools.items():
        headcount = heads.get(key)
        if not headcount:
            continue
        rate = pool / headcount
        rates[key] = rate
        conn.execute(
            "INSERT INTO overhead_rate VALUES (?,?,?,?,?,?,?,?)",
            (run_id, key[0], key[1], key[2], key[3], pool, headcount, rate))
    return rates


def _lookup_rate(rates, entity, period, pool_type, department):
    """Rate for the person's own entity, else the first entity that holds the pool."""
    own = rates.get((entity, period, pool_type, department))
    if own is not None:
        return own, False
    for (e, p, t, d), value in sorted(rates.items()):
        if p == period and t == pool_type and d == department:
            return value, True
    return 0.0, True


def run(conn, overhead_mode="derived"):
    if overhead_mode not in ("derived", "typed"):
        raise ValueError("unknown overhead_mode: %s" % overhead_mode)
    _clear(conn)
    params = {"overhead_mode": overhead_mode, "amortisation_start": "go-live month",
              "approved_status": "approved"}
    cur = conn.execute(
        "INSERT INTO calc_run (created_at, overhead_mode, input_hash, parameters) VALUES (?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), overhead_mode,
         load.input_hash(), json.dumps(params)))
    run_id = cur.lastrowid
    rates = _compute_rates(conn, run_id)

    capacity = {(r["entity_code"], r["person_type"], r["period"]): r
                for r in conn.execute("SELECT * FROM capacity_baseline")}
    costs = {}
    for r in conn.execute("SELECT * FROM cost_record"):
        costs[(r["person_id"], r["period"], r["cost_type"])] = r["amount"]
    typed = {(r["person_id"], r["period"]): r
             for r in conn.execute("SELECT * FROM typed_overhead_input")}
    persons = {r["person_id"]: r for r in conn.execute("SELECT * FROM person")}
    projects = {r["project_id"]: r for r in conn.execute("SELECT * FROM project")}

    lines = []
    for e in conn.execute("SELECT * FROM effort_record ORDER BY project_id, person_id, period"):
        person = persons[e["person_id"]]
        project = projects[e["project_id"]]
        if project["capitalisation_status"] != "approved" or e["weeks_logged"] <= 0:
            continue
        period = e["period"]
        cap = capacity[(person["entity_code"], person["person_type"], period)]
        net_weeks = cap["working_weeks"] - cap["holiday_weeks"]
        weeks = e["weeks_logged"]
        contribution = weeks / net_weeks
        flags = []
        if project["start_date"] and period < project["start_date"][:7]:
            flags.append(BEFORE_START_FLAG)

        def add(component, basis, amount, extra_flags=()):
            lines.append((run_id, e["person_id"], e["project_id"], project["entity_code"],
                          period, component, weeks, net_weeks, contribution, basis, amount,
                          ",".join(list(flags) + list(extra_flags))))

        if person["person_type"] == "internal":
            salary = costs[(e["person_id"], period, "salary")]
            add("salary", salary, salary / net_weeks * weeks)
            for comp in ("social_security", "esop"):
                basis = costs[(e["person_id"], period, comp)]
                add(comp, basis, basis * contribution)
        else:
            rate = costs[(e["person_id"], period, "hourly_rate")]
            add("contractor", rate, weeks * cap["hours_per_week"] * rate)

        if overhead_mode == "typed":
            t = typed[(e["person_id"], period)]
            general_rate, dept_rate = t["general_overhead"], t["dept_overhead"]
            general_fb = dept_fb = False
        else:
            general_rate, general_fb = _lookup_rate(
                rates, person["entity_code"], period, GENERAL, "")
            dept_rate, dept_fb = _lookup_rate(
                rates, person["entity_code"], period, DEPARTMENT, person["cost_centre"])
        add("general_overhead", general_rate, general_rate * contribution,
            [FALLBACK_FLAG] if general_fb else [])
        add("department_overhead", dept_rate, dept_rate * contribution,
            [FALLBACK_FLAG] if dept_fb else [])

    conn.executemany(
        """INSERT INTO capitalised_line (run_id, person_id, project_id, entity_code, period,
           cost_component, weeks_logged, net_weeks, contribution_pct, basis_amount, amount, flags)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", lines)
    _build_assets(conn, run_id)
    conn.commit()
    return run_id


def _month_add(period, months):
    year, month = int(period[:4]), int(period[5:7])
    index = year * 12 + (month - 1) + months
    return "%04d-%02d" % (index // 12, index % 12 + 1)


def _build_assets(conn, run_id):
    """One intangible asset per project with a go-live date and capitalised cost.

    The asset enters service in the go-live month (reclassification), and
    straight-line amortisation runs for useful_life_months starting that same
    month (30 Sep 2026 is month 1 of 60). Cents are rounded
    per month and the final month absorbs the rounding difference.
    """
    policy = conn.execute("SELECT * FROM amortisation_policy WHERE policy_id='default'").fetchone()
    life = policy["useful_life_months"]
    for p in conn.execute(
            """SELECT p.project_id, p.entity_code, p.go_live_date, SUM(l.amount) AS cost
               FROM project p JOIN capitalised_line l USING (project_id)
               WHERE p.go_live_date IS NOT NULL GROUP BY p.project_id"""):
        gross = cents(p["cost"])
        asset_id = "A-" + p["project_id"]
        start = p["go_live_date"][:7]
        conn.execute(
            "INSERT INTO intangible_asset VALUES (?,?,?,?,?,?,?,?)",
            (asset_id, run_id, p["project_id"], p["entity_code"], p["go_live_date"], float(gross),
             policy["policy_id"], life))
        conn.execute("INSERT INTO asset_movement VALUES (?,?,?,?,?,?,?)",
                     (asset_id, start, 0, "reclassification", float(gross), 0.0, float(gross)))
        monthly = cents(gross / life)
        accumulated = Decimal("0")
        for i in range(1, life + 1):
            amount = monthly if i < life else gross - accumulated
            accumulated += amount
            conn.execute("INSERT INTO asset_movement VALUES (?,?,?,?,?,?,?)",
                         (asset_id, _month_add(start, i - 1), i, "amortisation", float(amount),
                          float(accumulated), float(gross - accumulated)))
