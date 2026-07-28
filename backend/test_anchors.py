"""Regression tests for the evidence bank and the anchor draw.

Run: python3 backend/test_anchors.py    (no venv needed, the server deps are stubbed)

These exist because of two failures that shipped. The bank silently lost 45% of its entries once when a
`kind` whitelist did not match the data, and badly-written descriptions of huge events sat on the top
shelf for weeks because the only score measured the EVENT and nothing measured the SENTENCE.
"""
import json, os, sys, types, re

HERE = os.path.dirname(os.path.abspath(__file__))

for _n in ("fastapi", "fastapi.concurrency", "fastapi.middleware", "fastapi.middleware.cors",
           "fastapi.responses", "anthropic", "yt_dlp"):
    sys.modules.setdefault(_n, types.ModuleType(_n))
class _Any:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return self
    def __getattr__(self, n): return _Any()
for _n in ("FastAPI", "Request"): setattr(sys.modules["fastapi"], _n, _Any)
sys.modules["fastapi.concurrency"].run_in_threadpool = _Any()
sys.modules["fastapi.middleware.cors"].CORSMiddleware = _Any
sys.modules["fastapi.responses"].JSONResponse = _Any
sys.modules["anthropic"].Anthropic = _Any
sys.modules["yt_dlp"].YoutubeDL = _Any
sys.path.insert(0, HERE)
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
import main  # noqa: E402

SRC = json.load(open(os.path.join(HERE, "sources.json")))
EV = json.load(open(os.path.join(HERE, "evidence.json")))
# Descriptions of a write-up rather than of an event. The curator's line: "'The technical appendix gives
# per model numbers showing' should never slip through."
ACADEMIC = re.compile(r"technical appendix|per model numbers|study found|placed in simulated", re.I)

fails = []
def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  -> " + detail) if detail and not ok else ""))
    if not ok:
        fails.append(name)

print("evidence bank")
check("sources.json has not shrunk", len(SRC) >= 1524, "%d entries" % len(SRC))
check("evidence.json has not shrunk", sum(len(v) for v in EV.values()) >= 252,
      "%d cases" % sum(len(v) for v in EV.values()))
check("every source carries an escalation score",
      all(isinstance(s.get("esc"), int) for s in SRC),
      "%d missing" % sum(1 for s in SRC if not isinstance(s.get("esc"), int)))
check("every evidence case carries an escalation score",
      all(isinstance(c.get("esc"), int) for v in EV.values() for c in v))
graded = [s for s in SRC if isinstance(s.get("grab"), int)] + \
         [c for v in EV.values() for c in v if isinstance(c.get("grab"), int)]
check("the telling score survived the last write", len(graded) >= 500, "%d scored" % len(graded))
check("scores are inside 1-10", all(1 <= x["esc"] <= 10 for x in SRC if isinstance(x.get("esc"), int))
      and all(1 <= x["grab"] <= 10 for x in graded))

print("\nrank is the lower of the two scores")
check("min() is what _anchor_rank returns", main._anchor_rank(10, 1) == 1 and main._anchor_rank(3, 9) == 3)
check("an unscored anchor can never reach the top shelf",
      main._anchor_rank(10, None) < main.ANCHOR_TOP_TIER)
appendix = [x for x in SRC if ACADEMIC.search(x.get("shows") or "")] + \
           [c for v in EV.values() for c in v if ACADEMIC.search(c.get("what") or "")]
bad = [x for x in appendix
       if main._anchor_rank(x.get("esc") or 5, x.get("grab")) >= main.ANCHOR_TOP_TIER]
check("no write-up-shaped anchor sits on the top shelf", not bad,
      "; ".join((x.get("shows") or x.get("what"))[:70] for x in bad[:3]))

print("\nthe draw itself, over 25 rounds")
firsts, seen, leaks, empty = [], set(), [], 0
for _ in range(25):
    lines = [l[2:] for l in main.anchor_block(14).split("\n") if l.startswith("- ")]
    if not lines:
        empty += 1
        continue
    firsts.append(lines[0])
    seen.update(lines)
    leaks += [l for l in lines if ACADEMIC.search(l)]
check("the draw is never empty", empty == 0, "%d empty draws" % empty)
check("no write-up phrasing is ever handed to the generator", not leaks,
      "%d leaks, e.g. %s" % (len(leaks), leaks[0][:80] if leaks else ""))
# The prompt tells the model the list is ORDERED BEST FIRST, so the first line has to earn it.
# The original version of this check tested sentence LENGTH as a proxy and passed on anchors that were
# short and dull; check the actual rank instead.
rank_of = {}
for _s in SRC:
    rank_of["[%s %s] %s" % (_s.get("who", ""), _s.get("year", ""), _s.get("shows", ""))] = \
        main._anchor_rank(_s.get("esc") or 5, _s.get("grab"))
for _cs in EV.values():
    for _c in _cs:
        rank_of["[%s %s] %s" % (_c.get("who", ""), _c.get("year", ""), _c.get("what", ""))] = \
            main._anchor_rank(_c.get("esc") or 5, _c.get("grab"))
weak = [f for f in firsts if rank_of.get(f, 0) < main.ANCHOR_TOP_TIER]
check("the list opens on a top-shelf anchor", not weak,
      "%d of %d draws opened below rank %d, e.g. %s"
      % (len(weak), len(firsts), main.ANCHOR_TOP_TIER, weak[0][:80] if weak else ""))
check("draws stay varied", len(seen) >= 40, "only %d unique anchors across 25 draws" % len(seen))

print("\n%s (%d checks, %d failed)" % ("FAILED" if fails else "ALL PASS", 13, len(fails)))
sys.exit(1 if fails else 0)
