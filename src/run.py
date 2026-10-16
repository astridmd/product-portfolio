"""Entry point: load inputs, calculate, post, check, render the site.

Usage: python3 -m src.run [output_dir]   (default: public)
Exit code 1 if a reconciliation check fails.
"""
import os
import sys

from src import build_site, calc, load, posting
from src.money import fmt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(out_dir):
    conn = load.build(db_path=os.path.join(ROOT, "build", "prototype.db"))
    calc.run(conn)
    posting.post(conn)
    n_events = conn.execute("SELECT COUNT(*) FROM bonus_event").fetchone()[0]
    n_slices = conn.execute("SELECT COUNT(*) FROM recognition_slice").fetchone()[0]
    n_lines = conn.execute("SELECT COUNT(*) FROM posting_line").fetchone()[0]
    print("events: %d, slices: %d, journal lines: %d" % (n_events, n_slices, n_lines))
    cfg = calc.get_config(conn)
    p = conn.execute("SELECT * FROM posting_period WHERE posting_period_id = ? ORDER BY process_type_id",
                     (cfg["default_period"],)).fetchone()
    if p:
        print("%s: new bonuses %s, recognised %s" % (cfg["default_period"], fmt(p["new_bonuses"]),
                                                     fmt(p["recognised"], 2)))
    pages, check_list = build_site.build(conn, out_dir)
    failed = [c for c in check_list if not c["ok"]]
    print("checks: %d of %d pass" % (len(check_list) - len(failed), len(check_list)))
    for c in failed:
        print("FAIL: %s (%s)" % (c["name"], c["detail"]))
    print("wrote %d pages to %s" % (len(pages), out_dir))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "public")))
