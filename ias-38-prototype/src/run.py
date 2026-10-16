"""Entry point: load inputs, calculate, post, render the site.

Usage: python3 -m src.run [output_dir]   (default: public)
"""
import os
import sys

from src import build_site, calc, load, posting


def main(out_dir):
    conn = load.build()
    calc.run(conn, "derived")
    posting.post(conn)
    totals = conn.execute("SELECT COUNT(*), SUM(amount) FROM capitalised_line").fetchone()
    n_post = conn.execute("SELECT COUNT(*) FROM posting").fetchone()[0]
    print("capitalised lines: %d, total %.2f, postings: %d" % (totals[0], totals[1], n_post))
    pages = build_site.build(out_dir)
    print("wrote %d pages to %s" % (len(pages), out_dir))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "public")
