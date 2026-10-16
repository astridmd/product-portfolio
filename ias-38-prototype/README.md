# IAS 38 capitalisation prototype

A working prototype of an IAS 38 capitalisation calculation for internally generated software, run on a small invented example. It loads inputs into SQLite, calculates capitalised cost per person, project and cost component, proposes the project value journals, and builds the go-live reclassification and 60-month amortisation postings. Results are published as a static HTML site.

It is a proof of the logic, not a system. Nothing is sent to an accounting system.

All names, entities, projects, account numbers and figures in `data/` are invented. Do not load real payroll data. Salary data is personal data, and the generated site would show it per person.

## What it covers

| Functionality | Code |
|---|---|
| 1. Project register | `data/project.csv`, table `project` |
| 2. Cost allocation engine | `src/calc.py`, views `v_proj_cost_allocation`, `v_project_value` |
| 3. Project value posting | `src/posting.py` |
| 4. Amortisation schedule | `src/calc.py` (`_build_assets`) |
| 5. Amortisation postings | `src/posting.py` |

## Run it

Python 3.9 or later, standard library only.

```
python3 -m src.run            # builds public/ and prints a summary
python3 -m unittest discover -s tests -t .
open public/index.html
```

## How it works

Inputs live in `data/*.csv` and are loaded into SQLite from `schema/schema.sql`. Calculated tables are rebuilt on every run and carry a run identifier and an input fingerprint.

Contribution is weeks logged divided by net working weeks. Internal salary is monthly salary over net weeks times weeks logged. Social security and ESOP are the monthly amounts times contribution. Contractors are weeks times hours per week times the hourly rate and carry no social security or ESOP. Overhead is a rate times contribution, where the rate is the pool divided by headcount. A person whose entity has no overhead pool uses the rate of another entity and the line is flagged `rate_fallback`.

Postings are one balanced journal per entity and month. The debit is each project's capitalised cost on Software WIP (account 1001). The credit is the same total split across six capitalised-cost accounts (4001 to 4006) by component. At go-live the asset moves from 1001 to 1002, and amortisation debits 5001 and credits 1009 over 60 months, starting in the go-live month. Amounts are rounded to cents at the posting step only. A difference of up to five cents goes on the largest credit line, and a larger one stops the run.

Every journal line links to the allocation lines behind it in the HTML site.

## What this proves, and what it does not

It proves that the rules can run as repeatable code, that the output balances, and that every journal line can be traced to person-level inputs. With hand-typed overhead amounts, the engine reproduces figures calculated on paper, listed in `tests/reference_expected.json` and worked through in `tests/test_reference.py`.

It does not evaluate the IAS 38.57 recognition criteria. The `recognition_assessment` table exists and is empty. The `approved` status on a project is read as a decision a person made. It does not settle whether general overheads belong in capitalised cost under IAS 38.66 and 38.67, whether 60 months is the right useful life, or whether the figures are ready for an accounting system. It has no foreign exchange handling, no corrections and only one month of cost.

## Questions to settle before using it for real

- Useful life: which value is policy, and is it assessed per asset?
- Which parts of the general overhead pool are directly attributable under IAS 38.66 and 38.67?
- Whether a negative capitalised ESOP amount is accepted treatment. The example contains one.
- Who confirms the actual go-live date that starts amortisation.
- Whether cost recorded before a project's start date may be capitalised. The example has one such case, flagged `before_start_date`.
- Whether staff of an entity without its own overhead pool should use another entity's rate.

## Layout

```
schema/schema.sql     tables and views
data/*.csv            invented example inputs
src/load.py           create database, load inputs, validate
src/calc.py           allocation engine, amortisation schedule
src/posting.py        journals
src/build_site.py     static HTML
src/run.py            entry point
tests/                reference and posting tests
(repository root) .github/workflows/pages.yml: test job and GitHub Pages job
```

## Licence

MIT, see `LICENSE`.
