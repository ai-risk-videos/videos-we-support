"""Generate a batch, score it on every quality we track, append to history, build the review page.

This exists so "did anything improve?" is a command instead of a question. Every number here is one the
curator has raised, each measured identically on every run so the history table is comparable.

    python3 tools/measure.py [@channel]
"""
import sys, os, re, json, time, html, statistics as st, types, subprocess, tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
API = "https://videos-similar-api-production.up.railway.app"
HIST = os.path.join(HERE, "quality-history.jsonl")
LABELS = os.path.join(HERE, "labels.json")
OUT = os.path.expanduser("~/Downloads/review.html")

sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, HERE)          # label_widget lives beside this file
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
for _n in ("fastapi", "fastapi.concurrency", "fastapi.middleware", "fastapi.middleware.cors",
           "fastapi.responses", "anthropic", "yt_dlp"):
    sys.modules.setdefault(_n, types.ModuleType(_n))
class _A:
    def __init__(s, *a, **k): pass
    def __call__(s, *a, **k): return s
    def __getattr__(s, n): return _A()
for _n in ("FastAPI", "Request"): setattr(sys.modules["fastapi"], _n, _A)
sys.modules["fastapi.concurrency"].run_in_threadpool = _A()
sys.modules["fastapi.middleware.cors"].CORSMiddleware = _A
sys.modules["fastapi.responses"].JSONResponse = _A
sys.modules["anthropic"].Anthropic = _A
sys.modules["yt_dlp"].YoutubeDL = _A
import main

E = lambda s: html.escape(s or "")
def sents(t):
    return [p for p in re.split(r"(?<=[.?!])\s+", (t or "").strip()) if p.strip()]
META = re.compile(r"\bin this (?:episode|video)|we (?:look at|trace|follow|examine)|this piece\b", re.I)


def generate(channel):
    """Run the real product path, surviving the restarts that eat in-flight jobs."""
    body = json.dumps({"channelUrl": "https://www.youtube.com/" + channel.lstrip("/"),
                       "fresh": True}).encode()
    for attempt in range(3):
        t0 = time.time()
        req = urllib.request.Request(API + "/custom_start", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        job = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())["job"]
        print("  job %s (attempt %d)" % (job, attempt + 1), flush=True)
        for _ in range(80):
            time.sleep(15)
            try:
                d = json.loads(urllib.request.urlopen(
                    API + "/custom_result?job=" + job, timeout=25).read().decode())
            except Exception as e:
                if "404" not in str(e):
                    continue
                d = {"status": "unknown"}
            s = d.get("status")
            if s == "running":
                continue
            if s == "unknown":
                print("  job lost to a container restart, retrying", flush=True)
                break
            if s == "error":
                print("  error: %s" % str(d.get("error"))[:160], flush=True)
                break
            return d.get("ideas") or [], round(time.time() - t0)
    return [], 0


def measure(ideas, secs):
    allsents, lastsents = [], []
    for x in ideas:
        for f in ("title", "summary"):
            ss = sents(x.get(f) or "")
            allsents += ss
            if ss:
                lastsents.append(ss[-1])
    n = max(len(allsents), 1)
    m = max(len(lastsents), 1)
    ni = max(len(ideas), 1)
    red = [main._redundancy(x.get("title") or "")[0] for x in ideas]
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "secs": secs,
        "ideas": len(ideas),
        "rejected_shapes_pct": round(100 * sum(1 for s in allsents if main._taste_bad(s)) / n),
        "reread_pct": round(100 * sum(1 for s in allsents if main._sentence_cost(s) >= 1.3) / n),
        "passive_pct": round(100 * sum(1 for s in allsents if main._is_passive(s)) / n),
        "waypoint_endings_pct": round(100 * sum(1 for s in lastsents if main._ends_on_waypoint(s)) / m),
        "no_event_lead_pct": round(100 * sum(1 for x in ideas
                                             if main._lacks_event_lead(x.get("title") or "")) / ni),
        "redundant_paras": sum(1 for v in red if v >= main.REDUNDANCY_LIMIT),
        "meta_narration": sum(1 for x in ideas if META.search(x.get("title") or "")),
        "words_median": int(st.median([len((x.get("title") or "").split()) for x in ideas] or [0])),
        "sents_median": int(st.median([len([p for p in re.split(r"(?<=[.?!])\s+", (x.get("title") or "").strip())
                                            if p.strip()]) for x in ideas] or [0])),
        "fk_grade": round(st.mean([main._fk_grade(s) for s in allsents] or [0]), 1),
        "slog_pct": round(100 * sum(1 for s in allsents if main._slog(s) >= main.SLOG_LIMIT) / n),
        "dead_sents": sum(len(main._dead_sentences(x.get("title") or "")) for x in ideas),
        "grey_text": sum(1 for x in ideas if (x.get("summary") or "").strip()),
    }


ROWS = [("rejected_shapes_pct", "sentences matching a shape you marked bad", "%", True),
        ("reread_pct", "sentences over the reread bar", "%", True),
        ("passive_pct", "passive voice", "%", True),
        ("waypoint_endings_pct", "endings stopping at oversight", "%", True),
        ("no_event_lead_pct", "pitches not opening on a real event", "%", True),
        ("redundant_paras", "paragraphs restating themselves", "", True),
        ("meta_narration", "lines about the video itself", "", True),
        ("grey_text", "pitches still carrying grey text", "", True),
        ("words_median", "words per pitch (median)", "", True),
        ("sents_median", "sentences per pitch (median)", "", True),
        ("fk_grade", "reading grade (mean, target 5)", "", True),
        ("slog_pct", "sentences where nobody is doing anything", "%", True),
        ("dead_sents", "sentences that only announce the next one", "", True),
        ("secs", "seconds to generate", "", True)]


def trend(hist):
    if not hist:
        return ""
    cur = hist[-1]
    prev = hist[-2] if len(hist) > 1 else None
    first = hist[0]
    out = ["<table><tr><th>measure</th><th>now</th><th>last run</th><th>first tracked</th></tr>"]
    for k, lbl, unit, lower_better in ROWS:
        c = cur.get(k)
        p = None if prev is None else prev.get(k)
        f = first.get(k)
        cls = ""
        if p is not None and isinstance(c, (int, float)) and c != p and lower_better is not None:
            better = (c < p) if lower_better else (c > p)
            cls = " class='g'" if better else " class='b'"
        out.append("<tr><td>%s</td><td%s>%s%s</td><td>%s</td><td>%s</td></tr>" % (
            lbl, cls, c, unit,
            "-" if p is None else "%s%s" % (p, unit),
            "-" if f is None else "%s%s" % (f, unit)))
    out.append("</table>")
    return "".join(out)


def build_page(ideas, hist, known):
    rows = []
    for i, x in enumerate(ideas):
        for field in ("title", "summary"):
            for j, p in enumerate(sents(x.get(field) or "")):
                rows.append({"id": "%d-%s-%d" % (i, field, j), "idea": i, "field": field, "text": p,
                             "hits": main._taste_flags(p), "cost": main._sentence_cost(p),
                             "known": known.get(p.strip(), "")})
    body, cur = [], -1
    for r in rows:
        if r["idea"] != cur:
            if cur >= 0:
                body.append("</div></div>")
            cur = r["idea"]
            body.append("<div class='idea'><div class='n'>%d</div><div class='body'>" % (cur + 1))
        cls = "sent " + r["field"]
        if r["hits"] or r["cost"] >= 1.3:
            cls += " flag"
        if r["known"] == "bad":
            cls += " youbad"
        elif r["known"] == "good":
            cls += " yougood"
        tag = "<i>%s</i>" % ", ".join(r["hits"]) if r["hits"] else ""
        body.append("<span class='%s' data-id='%s' data-cost='%.2f'>%s%s</span> "
                    % (cls, r["id"], r["cost"], E(r["text"]), tag))
    body.append("</div></div>")

    import label_widget as LW
    page = (TEMPLATE.replace("__TREND__", trend(hist)).replace("__BODY__", "".join(body))
            .replace("__LABELCSS__", LW.CSS).replace("__LABELHTML__", LW.HTML)
            .replace("__LABELJS__", LW.JS("reviewlabels.v1")))
    page = page.replace("__RUNS__", str(len(hist))).replace("__NLAB__", str(len(known)))
    js = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(js)
        p = fh.name
    r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("REFUSING TO WRITE: page JS does not parse\n" + r.stderr)
    open(OUT, "w").write(page)


TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Review</title><style>
:root{--bg:#0e1116;--card:#161b22;--edge:#232a33;--tx:#e6edf3;--mut:#9aa7b4;--grn:#6fd39a;--red:#ff8b7d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.85 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;padding:24px 20px 150px}
.wrap{max-width:1100px;margin:0 auto}h1{font-size:23px;margin:0 0 4px}
.lead{color:var(--mut);font-size:14.5px;margin:0 0 14px;max-width:860px}
table{border-collapse:collapse;margin:0 0 16px;font-size:13px}
th,td{border:1px solid var(--edge);padding:5px 13px;text-align:left}
th{color:var(--mut);font-weight:500}
td.g{color:var(--grn);font-weight:600}td.b{color:var(--red);font-weight:600}
.how{background:#12171e;border:1px solid var(--edge);border-radius:10px;padding:11px 14px;color:var(--mut);font-size:13.4px;margin:0 0 16px}
.how b{color:var(--tx)}.how kbd{background:#222a33;border:1px solid #333d48;border-radius:4px;padding:1px 6px;font-size:12px}
.idea{display:flex;gap:12px;background:var(--card);border:1px solid var(--edge);border-radius:10px;padding:13px 15px;margin:0 0 11px}
.idea .n{color:#5a6673;font-size:11.5px;font-weight:700;padding-top:4px;min-width:20px}
.sent{padding:1px 2px;border-radius:3px;cursor:text}
.sent.summary{color:#9fabb6;font-size:14px}
.sent.flag{background:#3a1f1c;color:#ffb3a8}
.sent.youbad{outline:1px dashed var(--red)}.sent.yougood{outline:1px dashed var(--grn)}
.sent i{font-style:normal;font-size:9px;opacity:.6;margin-left:4px;vertical-align:super;text-transform:uppercase}
__LABELCSS__
#pop button{font:inherit;font-size:12.5px;border:0;border-radius:6px;padding:6px 11px;margin:0 3px;cursor:pointer}
#pop .gd{background:#1d4430;color:#a6f0c2}#pop .bd{background:#4a2320;color:#ffc0b6}
#bar{position:fixed;left:0;right:0;bottom:0;background:#12171ef2;border-top:1px solid var(--edge);padding:11px 20px;backdrop-filter:blur(8px)}
#bar .in{max-width:1100px;margin:0 auto;display:flex;gap:16px;align-items:center;flex-wrap:wrap;font-size:13px;color:var(--mut)}
#bar b{color:var(--tx)}
#bar button{font:inherit;font-size:13px;border:1px solid #33404e;background:#1b222b;color:#dbe3ea;border-radius:7px;padding:7px 13px;cursor:pointer}
</style></head><body><div class="wrap">
<h1>Review</h1>
<p class="lead">A fresh batch, every measure you have raised, and the same page takes your marks. Green means it moved the right way since the last run. Run <code>./review.sh</code> any time to regenerate this.</p>
__TREND__
<div class="how"><b>Mark anything.</b> Select any span inside a sentence, pick a reason chip or type one, then <b>Bad</b> or <b>Good</b> (or <kbd>b</kbd> / <kbd>g</kbd>). Click a highlight to remove it. Each mark sends immediately. Red shading is the detector's guess with the matched shape named; dashed outlines are marks you already made, so you can see where it still disagrees with you. __RUNS__ runs tracked, __NLAB__ labels so far.</div>
__BODY__
</div>
__LABELHTML__
<div id="bar"><div class="in">
  <span><b id="nb">0</b> bad</span><span><b id="ng">0</b> good</span>
  <button id="copy">Copy labels</button><button id="resend">Re-send all</button><button id="reset">Clear</button>
  <span id="agree" style="margin-left:auto"></span>
</div></div>
<script>
const $=s=>document.querySelector(s);
// the shared widget owns `labels`; this page just recounts whenever they change
window.onLabelChange=function(labels){
  $("#nb").textContent=labels.filter(l=>l.verdict==="bad").length;
  $("#ng").textContent=labels.filter(l=>l.verdict==="good").length;
  let miss=0,over=0;
  labels.forEach(l=>{const el=document.querySelector('[data-id="'+l.sid+'"]');if(!el)return;
    const f=el.classList.contains("flag");
    if(l.verdict==="bad"&&!f)miss++; if(l.verdict==="good"&&f)over++;});
  $("#agree").textContent=labels.length?("detector missed "+miss+", over-flagged "+over):"";
};
__LABELJS__
$("#copy").onclick=()=>navigator.clipboard.writeText(labelJSON()).then(()=>alert("Copied "+labelCount()));
$("#reset").onclick=()=>{if(confirm("Clear all labels?")){labels=[];lsave();location.reload();}};
$("#resend").onclick=labelResend;
</script></body></html>"""


def main_():
    channel = sys.argv[1] if len(sys.argv) > 1 else "@ColdFusion"
    print("generating a fresh batch for %s ..." % channel, flush=True)
    ideas, secs = generate(channel)
    if not ideas:
        raise SystemExit("generation failed three times; nothing to measure")
    m = measure(ideas, secs)
    with open(HIST, "a") as fh:
        fh.write(json.dumps(m) + "\n")
    hist = [json.loads(l) for l in open(HIST) if l.strip()]
    known = {}
    if os.path.exists(LABELS):
        for x in json.load(open(LABELS)):
            known[x["text"].strip()] = x["verdict"]
    build_page(ideas, hist, known)
    print("\n%d ideas in %ss. run %d of %d tracked." % (m["ideas"], secs, len(hist), len(hist)))
    for k, lbl, unit, _ in ROWS:
        prev = hist[-2].get(k) if len(hist) > 1 else None
        arrow = "" if prev is None or prev == m[k] else ("  (was %s)" % prev)
        print("   %-44s %s%s%s" % (lbl, m[k], unit, arrow))
    print("\nwrote %s" % OUT)


if __name__ == "__main__":
    main_()
