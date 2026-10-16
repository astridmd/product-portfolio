"""RaF journals per posting period.

Two steps, then one net journal:
  capitalisation  the whole bonus of deposits whose schedule starts in the month
  release         the recognised amount of the month, all active deposits
  net             capitalisation plus release, per account

The split into the two steps is our own reading, not something that has been confirmed.
The net journal is the one to post.

Amounts are rounded to cents at this step only. A difference up to the tolerance in config
goes on the largest credit line and the line is flagged. A larger one stops the run.
"""
from src.calc import get_config, placement_period
from src.money import D, ZERO, q2

PREPAYMENTS = "Prepayments"
CONTRACT_COSTS = "Contract costs (C)"
REVENUE = "Revenue"
SELLING = "Selling and distribution expenses"
ACCOUNTS = [PREPAYMENTS, CONTRACT_COSTS, REVENUE, SELLING]

REGIME_TEXT = {
    1: "N larger than R",
    2: "N between R / 2 and R",
    3: "N smaller than R / 2",
}
# Debit is +1, credit is -1, in the order of ACCOUNTS.
REGIME_SIGNS = {
    1: (1, 1, 1, -1),
    2: (-1, -1, 1, -1),
    3: (-1, -1, 1, 1),
}


def regime(n, r):
    """N is the new bonuses of the month, R is the recognised amount."""
    n, r = D(n), D(r)
    if n > r:
        return 1
    if n * 2 > r:
        return 2
    return 3


def net_formula(n, r):
    """Closed form of the net journal. Debit positive, credit negative, per account."""
    n, r = D(n), D(r)
    return {
        PREPAYMENTS: (n - r) / 2,
        CONTRACT_COSTS: (n - r) / 2,
        REVENUE: r / 2,
        SELLING: r / 2 - n,
    }


class PostingError(Exception):
    pass


def _balance(lines, tolerance, label):
    """Put a small rounding difference on the largest credit line. lines carry 'side' and 'amount'."""
    dr = sum((l["amount"] for l in lines if l["side"] == "Dr"), ZERO)
    cr = sum((l["amount"] for l in lines if l["side"] == "Cr"), ZERO)
    diff = dr - cr
    if diff == 0:
        return
    if abs(diff) > tolerance:
        raise PostingError("%s does not balance: debits %s credits %s" % (label, dr, cr))
    credits = [l for l in lines if l["side"] == "Cr"]
    if not credits:
        raise PostingError("%s has a rounding difference and no credit line" % label)
    target = max(credits, key=lambda l: l["amount"])
    target["amount"] += diff
    target["rounding_adjusted"] = 1


def _insert_line(conn, ppid, ptid, journal, line_no, account, side, amount, basis, share, adj):
    debit = amount if side == "Dr" else ZERO
    credit = amount if side == "Cr" else ZERO
    cur = conn.execute(
        "INSERT INTO posting_line (posting_period_id, process_type_id, journal, line_no, account, "
        "debit, credit, basis, share, rounding_adjusted) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ppid, ptid, journal, line_no, account, str(q2(debit)), str(q2(credit)), basis, share, adj),
    )
    return cur.lastrowid


def post(conn):
    """Rebuild posting_period, posting_line and posting_line_source from the journal summary."""
    cfg = get_config(conn)
    tolerance = D(cfg["rounding_tolerance"])
    conn.execute("DELETE FROM posting_line_source")
    conn.execute("DELETE FROM posting_line")
    conn.execute("DELETE FROM posting_period")

    ptids = [r[0] for r in conn.execute("SELECT DISTINCT process_type_id FROM posting_map")]
    periods = [r[0] for r in conn.execute(
        "SELECT DISTINCT posting_period_id FROM journal_summary ORDER BY posting_period_id")]
    events = conn.execute(
        "SELECT e.*, m.process_type_id FROM bonus_event e "
        "JOIN bonus_type_map m ON m.bonus_type = e.bonus_type ORDER BY e.event_id"
    ).fetchall()
    # Periods with new bonuses but no recognition in the window are still posted.
    for e in events:
        ppid = placement_period(e["placement_date"])
        if cfg["window_start"] <= ppid <= cfg["window_end"] and ppid not in periods:
            periods.append(ppid)
    periods.sort()

    for ptid in ptids:
        step_rows = {}
        for r in conn.execute(
            "SELECT * FROM posting_map WHERE process_type_id = ? ORDER BY step, line_no", (ptid,)
        ):
            step_rows.setdefault(r["step"], []).append(r)
        for ppid in periods:
            new_events = [e for e in events
                          if e["process_type_id"] == ptid and placement_period(e["placement_date"]) == ppid]
            slices = conn.execute(
                "SELECT slice_id, recognised FROM v_slice_detail WHERE posting_period_id = ? "
                "AND process_type_id = ? AND in_window = 1 ORDER BY slice_id", (ppid, ptid)
            ).fetchall()
            n = sum((D(e["amount"]) for e in new_events), ZERO)
            r_amt = sum((D(s["recognised"]) for s in slices), ZERO)
            if n == 0 and r_amt == 0:
                continue
            reg = regime(n, r_amt)
            conn.execute(
                "INSERT INTO posting_period (posting_period_id, process_type_id, new_bonuses, "
                "recognised, regime) VALUES (?,?,?,?,?)", (ppid, ptid, str(n), str(r_amt), reg))

            step_line_ids = {}  # (journal, account) -> (line_id, signed amount)
            for step in ("capitalisation", "release"):
                lines = []
                for row in step_rows.get(step, []):
                    base = n if row["basis"] == "new_bonuses" else r_amt
                    lines.append(dict(row=row, side=row["side"], raw=base * D(row["share"]),
                                      amount=q2(base * D(row["share"])), rounding_adjusted=0))
                _balance(lines, tolerance, "%s %s %s" % (ppid, ptid, step))
                for l in lines:
                    if l["amount"] == 0:
                        continue
                    row = l["row"]
                    lid = _insert_line(conn, ppid, ptid, step, row["line_no"], row["account"],
                                       row["side"], l["amount"], row["basis"], row["share"],
                                       l["rounding_adjusted"])
                    sign = 1 if row["side"] == "Dr" else -1
                    step_line_ids[(step, row["account"])] = (lid, sign * l["amount"])
                    share = D(row["share"])
                    if step == "capitalisation":
                        for e in new_events:
                            conn.execute(
                                "INSERT INTO posting_line_source VALUES (?,?,?,?)",
                                (lid, "event", e["event_id"], str(D(e["amount"]) * share)))
                    else:
                        for s in slices:
                            conn.execute(
                                "INSERT INTO posting_line_source VALUES (?,?,?,?)",
                                (lid, "slice", str(s["slice_id"]), str(D(s["recognised"]) * share)))

            # Net journal: step lines added per account, in the order of the release step.
            order = []
            for step in ("release", "capitalisation"):
                for row in step_rows.get(step, []):
                    if row["account"] not in order:
                        order.append(row["account"])
            net_total_dr = net_total_cr = ZERO
            line_no = 0
            for account in order:
                parts = [(k, v) for k, v in step_line_ids.items() if k[1] == account]
                signed = sum((v[1] for _, v in parts), ZERO)
                if signed == 0:
                    continue
                line_no += 1
                side = "Dr" if signed > 0 else "Cr"
                lid = _insert_line(conn, ppid, ptid, "net", line_no, account, side, abs(signed),
                                   None, None, 0)
                for (step, _acct), (src_id, src_signed) in sorted(parts):
                    conn.execute("INSERT INTO posting_line_source VALUES (?,?,?,?)",
                                 (lid, "line", str(src_id), str(src_signed)))
                if side == "Dr":
                    net_total_dr += abs(signed)
                else:
                    net_total_cr += abs(signed)
            if net_total_dr != net_total_cr:
                raise PostingError("net journal %s %s does not balance" % (ppid, ptid))
    conn.commit()
