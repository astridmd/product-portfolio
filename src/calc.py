"""Transformation, grouping and journal summary.

Transformation: straight line marketingBonusAmount / term from the placement month,
rounded to 8 decimals. Overnight (term 0): 100% in the placement month. The payout date
does not move recognition.

Grouping: one key per posting period and process type,
{posting_period_id}-{process_type_id}-{erp_currency_id}-{subsidiary_id}-{monthly_run_id}.

Journal summary: the amount behind each grouping key, summed over the deposit slices.
"""
from collections import defaultdict

from src.money import D, ZERO, q8


def get_config(conn):
    return dict(conn.execute("SELECT key, value FROM config").fetchall())


def period_id(year, month):
    return "%d%02d" % (year, month)


def advance(year, month, steps):
    index = year * 12 + (month - 1) + steps
    return index // 12, index % 12 + 1


def placement_period(placement_date):
    y, m, _ = (int(p) for p in placement_date.split("-"))
    return period_id(y, m)


def slices_for(event):
    """Recognition slices of one event as (month_index, posting_period_id, amount)."""
    y, m, _ = (int(p) for p in event["placement_date"].split("-"))
    amount = D(event["amount"])
    term = event["term"]
    if term == 0:
        return [(1, period_id(y, m), q8(amount))]
    monthly = q8(amount / D(term))
    out = []
    for idx in range(1, term + 1):
        yy, mm = advance(y, m, idx - 1)
        out.append((idx, period_id(yy, mm), monthly))
    return out


def run(conn):
    """Rebuild recognition_slice and journal_summary."""
    cfg = get_config(conn)
    win_start, win_end = cfg["window_start"], cfg["window_end"]
    conn.execute("DELETE FROM posting_line_source")
    conn.execute("DELETE FROM posting_line")
    conn.execute("DELETE FROM posting_period")
    conn.execute("DELETE FROM journal_summary")
    conn.execute("DELETE FROM recognition_slice")

    events = conn.execute("SELECT * FROM bonus_event ORDER BY event_id").fetchall()
    for e in events:
        for idx, ppid, amount in slices_for(e):
            in_window = 1 if win_start <= ppid <= win_end else 0
            conn.execute(
                "INSERT INTO recognition_slice (event_id, month_index, posting_period_id, "
                "recognised, in_window) VALUES (?,?,?,?,?)",
                (e["event_id"], idx, ppid, str(amount), in_window),
            )

    groups = defaultdict(lambda: [ZERO, 0])
    for r in conn.execute(
        "SELECT posting_period_id, process_type_id, recognised FROM v_slice_detail "
        "WHERE in_window = 1"
    ):
        g = groups[(r["posting_period_id"], r["process_type_id"])]
        g[0] += D(r["recognised"])
        g[1] += 1
    for (ppid, ptid), (amount, count) in sorted(groups.items()):
        conn.execute(
            "INSERT INTO journal_summary (posting_period_id, process_type_id, amount, "
            "grouping_key, slice_count) VALUES (?,?,?,?,?)",
            (ppid, ptid, str(amount), grouping_key(cfg, ppid, ptid), count),
        )
    conn.commit()


def grouping_key(cfg, ppid, ptid):
    return "%s-%s-%s-%s-%s" % (
        ppid, ptid, cfg["erp_currency_id"], cfg["subsidiary_id"], cfg["monthly_run_id"]
    )
