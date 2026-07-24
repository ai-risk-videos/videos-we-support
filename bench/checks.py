"""Every piece of writing feedback the curator has given, as an executable check.

Each check turns a piece of taste ("stop using em dashes", "the last sentence is too abstract",
"grade 4 is too low") into a number we can watch across prompt changes. Add a new check the moment
new feedback lands, so the feedback becomes a permanent regression test instead of a note in a
transcript that decays.

A check is (key, title, feedback, scope, fn):
  scope "idea"  -> fn(idea, ctx)  returns None or a short reason string (one violation per idea)
  scope "batch" -> fn(ideas, ctx) returns a list of (index, reason)
`ctx` carries the whole batch so idea-level checks can still see their neighbours.
"""
import re

# ---------------------------------------------------------------- text helpers
def summary(x):
    return (x.get("summary") or "") if isinstance(x, dict) else ""

def hook(x):
    return (x.get("title") or "") if isinstance(x, dict) else ""

def both(x):
    return (hook(x) + " " + summary(x)).strip()

def sentences(t):
    return [s for s in re.split(r'(?<=[.?!])\s+', (t or "").strip()) if s.strip()]

def last_sentence(t):
    s = sentences(t)
    return s[-1] if s else ""

def _syllables(w):
    w = re.sub(r'[^a-z]', '', w.lower())
    if not w:
        return 0
    n = len(re.findall(r'[aeiouy]+', w))
    if w.endswith('e') and n > 1:
        n -= 1
    return max(1, n)

def reading_grade(t):
    """Flesch-Kincaid grade. Meaningless on very short text, so callers should not apply it to hooks."""
    sents = [s for s in re.split(r'[.?!]+', t or "") if s.strip()]
    words = re.findall(r"[A-Za-z]+", t or "")
    if not sents or not words:
        return 0.0
    return round(0.39 * (len(words) / len(sents)) + 11.8 * (sum(_syllables(w) for w in words) / len(words)) - 15.59, 1)

# ---------------------------------------------------------------- patterns
DASH = re.compile(r'[—–]')
NOTXY = re.compile(
    r"\b(is|are|was|were)\s+not\s+[^.?!]{2,90}[.?!]+\s+(it|that|they)\s+(is|are|'s|was|were)\b"
    r"|\bis\s?n'?o?t\b[^,.?!]{2,90},\s*(it'?s|it is|that'?s|they'?re)\b"
    r"|\bnot\s+just\b[^.?!]{2,70}\bbut\b", re.I)
MOOD = re.compile(r'\b(quietly|slowly|inexorably|steadily|gradually|imperceptibly)\b', re.I)
META_I = re.compile(
    r'\bI\s+(?:show|explain|trace|follow|break\s+down|walk\s+(?:you\s+)?through|dig\s+into|unpack|'
    r'lay\s+out|map\s+out|examine|explore|look\s+at|ask\s+what|argue|make\s+the\s+case|tell\s+the\s+story)\b', re.I)
META_OPENER = re.compile(
    r'^\s*(?:A\s+(?:think[- ]piece|follow[- ]up|story|deep[- ]dive|video|film)\b|Reads\s+like|Applies\s+(?:his|her|their)|'
    r'Walks\s+through|Takes\s+\w+\s+and|Uses\s+(?:his|her|their|the)\b|In\s+(?:his|her|their)\s+\w+\s+style|'
    r'Handles\s+it\s+the\s+way)', re.I)
WHW = re.compile(r'\bwhat happens when\b', re.I)
# The implication sentence stops at a first-order inconvenience instead of the terminal stake.
# From: "an honest debate might be hard?? snooze. the implications need to point to really serious
# like civilizational collapse level stuff".
WEAK_STAKE = re.compile(
    r'\b(?:'
    r'(?:much |very |a lot |even |far )?(?:harder|tougher|more difficult|difficult|hard)\s+to\b'
    r'|makes?\s+it\s+(?:harder|tougher|difficult|complicated)'
    r'|complicat(?:es|ed|ing)\b'
    r'|less\s+likely\b|not\s+easy\b|challenging\b'
    r'|(?:erodes?|loses?|losing|hurts?)\s+(?:public\s+)?trust\b'
    r'|raises?\s+(?:hard\s+|real\s+|new\s+)?questions?\b'
    r'|worth\s+(?:watching|asking|thinking about)\b'
    r'|is\s+(?:a\s+)?(?:real\s+)?(?:problem|concern|worry)\b'
    r')', re.I)
# the opposite failure: reaching for generic doom instead of earning the stake from the mechanism
DOOM_TAG = re.compile(
    r'\b(?:could\s+end\s+(?:humanity|civili[sz]ation|us all)|an?\s+extinction\s+(?:risk|event)'
    r'|the\s+stakes\s+could\s+not\s+be\s+higher|the\s+end\s+of\s+(?:humanity|civili[sz]ation))', re.I)
DOOMER = re.compile(r'\bdoomers?\b', re.I)
AI_LABS = re.compile(r'\bAI\s+labs?\b', re.I)
VAGUE_SYSTEM = re.compile(r'\b(these systems|those systems|the system\b|a system\b|AI systems?)\b', re.I)
CHATBOT = re.compile(r'\bchatbots?\b', re.I)
NAMED_SOURCE = re.compile(
    r'\b(OpenAI|Anthropic|Google|DeepMind|Meta|Microsoft|METR|Palisade|Apollo Research|Apollo|DeepSeek|'
    r'Nvidia|Hinton|Altman|Amodei|Sutskever|Bengio|Leike|Thiel|RAND|NIST|Redwood|Epoch)\b')
# framings that make AI look like hype/a grift/too weak — the project's cardinal sin
CAUSE_HARM = re.compile(
    r'\b(just (?:a )?hype|only hype|is a grift|the grift|snake oil|just marketing|merely marketing|'
    r'a bubble about to|nothing but hype|overhyped and harmless|too dumb to|cannot really do anything)\b', re.I)

GRADE_MAX = 8.3          # summaries above this read too hard (target ~7)
Q_SHARE_MAX = 0.25       # at most 1 in 4 ideas may end on a question
WHW_MAX = 2              # the stock phrase, per batch

_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_WORDNUM = {"ten": 10, "hundred": 100, "hundreds": 100, "thousand": 1000, "thousands": 1000,
            "million": 1e6, "millions": 1e6, "billions": 1e9}

def _numbers(t):
    out = []
    for m in re.finditer(r'\$?\s*([\d][\d,]*(?:\.\d+)?)\s*(thousand|million|billion|trillion)?', t or ""):
        try:
            v = float(m.group(1).replace(",", ""))
        except Exception:
            continue
        if m.group(2):
            v *= _SCALE[m.group(2).lower()]
        out.append(v)
    return out

def ratio_error(t):
    """(claimed, actual) when a stated 'X to one' contradicts the numbers beside it."""
    m = re.search(r'\b([a-z]+|[\d,]+)\s+to\s+one\b', t or "", re.I)
    if not m:
        return None
    tok = m.group(1).lower().replace(",", "")
    claimed = _WORDNUM.get(tok)
    if claimed is None:
        try:
            claimed = float(tok)
        except Exception:
            return None
    nums = sorted(_numbers(t), reverse=True)
    if len(nums) < 2 or nums[1] <= 0:
        return None
    actual = nums[0] / nums[1]
    if actual > 0 and max(claimed / actual, actual / claimed) > 3.0:
        return (claimed, actual)
    return None

# ---------------------------------------------------------------- checks
def _c_em_dash(x, ctx):
    if DASH.search(both(x)):
        return "em dash in the text"
    return None

def _c_notxy(x, ctx):
    if NOTXY.search(summary(x)):
        return "the 'not X, it is Y' contrast cadence"
    return None

def _c_mood_closer(x, ctx):
    c = last_sentence(summary(x))
    if MOOD.search(c):
        return "closer leans on a mood adverb: " + c[:70]
    return None

def _c_meta_narration(x, ctx):
    c = last_sentence(summary(x))
    if META_I.search(c):
        return "closer narrates the video ('I show/explain...'): " + c[:70]
    if META_OPENER.search(summary(x)):
        return "summary opens by describing the video, not the content"
    return None

def _c_grade(x, ctx):
    g = reading_grade(summary(x))
    if g > GRADE_MAX:
        return "reads at grade %s (target about 7)" % g
    return None

def _c_ratio(x, ctx):
    r = ratio_error(summary(x))
    if r:
        return "says %d to one, the numbers give about %d to one" % (int(r[0]), int(round(r[1])))
    return None

def _c_banned_words(x, ctx):
    t = both(x)
    hits = []
    if DOOMER.search(t):
        hits.append("'doomer'")
    if AI_LABS.search(t):
        hits.append("'AI labs' (say AI companies)")
    if VAGUE_SYSTEM.search(t):
        hits.append("calls an AI a 'system'")
    if CHATBOT.search(t):
        hits.append("'chatbot'")
    return ", ".join(hits) if hits else None

def _c_cause_harm(x, ctx):
    m = CAUSE_HARM.search(both(x))
    if m:
        return "frames AI as hype/grift/too weak: '%s'" % m.group(0)
    return None

def _c_weak_implication(x, ctx):
    c = last_sentence(summary(x))
    if WEAK_STAKE.search(c):
        return "closer stops at a first-order inconvenience: " + c[:80]
    return None

def _c_doom_tag(x, ctx):
    c = last_sentence(summary(x))
    if DOOM_TAG.search(c):
        return "generic doom bolted on instead of the mechanism's own consequence: " + c[:80]
    return None

def _c_question_cadence(ideas, ctx):
    qs = [i for i, x in enumerate(ideas) if last_sentence(summary(x)).rstrip().endswith("?")]
    keep = max(1, int(len(ideas) * Q_SHARE_MAX)) if ideas else 0
    return [(i, "batch ends %d of %d ideas on a question (cap is %d)" % (len(qs), len(ideas), keep))
            for i in qs[keep:]]

def _c_whw(ideas, ctx):
    hits = [i for i, x in enumerate(ideas) if WHW.search(summary(x))]
    return [(i, "'What happens when' used %d times in this batch (cap is %d)" % (len(hits), WHW_MAX))
            for i in hits[WHW_MAX:]]

CHECKS = [
    ("em_dash", "No em dashes", "Em dashes read as AI slop; use commas or periods.", "idea", _c_em_dash),
    ("grade", "Reading grade about 7", "Grade 4 is too low and grade 10 is too hard.", "idea", _c_grade),
    ("notxy", "No 'not X, it's Y'", "A tired AI writing tell.", "idea", _c_notxy),
    ("mood_closer", "Closer names a real actor", "No agentless mood lines like 'the squeeze quietly tightens'.", "idea", _c_mood_closer),
    ("meta_narration", "No narrating the video", "Say the content, never 'I show how...' or 'A think-piece that'.", "idea", _c_meta_narration),
    ("question_cadence", "Vary how ideas end", "The forward-looking question is good, but not for every idea.", "batch", _c_question_cadence),
    ("what_happens_when", "Do not reuse one phrase", "'What happens when' should not open most closers.", "batch", _c_whw),
    ("weak_implication", "Implications reach the endgame",
     "An honest debate being hard is a shrug; go to what a society permanently loses.", "idea", _c_weak_implication),
    ("doom_tag", "No bolted-on doom", "Earn the stake from the mechanism, never tag on 'could end humanity'.", "idea", _c_doom_tag),
    ("ratio_math", "Ratios match their own numbers", "Simplifying must never break the arithmetic.", "idea", _c_ratio),
    ("banned_words", "House wording rules", "Never doomer, AI labs, chatbot, or calling an AI a system.", "idea", _c_banned_words),
    ("cause_harm", "Never make AI look like hype", "The mission is that the danger is real.", "idea", _c_cause_harm),
]

def run_checks(ideas):
    """-> {check_key: [(idea_index, reason), ...]} plus a few descriptive stats."""
    ctx = {"ideas": ideas}
    out = {}
    for key, _t, _f, scope, fn in CHECKS:
        found = []
        if scope == "idea":
            for i, x in enumerate(ideas):
                r = fn(x, ctx)
                if r:
                    found.append((i, r))
        else:
            found = list(fn(ideas, ctx) or [])
        out[key] = found
    return out

def stats(ideas):
    gs = [reading_grade(summary(x)) for x in ideas if summary(x)]
    named = sum(1 for x in ideas if NAMED_SOURCE.search(both(x)))
    qs = sum(1 for x in ideas if last_sentence(summary(x)).rstrip().endswith("?"))
    gs_sorted = sorted(gs)
    med = gs_sorted[len(gs_sorted) // 2] if gs_sorted else 0
    return {
        "n": len(ideas),
        "grade_median": round(med, 1),
        "grade_mean": round(sum(gs) / len(gs), 1) if gs else 0,
        "named_source_pct": round(100 * named / len(ideas)) if ideas else 0,
        "question_pct": round(100 * qs / len(ideas)) if ideas else 0,
    }
