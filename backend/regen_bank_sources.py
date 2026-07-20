#!/usr/bin/env python3
"""Regenerate precomputed source sets for curated bank ideas (bank_sources.json).

Usage:
  python3 regen_bank_sources.py "Exact Idea Title"        # regenerate one entry
  python3 regen_bank_sources.py --all                     # regenerate all 64 (slow, ~10 min)
  python3 regen_bank_sources.py --check                   # sanity: every bank title has an entry

How it works: for each requested BASE title (as written in frontend/build_species_ideas.py
LANES), it calls the live /sources endpoint with force=true (which bypasses the cache and
runs the full live pipeline: rarity-weighted menu -> model pick -> curation -> semantic vet),
prints the result for review, and on your confirmation writes it into bank_sources.json.
After writing, redeploy the API (railway up) for the new cache to serve.

If a regenerated entry looks off topic, just run it again (live results vary) or hand pick:
open sources.json, find 2-5 genuinely fitting entries, and write them into bank_sources.json
as [{"title","who","year","url"}, ...] under the exact idea title key.
"""
import json, sys, time, urllib.request, os, re

API = "https://videos-similar-api-production.up.railway.app/sources"
HERE = os.path.dirname(os.path.abspath(__file__))
BANK_FILE = os.path.join(HERE, "bank_sources.json")
BUILD = os.path.join(HERE, "..", "frontend", "build_species_ideas.py")


def bank_titles():
    src = open(BUILD, encoding="utf-8").read()
    m = re.search(r"^LANES\s*=\s*(\[.*?\n\])\s*$", src, re.S | re.M)
    data = eval(m.group(1))  # trusted local file
    out = []
    for lane in data:
        for it in lane.get("items", []):
            out.append({"title": it["t"], "summary": it["s"], "pitch": it.get("p", "")})
    return out


def fetch(idea):
    body = json.dumps({"title": idea["title"], "summary": idea["summary"],
                       "pitch": idea.get("pitch", ""), "force": True}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read()).get("sources", [])


def main():
    args = sys.argv[1:]
    ideas = bank_titles()
    bank = json.load(open(BANK_FILE))
    if args == ["--check"]:
        missing = [i["title"] for i in ideas if i["title"] not in bank]
        extra = [t for t in bank if t not in {i["title"] for i in ideas}]
        thin = [t for t, s in bank.items() if len(s) < 2]
        print("missing entries:", missing or "none")
        print("orphaned entries (title no longer in LANES):", extra or "none")
        print("entries with <2 sources:", thin or "none")
        return
    targets = ideas if args == ["--all"] else [i for i in ideas if i["title"] in args]
    if not targets:
        print("No matching bank title. Pass the EXACT base title from build_species_ideas.py LANES.")
        return
    for i, idea in enumerate(targets):
        srcs = fetch(idea)
        print(f"\n[{i+1}/{len(targets)}] {idea['title']}")
        for s in srcs:
            print(f"   - {s['title'][:70]} ({s['who']}) {s['url'][:60]}")
        if len(srcs) >= 2:
            bank[idea["title"]] = srcs
            print("   -> written")
        else:
            print("   -> SKIPPED (fewer than 2 sources; rerun or hand pick)")
        time.sleep(0.5)
    json.dump(bank, open(BANK_FILE, "w"), indent=1)
    print(f"\nSaved {BANK_FILE}. Review the printouts above, then redeploy: cd {HERE} && railway up")


if __name__ == "__main__":
    main()
