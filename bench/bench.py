#!/usr/bin/env python3
"""Run the golden set against the live backend and save a timestamped snapshot.

    python3 bench/bench.py --label "after closer fix"

Snapshots land in bench/runs/. Nothing here is destructive: every run is a new file, so any two
points in time can be compared later. Standard library only, so it runs anywhere.
"""
import argparse, json, os, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import checks  # noqa: E402

API = os.environ.get("BENCH_API", "https://videos-similar-api-production.up.railway.app")
RUNS = os.path.join(HERE, "runs")


def post(path, body, timeout=280):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get_key():
    """EVENTS_KEY gates /writeoff. Optional: without it we still run the channel half."""
    k = os.environ.get("EVENTS_KEY", "").strip()
    if k:
        return k
    for p in ("/tmp/.eventskey", os.path.join(HERE, ".eventskey")):
        try:
            with open(p) as f:
                v = f.read().strip()
                if v:
                    return v
        except Exception:
            pass
    return ""


def run_channel(ch):
    """The real product path a user hits, with fresh=true so we measure the CURRENT prompts."""
    url = "https://www.youtube.com/" + ch["handle"].lstrip("/")
    t0 = time.time()
    try:
        d = post("/custom", {"channelUrl": url, "fresh": True})
        ideas = d.get("ideas") or []
        return {"kind": "channel", "id": ch["handle"], "why": ch.get("why", ""),
                "ideas": [{"title": x.get("title", ""), "summary": x.get("summary", "")} for x in ideas],
                "secs": round(time.time() - t0, 1), "error": None}
    except Exception as e:
        return {"kind": "channel", "id": ch["handle"], "why": ch.get("why", ""), "ideas": [],
                "secs": round(time.time() - t0, 1), "error": str(e)[:200]}


def run_concepts(handle, items, key):
    """Same fixed ideas, rewritten by the current prompts. The tightest A/B signal we have."""
    url = "https://www.youtube.com/" + handle.lstrip("/")
    t0 = time.time()
    try:
        d = post("/writeoff", {"key": key, "channelUrl": url,
                               "concepts": [c["text"] for c in items]})
        ideas = d.get("opus") or []
        out = []
        for i, x in enumerate(ideas):
            out.append({"title": x.get("title", ""), "summary": x.get("summary", ""),
                        "concept": items[i]["text"] if i < len(items) else ""})
        return {"kind": "concepts", "id": handle, "why": "same fixed ideas, rewritten",
                "ideas": out, "secs": round(time.time() - t0, 1), "error": d.get("opus_err")}
    except Exception as e:
        return {"kind": "concepts", "id": handle, "why": "same fixed ideas, rewritten", "ideas": [],
                "secs": round(time.time() - t0, 1), "error": str(e)[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="", help="what changed since last time")
    ap.add_argument("--channels-only", action="store_true")
    a = ap.parse_args()

    golden = json.load(open(os.path.join(HERE, "golden.json")))
    key = get_key()
    jobs = [(run_channel, (c,)) for c in golden["channels"]]
    if not a.channels_only:
        if key:
            by = {}
            for c in golden.get("concepts", []):
                by.setdefault(c["channel"], []).append(c)
            for handle, items in by.items():
                jobs.append((run_concepts, (handle, items, key)))
        else:
            print("  (no EVENTS_KEY found, skipping the fixed-concept half)")

    print("running %d jobs against %s ..." % (len(jobs), API))
    groups = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(fn, *args) for fn, args in jobs]
        for f in futs:
            g = f.result()
            groups.append(g)
            print("  %-14s %-22s %3d ideas  %5ss  %s"
                  % (g["kind"], g["id"], len(g["ideas"]), g["secs"], g["error"] or "ok"))

    for g in groups:
        g["checks"] = {k: v for k, v in checks.run_checks(g["ideas"]).items()}
        g["stats"] = checks.stats(g["ideas"])

    all_ideas = [x for g in groups for x in g["ideas"]]
    snap = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "label": a.label,
        "api": API,
        "groups": groups,
        "totals": {
            "ideas": len(all_ideas),
            "violations": {k: sum(len(g["checks"].get(k, [])) for g in groups)
                           for k, _t, _f, _s, _fn in checks.CHECKS},
            "stats": checks.stats(all_ideas),
        },
    }
    os.makedirs(RUNS, exist_ok=True)
    path = os.path.join(RUNS, time.strftime("%Y%m%d-%H%M%S") + ".json")
    json.dump(snap, open(path, "w"), ensure_ascii=False, indent=1)
    print("\nsaved %s" % path)
    v = snap["totals"]["violations"]
    print("ideas %d | median grade %s | violations: %s"
          % (snap["totals"]["ideas"], snap["totals"]["stats"]["grade_median"],
             ", ".join("%s=%d" % (k, n) for k, n in v.items() if n) or "none"))
    return path


if __name__ == "__main__":
    main()
