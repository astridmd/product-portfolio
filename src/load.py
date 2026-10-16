"""Create the database, load data/*.csv, validate the inputs."""
import csv
import hashlib
import os
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
SCHEMA_PATH = os.path.join(ROOT, "schema", "schema.sql")

# Load order follows the foreign keys.
INPUT_TABLES = [
    "config",
    "process_type",
    "bonus_type_map",
    "posting_map",
    "bonus_event",
]


def read_csv(name, data_dir=DATA_DIR):
    with open(os.path.join(data_dir, name + ".csv"), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fingerprint(data_dir=DATA_DIR):
    h = hashlib.sha256()
    for name in INPUT_TABLES:
        h.update(name.encode())
        with open(os.path.join(data_dir, name + ".csv"), "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def _insert(conn, table, rows):
    if not rows:
        return
    cols = list(rows[0].keys())
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (table, ",".join(cols), ",".join("?" * len(cols)))
    conn.executemany(sql, [[r[c] for c in cols] for r in rows])


def _is_decimal(text):
    try:
        Decimal(text)
        return True
    except (InvalidOperation, TypeError):
        return False


def _parse_date(text):
    try:
        y, m, d = (int(p) for p in text.split("-"))
        return date(y, m, d)
    except (ValueError, AttributeError):
        return None


def validate(conn):
    """Return a list of error strings. An empty list means the inputs are usable."""
    errors = []
    cur = conn.cursor()

    seen = set()
    for r in cur.execute("SELECT * FROM bonus_event ORDER BY event_id"):
        eid = r["event_id"]
        if eid in seen:
            errors.append("%s: duplicate event_id" % eid)
        seen.add(eid)
        if not _is_decimal(r["amount"]) or Decimal(r["amount"]) <= 0:
            errors.append("%s: amount must be a positive number" % eid)
        if _parse_date(r["placement_date"]) is None:
            errors.append("%s: placement_date must be YYYY-MM-DD" % eid)
        if _parse_date(r["payout_date"]) is None:
            errors.append("%s: payout_date must be YYYY-MM-DD" % eid)
        if not isinstance(r["term"], int) or not isinstance(r["product_id"], int):
            errors.append("%s: term and product_id must be whole numbers" % eid)
            continue
        if r["term"] < 0:
            errors.append("%s: term must be 0 or more" % eid)
        if r["product_id"] == 1 and r["term"] == 0:
            errors.append("%s: a term deposit needs a term above 0" % eid)
        if r["product_id"] == 2 and r["term"] != 0:
            errors.append("%s: an overnight deposit has term 0" % eid)
        if r["product_id"] not in (1, 2):
            errors.append("%s: product_id must be 1 (term) or 2 (overnight)" % eid)
        prefix = {1: "FDA_", 2: "OMA_"}.get(r["product_id"])
        if prefix and not r["deposit_id"].startswith(prefix):
            errors.append("%s: deposit_id %s does not match product_id" % (eid, r["deposit_id"]))

    # Every step must balance on shares: debit shares equal credit shares per basis.
    rows = cur.execute(
        "SELECT process_type_id, step, basis, side, share FROM posting_map"
    ).fetchall()
    sums = {}
    for r in rows:
        if not _is_decimal(r["share"]):
            errors.append("posting_map: share %r is not a number" % r["share"])
            continue
        key = (r["process_type_id"], r["step"], r["basis"])
        dr, cr = sums.get(key, (Decimal(0), Decimal(0)))
        if r["side"] == "Dr":
            dr += Decimal(r["share"])
        elif r["side"] == "Cr":
            cr += Decimal(r["share"])
        else:
            errors.append("posting_map: side must be Dr or Cr, found %r" % r["side"])
        sums[key] = (dr, cr)
    for key, (dr, cr) in sums.items():
        if dr != cr:
            errors.append(
                "posting_map %s %s %s: debit shares %s differ from credit shares %s"
                % (key[0], key[1], key[2], dr, cr)
            )
        if key[2] not in ("new_bonuses", "recognised"):
            errors.append("posting_map: unknown basis %r" % key[2])
        if key[1] not in ("capitalisation", "release"):
            errors.append("posting_map: unknown step %r" % key[1])

    cfg = dict(cur.execute("SELECT key, value FROM config").fetchall())
    for key in ("subsidiary_id", "erp_currency_id", "monthly_run_id", "window_start",
                "window_end", "default_period", "rounding_tolerance"):
        if key not in cfg:
            errors.append("config: missing key %s" % key)
    return errors


def build(db_path=":memory:", data_dir=DATA_DIR):
    """Create a fresh database, load the inputs, validate. Raises ValueError on bad inputs."""
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        if os.path.exists(db_path):
            os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    for name in INPUT_TABLES:
        _insert(conn, name, read_csv(name, data_dir))
    conn.commit()
    errors = validate(conn)
    if errors:
        conn.close()
        raise ValueError("input validation failed:\n- " + "\n- ".join(errors))
    fp = fingerprint(data_dir)
    conn.execute("INSERT INTO run (run_id, fingerprint) VALUES (?, ?)", (fp[:12], fp))
    conn.commit()
    return conn
