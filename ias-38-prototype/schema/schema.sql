-- IAS 38 capitalisation prototype: SQLite schema.
-- Layers: reference and recorded inputs are loaded from data/*.csv.
-- Parameters are versioned by period. Calculated tables are written only by
-- src/calc.py and src/posting.py and are rebuilt on every run.

PRAGMA foreign_keys = ON;

-- Reference and recorded inputs ---------------------------------------------

CREATE TABLE legal_entity (
    entity_code         TEXT PRIMARY KEY,
    functional_currency TEXT NOT NULL
);

CREATE TABLE project (
    project_id            TEXT PRIMARY KEY,
    project_name          TEXT NOT NULL,
    entity_code           TEXT NOT NULL REFERENCES legal_entity(entity_code),
    product_idea_id       TEXT,
    capitalisation_status TEXT NOT NULL,   -- approved / not approved (a person's decision, recorded)
    project_status        TEXT NOT NULL,   -- in-progress / live
    start_date            TEXT,
    end_date              TEXT,
    go_live_date          TEXT
);

-- Holds a person's conclusion per IAS 38.57 criterion. The example data has no such
-- record, so the table exists and is empty. Nothing here evaluates a criterion.
CREATE TABLE recognition_assessment (
    project_id    TEXT NOT NULL REFERENCES project(project_id),
    criterion     TEXT NOT NULL,
    conclusion    TEXT NOT NULL,
    assessor      TEXT NOT NULL,
    assessed_on   TEXT NOT NULL,
    evidence_link TEXT,
    PRIMARY KEY (project_id, criterion, assessed_on)
);

CREATE TABLE person (
    person_id   TEXT PRIMARY KEY,
    hashed_id   TEXT,
    entity_code TEXT NOT NULL REFERENCES legal_entity(entity_code),
    person_type TEXT NOT NULL CHECK (person_type IN ('internal', 'external')),
    cost_centre TEXT NOT NULL,
    currency    TEXT NOT NULL
);

-- One row per person, period and cost type. Contractors carry an hourly_rate
-- instead of a salary, and no social security or ESOP (enforced by load.py).
CREATE TABLE cost_record (
    person_id TEXT NOT NULL REFERENCES person(person_id),
    period    TEXT NOT NULL,
    cost_type TEXT NOT NULL CHECK (cost_type IN ('salary', 'hourly_rate', 'social_security', 'esop')),
    amount    REAL NOT NULL,
    PRIMARY KEY (person_id, period, cost_type)
);

CREATE TABLE effort_record (
    person_id    TEXT NOT NULL REFERENCES person(person_id),
    project_id   TEXT NOT NULL REFERENCES project(project_id),
    period       TEXT NOT NULL,
    weeks_logged REAL NOT NULL,
    PRIMARY KEY (person_id, project_id, period)
);

-- Parameters ------------------------------------------------------------------

CREATE TABLE capacity_baseline (
    entity_code    TEXT NOT NULL REFERENCES legal_entity(entity_code),
    person_type    TEXT NOT NULL,
    period         TEXT NOT NULL,
    working_weeks  REAL NOT NULL,
    holiday_weeks  REAL NOT NULL,
    hours_per_week REAL NOT NULL,
    PRIMARY KEY (entity_code, person_type, period)
);

CREATE TABLE overhead_balance (
    entity_code TEXT NOT NULL REFERENCES legal_entity(entity_code),
    period      TEXT NOT NULL,
    pool_type   TEXT NOT NULL CHECK (pool_type IN ('general', 'department')),
    department  TEXT NOT NULL,             -- empty for the general pool
    category    TEXT NOT NULL,
    amount      REAL NOT NULL
);

-- Headcount as supplied. The example has 7 sample people against a headcount
-- of 97, so it cannot be derived from the person table here.
CREATE TABLE headcount_input (
    entity_code TEXT NOT NULL REFERENCES legal_entity(entity_code),
    period      TEXT NOT NULL,
    scope       TEXT NOT NULL CHECK (scope IN ('entity', 'department')),
    department  TEXT NOT NULL,
    headcount   REAL NOT NULL
);

CREATE TABLE amortisation_policy (
    policy_id          TEXT PRIMARY KEY,
    useful_life_months INTEGER NOT NULL,
    method             TEXT NOT NULL
);

CREATE TABLE gl_account (
    account_code TEXT PRIMARY KEY,
    account_name TEXT NOT NULL,
    account_type TEXT NOT NULL
);

CREATE TABLE posting_map (
    cost_component TEXT PRIMARY KEY,
    account_code   TEXT NOT NULL REFERENCES gl_account(account_code)
);

-- Overhead amounts typed by hand per person, as a spreadsheet would hold them
-- (rounded rates). Used only by the typed mode and its reference check.
CREATE TABLE typed_overhead_input (
    person_id        TEXT NOT NULL REFERENCES person(person_id),
    period           TEXT NOT NULL,
    general_overhead REAL NOT NULL,
    dept_overhead    REAL NOT NULL,
    PRIMARY KEY (person_id, period)
);

-- Calculated -------------------------------------------------------------------

CREATE TABLE calc_run (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    overhead_mode TEXT NOT NULL,           -- derived / typed
    input_hash    TEXT NOT NULL,
    parameters    TEXT NOT NULL
);

CREATE TABLE overhead_rate (
    run_id      INTEGER NOT NULL REFERENCES calc_run(run_id),
    entity_code TEXT NOT NULL,
    period      TEXT NOT NULL,
    pool_type   TEXT NOT NULL,
    department  TEXT NOT NULL,
    pool_amount REAL NOT NULL,
    headcount   REAL NOT NULL,
    rate        REAL NOT NULL,
    PRIMARY KEY (run_id, entity_code, period, pool_type, department)
);

-- Atomic output: one row per person, project, period and cost component.
CREATE TABLE capitalised_line (
    line_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES calc_run(run_id),
    person_id        TEXT NOT NULL REFERENCES person(person_id),
    project_id       TEXT NOT NULL REFERENCES project(project_id),
    entity_code      TEXT NOT NULL,        -- the project's entity
    period           TEXT NOT NULL,
    cost_component   TEXT NOT NULL CHECK (cost_component IN
                       ('salary', 'social_security', 'esop', 'contractor',
                        'general_overhead', 'department_overhead')),
    weeks_logged     REAL NOT NULL,
    net_weeks        REAL NOT NULL,
    contribution_pct REAL NOT NULL,
    basis_amount     REAL NOT NULL,        -- monthly amount or rate before contribution
    amount           REAL NOT NULL,
    flags            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_line_project ON capitalised_line(project_id, period);
CREATE INDEX idx_line_component ON capitalised_line(entity_code, period, cost_component);

CREATE TABLE intangible_asset (
    asset_id           TEXT PRIMARY KEY,
    run_id             INTEGER NOT NULL REFERENCES calc_run(run_id),
    project_id         TEXT NOT NULL REFERENCES project(project_id),
    entity_code        TEXT NOT NULL,
    go_live_date       TEXT NOT NULL,
    gross_cost         REAL NOT NULL,
    policy_id          TEXT NOT NULL REFERENCES amortisation_policy(policy_id),
    useful_life_months INTEGER NOT NULL
);

CREATE TABLE asset_movement (
    asset_id       TEXT NOT NULL REFERENCES intangible_asset(asset_id),
    period         TEXT NOT NULL,
    period_index   INTEGER NOT NULL,       -- 0 for the go-live reclassification
    movement_type  TEXT NOT NULL CHECK (movement_type IN ('reclassification', 'amortisation')),
    amount         REAL NOT NULL,
    accumulated    REAL NOT NULL,
    net_book_value REAL NOT NULL,
    PRIMARY KEY (asset_id, period, movement_type)
);

CREATE TABLE posting (
    posting_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES calc_run(run_id),
    entity_code   TEXT NOT NULL,
    period        TEXT NOT NULL,
    posting_type  TEXT NOT NULL CHECK (posting_type IN
                    ('project_value', 'reclassification', 'amortisation')),
    asset_id      TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'proposed',
    erp_reference TEXT,
    UNIQUE (entity_code, period, posting_type, asset_id)
);

CREATE TABLE posting_line (
    line_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id   INTEGER NOT NULL REFERENCES posting(posting_id),
    account_code TEXT NOT NULL REFERENCES gl_account(account_code),
    project_id   TEXT,
    debit        REAL NOT NULL DEFAULT 0,
    credit       REAL NOT NULL DEFAULT 0,
    description  TEXT NOT NULL,
    source_ref   TEXT NOT NULL             -- anchor into the allocation lines
);

-- Aggregations ------------------------------------------------------------

CREATE VIEW v_proj_cost_allocation AS
SELECT project_id, entity_code, period,
       CASE WHEN cost_component = 'contractor' THEN 'external' ELSE 'internal' END AS cost_type_group,
       SUM(CASE WHEN cost_component = 'salary' THEN amount ELSE 0 END)              AS salary,
       SUM(CASE WHEN cost_component = 'contractor' THEN amount ELSE 0 END)          AS contractor,
       SUM(CASE WHEN cost_component = 'social_security' THEN amount ELSE 0 END)     AS social_security,
       SUM(CASE WHEN cost_component = 'esop' THEN amount ELSE 0 END)                AS esop,
       SUM(CASE WHEN cost_component = 'general_overhead' THEN amount ELSE 0 END)    AS general_overhead,
       SUM(CASE WHEN cost_component = 'department_overhead' THEN amount ELSE 0 END) AS department_overhead,
       SUM(amount) AS total
FROM capitalised_line
GROUP BY project_id, entity_code, period, cost_type_group;

CREATE VIEW v_project_value AS
SELECT project_id, entity_code, period, SUM(amount) AS project_value
FROM capitalised_line
GROUP BY project_id, entity_code, period;

CREATE VIEW v_entity_total AS
SELECT entity_code, period, SUM(amount) AS total
FROM capitalised_line
GROUP BY entity_code, period;
