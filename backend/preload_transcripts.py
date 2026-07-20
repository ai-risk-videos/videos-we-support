#!/usr/bin/env python3
"""Preload channel TRANSCRIPTS so profiles (and therefore tailored leads + research packs)
are built from what creators actually say, not just titles.

Why this exists: YouTube blocks caption fetches from datacenter IPs (the server), but they
work instantly from a home connection. So this runs on the Mac, fetches transcripts of a
channel's recent videos (~1s each), and pushes them to the live server.

Usage (same ritual as pregen_channels.py):
  python3 preload_transcripts.py @kurzgesagt @veritasium ...   # preload these channels
  python3 preload_transcripts.py --file today.txt              # one channel per line
  python3 preload_transcripts.py --list                        # what the server has cached
  python3 preload_transcripts.py --force @chan                 # refresh even if cached

Each run: pushes to the live server (takes effect immediately for /tailor and /custom)
AND merges into local transcripts.json, so the cache survives redeploys (deploying ships
this file). ~20s per channel.
"""
import json, os, re, sys, time, urllib.request

BASE = "https://videos-similar-api-production.up.railway.app"
KEY = os.environ.get("SPECIES_OPS_KEY", "sk-17623772cdc7e1aa2f255a1ff57b7aa0e55dfca0")  # rotated 2026-07-14; this file lives only on this Mac
HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL = os.path.join(HERE, "transcripts.json")
N_VIDEOS = 12          # transcripts per channel (matches the server's _TR_MAX_VIDEOS)
MAX_CHARS = 12000      # per transcript (matches server cap)
MIN_SECONDS = 150      # skip Shorts / trailers


def chan_key(u):
    u = (u or "").strip().lower()
    u = re.sub(r"[?#].*$", "", u)
    u = re.sub(r"^https?://", "", u).replace("www.", "", 1).rstrip("/")
    return u


def full_url(c):
    c = c.strip()
    if c.startswith("http"):
        c = re.sub(r"[?#].*$", "", c)
        # strip tab suffixes: a pasted .../videos URL would otherwise key the cache under
        # "@chan/videos" (which /tailor never looks up) and request "/videos/videos"
        return re.sub(r"/(videos|featured|streams|shorts|playlists|community|about)/?$", "", c).rstrip("/")
    if not c.startswith("@"):
        c = "@" + c
    return "https://www.youtube.com/" + c


def recent_videos(url, n=25):
    """Flat listing of recent uploads (works from a residential IP)."""
    import yt_dlp
    o = {"quiet": True, "extract_flat": True, "playlistend": n, "skip_download": True,
         "socket_timeout": 15, "retries": 0, "extractor_retries": 0}
    base = url.rstrip("/") + "/videos"
    with yt_dlp.YoutubeDL(o) as y:
        info = y.extract_info(base, download=False)
    name = info.get("channel") or info.get("uploader") or info.get("title") or ""
    ents = [e for e in (info.get("entries") or []) if e and e.get("id") and e.get("title")]
    # drop Shorts when duration is known; keep unknown-duration entries (some flat listings omit it)
    ents = [e for e in ents if not e.get("duration") or e["duration"] >= MIN_SECONDS]
    return name, [(e["id"], e["title"]) for e in ents]


def fetch_transcript(vid):
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import NoTranscriptFound
    api = YouTubeTranscriptApi()
    try:
        tr = api.fetch(vid)  # english first
    except NoTranscriptFound:
        # non-English channel: take whatever language exists (the analyst model reads it fine),
        # preferring a human-made track over auto-generated
        listed = list(api.list(vid))
        if not listed:
            raise
        pick = next((t for t in listed if not t.is_generated), listed[0])
        tr = pick.fetch()
    return re.sub(r"\s+", " ", " ".join(s.text for s in tr)).strip()


def push(url, name, videos):
    payload = {"key": KEY, "channelUrl": url, "channel": name, "videos": videos}
    req = urllib.request.Request(BASE + "/transcripts-upload", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def main():
    args = [a for a in sys.argv[1:]]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--list":
        j = json.loads(urllib.request.urlopen(f"{BASE}/transcripts-status?key={KEY}", timeout=30).read())
        for c in j.get("channels", []):
            print(f"  {c['channel_key']}  ({c['videos']} videos, via {c['via']}, {c['age_days']}d old)  {c.get('channel','')}")
        print(f"{len(j.get('channels', []))} channels cached | on-demand proxy configured: {j.get('proxy_configured')}")
        return
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    if args and args[0] == "--file":
        channels = [l.strip() for l in open(args[1]) if l.strip() and not l.startswith("#")]
    else:
        channels = args
    try:
        local = json.load(open(LOCAL, encoding="utf-8"))
    except Exception:
        local = {}
    try:
        server = {c["channel_key"]: c.get("via", "") for c in json.loads(urllib.request.urlopen(
            f"{BASE}/transcripts-status?key={KEY}", timeout=30).read()).get("channels", [])}
    except Exception:
        server = {}

    for c in channels:
        url = full_url(c)
        key = chan_key(url)
        # skip only full preloads; a proxy-sourced or thin entry deserves the upgrade
        if server.get(key) == "preload" and not force:
            print(f"= already preloaded on server: {key} (use --force to refresh)")
            continue
        print(f"~ {url}", flush=True)
        try:
            name, vids = recent_videos(url)
        except Exception as e:
            print(f"! could not list channel: {str(e)[:120]}")
            continue
        got, skipped, blocked = [], 0, False
        for vid, title in vids:
            if len(got) >= N_VIDEOS:
                break
            try:
                txt = fetch_transcript(vid)
                if len(txt) < 200:
                    skipped += 1
                    continue
                if len(txt) > MAX_CHARS:  # keep the ending: profiles describe how videos close
                    txt = txt[:MAX_CHARS - 3000] + " [...] " + txt[-2900:]
                got.append({"id": vid, "title": title, "text": txt})
                print(f"  ✓ {len(txt.split()):>5} words  {title[:70]}", flush=True)
            except Exception as e:
                # YouTube temp-blocks even residential IPs after a burst; that kills the whole
                # run, so say so loudly instead of silently skipping every video
                if type(e).__name__ in ("IpBlocked", "RequestBlocked"):
                    blocked = True
                    print("\n!! YouTube has temporarily rate-limited this IP (happens after a burst of "
                          "fetches). It clears on its own, usually within the hour — rerun this then.")
                    break
                skipped += 1
                if skipped <= 2:
                    print(f"  x {type(e).__name__}: {title[:60]}")
            time.sleep(3)  # gentle pacing: bursts are what get an IP temp-blocked
        if blocked and len(got) < 5:
            # a thin block-interrupted batch would be stored, then skipped forever as "done";
            # better to keep titles-only until a full rerun
            print(f"! only {len(got)} transcripts before the block — NOT uploading; rerun in an hour")
            continue
        if not got:
            print(f"! no transcripts found for {key} (no captions? members-only?)")
            continue
        try:
            resp = push(url, name, got)
            local[key] = {"channel": name, "ts": time.time(), "via": "preload", "videos": got}
            tmp = LOCAL + ".tmp"  # atomic: Ctrl-C mid-write must not truncate the local cache
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(local, f, ensure_ascii=False)
            os.replace(tmp, LOCAL)
            print(f"✓ {name}: {resp.get('stored')} transcripts live on server ({skipped} skipped)")
        except Exception as e:
            print(f"! upload failed: {str(e)[:120]}")
    print("\nDone. Profiles built from now on use these transcripts. Run `railway up` from this "
          "folder whenever convenient so the cache survives future deploys.")


if __name__ == "__main__":
    main()
