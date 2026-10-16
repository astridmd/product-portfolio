"""Create the SQLite database from schema/schema.sql and load data/*.csv."""
import csv
import hashlib
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SCHEMA = os.path.join(ROOT, "schema", "schema.sql")

# CSV file -> (table, columns). Order respects foreign keys.
INPUTS = [
    ("legal_entity", ["entity_code", "functional_currency"]),
    ("project", ["project_id", "project_name", "entity_code", "product_idea_id",
                 "capitalisation_status", "project_status", "start_date", "end_date",
                 "go_live_date"]),
    ("person", ["person_id", "hashed_id", "entity_code", "person_type", "cost_centre",
                "currency"]),
    ("cost_record", ["person_id", "period", "cost_type", "amount"]),
    ("effort_record", ["person_id", "project_id", "period", "weeks_logged"]),
    ("capacity_baseline", ["entity_code", "person_type", "period", "working_weeks",
                           "holiday_weeks", "hours_per_week"]),
    ("overhead_balance", ["entity_code", "period", "pool_type", "department", "category",
                          "amount"]),
    ("headcount_input", ["entity_code", "period", "scope", "department", "headcount"]),
    ("amortisation_policy", ["policy_id", "useful_life_months", "method"]),
    ("gl_account", ["account_code", "account_name", "account_type"]),
    ("posting_map", ["cost_component", "account_code"]),
    ("typed_overhead_input", ["person_id", "period", "general_overhead", "dept_overhead"]),
]
NULLABLE = {"product_idea_id", "start_date", "end_date", "go_live_date", "hashed_id"}


def read_csv(name):
    with open(os.path.join(DATA, name + ".csv"), newline="") as handle:
        return list(csv.DictReader(handle))


def input_hash():
    digest = hashlib.sha256()
    for name, _ in INPUTS:
        with open(os.path.join(DATA, name + ".csv"), "rb") as handle:
            digest.update(handle.read())
    return digest.hexdigest()[:16]


def validate(conn):
    """Input rules that the schema cannot express."""
    bad = conn.execute(
        """SELECT p.person_id, c.cost_type, c.amount FROM cost_record c
           JOIN person p USING (person_id)
           WHERE p.person_type = 'external' AND c.cost_type IN ('social_security', 'esop')
             AND c.amount != 0""").fetchall()
    if bad:
        raise ValueError("contractors must carry no social security or ESOP: %r" % (bad,))
    orphan = conn.execute(
        """SELECT e.person_id, e.project_id FROM effort_record e
           LEFT JOIN cost_record c ON c.person_id = e.person_id AND c.period = e.period
                AND c.cost_type IN ('salary', 'hourly_rate')
           WHERE c.person_id IS NULL""").fetchall()
    if orphan:
        raise ValueError("effort without a cost record: %r" % (orphan,))


def build(db_path=":memory:"):
    if db_path != ":memory:" and os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    with open(SCHEMA) as handle:
        conn.executescript(handle.read())
    for table, columns in INPUTS:
        rows = []
        for record in read_csv(table):
            rows.append([(record[c] if record[c] != "" or c not in NULLABLE else None)
                         for c in columns])
        placeholders = ",".join("?" * len(columns))
        conn.executemany("INSERT INTO %s (%s) VALUES (%s)"
                         % (table, ",".join(columns), placeholders), rows)
    conn.commit()
    validate(conn)
    return conn
