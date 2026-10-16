"""Render the calculation and journals as a static HTML site.

JavaScript is used in one place only: the month picker on the Monthly posting page.
Without JavaScript every month is shown one after the other.
"""
import os
from html import escape

from src import calc, checks, posting
from src.money import D, ZERO, fmt

CSS = """
body{font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0;color:#1d2433;background:#f6f7f9}
header{background:#1d2433;color:#fff;padding:14px 28px}
header span{font-weight:600;margin-right:26px}
header a{color:#cfd6e4;margin-right:16px;text-decoration:none;font-size:14px}
header a:hover,header a.active{color:#fff;text-decoration:underline}
main{max-width:1180px;margin:0 auto;padding:24px 28px 60px}
h1{font-size:24px;margin:8px 0 4px}h2{font-size:18px;margin:32px 0 8px}h3{font-size:15px;margin:22px 0 6px}
table{border-collapse:collapse;width:100%;background:#fff;margin:8px 0 16px;font-size:13.5px}
th,td{border:1px solid #dde1e8;padding:5px 9px;text-align:left;vertical-align:top}
th{background:#eef1f6}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr.total td{font-weight:600;background:#f3f5f9}
tr.active td{background:#e7f2e4}
.note{background:#fff7e0;border:1px solid #ecd48a;padding:10px 14px;margin:12px 0}
.warn{background:#fdecea;border:1px solid #e6a9a3;padding:10px 14px;margin:12px 0}
.ok{color:#17703a}.bad{color:#b3261e}.muted{color:#6b7385}
code{font:12.5px/1.4 Menlo,Consolas,monospace}
a{color:#1a56c4}tr:target td{background:#fff3b0}
details summary{cursor:pointer;color:#1a56c4}
select{font:inherit;padding:3px 6px}
.flag{display:inline-block;background:#eef1f6;border-radius:3px;padding:0 6px;font-size:12px;margin-right:4px}
"""

NAV = [
    ("index.html", "Overview"),
    ("inputs.html", "Inputs"),
    ("transformation.html", "Transformation"),
    ("grouping.html", "Grouping and Journal summary"),
    ("posting.html", "Monthly posting"),
    ("reconciliation.html", "Reconciliation"),
    ("model.html", "Model and open points"),
]

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def month_label(ppid):
    return "%s %s" % (MONTHS[int(ppid[4:6]) - 1], ppid[:4])


def page(title, active, body, script=""):
    nav = "".join(
        '<a href="%s"%s>%s</a>' % (href, ' class="active"' if href == active else "", escape(label))
        for href, label in NAV)
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>%s | Bonus recognition prototype</title><style>%s</style></head><body>"
        "<header><span>Marketing bonus cost recognition prototype</span>%s</header><main>%s</main>%s</body></html>"
        % (escape(title), CSS, nav, body, ("<script>%s</script>" % script) if script else ""))


def table(headers, rows, numeric=(), row_ids=None, total_last=False, row_classes=None):
    """Cells are HTML. Callers escape what they put in."""
    out = ["<table><thead><tr>"]
    for i, h in enumerate(headers):
        out.append("<th%s>%s</th>" % (' class="n"' if i in numeric else "", escape(h)))
    out.append("</tr></thead><tbody>")
    for ri, row in enumerate(rows):
        classes = []
        if total_last and ri == len(rows) - 1:
            classes.append("total")
        if row_classes and row_classes[ri]:
            classes.append(row_classes[ri])
        attrs = ""
        if classes:
            attrs += ' class="%s"' % " ".join(classes)
        if row_ids and row_ids[ri]:
            attrs += ' id="%s"' % escape(row_ids[ri])
        out.append("<tr%s>" % attrs)
        for i, cell in enumerate(row):
            out.append("<td%s>%s</td>" % (' class="n"' if i in numeric else "", cell))
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def money_cell(text):
    """Blank for zero, so a journal reads as debits on the left and credits on the right."""
    return "" if D(text) == 0 else fmt(text)


def side_amount(signed):
    signed = D(signed)
    if signed == 0:
        return "0.00"
    return "%s %s" % ("Dr" if signed > 0 else "Cr", fmt(abs(signed)))


# Data access ----------------------------------------------------------------

def load_events(conn):
    return conn.execute(
        "SELECT e.*, m.process_type_id, p.process_type, p.posting_status FROM bonus_event e "
        "JOIN bonus_type_map m ON m.bonus_type = e.bonus_type "
        "JOIN process_type p ON p.process_type_id = m.process_type_id ORDER BY e.event_id").fetchall()


def event_link(event_id):
    return '<a href="inputs.html#event-%s">%s</a>' % (escape(event_id), escape(event_id))


def slice_link(slice_id, label=None):
    return '<a href="transformation.html#slice-%s">%s</a>' % (slice_id, escape(label or str(slice_id)))


# Pages ------------------------------------------------------------------------

def build_index(conn, cfg, check_list):
    run = conn.execute("SELECT * FROM run").fetchone()
    n_events = conn.execute("SELECT COUNT(*) FROM bonus_event").fetchone()[0]
    n_slices = conn.execute("SELECT COUNT(*) FROM recognition_slice").fetchone()[0]
    n_periods = conn.execute("SELECT COUNT(DISTINCT posting_period_id) FROM journal_summary").fetchone()[0]
    ok = sum(1 for c in check_list if c["ok"])
    default = cfg["default_period"]
    head = ""
    p = conn.execute("SELECT * FROM posting_period WHERE posting_period_id = ? AND process_type_id = "
                     "(SELECT MIN(process_type_id) FROM posting_map)", (default,)).fetchone()
    if p:
        net = conn.execute(
            "SELECT account, debit, credit FROM posting_line WHERE journal = 'net' AND "
            "posting_period_id = ? AND process_type_id = ? ORDER BY line_no",
            (default, p["process_type_id"])).fetchall()
        rows = [[escape(l["account"]), money_cell(l["debit"]), money_cell(l["credit"])] for l in net]
        head = (
            "<h2>Headline: %s</h2><p>New bonuses starting in the month are %s and the recognised amount "
            "is %s. The net journal to post for refer-a-friend is below. "
            "<a href=\"posting.html#p%s\">Open the full walkthrough</a>.</p>%s"
            % (month_label(default), fmt(p["new_bonuses"]), fmt(p["recognised"], 2), default,
               table(["Account", "Debit", "Credit"], rows, numeric=(1, 2))))
    body = (
        "<h1>Marketing bonus cost recognition: prototype</h1>"
        "<p class=\"muted\">A working prototype of the bonus recognition flow on an invented example. It takes bonus "
        "events per deposit, recognises them over the deposit term, groups them by posting period and "
        "process type, and builds the refer-a-friend (RaF) journals for the ERP. Nothing is sent to an "
        "accounting system.</p>"
        "<p>The flow is: Inputs, Transformation, Grouping, Journal summary, then Monthly posting with "
        "bonus payout capitalisation, bonus cost release and the net journal.</p>"
        "<div class=\"note\">All deposits, amounts and dates are invented (the Mickey Mouse sample).</div>"
        + head +
        "<h2>This run</h2>"
        + table(["Item", "Value"], [
            ["Run id", "<code>%s</code>" % escape(run["run_id"])],
            ["Input fingerprint", "<code>%s</code>" % escape(run["fingerprint"])],
            ["Bonus events", str(n_events)],
            ["Recognition slices", "%d (all months of every term)" % n_slices],
            ["Reporting window", "%s to %s (%d posting periods with activity)" % (
                month_label(cfg["window_start"]), month_label(cfg["window_end"]), n_periods)],
            ["Checks", '<a href="reconciliation.html">%d of %d pass</a>' % (ok, len(check_list))],
        ]) +
        "<h2>What this proves</h2><ul>"
        "<li>The rules can run as repeatable code on deposit grain: straight line from the placement "
        "month, overnight in the open month, payout date ignored.</li>"
        "<li>The journals balance and every line traces back to the events and slices behind it.</li>"
        "<li>The September 2026 figures match the Mickey Mouse workbook.</li></ul>"
        "<h2>What it does not prove</h2><ul>"
        "<li>That the split into bonus payout capitalisation and bonus cost release is how Finance books it. "
        "It is our own reading and has not been confirmed.</li>"
        "<li>The welcome bonus GL pattern. Welcome stops at the Journal summary.</li>"
        "<li>The IFRS 15 treatment itself, the IRRS performance obligation configuration, cancellations, "
        "early termination, foreign exchange, or the send to the ERP.</li></ul>"
        "<p><a href=\"model.html\">Open points with Alex and Finance</a></p>")
    return page("Overview", "index.html", body)


def build_inputs(conn, events):
    rows, ids = [], []
    for e in events:
        end = ""
        slices = conn.execute("SELECT MIN(posting_period_id) a, MAX(posting_period_id) b FROM recognition_slice "
                              "WHERE event_id = ?", (e["event_id"],)).fetchone()
        end = "%s to %s" % (month_label(slices["a"]), month_label(slices["b"]))
        rows.append([
            escape(e["event_id"]), escape(e["sample"]), escape(e["deposit_id"]), escape(e["bonus_type"]),
            "%s (%s)" % (e["process_type_id"], escape(e["process_type"])),
            "term" if e["product_id"] == 1 else "overnight", str(e["term"]), fmt(e["amount"]),
            escape(e["placement_date"]), escape(e["payout_date"]), end,
            '<a href="transformation.html#ev-%s">slices</a>' % escape(e["event_id"]),
        ])
        ids.append("event-" + e["event_id"])
    body = (
        "<h1>Inputs</h1><p>One row per bonus event. A deposit with a lead and a referrer bonus has two "
        "events. The bonus type decides the process type. Lead and referrer share refer_a_friend "
        "(confirmed by Alex), so they post identically.</p>"
        "<div class=\"note\">The payout date is metadata only. Recognition starts at the placement date, "
        "so moving the payout date does not move any amount.</div>"
        + table(["Event", "Sample", "depositId", "Bonus type", "Process type (placeholder id)", "Product",
                 "Term", "Amount", "Placement date", "Payout date", "Recognised", "Detail"],
                rows, numeric=(6, 7), row_ids=ids))
    return page("Inputs", "inputs.html", body)


def build_transformation(conn, events):
    parts = ["<h1>Transformation</h1><p>One row per bonus event per recognition month. Term deposits: "
             "amount divided by term, rounded to 8 decimals, from the placement month. Overnight: 100% "
             "in the placement month. Rows outside the reporting window are kept but do not reach the "
             "Journal summary.</p>"]
    for e in events:
        slices = conn.execute("SELECT * FROM recognition_slice WHERE event_id = ? ORDER BY month_index",
                              (e["event_id"],)).fetchall()
        total = sum((D(s["recognised"]) for s in slices), ZERO)
        rule = "amount / term" if e["term"] else "100% in the placement month"
        rows = [[str(s["month_index"]), month_label(s["posting_period_id"]), s["posting_period_id"],
                 fmt(s["recognised"], 8), "yes" if s["in_window"] else "no"] for s in slices]
        ids = ["slice-%d" % s["slice_id"] for s in slices]
        rows.append(["Total", "", "", fmt(total, 8), ""])
        ids.append(None)
        parts.append(
            '<h2 id="ev-%s">%s: %s, %s</h2><p class="muted">%s, %s, term %d, amount %s, rule %s, '
            'residual against the bonus amount %s. <a href="inputs.html#event-%s">Input row</a></p>%s'
            % (escape(e["event_id"]), escape(e["event_id"]), escape(e["deposit_id"]), escape(e["bonus_type"]),
               escape(e["sample"]), "term" if e["product_id"] == 1 else "overnight", e["term"],
               fmt(e["amount"]), rule, fmt(total - D(e["amount"]), 8), escape(e["event_id"]),
               table(["Month", "Posting month", "posting_period_id", "Recognised (8 dp)", "In window"],
                     rows, numeric=(0, 3), row_ids=ids, total_last=True)))
    return page("Transformation", "transformation.html", "".join(parts))


def build_grouping(conn, cfg):
    rows, ids = [], []
    labels = {r["process_type_id"]: r for r in conn.execute("SELECT * FROM process_type")}
    posted = {(r["posting_period_id"], r["process_type_id"])
              for r in conn.execute("SELECT posting_period_id, process_type_id FROM posting_period")}
    for s in conn.execute("SELECT * FROM journal_summary ORDER BY posting_period_id, process_type_id"):
        slices = conn.execute(
            "SELECT slice_id, deposit_id, bonus_type, recognised FROM v_slice_detail WHERE "
            "posting_period_id = ? AND process_type_id = ? AND in_window = 1 ORDER BY slice_id",
            (s["posting_period_id"], s["process_type_id"])).fetchall()
        detail = "<details><summary>%d slices</summary>%s</details>" % (
            len(slices), "<br>".join(
                "%s %s %s" % (slice_link(x["slice_id"], "slice %d" % x["slice_id"]), escape(x["deposit_id"]),
                              fmt(x["recognised"], 8)) for x in slices))
        pt = labels[s["process_type_id"]]
        if (s["posting_period_id"], s["process_type_id"]) in posted:
            nxt = '<a href="posting.html#p%s">journal</a>' % s["posting_period_id"]
        else:
            nxt = '<span class="muted">GL %s</span>' % escape(pt["posting_status"])
        rows.append([month_label(s["posting_period_id"]), s["posting_period_id"],
                     "%s (%s)" % (s["process_type_id"], escape(pt["process_type"])),
                     "<code>%s</code>" % escape(s["grouping_key"]), fmt(s["amount"], 8), detail, nxt])
        ids.append("js-%s-%s" % (s["posting_period_id"], s["process_type_id"]))
    body = (
        "<h1>Grouping and Journal summary</h1>"
        "<p>The Grouping key is <code>{posting_period_id}-{process_type_id}-%s-%s-%s</code> (erp currency, "
        "subsidiary, monthly run). The Journal summary is the sum of the deposit slices behind each key. "
        "No ERP customer or vendor is on the key.</p>"
        "<div class=\"note\">Welcome has a Journal summary line but no GL lines yet. The process type ids "
        "9001 and 9002 are placeholders.</div>%s"
        % (cfg["erp_currency_id"], cfg["subsidiary_id"], cfg["monthly_run_id"],
           table(["Posting month", "posting_period_id", "Process type", "Grouping key", "Aggregated amount",
                  "Slices", "Next"], rows, numeric=(4,), row_ids=ids)))
    return page("Grouping and Journal summary", "grouping.html", body)


def how_text(basis, share, n, r):
    pct = "{:f}%".format((D(share) * 100).normalize())
    if basis == "new_bonuses":
        return "%s of new bonuses (%s)" % (pct, fmt(n))
    return "%s of recognised amount (%s)" % (pct, fmt(r, 8))


def build_posting(conn, cfg):
    periods = conn.execute(
        "SELECT p.*, t.process_type FROM posting_period p JOIN process_type t USING (process_type_id) "
        "ORDER BY p.posting_period_id, p.process_type_id").fetchall()
    sections, options = [], []
    for p in periods:
        ppid, ptid = p["posting_period_id"], p["process_type_id"]
        n, r = D(p["new_bonuses"]), D(p["recognised"])
        options.append('<option value="%s">%s</option>' % (ppid, month_label(ppid)))
        parts = ['<section class="period" id="p%s"><h2>%s: %s</h2>' % (ppid, month_label(ppid), escape(p["process_type"]))]
        key = conn.execute("SELECT grouping_key FROM journal_summary WHERE posting_period_id = ? AND "
                           "process_type_id = ?", (ppid, ptid)).fetchone()
        parts.append(table(["Input", "Value", "Source"], [
            ["New bonuses (N)", fmt(n), "whole bonus of events whose schedule starts in the month"],
            ["Recognised amount (R)", fmt(r, 8),
             '<a href="grouping.html#js-%s-%s">Journal summary %s</a>' % (ppid, ptid, escape(key["grouping_key"]))
             if key else "no slices in the month"],
            ["Case", escape(posting.REGIME_TEXT[p["regime"]]), "from N and R, see the sign table below"],
        ]))
        for journal, title, explain in (
            ("capitalisation", "Step 1, bonus payout capitalisation",
             "The whole bonus of deposits that start in this month, once per bonus."),
            ("release", "Step 2, bonus cost release",
             "The recognised amount of the month, for every active deposit, following the transformation schedule."),
        ):
            lines = conn.execute(
                "SELECT * FROM posting_line WHERE journal = ? AND posting_period_id = ? AND process_type_id = ? "
                "ORDER BY line_no", (journal, ppid, ptid)).fetchall()
            parts.append("<h3>%s</h3><p class=\"muted\">%s</p>" % (title, explain))
            if not lines:
                parts.append("<p class=\"muted\">No lines. N is %s in this month.</p>" % fmt(n) if journal == "capitalisation"
                             else "<p class=\"muted\">No lines.</p>")
                continue
            rows, ids = [], []
            for l in lines:
                srcs = conn.execute("SELECT * FROM posting_line_source WHERE line_id = ?", (l["line_id"],)).fetchall()
                if journal == "capitalisation":
                    frm = ", ".join(event_link(s["source_ref"]) for s in srcs)
                else:
                    frm = "<details><summary>%d slices</summary>%s</details>" % (
                        len(srcs), "<br>".join("%s %s" % (slice_link(s["source_ref"], "slice " + s["source_ref"]),
                                                          fmt(s["amount"], 8)) for s in srcs))
                flag = ' <span class="flag">rounding</span>' if l["rounding_adjusted"] else ""
                rows.append([escape(l["account"]), money_cell(l["debit"]), money_cell(l["credit"]),
                             how_text(l["basis"], l["share"], n, r) + flag, frm])
                ids.append("line-%d" % l["line_id"])
            rows.append(["Total", fmt(sum((D(l["debit"]) for l in lines), ZERO)),
                         fmt(sum((D(l["credit"]) for l in lines), ZERO)), "debits equal credits", ""])
            ids.append(None)
            parts.append(table(["Account", "Debit", "Credit", "How", "From"], rows, numeric=(1, 2),
                               row_ids=ids, total_last=True))
        net = conn.execute("SELECT * FROM posting_line WHERE journal = 'net' AND posting_period_id = ? "
                           "AND process_type_id = ? ORDER BY line_no", (ppid, ptid)).fetchall()
        rows, ids = [], []
        for l in net:
            srcs = conn.execute("SELECT * FROM posting_line_source WHERE line_id = ?", (l["line_id"],)).fetchall()
            frm = []
            for s in srcs:
                src_line = conn.execute("SELECT journal FROM posting_line WHERE line_id = ?", (s["source_ref"],)).fetchone()
                frm.append('<a href="#line-%s">%s %s</a>' % (s["source_ref"], escape(src_line["journal"]),
                                                             side_amount(s["amount"])))
            rows.append([escape(l["account"]), money_cell(l["debit"]), money_cell(l["credit"]), " + ".join(frm)])
            ids.append("line-%d" % l["line_id"])
        rows.append(["Total", fmt(sum((D(l["debit"]) for l in net), ZERO)),
                     fmt(sum((D(l["credit"]) for l in net), ZERO)), "debits equal credits"])
        ids.append(None)
        formula = posting.net_formula(n, r)
        parts.append("<h3>Net journal to post</h3><p class=\"muted\">Step 1 plus step 2, per account. Formula: "
                     "Prepayments and Contract costs "
                     "(N minus R) / 2 each, Revenue R / 2, Selling and distribution N minus R / 2.</p>")
        parts.append(table(["Account", "Debit", "Credit", "Made of"], rows, numeric=(1, 2), row_ids=ids,
                           total_last=True))
        parts.append("<p class=\"muted\">Formula check: %s</p>" % escape(
            "; ".join("%s %s" % (a, side_amount(formula[a])) for a in posting.ACCOUNTS)))
        parts.append("<h3>Debit or credit side by month</h3>")
        sign_rows, classes = [], []
        for reg in (1, 2, 3):
            signs = posting.REGIME_SIGNS[reg]
            sign_rows.append([escape(posting.REGIME_TEXT[reg])] + ["Debit" if s > 0 else "Credit" for s in signs])
            classes.append("active" if reg == p["regime"] else "")
        parts.append(table(["Condition (N new bonuses, R recognised)"] + posting.ACCOUNTS, sign_rows,
                           row_classes=classes))
        welcome = conn.execute("SELECT s.amount FROM journal_summary s JOIN process_type t USING (process_type_id) "
                               "WHERE s.posting_period_id = ? AND t.posting_status != 'defined'", (ppid,)).fetchone()
        if welcome:
            parts.append("<div class=\"note\">The welcome Journal summary line for this month is %s. Its GL "
                         "posting is still to be confirmed with Alex and Finance, so it is not part of this "
                         "journal.</div>" % fmt(welcome["amount"], 8))
        parts.append("</section>")
        sections.append("".join(parts))
    # One select entry per month, even if several process types are posted.
    seen, unique = set(), []
    for o in options:
        if o not in seen:
            seen.add(o)
            unique.append(o)
    body = (
        "<h1>Monthly posting</h1><p>The RaF journal for one recognition month. It is calculated for that month "
        "only and posted in the next month's close. Every line links to what is behind it: step 1 to the "
        "bonus events, step 2 to the recognition slices, the net journal to its two step lines.</p>"
        "<p>Month: <select id=\"picker\" data-default=\"%s\">%s</select></p>%s"
        % (escape(cfg["default_period"]), "".join(unique), "".join(sections)))
    script = (
        "(function(){var sel=document.getElementById('picker');"
        "var secs=document.querySelectorAll('section.period');"
        "function show(id){secs.forEach(function(s){s.style.display=(s.id==='p'+id)?'':'none'});sel.value=id;}"
        "function route(){var h=location.hash.slice(1);var el=h?document.getElementById(h):null;"
        "var sec=el?el.closest('section.period'):null;"
        "if(sec){show(sec.id.slice(1));el.scrollIntoView();}else{show(sel.dataset['default']);}}"
        "sel.addEventListener('change',function(){show(sel.value);history.replaceState(null,'','#p'+sel.value);});"
        "window.addEventListener('hashchange',route);route();})();")
    return page("Monthly posting", "posting.html", body, script)


def build_reconciliation(check_list):
    rows = [['<span class="%s">%s</span>' % ("ok" if c["ok"] else "bad", "pass" if c["ok"] else "FAIL"),
             escape(c["name"]), escape(c["detail"])] for c in check_list]
    body = ("<h1>Reconciliation</h1><p>Each check recomputes from the inputs and compares with what the run "
            "wrote. A failing check means the run should not be trusted.</p>"
            + table(["Result", "Check", "Detail"], rows))
    return page("Reconciliation", "reconciliation.html", body)


def build_model(conn):
    tables = [
        ["bonus_event, bonus_type_map, process_type", "input", "One row per bonus event. The bonus type decides the process type."],
        ["posting_map", "input", "GL lines per process type and step, with the share of the basis. Edit this to change the GL pattern."],
        ["config", "input", "Subsidiary, currency, run id, reporting window, default month, rounding tolerance."],
        ["recognition_slice", "calculated", "Transformation: one row per event per month."],
        ["journal_summary", "calculated", "Grouping key and amount per posting period and process type."],
        ["posting_period", "calculated", "N, R and the case per month."],
        ["posting_line, posting_line_source", "calculated", "Capitalisation, release and net journals, with the rows behind each line."],
    ]
    body = (
        "<h1>Model and open points</h1><h2>Tables</h2>"
        + table(["Table", "Layer", "What it holds"], [[escape(a), b, escape(c)] for a, b, c in tables]) +
        "<h2>Open with Alex and Finance</h2><ul>"
        "<li>Whether the bonus payout is already booked in Selling and distribution when it is paid. The "
        "credit in bonus payout capitalisation reverses that. If the payout is booked straight to the "
        "balance sheet, only the release is needed.</li>"
        "<li>Why each amount splits 50% and 50% between Prepayments and Contract costs, and why one half of "
        "the release goes against Revenue and the other against Selling and distribution.</li>"
        "<li>Who posts bonus payout capitalisation once the automation is live.</li>"
        "<li>How often to post. Journals may be posted per year or quarter, this prototype posts monthly.</li>"
        "<li>The welcome bonus GL pattern.</li>"
        "<li>The treatment of an amount capitalised in the placement month when the payout happens weeks "
        "later. Alex said the payout date does not matter.</li></ul>"
        "<h2>Not covered</h2><ul>"
        "<li>The IFRS 15 recognition criteria. This is a calculation, not an assessment.</li>"
        "<li>The IRRS performance obligation configuration that must produce a straight line for "
        "marketing_bonus.</li>"
        "<li>Cancellations and early termination. The remaining asset of a deposit that ends early is not "
        "written off.</li>"
        "<li>Foreign exchange. All amounts are in one currency.</li>"
        "<li>A last-month true-up. Slices are rounded to 8 decimals, which leaves a residual of well below "
        "a cent per bonus.</li>"
        "<li>Corrections of an already posted month.</li>"
        "<li>The send to the ERP and the matching of ERP internal ids.</li></ul>"
        "<h2>Placeholders</h2><p>Process type ids 9001 and 9002 and the grouping key suffix 1-2-1 are "
        "placeholders taken from the Mickey Mouse sample.</p>")
    return page("Model and open points", "model.html", body)


def build(conn, out_dir):
    cfg = calc.get_config(conn)
    check_list = checks.run_checks(conn)
    events = load_events(conn)
    pages = {
        "index.html": build_index(conn, cfg, check_list),
        "inputs.html": build_inputs(conn, events),
        "transformation.html": build_transformation(conn, events),
        "grouping.html": build_grouping(conn, cfg),
        "posting.html": build_posting(conn, cfg),
        "reconciliation.html": build_reconciliation(check_list),
        "model.html": build_model(conn),
    }
    os.makedirs(out_dir, exist_ok=True)
    for name, html in pages.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(html)
    return sorted(pages), check_list
