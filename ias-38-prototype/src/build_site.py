"""Render the calculation and postings as a static HTML site (no JavaScript needed
except the optional Mermaid diagram on the model page)."""
import json
import os
from html import escape

from src import calc, load, posting

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTED_PATH = os.path.join(ROOT, "tests", "reference_expected.json")

CSS = """
body{font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0;color:#1d2433;background:#f6f7f9}
header{background:#1d2433;color:#fff;padding:14px 28px}
header a{color:#cfd6e4;margin-right:18px;text-decoration:none;font-size:14px}
header a:hover{color:#fff}
main{max-width:1180px;margin:0 auto;padding:24px 28px 60px}
h1{font-size:24px;margin:8px 0 4px}h2{font-size:18px;margin:32px 0 8px}h3{font-size:15px;margin:22px 0 6px}
table{border-collapse:collapse;width:100%;background:#fff;margin:8px 0 16px;font-size:13.5px}
th,td{border:1px solid #dde1e8;padding:5px 9px;text-align:left;vertical-align:top}
th{background:#eef1f6}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr.total td{font-weight:600;background:#f3f5f9}
.note{background:#fff7e0;border:1px solid #ecd48a;padding:10px 14px;margin:12px 0}
.warn{background:#fdecea;border:1px solid #e6a9a3;padding:10px 14px;margin:12px 0}
.ok{color:#17703a}.bad{color:#b3261e}.muted{color:#6b7385}
code,pre{font:12.5px/1.4 Menlo,Consolas,monospace}pre{background:#fff;border:1px solid #dde1e8;padding:12px;overflow:auto}
a{color:#1a56c4}tr:target td{background:#fff3b0}
.flag{display:inline-block;background:#eef1f6;border-radius:3px;padding:0 6px;font-size:12px;margin-right:4px}
"""

NAV = [("index.html", "Overview"), ("projects.html", "Projects"),
       ("allocation.html", "Cost allocation"), ("project_value.html", "Project value"),
       ("journals.html", "Journals"), ("amortisation.html", "Amortisation"),
       ("reconciliation.html", "Reconciliation"), ("model.html", "Data model")]


def fmt(x, places=2):
    return "{:,.{p}f}".format(x, p=places)


def td(value, num=False, extra=""):
    cls = ' class="n"' if num else ""
    return "<td%s%s>%s</td>" % (cls, extra, value)


def table(headers, rows, numeric=(), total_last=False, row_ids=None):
    out = ["<table><thead><tr>"]
    for i, h in enumerate(headers):
        out.append('<th%s>%s</th>' % (' class="n"' if i in numeric else "", escape(h)))
    out.append("</tr></thead><tbody>")
    for ri, row in enumerate(rows):
        cls = ' class="total"' if total_last and ri == len(rows) - 1 else ""
        if row_ids and row_ids[ri]:
            cls += ' id="%s"' % escape(row_ids[ri])
        out.append("<tr%s>" % cls)
        for i, cell in enumerate(row):
            out.append(td(cell, i in numeric))
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def page(title, body):
    nav = "".join('<a href="%s">%s</a>' % (h, escape(t)) for h, t in NAV)
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><title>%s</title>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='robots' content='noindex'><style>%s</style></head><body>"
            "<header>%s</header><main><h1>%s</h1>"
            "<div class='note'>Example data only. Every name and figure is invented. "
            "Do not load real payroll data here.</div>%s"
            "</main></body></html>" % (escape(title), CSS, nav, escape(title), body))


def ref_link(ref):
    if ref.startswith("asset-"):
        return '<a href="amortisation.html#%s">%s</a>' % (escape(ref), escape(ref))
    return '<a href="allocation.html#%s">%s</a>' % (escape(ref), escape(ref))


def flags_html(flags):
    return "".join('<span class="flag">%s</span>' % escape(f) for f in flags.split(",") if f)


def build(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with open(EXPECTED_PATH) as handle:
        expected = json.load(handle)

    # Run in typed mode, for the reconciliation page only.
    typed_conn = load.build()
    calc.run(typed_conn, "typed")
    posting.post(typed_conn)
    typed_values = {r["project_id"]: r["project_value"]
                    for r in typed_conn.execute("SELECT * FROM v_project_value")}

    conn = load.build()
    calc.run(conn, "derived")
    posting.post(conn)

    pages = {
        "index.html": page_index(conn),
        "projects.html": page_projects(conn),
        "allocation.html": page_allocation(conn),
        "project_value.html": page_project_value(conn),
        "journals.html": page_journals(conn),
        "amortisation.html": page_amortisation(conn),
        "reconciliation.html": page_reconciliation(conn, typed_values, expected),
        "model.html": page_model(),
    }
    for name, content in pages.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as handle:
            handle.write(content)
    return sorted(pages)


# Pages ---------------------------------------------------------------------

def page_index(conn):
    run = conn.execute("SELECT * FROM calc_run").fetchone()
    group = conn.execute("SELECT SUM(amount) FROM capitalised_line").fetchone()[0]
    n_post = conn.execute("SELECT COUNT(*) FROM posting").fetchone()[0]
    warnings = []
    for r in conn.execute(
            """SELECT project_id, project_name, start_date, MIN(l.period) AS first_period
               FROM project JOIN capitalised_line l USING (project_id)
               WHERE start_date IS NOT NULL AND substr(start_date,1,7) > l.period
               GROUP BY project_id"""):
        warnings.append("%s (%s) has capitalised cost for %s, but its start date is %s. "
                        "Costs before the start of development are not capitalisable under "
                        "IAS 38.57 and 38.65. Confirm the start date with Finance."
                        % (r["project_id"], r["project_name"], r["first_period"], r["start_date"]))
    fb = conn.execute("SELECT COUNT(*) FROM capitalised_line WHERE flags LIKE '%rate_fallback%'"
                      ).fetchone()[0]
    if fb:
        warnings.append("%d lines use the overhead rate of another entity because the staff member's own "
                        "entity has no overhead pool in the data. Decide whether that is acceptable." % fb)
    neg = conn.execute("SELECT person_id, amount FROM capitalised_line WHERE amount < 0").fetchall()
    for r in neg:
        warnings.append("Negative %s line for %s (%s). Kept as a negative credit; source value "
                        "needs checking." % ("ESOP", r["person_id"], fmt(r["amount"])))
    n_assess = conn.execute("SELECT COUNT(*) FROM recognition_assessment").fetchone()[0]
    if not n_assess:
        warnings.append("No IAS 38.57 recognition assessment is recorded for any project. The "
                        "data only holds an 'approved' status. The engine reads that status "
                        "and evaluates nothing.")
    body = "<p>This prototype runs a small invented IAS 38 example end to end: inputs, cost " \
           "allocation, project value postings, go-live reclassification and amortisation. " \
           "Every journal line links back to the allocation lines behind it.</p>"
    body += table(["Measure", "Value"], [
        ["Group capitalised cost (Jan 2026)", fmt(group)],
        ["Journals proposed", str(n_post)],
        ["Overhead mode", escape(run["overhead_mode"])],
        ["Input fingerprint", "<code>%s</code>" % escape(run["input_hash"])],
    ], numeric=(1,))
    body += "<h2>Review before relying on these numbers</h2>"
    body += "".join("<div class='warn'>%s</div>" % escape(w) for w in warnings)
    body += ("<h2>What this proves and what it does not</h2><p>It proves the capitalisation rules can be "
             "written as repeatable code with a balanced, traceable posting output. It does not "
             "prove that any project meets the IAS 38.57 criteria, that the overhead scope is "
             "acceptable under IAS 38.66 and 38.67, or that the numbers are ready for an accounting system. "
             "Nothing is sent to an ERP.</p>")
    return page("IAS 38 capitalisation prototype", body)


def page_projects(conn):
    rows = []
    for p in conn.execute("SELECT * FROM project ORDER BY project_id"):
        rows.append([escape(p["project_id"]), escape(p["project_name"]), escape(p["entity_code"]),
                     escape(p["product_idea_id"] or ""), escape(p["capitalisation_status"]),
                     escape(p["project_status"]), escape(p["start_date"] or ""),
                     escape(p["go_live_date"] or "")])
    body = table(["Project", "Name", "Entity", "Product idea", "Capitalisation status", "Status",
                  "Start", "Go-live"], rows)
    body += ("<p class='muted'>Capitalisation status is a recorded decision by a person. A "
             "real register would be maintained in the accounting system.</p>")
    return page("Projects", body)


def page_allocation(conn):
    body = "<p>One row per person, project, period and cost component. Contribution is weeks " \
           "logged divided by net working weeks. Journal lines link to the anchors below.</p>"
    for p in conn.execute("SELECT DISTINCT project_id, period FROM capitalised_line "
                          "ORDER BY project_id, period").fetchall():
        pid, period = p["project_id"], p["period"]
        body += '<h2 id="%s">%s, %s</h2>' % (escape(posting.anchor_project(pid, period)),
                                            escape(pid), escape(period))
        rows = []
        for l in conn.execute(
                """SELECT * FROM capitalised_line WHERE project_id=? AND period=?
                   ORDER BY person_id, line_id""", (pid, period)):
            rows.append([escape(l["person_id"]), escape(l["cost_component"]),
                         fmt(l["weeks_logged"]), fmt(l["net_weeks"], 1),
                         fmt(l["contribution_pct"] * 100) + "%", fmt(l["basis_amount"]),
                         fmt(l["amount"]), flags_html(l["flags"])])
        total = conn.execute("SELECT SUM(amount) FROM capitalised_line WHERE project_id=? "
                             "AND period=?", (pid, period)).fetchone()[0]
        rows.append(["Total", "", "", "", "", "", fmt(total), ""])
        body += table(["Person", "Component", "Weeks", "Net weeks", "Contribution",
                       "Basis (monthly amount or rate)", "Amount", "Flags"], rows,
                      numeric=(2, 3, 4, 5, 6), total_last=True)

    body += "<h2>By entity and cost component</h2>"
    rows, ids = [], []
    for r in conn.execute(
            """SELECT entity_code, period, cost_component, SUM(amount) AS v FROM capitalised_line
               GROUP BY entity_code, period, cost_component ORDER BY entity_code, period"""):
        ids.append(posting.anchor_component(r["entity_code"], r["period"], r["cost_component"]))
        rows.append([escape(r["entity_code"]), escape(r["period"]),
                     escape(r["cost_component"]), fmt(r["v"], 4)])
    body += table(["Entity", "Period", "Component", "Amount"], rows, numeric=(3,), row_ids=ids)
    return page("Cost allocation", body)


def page_project_value(conn):
    rows = []
    for r in conn.execute("SELECT v.*, p.project_name FROM v_project_value v "
                          "JOIN project p USING (project_id) ORDER BY project_id"):
        rows.append([escape(r["project_id"]), escape(r["project_name"]), escape(r["entity_code"]),
                     escape(r["period"]), fmt(r["project_value"])])
    body = "<h2>Project value (gross asset cost)</h2>"
    body += table(["Project", "Name", "Entity", "Period", "Project value"], rows, numeric=(4,))
    rows = [[escape(r["entity_code"]), escape(r["period"]), fmt(r["total"])]
            for r in conn.execute("SELECT * FROM v_entity_total ORDER BY entity_code")]
    group = conn.execute("SELECT SUM(amount) FROM capitalised_line").fetchone()[0]
    rows.append(["Group", "", fmt(group)])
    body += "<h2>Entity and group totals</h2>"
    body += table(["Entity", "Period", "Total"], rows, numeric=(2,), total_last=True)
    rows = []
    for r in conn.execute("SELECT * FROM v_proj_cost_allocation ORDER BY project_id"):
        rows.append([escape(r["project_id"]), escape(r["cost_type_group"])]
                    + [fmt(r[c]) for c in ("salary", "contractor", "social_security", "esop",
                                           "general_overhead", "department_overhead", "total")])
    body += "<h2>By cost component</h2>"
    body += table(["Project", "Type", "Salary", "Contractor", "Social security", "ESOP",
                   "General overhead", "Department overhead", "Total"], rows,
                  numeric=(2, 3, 4, 5, 6, 7, 8))
    return page("Project value", body)


def page_journals(conn):
    body = ("<p>Proposed journals. Debit is the capitalised cost per project to Software WIP "
            "(account 1001); credit is the same total split by cost component across the capitalised-"
            "cost accounts. Amounts are rounded to cents here, and a difference of up to 5 cents "
            "goes on the largest credit line.</p>")
    names = {r["account_code"]: r["account_name"] for r in conn.execute("SELECT * FROM gl_account")}
    postings = conn.execute("SELECT * FROM posting ORDER BY period, entity_code, posting_type").fetchall()
    shown_amort = 0
    for p in postings:
        if p["posting_type"] == "amortisation":
            shown_amort += 1
            if shown_amort > 3:
                continue
        lines = conn.execute("SELECT * FROM posting_line WHERE posting_id=? ORDER BY line_id",
                             (p["posting_id"],)).fetchall()
        rows = [[escape(l["account_code"] + " " + names[l["account_code"]]),
                 escape(l["project_id"] or ""), fmt(l["debit"]) if l["debit"] else "",
                 fmt(l["credit"]) if l["credit"] else "", escape(l["description"]),
                 ref_link(l["source_ref"])] for l in lines]
        rows.append(["Total", "", fmt(sum(l["debit"] for l in lines)),
                     fmt(sum(l["credit"] for l in lines)), "", ""])
        body += "<h2>%s, %s, %s</h2>" % (escape(p["posting_type"].replace("_", " ")),
                                         escape(p["period"]), escape(p["entity_code"]))
        body += table(["Account", "Project", "Debit", "Credit", "Description", "Source"], rows,
                      numeric=(2, 3), total_last=True)
    hidden = shown_amort - 3
    if hidden > 0:
        body += ("<p class='muted'>%d further monthly amortisation journals (60 in total) follow "
                 "the same pattern. The full schedule is on the amortisation page.</p>" % hidden)
    return page("Journals", body)


def page_amortisation(conn):
    body = ""
    for a in conn.execute("SELECT * FROM intangible_asset ORDER BY asset_id").fetchall():
        body += '<h2 id="asset-%s">%s, %s</h2>' % (escape(a["asset_id"]), escape(a["asset_id"]),
                                                  escape(a["entity_code"]))
        body += table(["Project", "Go-live", "Gross cost", "Useful life (months)"],
                      [[escape(a["project_id"]), escape(a["go_live_date"]), fmt(a["gross_cost"]),
                        str(a["useful_life_months"])]], numeric=(2, 3))
        rows = [[str(m["period_index"]), escape(m["period"]), escape(m["movement_type"]),
                 fmt(m["amount"]), fmt(m["accumulated"]), fmt(m["net_book_value"])]
                for m in conn.execute("SELECT * FROM asset_movement WHERE asset_id=? "
                                      "ORDER BY period_index", (a["asset_id"],))]
        body += table(["#", "Period", "Movement", "Amount", "Accumulated", "Net book value"],
                      rows, numeric=(0, 3, 4, 5))
    body += ("<p class='muted'>Useful life 60 months is the example default. Whether it is right "
             "for each asset is a policy decision for Finance. Month 1 is the go-live month. "
             "The last month absorbs the rounding difference.</p>")
    return page("Amortisation", body)


def page_reconciliation(conn, typed_values, expected):
    derived = {r["project_id"]: r["project_value"]
               for r in conn.execute("SELECT * FROM v_project_value")}
    rows = []
    for pid in sorted(expected["project_value"]):
        ref = expected["project_value"][pid]
        typed = typed_values[pid]
        rows.append([escape(pid), fmt(ref, 4), fmt(typed, 4), fmt(ref - typed, 4),
                     fmt(derived[pid], 4), fmt(derived[pid] - ref, 4)])
    body = ("<p>The reference values in <code>tests/reference_expected.json</code> were worked out "
            "by hand from the input files, not produced by the engine. The engine is run twice: "
            "once with overhead amounts typed per person and rounded, as a spreadsheet would hold "
            "them, and once with rates derived from pools and headcount (what the engine does on "
            "its own).</p>")
    body += table(["Project", "Reference", "Engine, typed rates", "Difference",
                   "Engine, derived rates", "Derived minus reference"], rows,
                  numeric=(1, 2, 3, 4, 5))
    rates = [[escape(r["entity_code"]), escape(r["pool_type"]), escape(r["department"] or "all"),
              fmt(r["rate"], 2)]
             for r in conn.execute("SELECT * FROM overhead_rate ORDER BY pool_type, department")]
    typed_rates = sorted({(r["general_overhead"], r["dept_overhead"])
                          for r in conn.execute("SELECT * FROM typed_overhead_input")})
    body += ("<h2>Reading the table</h2>"
             "<p>With the typed overhead amounts the engine reproduces every reference project "
             "value (difference column).</p>"
             "<p>The last two columns show the effect of typed, rounded overhead rates. The "
             "rates the pools and headcounts give are below. The typed amounts are %s.</p>"
             % escape(", ".join("%s general / %s department" % (fmt(g, 0), fmt(d, 0))
                                for g, d in typed_rates)))
    body += table(["Entity", "Pool", "Department", "Derived rate per head"], rates, numeric=(3,))
    entity = sorted(expected["entity_total"])[0]
    body += "<h2>Reference credits by account, %s</h2>" % escape(entity)
    got_rows = [[escape(acct), fmt(want, 4)] for acct, want in expected["credits_a"].items()]
    body += table(["Account", "Reference credit"], got_rows, numeric=(1,))
    body += ("<p class='muted'>The automated check of these against the engine runs in "
             "<code>tests/test_reference.py</code>, and the pipeline fails if it does not pass.</p>")
    return page("Reconciliation to the hand-calculated reference", body)


def page_model():
    erd = """erDiagram
    LEGAL_ENTITY ||--o{ PROJECT : owns
    LEGAL_ENTITY ||--o{ PERSON : employs
    PROJECT ||--o{ RECOGNITION_ASSESSMENT : assessed_by
    PERSON ||--o{ COST_RECORD : has
    PERSON ||--o{ EFFORT_RECORD : logs
    PROJECT ||--o{ EFFORT_RECORD : receives
    PERSON ||--o{ CAPITALISED_LINE : generates
    PROJECT ||--o{ CAPITALISED_LINE : accumulates
    PROJECT ||--o| INTANGIBLE_ASSET : becomes
    AMORTISATION_POLICY ||--o{ INTANGIBLE_ASSET : governs
    INTANGIBLE_ASSET ||--o{ ASSET_MOVEMENT : moves
    POSTING ||--|{ POSTING_LINE : contains
    GL_ACCOUNT ||--o{ POSTING_LINE : booked_to
    GL_ACCOUNT ||--o{ POSTING_MAP : mapped_by
"""
    with open(load.SCHEMA) as handle:
        ddl = handle.read()
    body = ("<p>Inputs are recorded or parameterised. Calculated tables are rebuilt on every run "
            "and carry the run identifier. Entities and links shown below are the ones in "
            "<code>schema/schema.sql</code>.</p>"
            "<pre class='mermaid'>%s</pre>"
            "<p class='muted'>The diagram needs internet access to load Mermaid. The DDL below "
            "is the source of truth.</p><h2>Schema</h2><pre>%s</pre>"
            "<script type='module'>import mermaid from "
            "'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';"
            "mermaid.initialize({startOnLoad:true});</script>"
            % (escape(erd), escape(ddl)))
    return page("Data model", body)
