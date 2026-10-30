"""Bake the curator's anchor votes into the bank, so the repo matches what is live.

He cuts and reorders anchors in anchors.html; each click posts an `anchorvote`, and the backend applies
it to the live bank at once and keeps it on its volume (see _take_anchor_vote in backend/main.py). This
pulls those votes from /anchorvotes, adds the new ones to tools/votes/anchor_votes.jsonl, and writes
`cut` / `bump` onto backend/sources.json with the same rules as the server. ./deploy.sh runs it before
every deploy; nobody needs to run it by hand any more (the step that was skipped for three months).

   python3 tools/apply_anchor_votes.py                    pull from the live backend (what deploy.sh does)
   python3 tools/apply_anchor_votes.py decisions.json     a file from the page's Copy decisions button
"""
import json, os, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://videos-similar-api-production.up.railway.app"
SRC = os.path.join(ROOT, "backend", "sources.json")
LOG = os.path.join(ROOT, "tools", "votes", "anchor_votes.jsonl")


def voter(v):  # same rule as backend _anchor_voter: unnamed votes never change the bank
    who = str(v.get("who") or "").strip()
    return who[:40] if who and who.lower() != "unknown" else ""


def apply_vote(bank, v):  # same rules as backend _apply_anchor_vote
    s = bank.get(str(v.get("id") or ""))
    who = voter(v)
    if not s or not who:
        return False
    if v.get("cut"):
        s["cut"] = True
        s["cut_by"] = who
    else:
        s.pop("cut", None)
        s.pop("cut_by", None)
    try:
        b = max(-20, min(20, int(v.get("bump") or 0)))
    except (TypeError, ValueError):
        b = 0
    if v.get("abs"):
        if b:
            s["bump"] = b
        else:
            s.pop("bump", None)
    elif b:
        s["bump"] = b
    return True


def votes_from_live():
    with urllib.request.urlopen(API + "/anchorvotes", timeout=25) as r:
        d = json.loads(r.read().decode())
    if not d.get("durable"):
        print("WARNING: the live backend says its vote file is not on the volume, so a redeploy would lose "
              "votes it has not been asked for. Check the /data volume on Railway.")
    return d.get("votes") or []


def votes_from_file(path):
    d = json.load(open(path))
    # whoever runs this by hand vouches for the file, so unsigned entries still count
    return [dict(v, id=k, who=v.get("who") or "curator") for k, v in d.items()]


def log_new(votes):
    seen = set()
    if os.path.exists(LOG):
        for line in open(LOG, encoding="utf-8"):
            if line.strip():
                e = json.loads(line)
                seen.add((e.get("id"), e.get("ts"), bool(e.get("cut")), int(e.get("bump") or 0)))
    new = [v for v in votes if (v.get("id"), v.get("ts"), bool(v.get("cut")), int(v.get("bump") or 0)) not in seen]
    if new:
        with open(LOG, "a", encoding="utf-8") as f:
            for v in new:
                f.write(json.dumps(v, sort_keys=True) + "\n")
    return len(new)


def main():
    live = len(sys.argv) < 2
    votes = votes_from_live() if live else votes_from_file(sys.argv[1])
    if live:
        print("%d live votes, %d new to tools/votes/anchor_votes.jsonl" % (len(votes), log_new(votes)))
    if not votes:
        return
    before = open(SRC, encoding="utf-8").read()
    src = json.loads(before)
    bank = {s.get("id"): s for s in src if s.get("id")}
    applied = sum(apply_vote(bank, v) for v in votes)
    after = json.dumps(src, indent=1, ensure_ascii=False)
    if after.strip() != before.strip():
        with open(SRC, "w", encoding="utf-8") as f:
            f.write(after)
        print("baked into backend/sources.json: %d votes, bank now has %d cut, %d moved"
              % (applied, sum(1 for s in src if s.get("cut")), sum(1 for s in src if s.get("bump"))))
    else:
        print("backend/sources.json already matches the votes (%d applied, no change)" % applied)


if __name__ == "__main__":
    main()
