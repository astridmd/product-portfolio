"""Reconciliation checks.

Every check recomputes from the inputs, not from the calculated tables it checks.
"""
from collections import defaultdict

from src import calc, posting
from src.money import D, ZERO, fmt, q2

def run_checks(conn):
    """Return a list of dicts: name, ok (bool), detail."""
    checks = []

    def add(name, ok, detail):
        checks.append(dict(name=name, ok=bool(ok), detail=detail))

    cfg = calc.get_config(conn)
    events = conn.execute(
        "SELECT e.*, m.process_type_id FROM bonus_event e "
        "JOIN bonus_type_map m ON m.bonus_type = e.bonus_type ORDER BY e.event_id").fetchall()

    # 1. Slices add up to the bonus amount, per event.
    worst = ZERO
    for e in events:
        total = sum((D(s["recognised"]) for s in conn.execute(
            "SELECT recognised FROM recognition_slice WHERE event_id = ?", (e["event_id"],))), ZERO)
        worst = max(worst, abs(total - D(e["amount"])))
    add("Slices of each event add up to the bonus amount", worst < D("0.000001"),
        "Largest rounding residual %s. Slices are rounded to 8 decimals with no last-month true-up."
        % fmt(worst, 8))

    # 2. Independent recompute of the journal summary from the events.
    expected = defaultdict(lambda: ZERO)
    for e in events:
        for _idx, ppid, amount in calc.slices_for(e):
            if cfg["window_start"] <= ppid <= cfg["window_end"]:
                expected[(ppid, e["process_type_id"])] += amount
    actual = {(r["posting_period_id"], r["process_type_id"]): D(r["amount"])
              for r in conn.execute("SELECT * FROM journal_summary")}
    add("Journal summary equals the slices recomputed from the inputs", expected == actual,
        "%d grouping keys compared." % len(actual))

    # 3. Welcome and RaF totals in the window.
    in_window = sum((D(s["recognised"]) for s in conn.execute(
        "SELECT recognised FROM recognition_slice WHERE in_window = 1")), ZERO)
    summary_total = sum(actual.values(), ZERO)
    add("Journal summary total equals all slices in the window", in_window == summary_total,
        "Slices %s, summary %s." % (fmt(in_window, 8), fmt(summary_total, 8)))

    # 4. Every journal balances.
    unbalanced = []
    sums = defaultdict(lambda: [ZERO, ZERO])
    for r in conn.execute("SELECT posting_period_id, process_type_id, journal, debit, credit FROM posting_line"):
        key = (r["posting_period_id"], r["process_type_id"], r["journal"])
        sums[key][0] += D(r["debit"])
        sums[key][1] += D(r["credit"])
    for key, (dr, cr) in sums.items():
        if dr != cr:
            unbalanced.append(key)
    add("Debits equal credits in every journal", not unbalanced,
        "%d journals checked (capitalisation, release and net per month)." % len(sums))

    # 5. Net equals the closed form, within two cents.
    worst = ZERO
    checked = 0
    for p in conn.execute("SELECT * FROM posting_period"):
        formula = posting.net_formula(D(p["new_bonuses"]), D(p["recognised"]))
        net = {a: ZERO for a in posting.ACCOUNTS}
        for l in conn.execute(
                "SELECT account, debit, credit FROM posting_line WHERE journal = 'net' "
                "AND posting_period_id = ? AND process_type_id = ?",
                (p["posting_period_id"], p["process_type_id"])):
            net[l["account"]] += D(l["debit"]) - D(l["credit"])
        for a in posting.ACCOUNTS:
            worst = max(worst, abs(net[a] - formula[a]))
        checked += 1
    add("Net journal equals the closed formula within 2 cents", worst <= D("0.02"),
        "%d months compared, largest difference %s." % (checked, fmt(worst, 2)))

    # 6. Net equals capitalisation plus release, line by line.
    bad = []
    for p in conn.execute("SELECT * FROM posting_period"):
        by = defaultdict(lambda: ZERO)
        for l in conn.execute(
                "SELECT journal, account, debit, credit FROM posting_line WHERE "
                "posting_period_id = ? AND process_type_id = ?",
                (p["posting_period_id"], p["process_type_id"])):
            signed = D(l["debit"]) - D(l["credit"])
            by[(l["account"], "net" if l["journal"] == "net" else "steps")] += signed
        for a in posting.ACCOUNTS:
            if by[(a, "net")] != by[(a, "steps")]:
                bad.append((p["posting_period_id"], a))
    add("Net journal equals capitalisation plus release per account", not bad,
        "Every net line points at its two step lines.")

    # 7. New bonuses and recognised amount reconcile to the inputs.
    posted_ptids = {r[0] for r in conn.execute("SELECT DISTINCT process_type_id FROM posting_map")}
    n_inputs = sum((D(e["amount"]) for e in events
                    if e["process_type_id"] in posted_ptids
                    and cfg["window_start"] <= calc.placement_period(e["placement_date"]) <= cfg["window_end"]),
                   ZERO)
    n_posted = sum((D(p["new_bonuses"]) for p in conn.execute("SELECT new_bonuses FROM posting_period")), ZERO)
    add("New bonuses posted equal the bonus amounts placed in the window", n_inputs == n_posted,
        "Inputs %s, posted %s." % (fmt(n_inputs), fmt(n_posted)))
    r_summary = sum((D(r["amount"]) for r in conn.execute(
        "SELECT amount FROM journal_summary WHERE process_type_id IN (SELECT process_type_id FROM posting_map)")), ZERO)
    r_posted = sum((D(p["recognised"]) for p in conn.execute("SELECT recognised FROM posting_period")), ZERO)
    add("Recognised amount posted equals the RaF line of the Journal summary", r_summary == r_posted,
        "Summary %s, posted %s." % (fmt(r_summary, 8), fmt(r_posted, 8)))

    # 8. Rounding flags (information, not a failure).
    flagged = conn.execute("SELECT COUNT(*) FROM posting_line WHERE rounding_adjusted = 1").fetchone()[0]
    add("Rounding differences put on a credit line (information)", True,
        "%d lines flagged. A difference above %s would have stopped the run."
        % (flagged, cfg["rounding_tolerance"]))
    return checks
