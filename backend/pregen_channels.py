#!/usr/bin/env python3
"""Pre-generate tailored ideas for the day's outreach batch, so each creator's personal
link (videos-we-support.web.app/?c=@handle) resolves INSTANTLY instead of making them
wait ~50 seconds.

Usage (Drew's morning ritual, ~1 min per channel):
  python3 pregen_channels.py @chan1 @chan2 ...       # pre-generate today's batch + print links + email blocks
  python3 pregen_channels.py --file today.txt        # one channel per line
  python3 pregen_channels.py --status                # who opened / generated / hit "I'd make this" 🙋
  python3 pregen_channels.py --list                  # show what's already pre-generated

Each run: calls the live /custom endpoint per channel (the server stores the result in its
pregen cache immediately) AND merges the results into the local pregen.json here, so the
cache survives redeploys (deploying ships this file). Then prints the personal link to put
in the email. ~50s per channel, run it while the coffee brews.
"""
import json, os, re, sys, time, urllib.request

API = "https://videos-similar-api-production.up.railway.app/custom"
_KEY = os.environ.get("SPECIES_OPS_KEY", "sk-17623772cdc7e1aa2f255a1ff57b7aa0e55dfca0")  # rotated 2026-07-14
EVENTS = f"https://videos-similar-api-production.up.railway.app/events?key={_KEY}&n=3000"
DASH = f"https://videos-similar-api-production.up.railway.app/dash?key={_KEY}"
HERE = os.path.dirname(os.path.abspath(__file__))
PREGEN = os.path.join(HERE, "pregen.json")


def chan_key(u):
    u = (u or "").strip().lower()
    u = re.sub(r"[?#].*$", "", u)
    u = re.sub(r"^https?://", "", u).replace("www.", "", 1).rstrip("/")
    return u


def full_url(c):
    c = c.strip()
    if c.startswith("http"):
        return re.sub(r"[?#].*$", "", c)
    if not c.startswith("@"):
        c = "@" + c
    return "https://www.youtube.com/" + c


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    try:
        local = json.load(open(PREGEN, encoding="utf-8"))
    except Exception:
        local = {}
    if args[0] == "--status":
        # morning pipeline check: who engaged since the last sends
        try:
            evs = json.loads(urllib.request.urlopen(EVENTS, timeout=30).read()).get("events", [])
        except Exception as e:
            print("could not fetch events:", str(e)[:80]); return
        opens = [e for e in evs if e.get("t") == "open" and not e.get("adm")]
        gens = [e for e in evs if e.get("t") in ("generate", "generate_done", "pregen_hit")]
        ints = [e for e in evs if e.get("t") == "interest"]
        print(f"since last deploy: {len(opens)} page opens, {len(gens)} generations, {len(ints)} 🙋 interests")
        for e in ints:
            print(f"  🙋 {e.get('channel') or e.get('c') or '?'} -> {e.get('title','')[:70]}")
        chans = {}
        for e in gens:
            k = e.get("ch") or e.get("c") or ""
            if k: chans[k] = chans.get(k, 0) + 1
        for k, n in sorted(chans.items(), key=lambda x: -x[1])[:15]:
            print(f"  generated: {k} x{n}")
        print(f"full table: {DASH}")
        return
    if args[0] == "--list":
        for k, v in sorted(local.items()):
            print(f"  {k}  ({len(v.get('ideas', []))} ideas, channel: {v.get('channel')})")
        print(f"{len(local)} channels pre-generated")
        return
    channels = []
    if args[0] == "--file":
        channels = [l.strip() for l in open(args[1]) if l.strip() and not l.startswith("#")]
    else:
        channels = args
    for c in channels:
        url = full_url(c)
        key = chan_key(url)
        if key in local:
            print(f"= already cached: {key} -> https://videos-we-support.web.app/?c={c.lstrip('@') if not c.startswith('http') else key.split('/')[-1].lstrip('@')}")
            continue
        print(f"~ generating for {url} (about a minute)…", flush=True)
        try:
            req = urllib.request.Request(API, data=json.dumps({"channelUrl": url, "exclude": []}).encode(),
                                         headers={"Content-Type": "application/json"})
            resp = json.loads(urllib.request.urlopen(req, timeout=240).read())
            if not resp.get("ideas"):
                print(f"! FAILED for {url}: {resp.get('error', 'no ideas')}")
                continue
            local[key] = resp
            json.dump(local, open(PREGEN, "w", encoding="utf-8"), ensure_ascii=False)
            handle = key.split("/")[-1].lstrip("@")
            link = f"https://videos-we-support.web.app/?c={handle}"
            first = resp["ideas"][0]["title"] if resp["ideas"] else ""
            print(f"✓ {resp.get('channel')} — {len(resp['ideas'])} ideas")
            print(f"  personal link: {link}")
            print( "  --- paste into the email (after 'Ideas' bullet) ---")
            print(f"  We actually already made some for you, based on your recent uploads: {link}")
            print(f"  (First one on your page: \"{first[:90]}\" — each comes with sources, so you can check everything is real.)")
            print( "  ---------------------------------------------------")
        except Exception as e:
            print(f"! ERROR for {url}: {str(e)[:120]}")
        time.sleep(2)
    print("\nDone. Results are live on the server now; run `railway up` from this folder "
          "whenever convenient to make them survive future deploys.")


if __name__ == "__main__":
    main()
