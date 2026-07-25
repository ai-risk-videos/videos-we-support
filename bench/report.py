#!/usr/bin/env python3
"""Diff the two most recent snapshots into a before/after page.

    python3 bench/report.py                # newest vs the one before it
    python3 bench/report.py A.json B.json  # two specific snapshots

Every rule the curator has given appears as a row: violations before, violations after, and whether
that moved the right way. Then the actual text, side by side, for the fixed ideas.
"""
import glob, html, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import checks  # noqa: E402

RUNS = os.path.join(HERE, "runs")
OUT = os.path.expanduser("~/Downloads/bench-before-after.html")


def esc(s):
    return html.escape(s or "")


def pick():
    """Newest two USABLE snapshots. A run that produced no ideas (network drop, machine asleep) is
    not evidence, so it never gets picked as a before or an after."""
    files = sorted(glob.glob(os.path.join(RUNS, "*.json")))
    good = []
    for f in files:
        try:
            if (json.load(open(f)).get("totals") or {}).get("ideas", 0) > 0:
                good.append(f)
        except Exception:
            pass
    if len(good) < 2:
        return (good[0] if good else None), None
    return good[-1], good[-2]


def group_map(snap):
    return {g["id"] + "|" + g["kind"]: g for g in snap.get("groups", [])}


def concept_pairs(before, after):
    """Match ideas by their fixed concept text, so we compare like with like."""
    pairs = []
    bmap, amap = group_map(before), group_map(after)
    for k, ag in amap.items():
        if ag["kind"] != "concepts":
            continue
        bg = bmap.get(k)
        if not bg:
            continue
        bidx = {(x.get("concept") or "")[:80]: x for x in bg["ideas"]}
        for x in ag["ideas"]:
            b = bidx.get((x.get("concept") or "")[:80])
            if b:
                pairs.append((ag["id"], x.get("concept", ""), b, x))
    return pairs


def build(after_path, before_path):
    after = json.load(open(after_path))
    before = json.load(open(before_path)) if before_path else None

    rows = ""
    net_better = net_worse = 0
    for key, title, feedback, _scope, _fn in checks.CHECKS:
        a = after["totals"]["violations"].get(key, 0)
        b = before["totals"]["violations"].get(key, 0) if before else None
        if b is None:
            cell, cls = "<td class='n'>-</td>", "n"
            delta = ""
        else:
            if a < b:
                cls, delta, = "good", "&darr; better"
                net_better += 1
            elif a > b:
                cls, delta = "warn", "&uarr; worse"
                net_worse += 1
            else:
                cls, delta = ("good" if a == 0 else "n"), "same"
            cell = "<td class='n'>%d</td>" % b
        rows += ("<tr><td><b>%s</b><span class='fb'>%s</span></td>%s"
                 "<td class='n %s'>%d</td><td class='%s'>%s</td></tr>"
                 % (esc(title), esc(feedback), cell, cls, a, cls, delta))

    def statline(s):
        if not s:
            return "-"
        return ("median grade %s &middot; %s%% end on a question &middot; %s%% name a real source"
                % (s["grade_median"], s["question_pct"], s["named_source_pct"]))

    pairs = concept_pairs(before, after) if before else []
    pair_html = ""
    for handle, concept, b, a in pairs:
        gb = checks.reading_grade(b.get("summary", ""))
        ga = checks.reading_grade(a.get("summary", ""))
        cls = "good" if ga <= gb else "warn"
        pair_html += """<div class="pair">
          <div class="seed"><span class="tag">SAME IDEA</span>%s</div>
          <div class="cols">
            <div class="card bad"><div class="t tb">BEFORE &middot; grade %s</div><p class="hk">%s</p><p>%s</p></div>
            <div class="card good"><div class="t tg %s">AFTER &middot; grade %s</div><p class="hk">%s</p><p>%s</p></div>
          </div></div>""" % (esc(concept), gb, esc(b.get("title")), esc(b.get("summary")),
                             cls, ga, esc(a.get("title")), esc(a.get("summary")))
    if not pair_html:
        pair_html = ("<div class='note'>No matched fixed-idea pairs yet. They appear once two runs "
                     "both include the concept half (needs EVENTS_KEY).</div>")

    # THE MAIN EVENT: the whole old batch next to the whole new batch, for each channel. Fresh runs
    # produce different ideas, so these are not paired line by line and are not meant to be. The
    # point is to read both lists and judge whether the batch as a whole got better.
    def idea_li(x, n):
        g = checks.reading_grade(x.get("summary", ""))
        return ("<li><div class='ihead'><span class='inum'>%d</span><span class='ig'>grade %s</span></div>"
                "<p class='ihk'>%s</p><p class='ism'>%s</p></li>"
                % (n, g, esc(x.get("title")), esc(x.get("summary"))))

    batches = ""
    if before:
        bmap, amap = group_map(before), group_map(after)
        for k, ag in amap.items():
            if ag["kind"] != "channel":
                continue
            bg = bmap.get(k)
            if not bg or not bg["ideas"]:
                continue
            bs, as_ = bg.get("stats") or {}, ag.get("stats") or {}
            bl = "".join(idea_li(x, i + 1) for i, x in enumerate(bg["ideas"]))
            al = "".join(idea_li(x, i + 1) for i, x in enumerate(ag["ideas"]))
            batches += """<div class="batch">
              <h3 class="chan">%s</h3>
              <div class="cols">
                <div class="bcol bad"><div class="bhead">OLD BATCH &middot; %d ideas &middot; median grade %s</div><ol class="ilist">%s</ol></div>
                <div class="bcol good"><div class="bhead">NEW BATCH &middot; %d ideas &middot; median grade %s</div><ol class="ilist">%s</ol></div>
              </div></div>""" % (esc(ag["id"]), len(bg["ideas"]), bs.get("grade_median", "-"), bl,
                                 len(ag["ideas"]), as_.get("grade_median", "-"), al)
    if not batches:
        batches = "<div class='note'>Need two runs that both cover the same channel.</div>"

    # every current violation, so nothing is hidden behind a count
    viol = ""
    for g in after["groups"]:
        items = []
        for key, title, _f, _s, _fn in checks.CHECKS:
            for idx, reason in g["checks"].get(key, []):
                idea = g["ideas"][idx] if idx < len(g["ideas"]) else {}
                items.append("<li><b>%s</b> &middot; idea %d: %s<div class='q'>%s</div></li>"
                             % (esc(title), idx + 1, esc(reason), esc((idea.get("summary") or "")[:220])))
        if items:
            viol += "<h4>%s</h4><ul class='viol'>%s</ul>" % (esc(g["id"]), "".join(items))
    if not viol:
        viol = "<div class='note'>No violations in this run.</div>"

    verdict = ("%d rules improved, %d got worse" % (net_better, net_worse)) if before else "first run, nothing to compare yet"
    vcls = "good" if net_worse == 0 else "warn"

    page = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bench: before and after</title><style>
:root{--bg:#0e1116;--card:#161b22;--edge:#232a33;--tx:#e6edf3;--mut:#9aa7b4;--bad:#e5837a;--good:#6fd39a;--warn:#e0b874;--acc:#6fb3ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;padding:30px 20px 80px}
.wrap{max-width:1040px;margin:0 auto}h1{font-size:24px;margin:0 0 4px}
.sub{color:var(--mut);font-size:14px;margin:0 0 6px}
.verdict{display:inline-block;font-weight:700;font-size:14px;padding:7px 13px;border-radius:9px;background:var(--card);border:1px solid var(--edge);margin:8px 0 18px}
.verdict.good{color:var(--good)}.verdict.warn{color:var(--warn)}
h2{font-size:17px;margin:30px 0 12px;border-bottom:1px solid var(--edge);padding-bottom:8px}
h4{font-size:13px;color:var(--acc);margin:18px 0 8px;text-transform:uppercase;letter-spacing:.05em}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--edge);border-radius:11px;overflow:hidden}
th,td{padding:10px 13px;text-align:left;font-size:14px;border-bottom:1px solid #1c222b}
th{color:var(--mut);font-size:11.5px;text-transform:uppercase;letter-spacing:.05em}
td.n{text-align:center;font-variant-numeric:tabular-nums}
td.good{color:var(--good);font-weight:700}td.warn{color:var(--warn);font-weight:700}
tr:last-child td{border-bottom:0}
.fb{display:block;color:var(--mut);font-size:12.5px;font-weight:400;margin-top:2px}
.pair{margin:0 0 16px}
.seed{background:#10203a;border:1px solid #1d3a63;border-radius:10px;padding:11px 13px;font-size:13.5px;color:#d6e4f7;margin-bottom:9px}
.tag{display:block;font-size:10px;letter-spacing:.12em;color:#7fa8dd;font-weight:700;margin-bottom:4px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.card{background:var(--card);border:1px solid var(--edge);border-radius:11px;padding:12px 14px}
.card.bad{border-left:3px solid var(--bad)}.card.good{border-left:3px solid var(--good)}
.t{font-size:10.5px;letter-spacing:.1em;font-weight:700;margin-bottom:7px}
.tb{color:var(--bad)}.tg{color:var(--good)}.tg.warn{color:var(--warn)}
.card p{margin:0;font-size:13.6px;color:#c4cdd6}.card .hk{font-weight:700;color:var(--tx);margin-bottom:6px}
ul.viol{list-style:none;padding:0;margin:0}
ul.viol li{background:var(--card);border:1px solid var(--edge);border-left:3px solid var(--warn);border-radius:9px;padding:10px 13px;margin:0 0 8px;font-size:13.5px}
ul.viol .q{color:var(--mut);font-size:12.5px;margin-top:5px;font-style:italic}
.batch{margin:0 0 30px}
.bcol{background:var(--card);border:1px solid var(--edge);border-radius:11px;padding:0 0 6px;overflow:hidden}
.bcol.bad{border-top:3px solid var(--bad)}.bcol.good{border-top:3px solid var(--good)}
.bhead{font-size:11px;letter-spacing:.09em;font-weight:700;padding:10px 14px;border-bottom:1px solid #1c222b;color:var(--mut)}
.bcol.bad .bhead{color:var(--bad)}.bcol.good .bhead{color:var(--good)}
ol.ilist{list-style:none;margin:0;padding:6px 14px 0}
ol.ilist li{padding:11px 0;border-bottom:1px solid #1a2029}
ol.ilist li:last-child{border-bottom:0}
.ihead{display:flex;align-items:center;gap:8px;margin-bottom:5px}
.inum{width:19px;height:19px;border-radius:50%;background:#1d3a63;color:#cfe0ff;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex:0 0 auto}
.ig{font-size:10.5px;color:var(--mut);font-variant-numeric:tabular-nums}
.ihk{font-weight:700;font-size:13.6px;margin:0 0 5px;line-height:1.4}
.ism{color:#c4cdd6;font-size:13px;margin:0}
.note{background:#12171e;border:1px solid var(--edge);border-radius:10px;padding:12px 14px;color:var(--mut);font-size:13.5px}
.how{background:#12171e;border:1px solid var(--edge);border-radius:10px;padding:12px 14px;color:var(--mut);font-size:13px;margin-top:26px}
.how code{color:var(--tx);background:#0b0e13;padding:2px 6px;border-radius:5px}
@media(max-width:760px){.cols{grid-template-columns:1fr}}
</style></head><body><div class="wrap">
<h1>Bench: before and after</h1>
<p class="sub">AFTER: __ATS____ALBL__ &middot; __ASTAT__</p>
<p class="sub">BEFORE: __BTS____BLBL__ &middot; __BSTAT__</p>
<div class="verdict __VCLS__">__VERDICT__</div>

<h2>The old batch next to the new batch</h2>
<p class="sub" style="margin-bottom:14px">Read both and judge. These are whole batches from the same channel, so the ideas differ, that is the point: is the new list of ideas better written and better thought out than the old list?</p>
__BATCHES__

<h2>Same idea, written before and after</h2>
<p class="sub" style="margin-bottom:14px">Here the model is handed the identical idea both times, so the only thing that changes is the writing.</p>
__PAIRS__

<h2>Your feedback, as checks</h2>
<table><tr><th>Rule</th><th>Before</th><th>After</th><th></th></tr>__ROWS__</table>

<h2>Everything still failing</h2>
__VIOL__

<div class="how">Run it again after any change:<br>
<code>cd ~/Downloads/ai-risk-videos && ./bench.sh --label "what I changed"</code><br>
That regenerates this page from two fresh snapshots. New feedback becomes a new row here: add it to
<code>bench/checks.py</code> so it is measured from then on.</div>
</div></body></html>"""

    page = (page
            .replace("__ATS__", esc(after["ts"]))
            .replace("__ALBL__", (" &middot; " + esc(after["label"])) if after.get("label") else "")
            .replace("__ASTAT__", statline(after["totals"]["stats"]))
            .replace("__BTS__", esc(before["ts"]) if before else "none")
            .replace("__BLBL__", (" &middot; " + esc(before["label"])) if before and before.get("label") else "")
            .replace("__BSTAT__", statline(before["totals"]["stats"]) if before else "-")
            .replace("__VCLS__", vcls).replace("__VERDICT__", esc(verdict))
            .replace("__BATCHES__", batches)
            .replace("__ROWS__", rows).replace("__PAIRS__", pair_html).replace("__VIOL__", viol))

    open(OUT, "w").write(page)
    return OUT


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        a, b = sys.argv[1], sys.argv[2]
    else:
        a, b = pick()
    if not a:
        print("no snapshots yet: run bench/bench.py first")
        sys.exit(1)
    p = build(a, b)
    print("wrote %s" % p)
    print("  after : %s" % os.path.basename(a))
    print("  before: %s" % (os.path.basename(b) if b else "(none)"))
