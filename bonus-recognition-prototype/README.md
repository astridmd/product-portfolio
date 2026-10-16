# Marketing bonus cost recognition prototype

A working prototype of the bonus recognition flow for marketing bonus cost recognition, run on the small invented Mickey Mouse example. It loads bonus events per deposit into SQLite, recognises each bonus over the deposit term, groups the result by posting period and process type, and builds the refer-a-friend (RaF) journals for the ERP. Results are published as a static HTML site.

It is a proof of the logic, not a system. Nothing is sent to an accounting system.

The deposits, amounts and dates in `data/` and `reference/` are invented. The repository holds no real company data.

The model follows a functionalities proposal for marketing bonus cost recognition, section "ERP posting logic for refer-a-friend (RaF)".

## What it covers

| Step in the flow | Code |
|---|---|
| Inputs: bonus events, bonus type to process type, GL lines per process type | `data/*.csv`, tables in `schema/schema.sql` |
| Transformation: straight line recognition per deposit and month | `src/calc.py`, table `recognition_slice` |
| Grouping and Journal summary: key and amount per posting period and process type | `src/calc.py`, table `journal_summary` |
| Bonus payout capitalisation, bonus cost release, net journal, debit or credit side by month | `src/posting.py`, tables `posting_line`, `posting_line_source` |
| Reconciliation checks | `src/checks.py` |

## Run it

Python 3.9 or later, standard library only.

```
python3 -m src.run            # builds public/ and prints a summary
python3 -m unittest discover -s tests -t .
open public/index.html
```

`python3 -m src.run` exits with 1 if a reconciliation check fails.

## How it works

Recognition is the bonus amount divided by the term, rounded to 8 decimals, starting in the placement month. An overnight deposit has no term, so 100% falls in the placement month. The payout date is metadata and does not move any amount (Alex confirmed that payout timing does not matter).

The grouping key is `{posting_period_id}-{process_type_id}-{erp_currency_id}-{subsidiary_id}-{monthly_run_id}`. The Journal summary is the sum of the slices behind each key, within the reporting window set in `data/config.csv` (Jan to Dec 2026, as in the workbook).

Lead and referrer bonuses share the process type refer_a_friend, so they post identically. Welcome has a Journal summary line and no GL lines, because its pattern is not confirmed.

For each month the RaF journals are:

1. Bonus payout capitalisation: the whole bonus of deposits whose schedule starts in the month. Dr Prepayments 50%, Dr Contract costs 50%, Cr Selling and distribution 100%.
2. Bonus cost release: the recognised amount of the month, for every active deposit. Cr Prepayments 50%, Cr Contract costs 50%, Dr Revenue 50%, Dr Selling and distribution 50%.
3. Net journal: both steps added per account. With N the new bonuses and R the recognised amount, Prepayments and Contract costs are (N minus R) / 2 each, Revenue is R / 2, and Selling and distribution is N minus R / 2 on the credit side.

The sides depend on the month:

| Condition | Prepayments | Contract costs | Revenue | Selling and distribution |
|---|---|---|---|---|
| N larger than R | Dr | Dr | Dr | Cr |
| N between R / 2 and R | Cr | Cr | Dr | Cr |
| N smaller than R / 2 | Cr | Cr | Dr | Dr |

The GL lines of steps 1 and 2 come from `data/posting_map.csv`, so a different pattern is a data change. Amounts are rounded to cents at the posting step only. A difference of up to five cents goes on the largest credit line and the line is flagged. A larger one stops the run.

Every journal line links to what is behind it in the HTML site: step 1 to the bonus events, step 2 to the recognition slices, and the net journal to its two step lines.

## What this proves, and what it does not

It proves that the rules can run as repeatable code on deposit grain, that the journals balance, and that every line traces to its inputs. September 2026 reproduces the hand-calculated figures in `tests/reference_expected.json` and the Mickey Mouse workbook CSVs in `reference/`: RaF 40.00 and welcome 40.00 in the Journal summary, new bonuses 120.00, and a net journal of Dr Prepayments 40, Dr Contract costs 40, Dr Revenue 20, Cr Selling and distribution 100.

It does not prove that the split into bonus payout capitalisation and bonus cost release is how Finance books it. That split is our own reading. It does not evaluate the IFRS 15 criteria, does not model the IRRS performance obligation configuration, and does not cover cancellations, early termination, foreign exchange, corrections or the send to the ERP. The sample has no true-up in the last month of a term, which leaves a residual far below a cent per bonus.


## Layout

```
data/*.csv            invented example inputs, GL map, config
schema/schema.sql     tables and views
src/load.py           create database, load inputs, validate
src/calc.py           transformation, grouping, Journal summary
src/posting.py        capitalisation, release, net journal, sign case
src/checks.py         reconciliation checks
src/build_site.py     static HTML
src/run.py            entry point
tests/                reference tests, posting rules
reference/            copies of the Mickey Mouse sample workbook CSVs, used by the tests
(repository root) .github/workflows/pages.yml: test job and GitHub Pages job
```

`build/` (the SQLite file) and `public/` (the site) are generated and ignored by git.

## Reference files and publishing

The tests compare the run with the three Mickey Mouse sample CSVs in `reference/` (transformation, Journal summary, monthly posting Sep 2026). All figures are invented.

The GitHub Actions workflow (`.github/workflows/pages.yml` at the repository root) runs the tests on every push to main, then builds the site and publishes it with GitHub Pages under `/bonus-recognition-prototype/`.
