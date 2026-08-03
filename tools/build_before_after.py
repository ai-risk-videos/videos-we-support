"""Before-and-after page for two saved batches, with the labelling widget on it.

He asked for this twice: show him the same story written the old way and the new way, side by side,
and let him mark spans with a reason on the SAME page so the comparison itself produces data.

  python3 tools/build_before_after.py <before.json> <after.json> [label]

Each input is a JSON array of pitches, each pitch an array of sentences (what
tools/measure.py already writes out of a run).
"""
import sys, os, re, json, html, subprocess, tempfile, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, HERE)
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("EVENTS_KEY", "x")
for _n in ("fastapi", "fastapi.concurrency", "fastapi.middleware", "fastapi.middleware.cors",
           "fastapi.responses", "anthropic", "yt_dlp"):
    try:
        __import__(_n)
    except Exception:
        import types
        m = types.ModuleType(_n)
        for a in ("FastAPI", "Request", "JSONResponse", "HTMLResponse", "CORSMiddleware",
                  "run_in_threadpool", "Anthropic", "YoutubeDL"):
            setattr(m, a, type(a, (), {"__init__": lambda self, *a, **k: None}))
        sys.modules[_n] = m
import main
import label_widget as LW

OUT = os.path.expanduser("~/Downloads/fluff-before-after.html")
E = html.escape
STOP = set("the a an and or of to in on for with that its it was were is are be been by from at as "
           "this his her their they them then than when who how what not no".split())


def bag(t):
    return {w for w in re.findall(r"[a-z]+", t.lower()) if len(w) > 3 and w not in STOP}


def pair_up(old, new, want=8):
    """Match each old pitch to the new pitch about the same story, best overlap first."""
    scored = []
    for i, o in enumerate(old):
        ob = bag(" ".join(o))
        for j, n in enumerate(new):
            nb = bag(" ".join(n))
            scored.append((len(ob & nb) / max(1, len(ob | nb)), i, j))
    scored.sort(reverse=True)
    pairs, ui, uj = [], set(), set()
    for v, i, j in scored:
        if v < 0.07 or i in ui or j in uj:
            continue
        ui.add(i); uj.add(j); pairs.append((old[i], new[j]))
        if len(pairs) >= want:
            break
    return pairs


def stats(B):
    al = [x for i in B for x in i]
    return {
        "words": int(st.median([sum(len(x.split()) for x in i) for i in B])),
        "sents": int(st.median([len(i) for i in B])),
        "dead": sum(len(main._dead_sentences(" ".join(i))) for i in B),
        "slog": round(100 * sum(1 for x in al if main._slog(x) >= main.SLOG_LIMIT) / len(al)),
        "grade": round(st.mean([main._fk_grade(x) for x in al]), 1),
        "passive": round(100 * sum(1 for x in al if main._is_passive(x)) / len(al)),
        "reread": round(100 * sum(1 for x in al if main._sentence_cost(x) >= 1.3) / len(al)),
        "closeq": round(100 * sum(1 for i in B if main._is_species_question(i[-1])) / len(B)),
    }


def render(sents, side):
    """Each sentence is a .sent so a selection inside it can be attributed to it."""
    out = []
    whole = " ".join(sents)
    dead = set(main._dead_sentences(whole))
    for k, p in enumerate(sents):
        cls = ["sent"]
        if main._slog(p) >= main.SLOG_LIMIT:
            cls.append("slog")
        if p in dead:
            cls.append("dead")
        if main._fk_grade(p) >= 10.5:
            cls.append("hard")
        if k == len(sents) - 1 and main._is_species_question(p):
            cls.append("closeq")
        out.append("<span class='%s' data-id='%s-%d' title='grade %.1f'>%s</span> "
                   % (" ".join(cls), side, k, main._fk_grade(p), E(p)))
    return "".join(out)


CSS = """
body{background:#14161a;color:#e8eaed;font:15.5px/1.62 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  max-width:1240px;margin:0 auto;padding:30px 24px 120px}
h1{font-size:25px;margin:0 0 8px}
h2{font-size:14px;color:#8b93a1;margin:30px 0 10px;font-weight:600;text-transform:uppercase;letter-spacing:.07em}
p.l{color:#b9c0cc;max-width:82ch}
table{border-collapse:collapse;margin:14px 0 6px;font-size:14px}
td,th{padding:6px 18px 6px 0;text-align:left;border-bottom:1px solid #23272e}
th{color:#8b93a1;font-weight:600}td.g{color:#6bcf7f;font-weight:600}td.b{color:#e0796f;font-weight:600}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:12px 0 22px}
.col{background:#1b1e24;border-radius:10px;padding:15px 17px}
.col.a{border-left:3px solid #b5544c}.col.b{border-left:3px solid #4d9c5a}
.tag{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:#8b93a1;margin-bottom:9px}
.sent{border-radius:3px;cursor:text}
.sent.slog{background:rgba(224,121,111,.17)}
.sent.dead{background:rgba(224,121,111,.42);text-decoration:line-through}
.sent.hard{box-shadow:inset 0 -2px 0 rgba(230,180,90,.8)}
.sent.closeq{background:rgba(77,156,90,.17);box-shadow:inset 0 -2px 0 #4d9c5a}
.key{color:#8b93a1;font-size:13px;margin:8px 0 0;max-width:90ch}.key b{color:#e8eaed}
#bar{position:fixed;bottom:0;left:0;right:0;background:#0f1114;border-top:1px solid #2a2f38;padding:10px 24px;z-index:50}
#bar .in{max-width:1240px;margin:0 auto;display:flex;gap:16px;align-items:center;font-size:13px;color:#8b93a1}
#bar button{background:#26303b;color:#e8eaed;border:1px solid #3a4653;border-radius:7px;padding:6px 13px;font-size:13px;cursor:pointer}
#bar b{color:#e8eaed}
@media(max-width:900px){.pair{grid-template-columns:1fr}}
"""


def build(old, new, label=""):
    o, n = stats(old), stats(new)

    def row(lbl, k, sfx="", up_is_good=False):
        a, b = o[k], n[k]
        cls = "" if a == b else ("g" if ((b > a) if up_is_good else (b < a)) else "b")
        return "<tr><td>%s</td><td>%s%s</td><td class='%s'>%s%s</td></tr>" % (lbl, a, sfx, cls, b, sfx)

    T = ("<table><tr><th>measure</th><th>before</th><th>after</th></tr>"
         + row("words per pitch (median)", "words")
         + row("sentences per pitch (median)", "sents")
         + row("sentences that only announce the next one", "dead")
         + row("sentences where nobody is doing anything", "slog", "%")
         + row("passive voice", "passive", "%")
         + row("over the reread bar", "reread", "%")
         + row("reading grade (mean)", "grade")
         + row("endings that ask the blunt question at scale", "closeq", "%", up_is_good=True)
         + "</table>")

    body = ["<h1>Before and after%s</h1>" % (" &middot; " + E(label) if label else ""),
            "<p class=l>Same channel, same settings. Left is the older batch, right is the newer one.</p>",
            T,
            "<p class=key><b>Shading:</b> red = nobody is doing anything in that sentence. "
            "Solid red struck through = the sentence only announces the next one. "
            "Amber underline = reads above grade 10. Green = the closing question landed at scale. "
            "Hover a sentence for its grade.</p>",
            "<p class=key><b>Mark anything.</b> Select any span inside a sentence, pick a reason chip "
            "or type one, then Bad or Good. <b>b</b> and <b>g</b> work too. Click a highlight to "
            "remove it. Every mark sends immediately.</p>",
            "<h2>The same story, before and after</h2>"]
    for idx, (a, b) in enumerate(pair_up(old, new)):
        body.append("<div class=pair>"
                    "<div class='col a'><div class=tag>before &middot; %d words</div>%s</div>"
                    "<div class='col b'><div class=tag>after &middot; %d words</div>%s</div></div>"
                    % (len(" ".join(a).split()), render(a, "b%d" % idx),
                       len(" ".join(b).split()), render(b, "a%d" % idx)))
    body.append("<div id=bar><div class=in><span><b id=nb>0</b> bad</span>"
                "<span><b id=ng>0</b> good</span><span id=agree></span>"
                "<button id=copy>Copy labels</button><button id=resend>Re-send all</button>"
                "<button id=reset>Clear</button></div></div>")

    js = ('const $=s=>document.querySelector(s);\n'
          'window.onLabelChange=function(labels){\n'
          '  $("#nb").textContent=labels.filter(l=>l.verdict==="bad").length;\n'
          '  $("#ng").textContent=labels.filter(l=>l.verdict==="good").length;\n'
          '  const withWhy=labels.filter(l=>l.why).length;\n'
          '  $("#agree").textContent=labels.length?(withWhy+" of "+labels.length+" have a reason"):"";\n'
          '};\n'
          + LW.JS("beforeafterlabels.v1")
          + '$("#copy").onclick=()=>navigator.clipboard.writeText(labelJSON())'
            '.then(()=>alert("Copied "+labelCount()+" labels."));\n'
            '$("#reset").onclick=()=>{if(confirm("Clear all labels?")){labels=[];lsave();location.reload();}};\n'
            '$("#resend").onclick=labelResend;\n')

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(js)
        p = fh.name
    r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("REFUSING TO WRITE: page JS does not parse\n" + r.stderr)

    page = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Before and after</title><style>%s\n%s</style></head><body>%s%s"
            "<script>%s</script></body></html>" % (CSS, LW.CSS, "".join(body), LW.HTML, js))
    open(OUT, "w").write(page)
    return OUT, o, n


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    old = json.load(open(sys.argv[1]))
    new = json.load(open(sys.argv[2]))
    out, o, n = build(old, new, sys.argv[3] if len(sys.argv) > 3 else "")
    print("wrote %s" % out)
    print("  words  %s -> %s | sentences %s -> %s | closing question %s%% -> %s%%"
          % (o["words"], n["words"], o["sents"], n["sents"], o["closeq"], n["closeq"]))
