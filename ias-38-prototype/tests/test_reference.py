"""Reference check of the engine against figures calculated by hand.

Run:  python3 -m unittest discover -s tests -t .

tests/reference_expected.json holds values worked out on paper from data/*.csv,
not produced by the engine. With the hand-typed overhead amounts
(data/typed_overhead_input.csv) the engine must match them within a cent.

Worked example for P001 (Entity A, January 2026, net weeks 4.0):

  INT001  2.0 weeks, contribution 0.5    salary 8000 / 4 * 2 = 4000, social security 800,
                                         general overhead 620 * 0.5 = 310,
                                         department overhead 484 * 0.5 = 242
  INT002  1.0 week,   contribution 0.25  salary 1500, social security 300, ESOP -50,
                                         general 155, department 121
  EXT002  0.5 week,   contribution 0.125 contractor 0.5 * 40 * 80 = 1600,
                                         general 77.5, department 60.5

  P001 = 5500 + 1100 - 50 + 1600 + 542.5 + 423.5 = 9116.00

Amortisation: 9116.00 / 60 = 151.93 for 59 months, the last month takes the
remainder, 9116.00 - 59 * 151.93 = 9116.00 - 8963.87 = 152.13.
"""
import json
import os
import unittest

from src import calc, load, posting

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "reference_expected.json")) as handle:
    EXPECTED = json.load(handle)

ENTITY_A = "Entity A"
ENTITY_B = "Entity B"
ESOP_ACCOUNT = "4003"
WIP_ACCOUNT = "1001"


def project_values(conn):
    return {r["project_id"]: r["project_value"]
            for r in conn.execute("SELECT * FROM v_project_value")}


def credits(conn, entity):
    rows = conn.execute(
        """SELECT account_code, SUM(credit) AS c FROM posting_line
           JOIN posting USING (posting_id)
           WHERE posting_type='project_value' AND entity_code=? AND credit != 0
           GROUP BY account_code""", (entity,))
    return {r["account_code"]: r["c"] for r in rows}


class TypedModeReference(unittest.TestCase):
    """Hand-calculated values, using the typed per-person overhead amounts."""

    @classmethod
    def setUpClass(cls):
        cls.conn = load.build()
        calc.run(cls.conn, "typed")
        posting.post(cls.conn)

    def test_project_values(self):
        got = project_values(self.conn)
        for pid, want in EXPECTED["project_value"].items():
            self.assertAlmostEqual(got[pid], want, delta=0.01, msg=pid)

    def test_entity_and_group_totals(self):
        totals = {r["entity_code"]: r["total"]
                  for r in self.conn.execute("SELECT * FROM v_entity_total")}
        for entity, want in EXPECTED["entity_total"].items():
            self.assertAlmostEqual(totals[entity], want, delta=0.01, msg=entity)
        self.assertAlmostEqual(sum(totals.values()), EXPECTED["group_total"], delta=0.01)

    def test_posting_credits_by_component(self):
        for entity, key in ((ENTITY_A, "credits_a"), (ENTITY_B, "credits_b")):
            got = credits(self.conn, entity)
            self.assertEqual(sorted(got), sorted(EXPECTED[key]), entity)
            for acct, want in EXPECTED[key].items():
                self.assertAlmostEqual(got[acct], want, delta=0.03, msg=acct)

    def test_contractors_have_no_social_security_or_esop(self):
        n = self.conn.execute(
            """SELECT COUNT(*) FROM capitalised_line l JOIN person p USING (person_id)
               WHERE p.person_type='external'
                 AND l.cost_component IN ('salary','social_security','esop')""").fetchone()[0]
        self.assertEqual(n, 0)

    def test_amortisation_schedule(self):
        asset = self.conn.execute("SELECT * FROM intangible_asset").fetchone()
        self.assertAlmostEqual(asset["gross_cost"], EXPECTED["gross_asset_cost_p001"], delta=0.01)
        rows = self.conn.execute(
            "SELECT * FROM asset_movement WHERE movement_type='amortisation' ORDER BY period_index"
        ).fetchall()
        self.assertEqual(len(rows), EXPECTED["useful_life_months"])
        self.assertEqual(rows[0]["period"], "2026-09")
        self.assertEqual(rows[-1]["period"], "2031-08")
        self.assertAlmostEqual(rows[0]["amount"], EXPECTED["monthly_amortisation"], places=2)
        self.assertAlmostEqual(rows[-1]["amount"], EXPECTED["final_month_amortisation"], places=2)
        self.assertAlmostEqual(sum(r["amount"] for r in rows), asset["gross_cost"], places=2)
        self.assertAlmostEqual(rows[-1]["net_book_value"], 0.0, places=2)


class PostingProperties(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = load.build()
        calc.run(cls.conn, "derived")
        posting.post(cls.conn)

    def test_every_journal_balances(self):
        rows = self.conn.execute(
            """SELECT posting_id, ROUND(SUM(debit) - SUM(credit), 2) AS diff
               FROM posting_line GROUP BY posting_id""").fetchall()
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["diff"], 0.0, r["posting_id"])

    def test_wip_clears_to_zero_for_live_project(self):
        balance = self.conn.execute(
            """SELECT ROUND(SUM(debit) - SUM(credit), 2) FROM posting_line
               WHERE account_code=? AND project_id='P001'""", (WIP_ACCOUNT,)).fetchone()[0]
        self.assertEqual(balance, 0.0)

    def test_projects_not_live_keep_wip(self):
        balance = self.conn.execute(
            """SELECT ROUND(SUM(debit) - SUM(credit), 2) FROM posting_line
               WHERE account_code=? AND project_id='P002'""", (WIP_ACCOUNT,)).fetchone()[0]
        self.assertGreater(balance, 0)

    def test_reposting_is_idempotent(self):
        before = self.conn.execute("SELECT COUNT(*) FROM posting").fetchone()[0]
        lines = self.conn.execute("SELECT COUNT(*) FROM posting_line").fetchone()[0]
        posting.post(self.conn)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM posting").fetchone()[0], before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM posting_line").fetchone()[0], lines)

    def test_negative_esop_stays_a_negative_credit(self):
        row = self.conn.execute(
            """SELECT credit, debit FROM posting_line JOIN posting USING (posting_id)
               WHERE account_code=? AND entity_code=?""", (ESOP_ACCOUNT, ENTITY_A)).fetchone()
        self.assertLess(row["credit"], 0)
        self.assertEqual(row["debit"], 0)

    def test_recalculation_gives_same_totals(self):
        first = project_values(self.conn)
        calc.run(self.conn, "derived")
        self.assertEqual(first, project_values(self.conn))
        posting.post(self.conn)

    def test_derived_rates_differ_from_typed_by_rounding_only(self):
        derived = project_values(self.conn)
        for pid, want in EXPECTED["project_value"].items():
            diff = derived[pid] - want
            self.assertNotEqual(round(diff, 2), 0.0, pid)  # typed rounding is visible
            self.assertLess(abs(diff), 2.0, pid)           # and small

    def test_flags(self):
        flags = {r["project_id"]: r["flags"] for r in self.conn.execute(
            "SELECT project_id, GROUP_CONCAT(flags) AS flags FROM capitalised_line GROUP BY project_id")}
        self.assertIn("before_start_date", flags["P001"])
        self.assertNotIn("before_start_date", flags["P002"])
        self.assertIn("rate_fallback", flags["P003"])  # Entity B has no pool and falls back
        self.assertNotIn("rate_fallback", flags["P001"])


class InputRules(unittest.TestCase):
    def test_contractor_social_security_is_rejected(self):
        conn = load.build()
        conn.execute("UPDATE cost_record SET amount = 5 WHERE person_id='EXT001' "
                     "AND cost_type='social_security'")
        with self.assertRaises(ValueError):
            load.validate(conn)


if __name__ == "__main__":
    unittest.main()
