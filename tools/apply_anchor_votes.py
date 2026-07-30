"""Apply the curator's anchor decisions from the event log to the bank.

He cuts and reorders anchors in anchors.html; each click posts an `anchorvote`. This reads them and
writes `cut` / `bump` onto sources.json, which is what the draw actually reads. Run it after a curation
session, then commit.
"""
import json, os, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://videos-similar-api-production.up.railway.app"
SRC = os.path.join(ROOT, "backend", "sources.json")


def votes_from_events():
    key = open("/tmp/.eventskey").read().strip()
    ev = json.loads(urllib.request.urlopen(API + "/events?key=" + key, timeout=25).read().decode())
    out = {}
    for e in (ev.get("events") or []):
        if e.get("t") == "anchorvote" and e.get("id"):
            out[e["id"]] = {"cut": bool(e.get("cut")), "bump": int(e.get("bump") or 0),
                            "who": str(e.get("who") or "unknown")}
    return out


def votes_from_file(path):
    d = json.load(open(path))
    return {k: {"cut": bool(v.get("cut")), "bump": int(v.get("bump") or 0)} for k, v in d.items()}


def main():
    votes = votes_from_file(sys.argv[1]) if len(sys.argv) > 1 else votes_from_events()
    if not votes:
        print("no anchor decisions found. Either none were made yet, or the event buffer was cleared "
              "by a restart - in that case use the page's Copy decisions button and pass the file:\n"
              "   python3 tools/apply_anchor_votes.py ~/Downloads/decisions.json")
        return
    src = json.load(open(SRC))
    cut = bumped = 0
    for s in src:
        v = votes.get(s.get("id"))
        if not v:
            continue
        if v["cut"] and not s.get("cut"):
            s["cut"] = True
            s["cut_by"] = v.get("who", "unknown")     # who cut it, so a disagreement is visible
            cut += 1
        elif not v["cut"] and s.get("cut"):
            s.pop("cut", None)
        if v["bump"]:
            s["bump"] = v["bump"]; bumped += 1
    json.dump(src, open(SRC, "w"), indent=1, ensure_ascii=False)
    print("applied to the bank: %d cut, %d re-ranked (of %d decisions)" % (cut, bumped, len(votes)))
    print("now run ./deploy.sh --backend so the generator sees it")


if __name__ == "__main__":
    main()
