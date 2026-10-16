-- Marketing bonus cost recognition prototype: SQLite schema.
-- Inputs are loaded from data/*.csv. Calculated tables are written only by
-- src/calc.py and src/posting.py and are rebuilt on every run.
-- Amounts are stored as TEXT so that decimals are never turned into floats.

PRAGMA foreign_keys = ON;

-- Inputs ---------------------------------------------------------------------

CREATE TABLE config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE process_type (
    process_type_id INTEGER PRIMARY KEY,
    process_type    TEXT NOT NULL,
    posting_status  TEXT NOT NULL,      -- defined / TBC
    note            TEXT
);

CREATE TABLE bonus_type_map (
    bonus_type      TEXT PRIMARY KEY,
    process_type_id INTEGER NOT NULL REFERENCES process_type(process_type_id)
);

-- GL lines per process type and step. basis says which amount the share applies to:
-- new_bonuses (whole bonus of deposits whose schedule starts in the month) or
-- recognised (amount recognised in the month).
CREATE TABLE posting_map (
    process_type_id INTEGER NOT NULL REFERENCES process_type(process_type_id),
    step            TEXT NOT NULL,      -- capitalisation / release
    line_no         INTEGER NOT NULL,
    account         TEXT NOT NULL,
    side            TEXT NOT NULL,      -- Dr / Cr
    basis           TEXT NOT NULL,      -- new_bonuses / recognised
    share           TEXT NOT NULL,
    PRIMARY KEY (process_type_id, step, line_no)
);

CREATE TABLE bonus_event (
    event_id       TEXT PRIMARY KEY,
    sample         TEXT NOT NULL,
    deposit_id     TEXT NOT NULL,
    bonus_type     TEXT NOT NULL REFERENCES bonus_type_map(bonus_type),
    product_id     INTEGER NOT NULL,    -- 1 term, 2 overnight
    term           INTEGER NOT NULL,    -- months, 0 for overnight
    amount         TEXT NOT NULL,
    placement_date TEXT NOT NULL,       -- recognition starts here
    payout_date    TEXT NOT NULL        -- metadata only, does not move recognition
);

-- Calculated -----------------------------------------------------------------

CREATE TABLE run (
    run_id      TEXT PRIMARY KEY,       -- first 12 characters of the input fingerprint
    fingerprint TEXT NOT NULL
);

-- Step 1 of the sample: one row per bonus event per recognition month.
CREATE TABLE recognition_slice (
    slice_id          INTEGER PRIMARY KEY,
    event_id          TEXT NOT NULL REFERENCES bonus_event(event_id),
    month_index       INTEGER NOT NULL,
    posting_period_id TEXT NOT NULL,
    recognised        TEXT NOT NULL,    -- 8 decimals, rounded half up
    in_window         INTEGER NOT NULL  -- 1 if inside the reporting window of config
);

-- Step 2 and 3: grouping key per posting period and process type, with the amount.
CREATE TABLE journal_summary (
    summary_id        INTEGER PRIMARY KEY,
    posting_period_id TEXT NOT NULL,
    process_type_id   INTEGER NOT NULL REFERENCES process_type(process_type_id),
    amount            TEXT NOT NULL,
    grouping_key      TEXT NOT NULL,
    slice_count       INTEGER NOT NULL,
    UNIQUE (posting_period_id, process_type_id)
);

-- Inputs to the RaF journal for one month: N is new_bonuses, R is recognised.
CREATE TABLE posting_period (
    posting_period_id TEXT NOT NULL,
    process_type_id   INTEGER NOT NULL REFERENCES process_type(process_type_id),
    new_bonuses       TEXT NOT NULL,
    recognised        TEXT NOT NULL,
    regime            INTEGER NOT NULL, -- 1 N larger than R, 2 N between R / 2 and R, 3 N smaller than R / 2
    PRIMARY KEY (posting_period_id, process_type_id)
);

CREATE TABLE posting_line (
    line_id           INTEGER PRIMARY KEY,
    posting_period_id TEXT NOT NULL,
    process_type_id   INTEGER NOT NULL,
    journal           TEXT NOT NULL,    -- capitalisation / release / net
    line_no           INTEGER NOT NULL,
    account           TEXT NOT NULL,
    debit             TEXT NOT NULL,
    credit            TEXT NOT NULL,
    basis             TEXT,
    share             TEXT,
    rounding_adjusted INTEGER NOT NULL DEFAULT 0
);

-- What is behind a journal line. source_kind is event (capitalisation), slice (release)
-- or line (net, pointing at the step lines it adds up).
CREATE TABLE posting_line_source (
    line_id     INTEGER NOT NULL REFERENCES posting_line(line_id),
    source_kind TEXT NOT NULL,
    source_ref  TEXT NOT NULL,
    amount      TEXT NOT NULL
);

CREATE VIEW v_slice_detail AS
SELECT s.slice_id, s.event_id, e.deposit_id, e.bonus_type, m.process_type_id,
       s.month_index, s.posting_period_id, s.recognised, s.in_window
FROM recognition_slice s
JOIN bonus_event e ON e.event_id = s.event_id
JOIN bonus_type_map m ON m.bonus_type = e.bonus_type;

CREATE VIEW v_journal AS
SELECT posting_period_id, process_type_id, journal, line_no, account, debit, credit,
       rounding_adjusted
FROM posting_line;
