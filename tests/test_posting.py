"""Posting rules on small synthetic cases, and input validation."""
import csv
import os
import shutil
import sqlite3
import tempfile
import unittest

from src import calc, load, posting
from src.money import D

EVENT_HEADER = ["event_id", "sample", "deposit_id", "bonus_type", "product_id", "term",
                "amount", "placement_date", "payout_date"]


def make_conn(events=None, config=None, mutate=None):
    """Build a database from a temporary copy of data/ with replaced events or config."""
    tmp = tempfile.mkdtemp()
    try:
        for name in os.listdir(load.DATA_DIR):
            shutil.copy(os.path.join(load.DATA_DIR, name), tmp)
        if events is not None:
            with open(os.path.join(tmp, "bonus_event.csv"), "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(EVENT_HEADER)
                w.writerows(events)
        if config:
            rows = load.read_csv("config", tmp)
            for r in rows:
                if r["key"] in config:
                    r["value"] = config[r["key"]]
            with open(os.path.join(tmp, "config.csv"), "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["key", "value"])
                w.writeheader()
                w.writerows(rows)
        if mutate:
            mutate(tmp)
        conn = load.build(data_dir=tmp)
    finally:
        shutil.rmtree(tmp)
    return conn


def lines(conn, journal, period):
    return {r["account"]: (r["debit"], r["credit"], r["rounding_adjusted"]) for r in conn.execute(
        "SELECT * FROM posting_line WHERE journal = ? AND posting_period_id = ?", (journal, period))}


class RegimeTest(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(posting.regime(120, 40), 1)
        self.assertEqual(posting.regime(40, 40), 2)
        self.assertEqual(posting.regime(21, 40), 2)
        self.assertEqual(posting.regime(20, 40), 3)
        self.assertEqual(posting.regime(0, 40), 3)

    def test_formula_sides_follow_the_sign_table(self):
        for n in range(0, 301, 10):
            for r in range(10, 301, 10):
                reg = posting.regime(n, r)
                f = posting.net_formula(n, r)
                for acct, sign in zip(posting.ACCOUNTS, posting.REGIME_SIGNS[reg]):
                    v = f[acct]
                    if v != 0:
                        self.assertEqual(1 if v > 0 else -1, sign, (n, r, acct))

    def test_formula_balances(self):
        f = posting.net_formula(D("123.45"), D("67.89"))
        self.assertEqual(sum(f.values()), 0)


class EngineTest(unittest.TestCase):
    def test_overnight_raf_does_both_steps_in_the_same_month(self):
        conn = make_conn(events=[
            ["X1", "Overnight RaF", "OMA_X_001", "RaF Bonus (Lead)", 2, 0, "50.00", "2026-05-01", "2026-05-20"]])
        calc.run(conn)
        posting.post(conn)
        self.assertEqual(len(lines(conn, "capitalisation", "202605")), 3)
        self.assertEqual(len(lines(conn, "release", "202605")), 4)
        net = lines(conn, "net", "202605")
        # N = R = 50: Prepayments and Contract costs net to zero and drop out.
        self.assertEqual(set(net), {"Revenue", "Selling and distribution expenses"})
        self.assertEqual(net["Revenue"][:2], ("25.00", "0.00"))
        self.assertEqual(net["Selling and distribution expenses"][:2], ("0.00", "25.00"))

    def test_term_deposit_capitalises_once_and_releases_every_month(self):
        conn = make_conn(events=[
            ["X1", "Term RaF", "FDA_X_001", "RaF Bonus (Referrer)", 1, 3, "90.00", "2026-02-01", "2026-04-15"]])
        calc.run(conn)
        posting.post(conn)
        cap = conn.execute("SELECT DISTINCT posting_period_id FROM posting_line WHERE journal = 'capitalisation'").fetchall()
        rel = conn.execute("SELECT DISTINCT posting_period_id FROM posting_line WHERE journal = 'release' "
                           "ORDER BY posting_period_id").fetchall()
        self.assertEqual([r[0] for r in cap], ["202602"])
        self.assertEqual([r[0] for r in rel], ["202602", "202603", "202604"])

    def test_payout_date_does_not_move_recognition(self):
        a = make_conn(events=[["X1", "t", "FDA_X_001", "RaF Bonus (Lead)", 1, 6, "60.00", "2026-03-01", "2026-03-05"]])
        b = make_conn(events=[["X1", "t", "FDA_X_001", "RaF Bonus (Lead)", 1, 6, "60.00", "2026-03-01", "2026-09-30"]])
        for conn in (a, b):
            calc.run(conn)
        q = "SELECT posting_period_id, recognised FROM recognition_slice ORDER BY slice_id"
        self.assertEqual(a.execute(q).fetchall(), b.execute(q).fetchall())

    def test_slices_use_8_decimals_and_overnight_is_one_slice(self):
        conn = make_conn(events=[
            ["X1", "t", "FDA_X_001", "RaF Bonus (Lead)", 1, 12, "100.00", "2026-01-01", "2026-01-05"],
            ["X2", "t", "OMA_X_001", "welcome", 2, 0, "40.00", "2026-01-01", "2026-01-05"]])
        calc.run(conn)
        first = conn.execute("SELECT recognised FROM recognition_slice WHERE event_id = 'X1'").fetchone()[0]
        self.assertEqual(first, "8.33333333")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM recognition_slice WHERE event_id = 'X2'").fetchone()[0], 1)

    def test_year_end_rolls_into_the_next_year(self):
        conn = make_conn(events=[
            ["X1", "t", "FDA_X_001", "RaF Bonus (Lead)", 1, 3, "30.00", "2026-11-01", "2026-11-05"]])
        calc.run(conn)
        periods = [r[0] for r in conn.execute("SELECT posting_period_id FROM recognition_slice ORDER BY slice_id")]
        self.assertEqual(periods, ["202611", "202612", "202701"])
        in_window = [r[0] for r in conn.execute("SELECT in_window FROM recognition_slice ORDER BY slice_id")]
        self.assertEqual(in_window, [1, 1, 0])

    def test_rounding_difference_goes_on_the_largest_credit_and_is_flagged(self):
        conn = make_conn(events=[
            ["X1", "t", "OMA_X_001", "RaF Bonus (Lead)", 2, 0, "0.01", "2026-05-01", "2026-05-20"]])
        calc.run(conn)
        posting.post(conn)
        cap = lines(conn, "capitalisation", "202605")
        self.assertEqual(cap["Selling and distribution expenses"], ("0.00", "0.02", 1))
        dr = sum(D(v[0]) for v in cap.values())
        cr = sum(D(v[1]) for v in cap.values())
        self.assertEqual(dr, cr)

    def test_difference_above_tolerance_stops_the_run(self):
        conn = make_conn(events=[
            ["X1", "t", "OMA_X_001", "RaF Bonus (Lead)", 2, 0, "0.01", "2026-05-01", "2026-05-20"]],
            config={"rounding_tolerance": "0.00"})
        calc.run(conn)
        with self.assertRaises(posting.PostingError):
            posting.post(conn)

    def test_welcome_is_summarised_but_not_posted(self):
        conn = make_conn(events=[
            ["X1", "t", "OMA_X_001", "welcome", 2, 0, "40.00", "2026-05-01", "2026-05-20"]])
        calc.run(conn)
        posting.post(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM journal_summary").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM posting_line").fetchone()[0], 0)

    def test_lead_and_referrer_post_identically(self):
        def net_for(bonus_type):
            conn = make_conn(events=[["X1", "t", "FDA_X_001", bonus_type, 1, 6, "60.00", "2026-03-01", "2026-03-05"]])
            calc.run(conn)
            posting.post(conn)
            return sorted(tuple(r) for r in conn.execute(
                "SELECT posting_period_id, account, debit, credit FROM posting_line WHERE journal = 'net'"))
        self.assertEqual(net_for("RaF Bonus (Lead)"), net_for("RaF Bonus (Referrer)"))


class ValidationTest(unittest.TestCase):
    base = ["X1", "t", "FDA_X_001", "RaF Bonus (Lead)", 1, 6, "60.00", "2026-03-01", "2026-03-05"]

    def build_with(self, **changes):
        names = EVENT_HEADER
        row = list(self.base)
        for k, v in changes.items():
            row[names.index(k)] = v
        return make_conn(events=[row])

    def test_valid_row_loads(self):
        self.assertIsNotNone(self.build_with())

    def test_term_deposit_needs_a_term(self):
        with self.assertRaises(ValueError):
            self.build_with(term=0)

    def test_overnight_has_no_term(self):
        with self.assertRaises(ValueError):
            self.build_with(product_id=2, deposit_id="OMA_X_001", term=6)

    def test_deposit_prefix_must_match_product(self):
        with self.assertRaises(ValueError):
            self.build_with(deposit_id="OMA_X_001")

    def test_amount_must_be_positive(self):
        with self.assertRaises(ValueError):
            self.build_with(amount="-5")

    def test_dates_must_parse(self):
        with self.assertRaises(ValueError):
            self.build_with(placement_date="01/03/2026")

    def test_unknown_bonus_type_is_rejected(self):
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            self.build_with(bonus_type="Goodwill")

    def test_duplicate_event_id_is_rejected(self):
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            make_conn(events=[self.base, self.base])

    def test_unbalanced_posting_map_is_rejected(self):
        def mutate(tmp):
            path = os.path.join(tmp, "posting_map.csv")
            with open(path, encoding="utf-8") as f:
                text = f.read().replace("9001,release,4,Selling and distribution expenses,Dr,recognised,0.5",
                                        "9001,release,4,Selling and distribution expenses,Dr,recognised,0.4")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        with self.assertRaises(ValueError):
            make_conn(mutate=mutate)


if __name__ == "__main__":
    unittest.main()
