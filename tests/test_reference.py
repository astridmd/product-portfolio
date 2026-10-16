"""The prototype against hand-calculated September 2026 figures and against the
Mickey Mouse sample workbook CSVs in reference/."""
import csv
import json
import os
import unittest
from collections import Counter

from src import calc, load, posting
from src.money import D
from tests.reference_paths import reference_dir

HERE = os.path.dirname(os.path.abspath(__file__))
ARTEFACTS = reference_dir("mickey-mouse-ifrs15-journal-summary.csv")
with open(os.path.join(HERE, "reference_expected.json"), encoding="utf-8") as _f:
    EXPECTED = json.load(_f)
HAVE_ARTEFACTS = ARTEFACTS is not None


def rows_of(name):
    with open(os.path.join(ARTEFACTS, name), newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


class ReferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = load.build()
        calc.run(cls.conn)
        posting.post(cls.conn)

    def journal(self, name, period="202609", ptid=9001):
        out = {}
        for r in self.conn.execute(
                "SELECT account, debit, credit FROM posting_line WHERE journal = ? AND "
                "posting_period_id = ? AND process_type_id = ?", (name, period, ptid)):
            out[r["account"]] = [r["debit"], r["credit"]]
        return out

    def test_journal_summary_september(self):
        got = {r["process_type_id"]: r["amount"] for r in self.conn.execute(
            "SELECT * FROM journal_summary WHERE posting_period_id = ?", (EXPECTED["period"],))}
        self.assertEqual(got[9001], EXPECTED["raf_journal_summary"])
        self.assertEqual(got[9002], EXPECTED["welcome_journal_summary"])

    def test_new_bonuses_and_regime(self):
        p = self.conn.execute("SELECT * FROM posting_period WHERE posting_period_id = ? "
                              "AND process_type_id = 9001", (EXPECTED["period"],)).fetchone()
        self.assertEqual(D(p["new_bonuses"]), D(EXPECTED["new_bonuses"]))
        self.assertEqual(D(p["recognised"]), D(EXPECTED["raf_journal_summary"]))
        self.assertEqual(p["regime"], EXPECTED["regime"])

    def test_capitalisation(self):
        self.assertEqual(self.journal("capitalisation"), EXPECTED["capitalisation"])

    def test_release(self):
        self.assertEqual(self.journal("release"), EXPECTED["release"])

    def test_net(self):
        self.assertEqual(self.journal("net"), EXPECTED["net"])

    def test_welcome_has_no_gl_lines(self):
        n = self.conn.execute("SELECT COUNT(*) FROM posting_line WHERE process_type_id = 9002").fetchone()[0]
        self.assertEqual(n, 0)

    def test_every_net_line_traces_to_step_lines(self):
        for r in self.conn.execute("SELECT line_id FROM posting_line WHERE journal = 'net'"):
            kinds = {k[0] for k in self.conn.execute(
                "SELECT source_kind FROM posting_line_source WHERE line_id = ?", (r["line_id"],))}
            self.assertEqual(kinds, {"line"})

    def test_release_lines_trace_to_the_slices_of_the_month(self):
        line = self.conn.execute(
            "SELECT line_id FROM posting_line WHERE journal = 'release' AND "
            "posting_period_id = '202609' AND account = 'Revenue'").fetchone()
        sources = self.conn.execute(
            "SELECT source_ref, amount FROM posting_line_source WHERE line_id = ?",
            (line["line_id"],)).fetchall()
        self.assertEqual(len(sources), 6)
        self.assertEqual(sum(D(s["amount"]) for s in sources), D("20"))


@unittest.skipUnless(HAVE_ARTEFACTS, "Mickey Mouse CSVs not found in reference/")
class WorkbookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = load.build()
        calc.run(cls.conn)
        posting.post(cls.conn)

    def test_slices_match_transformation_csv(self):
        expected = Counter()
        for row in rows_of("mickey-mouse-ifrs15-transformation.csv"):
            if len(row) > 7 and row[1][:4] in ("FDA_", "OMA_") and len(row[4]) == 6 and row[4].isdigit():
                expected[(row[1], row[2], row[4], row[7])] += 1
        got = Counter()
        for r in self.conn.execute(
                "SELECT deposit_id, bonus_type, posting_period_id, recognised FROM v_slice_detail"):
            got[(r["deposit_id"], r["bonus_type"], r["posting_period_id"], r["recognised"])] += 1
        self.assertGreater(sum(expected.values()), 40)
        self.assertEqual(got, expected)

    def test_journal_summary_matches_csv(self):
        expected = {}
        for row in rows_of("mickey-mouse-ifrs15-journal-summary.csv")[1:]:
            if row and row[0] in ("RaF journal", "welcome journal"):
                expected[(row[2], int(row[3]))] = row[5]
        got = {(r["posting_period_id"], r["process_type_id"]): r["amount"]
               for r in self.conn.execute("SELECT * FROM journal_summary")}
        self.assertEqual(got, expected)

    def test_grouping_keys_match_csv(self):
        expected = {}
        for row in rows_of("mickey-mouse-ifrs15-journal-summary.csv")[1:]:
            if row and row[0] in ("RaF journal", "welcome journal"):
                expected[(row[2], int(row[3]))] = row[6]
        got = {(r["posting_period_id"], r["process_type_id"]): r["grouping_key"]
               for r in self.conn.execute("SELECT * FROM journal_summary")}
        self.assertEqual(got, expected)

    def test_monthly_posting_september_matches_csv(self):
        blocks = {"2b": "capitalisation", "2c": "release", "2d": "net"}
        current = None
        expected = {k: {} for k in blocks.values()}
        for row in rows_of("mickey-mouse-ifrs15-monthly-posting-sep2026.csv"):
            if row and row[0][:2] in blocks and "\u2014" in row[0][:6]:
                current = blocks[row[0][:2]]
            elif row and row[0][:2] in ("2e", "3 ", "3\u2014"):
                current = None
            elif current and len(row) > 4 and row[0] == "Sep 2026" and row[2] in posting.ACCOUNTS:
                expected[current][row[2]] = [row[3] or "0.00", row[4] or "0.00"]
        for journal, lines in expected.items():
            got = {}
            for r in self.conn.execute(
                    "SELECT account, debit, credit FROM posting_line WHERE journal = ? AND "
                    "posting_period_id = '202609' AND process_type_id = 9001", (journal,)):
                got[r["account"]] = [r["debit"], r["credit"]]
            self.assertEqual(got, lines, journal)


if __name__ == "__main__":
    unittest.main()
