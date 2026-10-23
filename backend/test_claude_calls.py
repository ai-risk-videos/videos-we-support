"""Offline checks for how main.py calls Claude (no network, no API key, no venv).

Run: python3 backend/test_claude_calls.py

Claude Opus 5.5 rejects several things older code sent routinely (a 400 for `thinking: disabled`,
`temperature`, forced `tool_choice`), counts thinking against max_tokens, can open a reply with
thinking blocks, and can decline on safety grounds with HTTP 200 and stop_reason "refusal". Every call
in main.py goes through claude(); these tests pin what claude() sends and how it reads replies, and
scan main.py so a new call site cannot quietly skip it.
"""
import os, re, sys, types

HERE = os.path.dirname(os.path.abspath(__file__))
for _n in ("fastapi", "fastapi.concurrency", "fastapi.middleware", "fastapi.middleware.cors",
           "fastapi.responses", "anthropic", "yt_dlp"):
    sys.modules.setdefault(_n, types.ModuleType(_n))
class _Any:
    def __init__(self, *a, **k): self.a, self.k = a, k
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
os.environ["EVENTS_PATH"] = os.devnull
import main  # noqa: E402

fails = []
def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  -> " + str(detail)) if detail and not ok else ""))
    if not ok:
        fails.append(name)


class B:  # a content block
    def __init__(self, type, text=""):
        self.type, self.text = type, text
class Det:
    def __init__(self, category): self.category = category
class Msg:
    def __init__(self, blocks, stop="end_turn", category=None, model="claude-opus-5-5"):
        self.content, self.stop_reason, self.model = blocks, stop, model
        self.stop_details = Det(category) if stop == "refusal" else None
class FakeClient:
    def __init__(self, reply):
        self.reply, self.sent, self.streamed, self.created = reply, None, False, False
        outer = self
        class _M:
            def create(self, **kw):
                outer.sent, outer.created = kw, True
                return outer.reply
            def stream(self, **kw):
                outer.sent, outer.streamed = kw, True
                class _S:
                    def __enter__(s): return s
                    def __exit__(s, *a): return False
                    def get_final_message(s): return outer.reply
                return _S()
        self.messages = _M()


print("what claude() sends")
fc = FakeClient(Msg([B("text", "ok")]))
main.claude("t", "low", client=fc, max_tokens=4000, messages=[{"role": "user", "content": "hi"}])
s = fc.sent
check("model defaults to MODEL (claude-opus-5-5)", s.get("model") == main.MODEL == "claude-opus-5-5", s.get("model"))
check("effort is always set", s.get("output_config") == {"effort": "low"}, s.get("output_config"))
check("server-side fallback beta header on Opus 5.5",
      (s.get("extra_headers") or {}).get("anthropic-beta") == "server-side-fallback-2026-07-01", s.get("extra_headers"))
check("fallbacks: default in the body on Opus 5.5", (s.get("extra_body") or {}).get("fallbacks") == "default", s.get("extra_body"))
check("no thinking / temperature / top_p / top_k sent",
      not any(k in s for k in ("thinking", "temperature", "top_p", "top_k")), sorted(s))
# EVERY call streams. A plain request is bounded by the client timeout end to end, so a slow
# high-effort reply timed out, retried silently and took ~300s to fail (a real /retitle took 170s).
check("a 4000-token call streams too (no plain request)", fc.streamed and not fc.created)
check("a low/medium call keeps the max_tokens it asked for", s.get("max_tokens") == 4000, s.get("max_tokens"))
fc = FakeClient(Msg([B("text", "ok")]))
main.claude("t", "high", client=fc, max_tokens=64000, messages=[{"role": "user", "content": "hi"}])
check("a 64000-token call streams", fc.streamed and not fc.created)
check("a max_tokens above the floor is kept", fc.sent.get("max_tokens") == 64000, fc.sent.get("max_tokens"))
fc = FakeClient(Msg([B("text", "ok")]))
main.claude("t", "high", client=fc, max_tokens=16000, messages=[{"role": "user", "content": "hi"}])
check("high effort raises max_tokens to at least 32000 (it can think 26k+ and truncate the JSON)",
      fc.sent.get("max_tokens") == 32000 == main.MIN_TOKENS_HIGH, fc.sent.get("max_tokens"))
fc = FakeClient(Msg([B("text", "ok")]))
main.claude("t", "medium", client=fc, max_tokens=1000, messages=[{"role": "user", "content": "hi"}])
check("any effort raises max_tokens to at least 4000", fc.sent.get("max_tokens") == 4000 == main.MIN_TOKENS,
      fc.sent.get("max_tokens"))
fc = FakeClient(Msg([B("text", "ok")]))
main.claude("t", "low", client=fc, max_tokens=8000, messages=[{"role": "user", "content": "hi"}])
check("a low-effort call is not raised to the high floor", fc.sent.get("max_tokens") == 8000, fc.sent.get("max_tokens"))
main._client = None
_c = main.get_client()
_k = getattr(_c, "k", {})
check("shared client: timeout is a silence bound of 120-600s, one retry",
      _k.get("max_retries") == 1 and 120 <= float(_k.get("timeout") or 0) <= 600, _k)
main._client = None
fc = FakeClient(Msg([B("text", "ok")], model="claude-haiku-5-5"))
main.claude("t", "low", client=fc, model="claude-haiku-5-5", max_tokens=4000, messages=[{"role": "user", "content": "hi"}])
check("no fallback opt-in on a model that has no server-side fallback",
      "extra_body" not in fc.sent and "extra_headers" not in fc.sent, sorted(fc.sent))

print("\nhow claude() reads replies")
m = Msg([B("thinking"), B("text", "Hello "), B("fallback"), B("text", "world")])
check("text joins every text block, after thinking/fallback blocks", main.msg_text(m) == "Hello world", main.msg_text(m))
check("empty content reads as empty text", main.msg_text(Msg([])) == "")
fc = FakeClient(Msg([], stop="refusal", category="bio"))
try:
    main.claude("t", "high", client=fc, max_tokens=16000, messages=[{"role": "user", "content": "hi"}])
    check("a refusal raises ModelRefusal", False, "returned normally")
except main.ModelRefusal as e:
    check("a refusal raises ModelRefusal", True)
    check("the refusal message is readable and names the category",
          "safety filter" in str(e) and "bio" in str(e) and e.category == "bio", str(e))
fc = FakeClient(Msg([], stop="refusal", category=None))
try:
    main.claude("t", "high", client=fc, max_tokens=16000, messages=[{"role": "user", "content": "hi"}])
except main.ModelRefusal as e:
    check("a refusal with no category still reads cleanly", e.category == "unspecified" and "None" not in str(e), str(e))

print("\nmain.py call sites")
src = open(os.path.join(HERE, "main.py"), encoding="utf-8").read()
body = src.split("def claude(", 1)[1].split("\ndef ", 1)[0]
rest = src.replace(body, "")
check("no messages.create / messages.stream outside claude()",
      not re.search(r"\.messages\.(create|stream)\(", rest), re.findall(r".{40}\.messages\.(?:create|stream)\(", rest)[:3])
check("no request sends thinking / temperature / top_p / top_k",
      not re.search(r"\b(thinking|temperature|top_p|top_k)\s*=", src), re.findall(r".{30}\b(?:thinking|temperature|top_p|top_k)\s*=.{20}", src)[:3])
check("no forced tool_choice", not re.search(r"tool_choice[^\n]*\"(any|tool)\"", src))
check("nothing reads .content[0]", not re.search(r"\.content\[0\]|\[\"content\"\]\[0\]", src))
sites = [src[m.end():m.end() + 500] for m in re.finditer(r"(?<![\w.])claude\((?!\))", src)  # claude() in prose is not a call
         if not src[max(0, m.start() - 4):m.start()].endswith("def ")]
check("found the call sites", len(sites) >= 25, len(sites))
eff, toks, bad = [], [], []
for site in sites:
    e = re.match(r"\s*(?:#[^\n]*)?\s*\"[^\"]+\"[^,]*,\s*(\"\w+\"|GEN_EFFORT)", site)
    t = re.search(r"max_tokens=(\w+)", site)
    if not (e and t):
        bad.append(site[:80])
        continue
    eff.append(e.group(1)); toks.append(t.group(1))
check("every call names a tag, an effort and max_tokens", not bad, bad)
low = [n for n in toks if n.isdigit() and int(n) < 4000]
check("every max_tokens is at least 4000 (thinking shares it)", not low, low)
high_low = [t for e, t in zip(eff, toks) if e in ('"high"', "GEN_EFFORT") and t.isdigit() and int(t) < 32000]
check("every high-effort call site asks for at least 32000", not high_low, high_low)
check("claude() itself never makes a plain request", "messages.create(" not in body and "messages.stream(" in body)
check("efforts are low/medium/high", all(e in ('"low"', '"medium"', '"high"', "GEN_EFFORT") for e in eff), set(eff))

print("\n" + ("ALL PASS" if not fails else "%d FAILED: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
