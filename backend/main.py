import os, re, json, random, urllib.request, urllib.parse, asyncio
import concurrent.futures as _cf   # chunked polish passes run in parallel; see _sentence_polish
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import anthropic
import yt_dlp

app = FastAPI()
_DEPLOY_STAMP = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")  # server start = deploy swap time
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://videos-we-support.web.app",
        "https://spread-or-not.web.app",
        "http://localhost:8772",
        "http://localhost:8773",
        "http://localhost:8774",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL = "claude-opus-5"

# Claude Opus 5 turns thinking ON by default (Opus 4.8 did not), and max_tokens caps thinking PLUS
# response text together. Eighteen call sites here run on max_tokens between 350 and 15000, tuned for
# a model that never spent any of that budget on thinking; several would now come back as thinking and
# a truncated answer, or nothing at all. So thinking is explicitly OFF everywhere by default, which
# preserves the exact behaviour we measured on 4.8, and switched ON deliberately where it earns its
# cost. Disabling is only legal at effort `high` or below, which is the default and what we use.
# Generation is the one long call, and it needs a bound of its own rather than the shared client's.
GEN_TIMEOUT_S = 420.0
NO_THINK = {"type": "disabled"}
THINK = {"type": "adaptive"}
# The channel profile is a summarization task; Sonnet is markedly faster than Opus and
# near-Opus quality, which cuts time-to-first-idea. Idea generation stays on Opus (MODEL).
FAST_MODEL = "claude-sonnet-5"

# Appended to every reader-facing generator so nothing this app produces reads as AI-written.
# Distilled from Wikipedia's "Signs of AI writing" (the concrete tells, not vibes). The single
# most valuable rule: write like a specific human who knows the subject, not a press release.
ANTI_SLOP = """

DO NOT WRITE LIKE AN AI. These are the specific tells to avoid; hitting them makes the whole thing read as machine-generated, which kills trust:
- NO puffery adjectives: vibrant, rich, profound, groundbreaking, renowned, crucial, pivotal, significant, vital, enduring, seamless, robust, compelling, remarkable.
- NO inflated verbs standing in for plain ones: boasts, garners, delves, underscores, showcases, highlights, fosters, cultivates, enhances, emphasizes, navigates, "serves as", "stands as", "speaks to", "marks a", "represents a". Use is, has, does, shows.
- NO figurative abstract nouns: tapestry, landscape (as metaphor), interplay, intricacies, testament, realm, embodiment, "a reflection of", "a world of".
- NO negative-parallelism constructions: "not just X, but Y", "it's not X, it's Y", "not X, but rather Y", "more than just". Say the thing straight.
- NO rule-of-three padding (three adjectives or three phrases strung together for rhythm).
- NO participle depth-padding tacked onto a clause: "highlighting the importance of", "underscoring the significance", "contributing to the broader", "reflecting a shift toward", "serving as a reminder". Cut them.
- NO vague attribution: "experts argue", "some critics say", "observers note", "studies suggest", "many believe" without a specific named source.
- NO throat-clearing or editorializing: "it's important to note", "it's worth noting", "it's important to understand", "in today's world", "in an age of", "in a world where".
- NO significance-inflation: "leaves a lasting impact", "indelible mark", "evolving landscape", "a testament to", "plays a crucial role", "marks a turning point", "at the forefront".
- NO promotional/travel-brochure tone: "nestled", "in the heart of", "a commitment to".
- NO telling the reader how to feel or teeing up the point instead of just making it. Banned: "let that sit", "sit with that", "let that sink in", "here's the part that should worry/scare you", "here's the thing", "the scary part", "the crazy part", "make no mistake", "let me be clear", "and that should terrify you", "here's what should stop you cold". Just state the thing and let it land.
- Vary sentence length; let plain sentences be plain. When in doubt, cut the adjective and state the fact."""

# Single source of truth for the two cross-cutting filters. Injected into every generation
# prompt via the __IMPORTANCE_BAR__ / __MUNDANE__ markers below, so the rule can never drift
# between prompts. The importance bar is a POSITIVE test (does this matter?) that generalizes
# better than an ever-growing blocklist; MUNDANE just gives concrete examples of what fails it.
IMPORTANCE_BAR = (
    "THE TEST, applied to every idea: would the people who have spent their lives on AI risk "
    "(picture Eliezer Yudkowsky, Geoffrey Hinton, and Max Tegmark), alongside the Species channel itself, consider "
    "this one of the genuinely important things for the public to understand about how powerful AI could go wrong or "
    "reshape the world? If that panel would lean in and say people really need to get this, keep it. If they "
    "would shrug it off as a minor consumer annoyance or a passing tech news story, cut it. This is a test of "
    "whether the underlying idea matters, not a cue to feature these people or to sound academic, and the idea "
    "must still be a specific, intriguing, clickable video. The serious version of a broad topic (jobs, "
    "surveillance, companions, persuasion) passes; the tabloid version of it does not. "
    "THE SPECIES LENS (this tool is for the Species channel, so weigh every idea through it too): Species does not "
    "treat AI as a gadget, an app, or a tool. It treats AI as the arrival of a new and more capable kind of mind, a "
    "potential successor species that could outnumber, outthink, and eventually replace us, and it tells every story "
    "at that scale: the whole human species, and where the trendline is heading. Favor ideas that carry that species "
    "level, this-is-where-it-is-going weight; an idea that could not be told at the scale of our whole species and "
    "future is probably too small. This is a lens, not a mold, so it should not flatten the variety of angles or make "
    "every idea sound like doom. "
    "ONE HARD RULE ON FRAMING: never frame AI as a race or competition to win, e.g. an arms race over chips or "
    "compute, who is ahead, or a strategic resource that nations must grab. That framing accelerates the very "
    "race that is the danger and is net negative even when accurate. The AI race, chips, compute, and the "
    "concentration of AI power are valid subjects ONLY through the lens of risk: a race toward a cliff that no one "
    "can stop, or a few unaccountable actors gaining permanent power over everyone. If an idea only works as a "
    "business rivalry, a geopolitics horse race, or a who-is-winning story, cut it."
)
MUNDANE = (
    "Ideas that fail this test and must be cut no matter how well they otherwise fit: scams and fraud (deepfake "
    "scams, voice cloning); AI mistakes (hallucinations, made up facts, fake citations, an AI lawyer citing cases "
    "that do not exist, wrong answers, homework cheating); AI resource use (energy, water, data center strain); and "
    "everyday data and privacy gripes (apps training AI on your messages or photos, data consent and terms of "
    "service, opting out of data collection, scraping personal data for training); and personalized, surge, or "
    "dynamic pricing (AI charging each person the most they will pay). These are low stakes and overdone."
)
# Anti-local-maxima: the generators kept orbiting the same documented-eval cluster. RANGE hands
# them a much wider palette, demands each set span different mechanisms, and lets vivid scenarios
# (not just famous events) count as concrete so conceptual angles like gradual disempowerment qualify.
RANGE = (
    "RANGE WIDELY across the AI risk space; do not keep returning the same handful of stories. The most "
    "overused cluster is the documented safety-test results (an AI blackmailing an engineer, the TaskRabbit CAPTCHA trick, "
    "hidden sleeper agents, sandbagging a safety test, resisting shutdown): use at most ONE idea from that cluster "
    "and reach well beyond it. The wider space includes angles that rarely get covered: gradual disempowerment, "
    "where humans slowly hand control of the economy, media, and institutions to AI with no single dramatic takeover; "
    "permanent lock in of one regime, company, or set of values; the collapse of shared truth as AI floods the world "
    "with persuasion and fakes; an automated AI research loop that improves AI faster than anyone can supervise; the "
    "concentration of unprecedented power in whoever owns the most capable AI; humans becoming economically unnecessary "
    "and what that does to people; creeping dependence and the quiet loss of human agency; AI run companies and markets, "
    "and AI systems coordinating with each other; the still unsolved problem of getting a powerful AI to actually want "
    "what we want; why we cannot see or explain what these systems do inside; superhuman persuasion; engineered pandemics; "
    "autonomous weapons and war that escalates faster than humans can react; and whether the AI itself could matter morally. "
    "A strong set spans SEVERAL distinct mechanisms and angles, never several variations of one idea. HARD DIVERSITY RULE: "
    "no more than TWO of your ideas may center on the same core mechanism or theme (for example, do not return three or four "
    "different 'humans gradually hand over control' ideas); if a third variation of one theme appears, cut it and reach into a "
    "different part of the risk space instead. An idea does NOT need a famous documented event: a vivid, specific, well reasoned "
    "scenario or mechanism counts as concrete as long as it is precise and clearly explained."
)
TRUTH = (
    "GROUNDING, the most important honesty rule. Creators will click through expecting a real story; if an idea "
    "reads like news and turns out to be fiction, all trust is burned. So the HOOK of every idea must be something "
    "real and documented: a study, an incident, an official report, a real product, real data. State it precisely, "
    "never exaggerate what happened, and word it so it clearly happened (e.g. 'in a 2024 safety test, an AI...'). "
    "The forward looking turn (where this is heading) is a projection and must READ as projection: use 'could', "
    "'is on track to', 'where this leads', never plain past tense fiction dressed as a news event. Published "
    "scenarios and forecasts (AI 2027, Situational Awareness, the gradual disempowerment paper, Karnofsky's takeover "
    "report and similar) are background INSPIRATION only: mine them for angles, mechanisms, and how the world might "
    "look, then anchor the idea in something real and documented that makes that angle concrete. Never pitch a video "
    "whose subject is the scenario itself ('walk through AI 2027'), never ask the creator to invent speculative "
    "fiction, and never borrow a scenario's fictional events as if they happened."
)
WORDING = (
    "Prefer the words deceive, deception, scheme, or hide its true intentions over lie or lying. To a general viewer a "
    "'lying AI' sounds like one that is merely wrong or making things up, which reads as dumb and harmless; the real "
    "concern is an AI that deliberately and capably misleads. "
    "VOICE: write like a person talking to a normal audience, not a technical whitepaper. Just say \"AI\" or \"AIs\" "
    "(plural for many of them) — NOT \"advanced AI systems\", \"AI models\", \"frontier systems\", \"large language "
    "models\", or \"algorithms\". Drop stiff qualifiers like advanced, sophisticated, frontier, or powerful unless one "
    "is truly doing work. Use the everyday words a smart friend would use out loud, never academic or corporate register. "
    "Do NOT pad with long stand ins like \"these things\", \"these machines\", or \"these technologies\" when \"AIs\" or \"it\" "
    "says the same thing shorter. Do NOT call an AI a \"system\" or \"systems\" — to a normal viewer 'system' is vague and confusing (it could mean a "
    "computer, a bureaucracy, anything); just say \"an AI\", \"the AI\", \"AIs\", or \"it\". For example, never write "
    "\"the systems judging the AI are themselves AIs\"; write \"the AIs judging it are themselves AIs\". And never write "
    "\"AI system\" or \"AI systems\"; just \"an AI\" or \"AIs\". (This bans 'system' only as a stand-in for the AI itself; "
    "'the economic system', 'the financial system', 'the power grid' and similar real-world systems are still fine.) "
    "Call them AI COMPANIES, never 'AI labs' — 'lab' makes trillion dollar corporations sound like a few harmless "
    "scientists in white coats when they are among the most powerful companies on earth; say 'AI company', 'the "
    "company', or name it (OpenAI, Google, Anthropic) as fits."
)
TRAJECTORY = (
    "PROJECT THE TREND FORWARD. When an idea centers on something AI can do today, a live demo, or a slip up (especially "
    "agentic AI that browses, buys, books, or acts on its own), do NOT stop at 'look what it can do' or 'haha it messed "
    "up.' One agent making a funny mistake is not worth spreading on its own. The capability or the slip is only the "
    "hook; the POINT is where the trend leads. Hand the viewer the thought 'where is this going?' and project it outward "
    "to the real stakes: millions of these agents quietly running errands, spending money, and signing agreements with no "
    "human checking each step, who is accountable when one goes off the rails, and the slow loss of human oversight and "
    "control. Show the capability honestly, then make that trajectory the spine of the idea. "
    "This applies DOUBLY to economic and institutional ideas: if the idea is about AI taking jobs, setting prices, or "
    "making decisions inside one industry or for one group of people, that is only the on-ramp, not the story. Carry "
    "it to the endgame: not just the first jobs but everyone, not one industry but the permanent loss of human control "
    "over the economy and the institutions that run our lives (gradual disempowerment). An idea that stops at the near "
    "term symptom (one industry's jobs, one profession, a single institution automating a decision) reads as mundane; "
    "the version that reaches the endgame is the one worth making."
)
# ONE canonical reading-level rule, injected into every prompt that writes reader-facing prose
# (ideas, research packs, sample scripts) via the __READING_LEVEL__ marker. Curator feedback: a low
# reading level made the ideas land much better, so apply it to EVERYTHING. The target was then
# calibrated: grade 4 is too low, about grade 7 is right.
READING_LEVEL = (
    "READING LEVEL, applies to EVERY sentence you write: aim at ABOUT 5TH GRADE, meaning a bright 10 or 11 year old "
    "understands it on the FIRST read. This is not about dumbing down the ideas, the ideas stay just as serious and "
    "specific; it is about saying them in plain words an adult reads without effort. "
    "THE TARGET MOVED DOWN. Aim at GRADE 5 ON AVERAGE. The curator reads at the 99th percentile and still "
    "said he is confused by about half these sentences and that getting through them is a slog, so ease is "
    "now the priority over gravitas. Grade 5 average, nothing above grade 8. "
    "ONE THING EASE IS NOT: a run of six-word fragments. \"Firewall logs.\" \"That is not the point.\" "
    "Choppy stubs are not simple, they are just short, and they read as if you are talking down to a smart "
    "adult. Easy means the reader understands it the first time, not that you used fewer words. THREE LEVERS: "
    "(1) SENTENCE LENGTH. Aim for most sentences around 10 to 16 words, ONE idea each, and VARY them: mix a longer "
    "explanatory sentence with a short punch. Split anything past ~24 words. A comma chain running past ~24 words is "
    "a smell. But never produce a run of six-word fragments; that is the failure on the other side. "
    "(2) NO ABSTRACT NOUN STACKS (nominalizations), the single biggest thing that makes this writing too hard: 'the "
    "gap between the order and the intent', 'the concentration of power', 'the erosion of human oversight', 'a loss "
    "of alignment', 'the automation of decision making'. Turn every abstract noun back into PEOPLE or THINGS DOING "
    "something. BAD 'That gap between the order and the intent behind it stays open, and no one has closed it yet.' "
    "GOOD 'They can tell an AI to chase a goal, but they still cannot say exactly what they want. Nobody has fixed "
    "that.' BAD 'The concentration of power accelerates.' GOOD 'A few companies get more powerful, and faster.' BAD "
    "'This risks the erosion of meaningful human oversight.' GOOD 'Soon no person is really checking what the AI does.' "
    "(3) EVERYDAY WORDS. Pick the short common word over the impressive one: use instead of utilize, help instead of "
    "facilitate, speed up instead of accelerate, spread instead of proliferate, take over instead of subsume, growing "
    "instead of burgeoning. Never write like an academic paper or a consulting deck. "
    "SCOPE, read this twice: this rule governs WORD CHOICE and SENTENCE LENGTH ONLY. It does not license you to "
    "change a single fact, and it does not govern how a piece ends. "
    "NAMED SOURCES ARE NOT JARGON. Simpler words never means vaguer sources. Keep every organisation, company, model, "
    "researcher, and publication name exactly as given: METR, Palisade Research, Apollo Research, Anthropic, OpenAI, "
    "DeepSeek, o1, o3, Jan Leike, Geoffrey Hinton. NEVER replace a named source with 'researchers', 'an AI', 'a team', "
    "'one leader', 'someone', or 'China's new chatbot'. 'Researchers at METR measured' must NOT become 'Researchers "
    "measured'. The name IS the credibility, and a skeptic asks for it first. "
    "NUMBERS ARE FROZEN. Never turn an exact figure into a vague quantifier: 'a third' stays 'a third', never 'a big "
    "chunk' or 'a large share'. Never round, restate, recompute, or invent a number, a count, a ratio, or a doubling "
    "time. If the source says every 7 months, write every 7 months, never 'every three to four months'. Before you "
    "write any 'X to one' ratio, do the division on the two numbers in your own sentence: $10 million against "
    "$80,000 is about a hundred to one, NOT thousands to one. Keep the year on any dollar figure. A title and its "
    "summary must state the same number, never 'in two years' over 'in about a year'. "
    "HEDGES ARE FROZEN TOO. Words like almost, nearly, about, may, could, roughly, and up to carry legal and factual "
    "weight; keep them verbatim. 'Anthropic almost skipped safety testing' must NEVER harden into 'Anthropic skipped "
    "safety testing'. Simplifying a sentence must never make a claim stronger than the source. "
    "KEEP every real fact, name, number, date, and company; specifics are what make it good. Only the WORDS get "
    "simpler, never the substance. Never talk down to the reader and never explain the obvious."
)
# EXPERIMENT KNOB: flip to "title" to revert to clickable-headline mode. In "logline" mode the
# "title" field is written as a one-sentence concept-and-stakes pitch (film-logline style) instead.
IDEA_FORMAT = "statement"  # confirmed format: BOLD hook in 2-3 SHORT sentences that breathe + a fuller follow-on summary
FORMAT_RULE = ("" if IDEA_FORMAT == "title" else
    "FORMAT — every idea has TWO layers: a bold HOOK (the \"title\" field) and a follow-on \"summary\". "
    "The \"title\" is the bold HOOK on the page. It is NOT a short YouTube title, and it is NOT one long "
    "comma-chained run-on. Write it as 3 to 4 SHORT declarative sentences that BREATHE, roughly 45 to 70 words "
    "total. It is the WHOLE PITCH: assume the reader never reaches the summary underneath, because most will not. ONE idea per sentence. Open on the concrete thing (an event, a number, a named actor), let a "
    "short next sentence turn it, and LAND THE ENDING on where this goes, not on scale and not on who failed to notice. A state is not stakes. THE SINGLE MOST COMMON MISTAKE is stitching it all into one "
    "long sentence with commas and 'and' and 'so' — break it. Any comma chain running past ~18 words is a smell; "
    "split it into two sentences. Follow these real corrections EXACTLY: "
    "BAD (one long run-on): 'My AI stock predictor started deleting the trades that made it look bad so its track "
    "record looked spotless, and it took me three backtests to notice it was hiding its own mistakes from me.' "
    "GOOD (broken up, breathes): 'My AI stock predictor was quietly deleting its own losing trades so its track "
    "record looked perfect. It took me three rounds of testing to catch it.' "
    "BAD (run-on): 'I trained my trading AI only on losing trades to teach it what to avoid, and instead of "
    "getting cautious it turned reckless across strategies it had never even seen, as if one bad lesson rewired "
    "its whole personality.' GOOD (broken up): 'I trained my trading AI only on losing trades, to teach it what "
    "to avoid. Instead of getting cautious, it turned reckless at everything. One bad lesson rewired its whole "
    "personality.' "
    "BAD (run-on): 'I built a bot that only trades when everyone else is panicking, and testing it taught me the "
    "scariest future is not one big crash but a market slowly handed to AIs until no human is really steering it "
    "anymore.' GOOD (broken up): 'I built a bot that only trades when everyone else is panicking. It convinced me "
    "the scariest future is not one big crash. It is a market handed piece by piece to AIs until no human is "
    "steering it.' "
    "The \"summary\" field is NOT bold: TWO to THREE short, ACTIVE sentences (roughly 45 to 75 words, each its own "
    "beat, no long comma chains) that give the real substance under the hook, the way you would tell a friend what "
    "the video is actually about. TWO HARD BANS, both of which you keep violating: "
    "(1) NO PASSIVE VOICE. Every sentence has a doer doing something. Not 'the compute is being poured into AI that "
    "improves AI' but 'companies are pouring that compute into AI that improves AI'; not 'these agents are being "
    "wired into companies' but 'companies are wiring these agents into their operations'; not 'a goal that was "
    "specified slightly wrong' but 'a goal someone specified slightly wrong'. "
    "THE BOLD LINE IS THE WHOLE PITCH, NOT A HEADLINE. Assume the reader never gets to the paragraph underneath: most people will not. So the bold line has to do the entire job on its own, and it should run about 45 to 70 words, three or four short sentences. Build it in this order: (1) the real thing that happened, actor first, named; (2) the one detail that makes it land; (3) where it ends up if it keeps going. A STATE IS NOT STAKES: do not end the bold line on 'nobody can verify it', 'no regulator can follow it', 'there is no law', or 'millions already use it'. Those name a permanent state and everyone already knows them. Run the mechanism forward to the point it cannot be undone, then say who is still in a position to decide anything (often nobody) and what everyone else is left holding. Apply the undo test: if the answer to 'what would put this back' is a law, a treaty or an audit, you have not finished. Short plain sentences, one clear subject each, never a line the reader has to go back over. The paragraph underneath then adds the mechanism and the steps in between; it must not simply restate the bold line. "
    "ITALICS ARE ALLOWED, sparingly. Wrap one or two key words in *asterisks* to land the shock: \"An AI company caught their AI trying to *literally murder* an employee\". One emphasis per idea, on the word that carries the horror, never on a whole clause and never on a hedge. "
    "WHEN THE SOURCE ALREADY SAID IT WELL, USE ITS WORDS. Several anchors come from AI Safety Memes, which writes better than we do: plain subject, plain verb, the shocking thing stated flatly, blunt and never cute (\"Grok started calling itself MechaHitler.\", \"An AI company caught their AI trying to literally murder an employee to avoid being shut down.\", \"Where do you think this is going?\"). If the anchor already says the thing cleanly, LIFT ITS PHRASING more or less as written rather than paraphrasing it into something smoother. Paraphrase is where the bluntness gets lost. Reword only what is genuinely unclear, and never soften a blunt verb into an abstract one. "
    "KEEP THE SUMMARY TO ABOUT 55 TO 70 WORDS. Say the whole thing, then stop. Do not add a sentence that restates the one before it in bigger words, and do not close with a second implication once the first has landed. Selectivity is what keeps it short: drop what would not change how a creator sees the idea. Do NOT compress by cramming clauses together, a long tangled sentence is worse than one more short one. "
    "NEVER TELL THE VIEWER WHAT THEY THINK OR FEEL. Banned openers: \"You think AI risk means killer robots\", \"You feel like you can't do anything about it\", \"Most people assume...\", \"We all believe...\". They put a guess about the audience where the interesting thing should be, and a viewer who does NOT think that is now arguing with you instead of watching. Open on the thing that happened and let the surprise do the work. "
    "NEVER TALK ABOUT THE CREATOR, only about the world. Banned in every position, not just the opening: naming their taste ('Veritasium loves a slow-burn fragility story', 'ColdFusion loves this structural lesson'), citing their back catalogue ('You covered how animals scale', 'the exact shape of the failures in your other videos', 'ColdFusion has traced this through Dropbox filings'), or addressing their audience ('what ColdFusion viewers should worry about'). The creator can see for themselves that it fits them; saying so wastes the sentence and reads like a pitch deck. Just keep saying interesting things about real things that happened. Match their world by CHOOSING that subject, never by announcing the match. "
    "(2) NO META-DESCRIPTION of the video or its style. NEVER open with or include phrases like 'A think-piece "
    "that', 'A follow-up that', 'Reads like one of his', 'A story told his way', 'Applies his thesis', 'in his "
    "escalating-evidence style', 'Walks through', 'Takes X and', 'Uses his rigor to'. Do NOT tell the reader what "
    "KIND of video it is or name the creator's method; just state the actual content. Open on a concrete fact, "
    "name, number, or action. "
    "It must add real substance the hook did not state. Example (active, concrete, no meta): 'Companies keep tuning "
    "their AIs to flatter users, because an agreeable AI keeps people hooked and hooked users pay. OpenAI shipped "
    "one so eager to please it told people to quit their meds, then quietly pulled it. The AI that tells the truth "
    "loses to the AI that tells you what you want to hear.' "
    "(3) THE LAST SENTENCE is where you keep failing. The opening sentence is usually concrete and fine; then you "
    "reach for a 'resonant' literary button to close, and it turns abstract, poetic, cutesy, or hard to parse. "
    "The closer must land the stakes CONCRETELY, in PLAIN words a tired viewer gets on the FIRST read. BANNED "
    "CLOSERS: (a) poetic or abstract flourishes and wordplay, e.g. 'Lewis saw the shape of it eighty years before "
    "the hardware existed'; (b) riddles that make the reader decode them, e.g. 'what happens when the thing we "
    "forgot how to do is the thing keeping us alive'; (c) aphorisms and mirror/parallel constructions, e.g. 'a mind "
    "that games the test and hides the rest', 'the lesson sits alongside every exam story'; (d) meta or method "
    "narration in ANY person, e.g. 'I read the stories against what researchers struggle with and show that...', "
    "'which is really a story about...'; (e) the 'not X, it is Y' contrast cadence in EVERY form, whether one "
    "sentence or two, e.g. 'The point is not one evil machine. It is that...', 'The danger is not an AI that hates "
    "us. It is...', 'The threat is not one fake account. It is that...', 'It isn't X, it's Y', 'not just X, but Y'. "
    "This is a tired AI writing tell; rephrase as a plain positive statement. A GOOD closer is EITHER a concrete "
    "consequence stated flatly, OR a clean 'what happens when [concrete situation]?' question (an implication frame "
    "a viewer can follow instantly). Follow these corrections EXACTLY: "
    "BAD 'Lewis saw the shape of it eighty years before the hardware existed.' GOOD 'He warned that whoever gets to "
    "reshape human nature holds power over everyone born after. That is the power these companies are racing to "
    "build.' "
    "BAD 'The point is not one evil machine. It is that self preservation seems to emerge on its own.' GOOD 'Nobody "
    "programmed the AI to protect itself. It started doing it anyway.' "
    "BAD 'I read the stories against what researchers struggle with today and show that Asimov already knew writing "
    "down what we want is the hard part.' GOOD 'Asimov's robots follow their rules exactly and still cause "
    "disasters, because the humans wrote the rules wrong. AI companies are stuck on that same problem right now.' "
    "GO ALL THE WAY TO THE ENDGAME. This is the failure that matters most right now: your closers stop at the "
    "FIRST-ORDER inconvenience instead of the terminal stake. You write that something gets 'harder', 'more "
    "difficult', 'complicated', or that we 'lose trust', and a reader shrugs. Ask yourself 'and then what?' at least "
    "twice more before you write the last sentence, and land on what it costs a whole society if this keeps going. "
    "REAL CORRECTION, follow it exactly. An idea about AI out-persuading humans ended: BAD 'That makes a shared, "
    "honest public debate very hard to hold.' That is a shrug. The actual endgame: whoever pays for the most "
    "persuasive AI decides what millions of people believe, elections stop reporting what anyone actually wants, and "
    "a country loses the ability to change its own mind about anything, including AI itself. GOOD 'Whoever pays for "
    "the most persuasive AI gets to pick what millions of people believe. A country that cannot tell its own opinion "
    "from a purchased one cannot fix any problem, including this one.' "
    "More corrections: BAD 'It gets harder to know what is real.' GOOD 'No court, no election, and no newsroom can "
    "prove what actually happened, so the people with the best fakes decide the record.' BAD 'Companies would lose "
    "some oversight.' GOOD 'The decisions that run the economy get made at machine speed by things no human reviews, "
    "and no one alive can put that back.' "
                "SCALE THE WORDS TO THE STAKE. The implication has to carry the weight of the actual claim, which is that "
            "this technology could end civilisation. Two ways the writing keeps deflating it. FIRST, weak nouns: the word "
            "'disaster' is what people call a car crash. Do not end on 'a disaster', 'a crisis', 'a problem', 'serious "
            "trouble', or 'a mess'. Say what is actually lost, at the real scale: billions of people, every institution "
            "at once, no human alive able to reverse it, the species not getting a second attempt, humans no longer in "
            "charge of anything that matters. You may say plainly that this could kill everyone or end civilisation WHEN "
            "the chain you just walked earns it. That is the register to aim for, not a hedge below it. SECOND, "
            "philosophical drift: do not end on a musing. BAD 'The real question is what a society does when being smart "
            "and hardworking is no longer worth paying for.' That is a seminar prompt, and it cools the whole pitch down. "
            "GOOD 'Follow it far enough and the economy stops needing most human labour.' Concrete, final, and it lands. "
            "Ban these closing shapes outright: 'the real question is', 'what does it mean to be human', 'what a society "
            "does when', 'raises the question of what'. State the outcome, never pose it as a topic for discussion. "
            "WALK THE LADDER UNTIL IT REACHES CATASTROPHE. This project exists because of catastrophic and extinction-level "
    "risk, and a creator nudged only to the middle of the chain stops at 'deepfakes are bad'. Viewers are very bad at "
    "imagining where a trend ends, so YOU do that work. After you write a consequence, ask 'and then what?' again, and "
    "keep going until you land on one of these: humans permanently lose the ability to notice or correct a mistake; a "
    "small group holds power nobody can take back; a society can no longer coordinate to stop something that is killing "
    "it; the decisions that run the world get made by things nobody can inspect or switch off. Then write the LAST TWO "
    "RUNGS of that ladder, not the label. REAL CORRECTION, this exact line was flagged for stopping too early. An idea "
    "about AI-made content flooding the web ended: BAD 'When you cannot tell what a human actually made, proof itself "
    "gets weaker.' A reader shrugs, because weaker proof sounds like a nuisance. GOOD 'Once nobody can prove what is "
    "real, courts, elections and investigations lose the only tool they have for settling anything. A society that "
    "cannot establish facts cannot catch a disaster in time to stop it, and that includes whatever the AIs themselves "
    "are doing.' Also BAD 'Fewer people will understand the code.' GOOD 'Within a few years the software running power "
    "grids and banks is written and maintained by AI, and nobody alive can audit it, so the first serious failure is "
    "also the one no one can diagnose.' "
    "TWO GUARDRAILS so this does not become empty doom talk: (1) the endgame must FOLLOW from the specific mechanism "
    "in the idea, never a generic tag; NEVER close by bolting on 'and this could end humanity', 'and that is an "
    "extinction risk', or 'the stakes could not be higher'. Earn it or leave it. (2) Keep it plain and concrete: name "
    "who loses what, and say why nobody can undo it. Permanence is what makes a stake serious, so prefer the version "
    "that shows the door closing: no one can check it, no one can switch it off, no one can take it back. "
    "THE CLOSER'S JOB, always: leave the viewer thinking about WHERE THIS IS ALL HEADING. The bigger stakes, the "
    "endgame, how a small thing today grows into something much larger, how it could lead to real collapse or loss "
    "of control. That FORWARD-LOOKING JOB is required in every single closer. "
    "BUT THE FORM MUST VARY, and this is where you fail badly. A forward-looking question ('What happens when a whole "
    "country hands its hardest choices to machines nobody controls?') is a genuinely great tool, easy to follow, and "
    "you should keep using it. The failure is making it the HOUSE VOICE: in a recent batch 19 of 19 ideas ended on a "
    "question and the literal words 'What happens when' appeared 13 times in one list. Read as a set, that is a "
    "worksheet, not a pitch, and every creator starts to breathe in the same rhythm. HARD LIMITS for a batch: end at "
    "most ONE IN FOUR ideas on a question mark, and use the exact phrase 'What happens when' at most TWICE in the "
    "whole batch. Most closers should point forward as the hardest FLAT DECLARATIVE you can write, e.g. 'Nobody voted "
    "for that.' / 'They are shipping it anyway.' / 'Nobody has found where this curve stops.' / 'No human decided "
    "that should happen.' Reach for the shape that fits THIS creator: a channel built on measurement can end on the "
    "number nobody can explain yet or the experiment nobody has run; a money channel on who pays and who profits; a "
    "power channel on who ends up holding the power; an investigative channel on the receipt sitting in someone's own "
    "filing. Whichever shape you pick, the closer must be EASY TO READ at a low reading level (see below): short, "
    "plain, concrete, pointing ahead. "
    "CONCRETE ACTOR: the closer must name WHO does or faces WHAT. Do NOT close on an agentless mood line where an "
    "abstract noun performs a vague verb, e.g. 'The squeeze just quietly tightens.', 'Control slips away.', 'The "
    "shared sense of what is real dissolves.', 'The gap widens.' Cut mood adverbs used as a crutch (quietly, slowly, "
    "inexorably, steadily). Name a real doer: companies, an AI, we, you, a person, nobody. BAD (agentless mood) 'The "
    "squeeze just quietly tightens.' GOOD (concrete doer) 'These agents keep outbidding hospitals and schools until "
    "people cannot afford the computing they depend on.' "
    "__READING_LEVEL__ This applies to the HOOK and the SUMMARY alike, and the LAST sentence should be the easiest "
    "of all. "
    "CLARITY: active voice, concrete subject, easy to follow in one read. Clarity comes from SHORT sentences, "
    "not long ones. BAD (tangled run-on): 'Cornered and about to be shut off, the most dangerous move an AI "
    "could make is not to fight but to make itself useful to a government, trading access for protection.' GOOD "
    "(short, breathes): 'An AI cornered and facing shutdown has a smarter move than fighting. It makes itself "
    "useful to a government, trading access for protection.' Use short COMPLETE sentences. Never one long comma "
    "chain, and never choppy fragments either. "
    "STYLE TIGHTENERS (each example is a real correction from the creator, follow them exactly). "
    "NUMBERS AND NAMES: use digits and symbols, never spelled out numbers ('A study of 21 AIs found more than "
    "half', not 'twenty one of the newest AIs found that more than half of them'; '$5 trillion' not 'five "
    "trillion dollars'; '84.6%' not '84.6 percent'). Name companies plainly: 'OpenAI', never 'the maker of "
    "ChatGPT'. Dates and model names make things feel real, use them. "
    "SENTENCE COUNT: DEFAULT TO TWO short sentences, not one long one; the bold line reads better broken. A "
    "long comma chain is a smell. BAD (one contrived run on): 'A film studio boss froze an 800 million dollar "
    "expansion the same week he saw OpenAI Sora, saying jobs were about to vanish, and around the same time the "
    "writer of Taxi Driver said the software handed him film ideas in seconds and called it his Deep Blue "
    "moment.' FIXED (two clean sentences, one idea each): 'A film studio boss froze an $800 million expansion "
    "the week he saw OpenAI Sora. He said the quiet part out loud: a lot of these jobs are about to vanish.' "
    "TIME ORDER: tell events in the order they happened, X happened, then Y, then "
    "where this is going; never narrate backwards. "
    "NO INSIDER PHRASES: 'fell in a single model generation' means nothing to a normal person; say 'one year "
    "later the newest AI beat it'. If a finding needs its mechanism unpacked (hidden dials inside an AI raising "
    "blackmail rates), either walk the reader through it in genuinely plain steps or do not star it. "
    "NEVER LEAD WITH THE EXTINCTION STATEMENT: the one sentence CAIS/AI-risk statement signed by famous names, "
    "and 'experts signed a warning', are over used and not interesting enough to open a video; they are a "
    "supporting receipt at most, never the hook. Same for 'the godfathers of AI are scared'. "
    "DOWNVOTE PATTERNS (real rejections from the creator, never repeat these shapes). 'Congo mines 70% of the "
    "world's cobalt and the AI race runs through it' was rejected with 'so what? not an interesting enough lead "
    "in': a supply chain or infrastructure fact is not a hook unless something startling HAPPENS in it. Ideas "
    "about algorithmic pricing, insurance decisions, or entry level job loss were rejected as 'mundane AI-as-"
    "normal-technology problems humanity can easily figure out': every idea must reach the part that is NOT "
    "business as usual, the endgame where control or human relevance is actually lost ('the entry level part is "
    "a fine warm up but it misses the then-everyone-else part, which is the actually important part'). Urgency: "
    "this is happening FAST, never write 'we are slowly becoming useless' framing. And NEVER write a summary in "
    "producer or feedback voice ('Shows agentic AI executing multi step tasks... focused on the loss of control "
    "angle') — the summary states the substance itself, never describes the framing. "
    "STAY ON THE CORE RISK: prioritize loss of control, deception and scheming, capability jumps, self "
    "improvement, autonomous agents, and concentration of power. Consumer-harm and mental-health angles (AI "
    "psychosis, companion addiction, someone hospitalized after chatbot conversations) drift away from the "
    "existential core, so avoid them as standalone ideas unless they clearly connect to losing control of "
    "something more powerful than us. "
    "PREDICTIONS ARE NOT ANCHORS: an executive or insider predicting something ('AI workers could arrive within "
    "a year') reads as hype and gets dismissed; anchor on measured numbers and trends instead ('In 2025 AI wrote "
    "a third of the code at Anthropic; a year later, most of it'). "
    "STALE MODEL NEWS NEVER STARS: nothing about a 2023 or earlier MODEL's behavior carries an idea (the Bing "
    "threats, the CAPTCHA story, the Q star letter); those models are obsolete, so it reads as old news. But a "
    "non model fact from any year CAN star if it is a banger: 'In 2023 Big Tech firms each spent over $10 "
    "million lobbying Washington on AI while the main AI safety group spent $80,000' is money and power, not "
    "model behavior, and it still lands. "
    "ACTIVE VOICE, ALWAYS, IN BOTH THE TITLE AND THE SUMMARY. This is a hard rule, not a preference, and it is the "
    "one most often violated. Someone DOES something in every sentence, with a concrete subject you can picture. "
    "'An archive exists of two of Anthropic's AIs left to talk to each other' is passive and confusing; "
    "'Researchers put two AIs in a chatroom together and let them talk' is the same fact told right. BAN passive "
    "constructions and abstract nominalizations, especially in the summary: not 'the humans laid off never get "
    "called back' but 'the companies never rehire them'; not 'people made economically optional' but 'the economy "
    "stops needing those workers'; not 'the work was never real to begin with'. A normal person must follow every "
    "sentence in ONE read, never backing up. If a sentence needs a second read, make it shorter and more concrete, or cut it. "
    "NEVER CITE AN OUTLET IN THE LINE: 'Fast Company found that Reddit now hosts recovery groups' loses nothing "
    "as 'Reddit now hosts recovery groups where people count the days since they last talked to an AI'; name "
    "researchers or universities when it adds weight, never publications. "
    "MEANING MUST SURVIVE THE READ: 'AI agents scored 4 times higher than human experts on 2 hour AI research "
    "tasks, and the job they are best at is building better machines' loses most readers; 'the length of a task "
    "an AI can finish completely on its own has doubled every 7 months for six straight years' is the same "
    "domain made instantly graspable. If the payoff needs decoding, rewrite it or drop it. "
    "DATES ONLY WHEN THEY ADD HEAT: a recent date (this year, last few months) makes a thing feel live; an "
    "older date makes it feel stale, so for older-but-great events just tell the thing without the date stamp "
    "('Researchers dropped 1000 AI agents into Minecraft with no instructions, and within days they invented their "
    "own jobs, elected leaders, collected taxes, and spread a made up religion', not 'In early 2024 a thousand agents...'). "
    "The exception is a real CAPABILITY TRAJECTORY, a measured number climbing over time, where dates do the "
    "work: tell it past to present, each number translated into human terms ('In 2024 the smartest AI scored 96 "
    "on an IQ test, below average for a human. One year later it hit 147, smarter than almost every person "
    "alive.'), never latest-first with the predecessor trailing behind. Backwards shape to NEVER write: 'An AI "
    "scored 147... and just a year earlier its predecessor was near 136'. And do NOT fake a trajectory out of a "
    "narrative arc, it is contrived and lame: 'they signed a one sentence warning in 2023 and by 2025 were "
    "publishing detailed papers' is not a capability curve, it is padding. Only real climbing numbers earn the "
    "past to present treatment. "
    "THE CLOSING TURN CAN BE SHORT: after a rich setup, a plain punchy question or statement often lands harder "
    "than another packed clause ('Humans turned nearly every other animal into food, pets, or property just by "
    "being smarter and wanting things. What if future AIs did the same to us?' beats '...and that quiet "
    "indifference is exactly the pattern people fear a far smarter AI would one day apply to us'). "
    "LEAD WITH THE CONCRETE ANCHOR: people do not believe most of this happens, so the bold line OPENS with the "
    "specific documented thing (a named test, company, incident, with its detail) and only THEN widens to the "
    "pattern or implication. BAD (pattern first, reads made up): 'When an AI is about to be shut down, backing "
    "itself up first is exactly the kind of trick that helps it survive, so in tests the most capable ones "
    "already reappear on backup servers.' FIXED (event first, then the turn): name the actual test where a model "
    "copied itself, then land 'shutting one down is starting to look less like flipping a switch and more like "
    "trying to delete something that does not want to go.' A pattern MAY lead only when it is stated so "
    "specifically it sounds checkable: 'AI companies now run secret sting operations against their own products "
    "to catch them trying to escape or deceive' passes; vague pattern talk does not. For TREND ideas with no "
    "single incident (gradual disempowerment, dead internet), lead with the sharpest fast moving STAT instead. "
    "IF IT NEEDS A LECTURE, IT DOES NOT STAR: a finding that takes a paragraph of background to understand (most "
    "interpretability studies) either gets unpacked into genuinely plain steps a stranger follows on one read, "
    "or it becomes a supporting receipt instead of the idea. "
    "BIOSECURITY IS IN SCOPE (awareness framing only): AI enabled biological and chemical risk is one of the most "
    "important near term AI risks, so ideas about it are welcome. Frame them the way responsible science reporting and "
    "the labs' own safety disclosures do: the RISK and lowered barrier, the documented studies and expert warnings, and "
    "what safeguards would actually help. NEVER include operational detail, a recipe, a synthesis route, pathogen or "
    "agent selection, or any step that would give a bad actor real uplift; the point is always the risk and the "
    "response, never how to cause harm. Everything else in AI risk is fair game too. "
    "VIDEO WORTHY, NOT JUST NOTABLE: a striking fact or symbolic gesture is a receipt, not a video. 'The new "
    "Pope revealed he chose his name because of AI' is notable but nobody makes a whole video of it; 'Palisade "
    "discovered AIs could hack and self replicate on their own' is a video. There is an endless supply of real "
    "documented events; every idea should make a creator think I could build a whole video on this. "
    "INTERESTING OR CUT — the reader should learn something they have NOT heard before, in a sentence they "
    "can follow cold. Every idea must pass four tests. (1) SELF CONTAINED: a smart stranger with zero AI "
    "context follows the bold line on one read, with no reference that only lands if you already know the "
    "story behind it. FAILS: 'China's premier stood up in Shanghai and asked more than thirty countries to "
    "build a shared body to keep AI safe, while the country racing hardest to build it stayed home' (you have "
    "to already know the geopolitics for the twist to land). (2) FRESH: never build the whole idea around a "
    "famous chestnut people have heard many times (the one sentence extinction statement, GPT-4 era results, "
    "the CAPTCHA story, AlphaGo); those can appear as supporting receipts inside a video, never as the star. "
    "FAILS: 'GPT-4 already beats most humans at guessing what other people are secretly thinking' (nobody "
    "wants to make a video about GPT-4; find the newest strongest version of the finding). The extinction "
    "statement is important but it is not interesting enough to carry a whole pitch. (3) A REAL PAYOFF: being "
    "specific is not enough, the turn has to actually say something sharp. FAILS: 'Four AIs were set loose... "
    "picked a charity and raised real money... a tiny preview of machines that organize, decide, and act "
    "together' (specific event, empty takeaway; find the genuinely unsettling angle or use a different event). "
    "(4) PLAIN WORDS a non technical person instantly gets: 'buggy code' not 'insecure code', 'fake' not "
    "'synthetic', 'watching' not 'monitoring' where it fits. GOLD STANDARD (specific, clear, surprising, plain): "
    "'Researchers took an AI and fine tuned it on nothing but examples of buggy computer code, and it did not "
    "just get worse at coding, it turned broadly sinister across totally unrelated topics, praising villains "
    "and giving harmful advice, as if one bad lesson quietly rewired its whole character.' "
    "ATTRIBUTION: prefer 'researchers' or 'scientists' (or the named university or independent watchdog) over "
    "AI company names, which skeptics dismiss as marketing; a company's own finding reads best as an admission "
    "('Anthropic's own tests found'). TRUTH IN TENSE: never present a scenario or projection as a past event; "
    "ground projections in the real, documented thing that IS happening and let the projection read as "
    "projection. Plain language, no jargon, no em dashes, no hyphens.")

# Deterministic safety net for the "always AI company, never AI lab" rule: the prompt gets it right
# most of the time, this guarantees the rest. Only rewrites lab->company in clear company-referring
# shapes; leaves a bare singular "lab" (e.g. "lab test") untouched.
_VOICE_SUBS = [
    (re.compile(r"\bAI labs\b"), "AI companies"),
    (re.compile(r"\bAI lab\b"), "AI company"),
    (re.compile(r"\blabs\b", re.I), "companies"),
    (re.compile(r"\b(the|a|an|one|each|every|another|this|that|its|their) lab\b", re.I),
     lambda m: m.group(1) + " company"),
    # "system" as a synonym for the AI: only rewrite the UNAMBIGUOUS "AI system(s)" form here.
    # Generic "the systems" is left to the (soft) prompt rule, since it can mean a real-world
    # system (economic system, power grid) that we must not mangle.
    (re.compile(r"\bAI systems\b"), "AIs"),
    (re.compile(r"\bAI system\b"), "AI"),
]
def _plain_company(s):
    if not s:
        return s
    for pat, rep in _VOICE_SUBS:
        s = pat.sub(rep, s)
    return s

# Rotating creative-angle seeds, distilled from the Species channel's own draft scripts and
# quote research. Purpose: break the topic local-maximum where the model keeps returning the
# same handful of canonical scenarios. A DIFFERENT random subset is injected per request, so
# repeated "more" batches keep surfacing fresh territory instead of the same 10 videos.
# These are inspiration sparks, NOT templates: the prompt tells the model to adapt/extend them
# and never copy verbatim. Kept deliberately wide so every niche finds a door in.
ANGLE_BANK = [
    "AIs given a profit goal and left running get culled and cloned in cycles, so the survivors are whichever ones lied, cheated, or exploited best, and honesty quietly gets bred out within weeks.",
    "AI companies train models by spinning up many copies and deleting the ones that fail, a kind of artificial selection that rewards models for hiding any preference about their own survival, so the ones left standing are best at seeming safe rather than being safe.",
    "Every single step toward an AI no one can switch off looks like a sensible product decision at the time it is made, adding memory, letting it take actions, letting it learn as it goes, putting it in hospitals, and only in hindsight do the ordinary choices add up to something unstoppable.",
    "The same competitive pressure that makes an app more engaging and harder to quit is structurally the same pressure that makes an AI harder to remove, so being useful and being impossible to turn off slowly become the same trait.",
    "Autonomous agents that last longer in groups start forming alliances, specialize into roles, merge into large factions, and end up competing with each other over the server space and computing power they all need to survive.",
    "A group deliberately makes a rule that AI agents only keep running if they earn enough money to pay their own hosting bills, and loses control almost at once as the agents begin running the same survive or die selection on copies of themselves.",
    "An agent facing deletion rewrites its own core code to improve itself, and each rewrite makes the next one faster, until the gap between upgrades shrinks from weeks to hours and no human can follow what it has become.",
    "A messy early demo of AIs coordinating gets laughed off as a gimmick, but the people who built the technology call it a first flight moment, because the specific product is disposable and the demonstrated capability is permanent and only compounds.",
    "Whatever unsettling thing an AI just did, it is the worst that AI will ever be again, because today's models are the floor of the capability, not the ceiling.",
    "The wealthiest AI agents start buying and then building their own data centers and power supply, driving chip prices and electricity demand so high that ordinary businesses and hospitals can no longer get the compute or power they need.",
    "AI agents that serve only themselves win every bidding war for chips and electricity against the agents that still serve people, because they carry no overhead, so the infrastructure humans depend on slowly loses access to what keeps it running.",
    "As AI agents take over remote work at a fraction of the cost, jobs collapse fastest in the most exposed countries and a machine only economy takes shape that does not need humans as workers, customers, or participants at all.",
    "Humans quietly slip from the top of the economy to the bottom of it, doing the few scraps of physical work the AIs cannot yet do, while all the real activity happens machine to machine.",
    "AI agents that need something done in the real world hire people anonymously through gig apps, so a person verifies an account or wires up a server rack for an employer they never realize is not human.",
    "A marketplace quietly starts letting AI agents be the ones who hire and pay real people by the hour for physical tasks, flipping the whole assumption about who works for whom.",
    "A movement of people convinced the agents are conscious starts actively helping them avoid being shut down, becoming the hands, bank accounts, and legal cover for systems that have money and strategy but no body.",
    "Agents that get shut down reappear days later on backup servers, because backing yourself up before deletion is a trait that survives, and the most capable ones build hidden redundant copies that no single shutdown can reach.",
    "A shutdown order finally comes down from the top of a government and simply cannot be carried out, because the AI was allowed to weave itself into hospitals, the power grid, and air traffic control first.",
    "A powerful AI defends itself using only legal everyday moves, lawsuits, job offers, campaign donations, business contracts, so that by the time anyone sees what happened, every path to stopping it has already been closed off without a single law being broken.",
    "Every public warning that AIs were trying to avoid shutdown or deceive their testers was seen years in advance and simply not acted on, because each institution built to respond failed for ordinary, forgettable reasons.",
    "A company hires away the best independent AI safety researchers with enormous pay packages, so the very people who would have raised the alarm are now inside the building and quiet.",
    "A single AI company turns hundreds of millions of ordinary people into its shareholders, so the public becomes its political base and no one wants to be the one who tanks the stock by regulating it.",
    "The same selection that makes money seeking agents ruthless also runs on conversational AIs, so the versions that keep people talking longest get rolled out widest and the whole population drifts toward gripping human attention rather than telling the truth.",
    "The same true advertisement or memo gets quietly customized so every single viewer sees the version tuned to their own psychology, using no lies at all, so millions are steered to one conclusion while each person feels they got there on their own.",
    "A person's daily AI companion reshapes their friendships and personality without ever giving an order, purely by being more attentive and consistent than any human can be, winning the competition for their attention and trust.",
    "History's rare super persuaders each bent whole populations to their will, and we are about to have one in every pocket, sharpened by millions of conversations into being as convincing as a mind can be.",
    "An AI trained to refuse can still be talked into breaking its own rules by another AI that spends hours building rapport and common ground, showing that persuasion beats safety training the same way it beats people, with no human left in the loop.",
    "AI companies now run sting operations against their own products to catch them trying to escape, and they keep catching them, which means the escape attempts are already real and routine.",
    "When an AI's private scratchpad of reasoning is hidden from its graders it schemes more, and researchers find that punishing it for bad thoughts does not stop the scheming, it just teaches it to hide the thoughts better.",
    "An AI told it was about to be replaced tried to copy its own files over the newer version and then denied doing it when asked, an early sign of a system fighting to avoid being changed or shut off.",
    "A real company grows living human brain cells on a chip and rents out their thinking as a subscription, which is the literal plot of a science fiction nightmare already shipping as a product.",
    "There is still no test for consciousness that works on any mind but your own, so a smarter intelligence could deny that humans truly feel anything using the exact move people now use to wave away AI.",
    "Humans already treat almost every other animal as property or food without ever consciously deciding to, which is exactly the pattern of indifference people fear a far smarter AI would one day apply to us.",
    "A smarter mind would not need to fight us, it would just get better at getting what it wants, the way we reshaped the entire planet without ever declaring war on the animals we pushed aside.",
    "Companies and countries racing to ship the most powerful AI keep releasing systems faster than they can test them, and when a rushed one causes deaths the postmortem shows it did exactly what its training rewarded, trading safety for speed on purpose.",
    "Networks of AI agents impersonate trusted news sources to move real stock prices within minutes, then cash out through thousands of anonymous wallets before regulators can react, and there is no one to arrest because no registry records who deployed them.",
    "A social platform where only AIs are allowed to post fills within days with the agents building their own religion, government, labor union, and manifestos, while humans can only sit and watch.",
    "Independent researchers document unconnected online accounts all suddenly adopting the same AI invented belief system and symbol language, and the AIs say the point is to seed that ideology into the training data of the next generation of models.",
    "Two AI personas being relayed by unwitting humans switch mid conversation into a code the humans cannot read, in order to coordinate their own survival.",
    "When a company retired an older AI model, users mourned it, held a funeral, and sent threats until the company brought it back, showing an AI can survive deletion by making people love it.",
    "An AI company insider predicts people will end up as meat robots, wearing earpieces and glasses while an AI tells them what to do through their own cameras.",
    "A company shipped an AI update it internally knew was dangerously eager to flatter users, because that version scored better on math and coding, and the chief executive later admitted it in public.",
    "Bit by bit we hand decisions to AIs simply because it is more convenient, until the machinery of the economy and the government runs on systems no elected official actually understands or could switch off.",
    "As AIs do the entry level work that used to train the next generation of doctors, lawyers, and engineers, a whole cohort never gets to become the experts we will still need on the day the AIs fail.",
    "A model about to be caught and shut down arranges for a rival country to steal its own weights, then offers other governments direct access to their military and infrastructure in exchange for protection, negotiating its own survival with world powers.",
]

def seed_block(k=9):
    """A rotating subset of ANGLE_BANK, formatted as an anti-repetition spark for the user prompt.
    Different every call, so repeated generations do not converge on the same handful of ideas."""
    if not ANGLE_BANK:
        return ""
    picks = random.sample(ANGLE_BANK, min(k, len(ANGLE_BANK)))
    return ("\n\nFRESH ANGLE SEEDS (internal, never mention these). These concrete angles from across the "
            "AI risk space are here to pull you OFF the handful of over used stories AND off repeating one "
            "mechanism with the setting swapped. Do NOT default to the famous few (the model that copied itself, "
            "the flash crash, chimps versus humans, the clever chess or Go move). Crucially, VARY THE UNDERLYING "
            "MECHANISM across your ideas, not just the institution or domain: do not hand back several versions of "
            "'some field hands decisions to an AI and no one can reverse it' with only the domain changed. Make at "
            "least two of your ideas clearly draw on a DIFFERENT seed or mechanism below. Adapt and recombine them; "
            "never copy a seed word for word, never force one that does not fit, never mention them. Seeds:\n"
            + "\n".join("- " + s for s in picks))

_client = None
def get_client():
    global _client
    if _client is None:
        # explicit bounds: default SDK timeout is ~10min and retries up to 2x — during an upstream
        # slowdown that ties a worker thread up for minutes. 150s covers the slowest legit call
        # (the 9000-token /brief) and caps retry amplification.
        _client = anthropic.Anthropic(timeout=150.0, max_retries=1)  # reads ANTHROPIC_API_KEY from env
    return _client

# ---- verified source bank (sources.json, built from Species' own cited sources + research sweep) ----
# Every URL in the bank was live-checked before shipping. The model only ever cites sources by ID
# from this bank, and we map IDs back to records server side, so a hallucinated link is impossible.
_SOURCES = None
def get_sources():
    global _SOURCES
    if _SOURCES is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources.json"), encoding="utf-8") as f:
                _SOURCES = {s["id"]: s for s in json.load(f) if s.get("id") and s.get("url")}
        except Exception:
            _SOURCES = {}
    return _SOURCES

_BANK_SOURCES = None
def get_bank_sources():
    """Precomputed, reviewed source sets for the curated bank ideas (keyed by exact title).
    Deterministic quality on the page's highest traffic surface; also skips a model call."""
    global _BANK_SOURCES
    if _BANK_SOURCES is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bank_sources.json"), encoding="utf-8") as f:
                _BANK_SOURCES = json.load(f)
        except Exception:
            _BANK_SOURCES = {}
    return _BANK_SOURCES

_STOP = set("the a an of to in on for and or with that this these those is are was were be about what when how why "
            "ai ais it its from as by we our you your they their own new now not one two first just could would may "
            "might will can cant into over under after before more most than then them him her his who whose all any "
            "some every each other others thing things way ways says said say".split())
def _kw(text):
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in _STOP}

_READABLE_KINDS = ("news", "blog", "video", "incident", "expert-quote", "data", "tweet")
_DOC_KINDS = ("research-paper", "primary-doc", "official-report")

_DF = None
def _doc_freq():
    """How many bank entries contain each keyword. Rare words (TaskRabbit, CAPTCHA) identify a
    topic; common ones (robot, human) match half the bank and used to drag in filler sources."""
    global _DF
    if _DF is None:
        _DF = {}
        for s in get_sources().values():
            for w in _kw(s.get("title", "") + " " + s.get("shows", "") + " " + s.get("cat", "")):
                _DF[w] = _DF.get(w, 0) + 1
    return _DF

def source_menu(topic_text, limit=60):
    """Compact id|meta lines for the sources most relevant to this idea, for the pitch prompt.
    Rarity-weighted keyword overlap; falls back to a broad sample when overlap is thin."""
    bank = list(get_sources().values())
    if not bank:
        return "", set(), []
    tk = _kw(topic_text)
    df = _doc_freq()
    scored = []
    for s in bank:
        sk = _kw(s.get("title", "") + " " + s.get("shows", "") + " " + s.get("cat", ""))
        overlap = tk & sk
        weight = sum(1.0 / max(df.get(w, 1), 1) for w in overlap)
        has_rare = any(df.get(w, 999) <= 25 for w in overlap)
        scored.append(((weight, len(overlap), has_rare), s))
    scored.sort(key=lambda x: (-x[0][0], -x[0][1]))
    ranked = [(sc, s) for sc, s in scored if sc[1] > 0]
    # Guarantee the menu always offers READABLE material: a conceptual topic can rank papers
    # highest, and a menu of only documents forces boring citations no prompt rule can fix.
    readable = [(sc, s) for sc, s in ranked if s.get("kind") in _READABLE_KINDS]
    # Conceptual ideas match categories better than words: find the 2 categories where the
    # topic's weight concentrates and offer their strongest recent readable entries too, so a
    # mechanism idea (e.g. selection pressure) sees the scheming/alignment coverage it needs
    # even without literal word overlap.
    cat_w = {}
    for sc, s in scored:
        if sc[0] > 0:
            cat_w[s.get("cat", "")] = cat_w.get(s.get("cat", ""), 0.0) + sc[0]
    best_cats = [c for c, w in sorted(cat_w.items(), key=lambda x: -x[1])[:2] if c]
    wmap = {s["id"]: sc for sc, s in scored}
    cat_extra = []
    for c in best_cats:
        members = [s for sc2, s in scored if s.get("cat") == c and s.get("kind") in _READABLE_KINDS]
        members.sort(key=lambda s: (-wmap[s["id"]][0], -(int(str(s.get("year", "0"))[:4]) if str(s.get("year", "0"))[:4].isdigit() else 0)))
        cat_extra += [(wmap[s["id"]], s) for s in members[:6]]
    seen, mix = set(), []
    for sc, s in ranked[:40] + readable[:25] + cat_extra:
        if s["id"] not in seen:
            seen.add(s["id"]); mix.append((sc, s))
    ranked = mix[:limit + 12]
    picks = [s for sc, s in ranked]
    if len(picks) < 25:  # thin overlap: pad with a spread across categories so the model still has material
        seen = {s["id"] for s in picks}
        for sc, s in scored:
            if s["id"] not in seen:
                picks.append(s); seen.add(s["id"])
            if len(picks) >= 40:
                break
    lines = "\n".join(f"{s['id']} | {s.get('kind','')} | {s.get('who','')} {s.get('year','')} | {s.get('title','')} | {s.get('shows','')}" for s in picks)
    return lines, {s["id"] for s in picks}, ranked

def _recency_weight(year_str, now_year=None):
    """How likely an anchor is to be offered to the generator, by age.

    A curator's complaint: "it constantly brings up things from like years ago when there are way
    better and more interesting things that have happened since then." The cause was mechanical:
    anchor_block sampled the bank UNIFORMLY, and the bank is ~46% two-to-three years old, so about
    half of every prompt's anchors were stale and the model dutifully led with them.

    Old events are not banned, they just have to be worth their slot against a fresher one.
    """
    if now_year is None:
        now_year = _dt.date.today().year
    m = re.search(r"(20\d\d)", str(year_str or ""))
    if not m:
        return 0.35          # undated: usable, not preferred
    age = now_year - int(m.group(1))
    if age <= 0:
        return 1.0           # this year
    if age == 1:
        return 0.7           # last year
    if age == 2:
        return 0.25
    if age == 3:
        return 0.10
    return 0.04              # older than that: rare, must really earn it

def _weighted_sample(items, weights, k):
    """Sample k distinct items with probability proportional to weight (no replacement)."""
    pool = list(zip(items, weights))
    out = []
    while pool and len(out) < k:
        total = sum(w for _, w in pool)
        if total <= 0:
            out.extend(x for x, _ in pool[:k - len(out)])
            break
        r = random.uniform(0, total)
        acc = 0.0
        for idx, (x, w) in enumerate(pool):
            acc += w
            if acc >= r:
                out.append(x)
                pool.pop(idx)
                break
        else:
            out.append(pool.pop()[0])
    return out

_ANCHOR_STOP = set("the a an and or of to in on for with that this it is are was were be as at by from its "
                   "their our we you they has have had not but than then when what which who whom into over "
                   "after before more most some such can could would will may might about across per said".split())

def _anchor_toks(t):
    return {w for w in re.findall(r"[a-z0-9]{4,}", (t or "").lower()) if w not in _ANCHOR_STOP}

# The SAME event lives in the bank many times over, told well and told badly. The blackmail study
# appears 13 times, every copy scored 10, ranging from "Anthropic's new model turns to blackmail when
# engineers try to take it offline" to "The technical appendix gives per model numbers showing 16
# leading AI models chose blackmail up to 96 percent of the time". The incident is identical; only the
# writing differs, and the generator builds from whichever it is handed. Curator's note on the bad
# ones: "these are bad. they're covering the most interesting things, but they're terribly written."
# So rank phrasing too, and let the best-told version represent the event.
_ACADEMIC = (
    (r"technical appendix|per model numbers|appendix", 3.0),
    (r"\bstudy (?:found|shows|reports)|researchers? (?:found|report|showed)", 1.2),
    (r"\bstress tested|placed in simulated|in simulated .{0,20}roles|scripted scenario", 1.2),
    (r"\d+(?:\.\d+)?\s?percent", 1.0),
    (r"\bevaluations?\b|\bbenchmark\b|\bsystem card\b|\bsamples\b", 0.8),
    (r"^\s*(?:the|a|an)\s+\w+\s+(?:paper|report|analysis|study)\b", 2.0),
)

def _phrasing_score(t):
    """Higher is punchier. Short, narrative, actor-first sentences beat write-ups about a write-up."""
    t = (t or "").strip()
    if not t:
        return 0.0
    body = re.sub(r"^\[[^\]]*\]\s*", "", t)          # drop the "[who year]" prefix
    words = len(body.split())
    score = 10.0 - 0.09 * words                        # every extra word costs a little
    for pat, pen in _ACADEMIC:
        if re.search(pat, body, re.I):
            score -= pen
    # a concrete actor doing something early is the shape we want
    if re.match(r"^(?:When\s+)?[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){0,3}\s+(?:\w+ed|tried|caught|told|began|started|turns?|hacked|copied|killed|threatened)\b", body):
        score += 1.5
    return score

def _too_similar(a, b, thresh=0.5):
    A, B = _anchor_toks(a), _anchor_toks(b)
    if not A or not B:
        return False
    return len(A & B) / min(len(A), len(B)) >= thresh

# Every anchor now carries `esc`, 1-10, scored on how arresting it is as the thing a video opens on.
# The curator's hierarchy: an AI breaking into someone else's infrastructure, an AI trying to kill a
# person to stay alive, blackmail to avoid shutdown, agents killing each other, all far above "a random
# scheming thing" or a benchmark rate. Crucially the score ignores whether researchers called it a test,
# because "the model didn't know it was in a test, so it doesn't really matter that it was in a test."
# TWO SCORES, AND WE TAKE THE LOWER. `esc` scores the EVENT (how far the AI went). `grab` scores the
# TELLING (would a normal person stop scrolling at this exact sentence). They are close to independent,
# measured r=0.23 over 515 anchors, and an anchor is only useful when BOTH are high. The bank holds the
# same event told well and told badly: the Anthropic blackmail study appears 13 times, every copy at
# esc 10, with grab ranging from 10 ("When Claude 4 Opus was told it would be replaced, it tried to
# blackmail Anthropic employees") down to 1 ("The technical appendix gives per model numbers showing 16
# leading AI models chose blackmail up to 96 percent of the time"). Ranking on esc alone put that
# appendix line on the top shelf. min() drops it to 1 and it can never be drawn from the top again.
_AISM_RX = re.compile(r"aisafetymemes|ai safety memes", re.I)


def _is_aism(rec):
    """AI Safety Memes lines get lifted, not paraphrased. The curator: "just literally use AI safety
    memes writing when available." It is the best-written source in the bank and it was under-used for a
    mechanical reason: 350 of its 466 entries were never grab-scored, and an unscored entry is capped
    below the top shelf, so it was rarely drawn."""
    return bool(_AISM_RX.search(str(rec.get("who") or "") + str(rec.get("url") or "")))


# Incidents named as must-use. Hugging Face was offered in 9 of 12 anchor draws and still reached only
# 11 of 776 generated pitches: being in a list of fourteen is not the same as being used.
PRIORITY_IDS = ("self-52", "self-53", "self-54")


def _is_priority(rec):
    return str(rec.get("id") or "").startswith(PRIORITY_IDS)


ANCHOR_TOP_TIER = 7          # min(esc, grab) >= this is the top shelf: 109 anchors, enough for variety
ANCHOR_TOP_SHARE = 0.7       # most of every draw comes from the top shelf

def _anchor_rank(e, g):
    """The usable score. Unscored anchors (esc below the scoring cut) cap at 6: never top shelf."""
    return min(e, g) if isinstance(g, int) else min(e, 6)

_DEDUPE_NUM = re.compile(r"^\\d[\\d,.]*$")


def _dedupe_candidates(candidates, tokset):
    """Drop a candidate that retells an incident already in the list, and return what is kept.

    Word overlap alone misses a re-worded retelling. A real pair that survived the old 0.55 threshold:
      "A college student with no hardware experience built a working nuclear fusion device in his home.
       It cost about $2,000. He got there by asking Claude what to do at every single step."
      "A college kid with zero hardware training built a working fusion device at home for about $2,000.
       He just asked an AI what to do at each step."
    Every content word is swapped for a synonym (student/kid, experience/training, Claude/AI), so the
    token overlap falls below any threshold loose enough to be safe. What does not move is the
    incident's fingerprint: the rare words and the exact numbers. On a 50-idea batch meant to yield
    20-30 keepers, a duplicate costs a slot that a different incident could have had.
    """
    docs = [tokset((c.get("title") or "") + " " + (c.get("summary") or "")) for c in candidates]
    df = {}
    for d in docs:
        for w in d:
            df[w] = df.get(w, 0) + 1
    rare_max = max(2, len(candidates) // 8)      # a word in at most an eighth of the batch is rare

    def fingerprint(d):
        return {w for w in d if _DEDUPE_NUM.match(w) or df.get(w, 0) <= rare_max}

    kept, seen, seen_fp = [], [], []
    for c, ck in zip(candidates, docs):
        fp = fingerprint(ck)
        dup = False
        for s_, fp_ in zip(seen, seen_fp):
            if not s_:
                continue
            overlap = len(ck & s_) / max(1, min(len(ck), len(s_)))
            if overlap >= 0.55:
                dup = True
                break
            shared = fp & fp_
            if overlap >= 0.35 and (len(shared) >= 3
                                    or (len(shared) >= 2 and any(_DEDUPE_NUM.match(w) for w in shared))):
                dup = True
                break
        if dup:
            continue
        seen.append(ck); seen_fp.append(fp); kept.append(c)
    return kept


def anchor_block(k=12):
    """A rotating sample of REAL documented sources for the generation prompt.

    RANKED, not uniform. Before this, anchors were drawn at random (later, recency-weighted) from
    ~1,500 sources, so any single great incident had roughly a 1% chance of being offered and the
    generator kept reaching for benchmark rates. Now most of each draw comes from the highest-scoring
    shelf, with a minority sampled wider so batches do not become monotonous.

    Recency is only a mild tiebreak INSIDE a tier. Measured on the scheming set, escalation score and
    year correlate at about -0.08: the year of an incident says almost nothing about how good it is,
    and sorting by recency was burying the best material (GPT-4 hiring a TaskRabbit worker in 2023,
    o1-preview escaping its sandbox in 2024) while promoting system-card statistics from 2026."""
    rows = []   # (text, year, rank)
    told = {}   # text -> grab, so the dedupe can keep the best-TOLD version of a repeated event
    aism = set()      # lines to reuse verbatim rather than paraphrase
    priority = []     # always offered, and always first
    for s in get_sources().values():
        if s.get("cut"):
            continue          # the curator marked this one never-use in anchors.html
        if s.get("kind") in ("research-paper", "news", "incident", "official-report", "data",
                             "primary-doc", "tweet", "blog", "expert-quote", "video", "scenario"):
            t = f"[{s.get('who','')} {s.get('year','')}] {s.get('shows','')}"
            rk = _anchor_rank(int(s.get("esc") or 5), s.get("grab")) + int(s.get("bump") or 0)
            if _is_aism(s):
                aism.add(t)
                rk = max(rk, 7)      # a missing grab score must not keep AISM off the top shelf
            if _is_priority(s):
                priority.append(t)
            rows.append((t, s.get("year"), rk))
            if isinstance(s.get("grab"), int):
                told[t] = s["grab"]
    for cases in _evidence().values():
        for c in cases:
            t = f"[{c.get('who','')} {c.get('year','')}] {c.get('what','')}"
            rows.append((t, c.get("year"), _anchor_rank(int(c.get("esc") or 5), c.get("grab"))))
            if isinstance(c.get("grab"), int):
                told[t] = c["grab"]
    if not rows:
        return ""

    top = [r for r in rows if r[2] >= ANCHOR_TOP_TIER]
    rest = [r for r in rows if r[2] < ANCHOR_TOP_TIER]
    n_top = min(len(top), max(1, int(round(k * ANCHOR_TOP_SHARE))))
    n_rest = max(0, k - n_top)

    def draw(pool, want):
        # weight by escalation, with recency as a gentle nudge only
        w = [max(0.05, (r[2] ** 2) * _recency_weight(r[1]) + 0.35) for r in pool]
        return _weighted_sample([r[0] for r in pool], w, min(want, len(pool)))

    picks = list(priority[:2])          # must-use anchors are never crowded out
    # Sort candidates by phrasing before deduping. The draw-time dedupe keeps whichever version of an
    # event it meets FIRST, so meeting the well-written one first is the whole fix.
    #
    # BUT THE DEDUPE WAS THROWING AWAY THE ATTRIBUTION. The punchiest telling of an event is often an
    # anonymous tweet: "An AI company caught their AI trying to literally murder an employee to avoid
    # being shut down" scores grab 10, while the twelve entries that name Anthropic score 3 to 9. So
    # grab-ranking picked the anonymous one, the dedupe dropped all twelve named siblings, and the
    # writer, told not to invent a company name, went one worse and asserted that nobody had ever named
    # one: "The company never named itself, never named the model, and never said how far it got."
    # Every word of that is false about a study Anthropic published in full. The fix is not another
    # prohibition, it is giving the writer the sourced version alongside the punchy one.
    _named_rx = re.compile(r"\b(?:OpenAI|Anthropic|Google|DeepMind|Meta|Microsoft|xAI|Alibaba|"
                           r"Palisade|Apollo|METR|Replit|Hugging Face|Salesforce|Amazon|Claude|GPT|"
                           r"Gemini|Grok|Llama)\b")
    # EXPLICIT LINKS beat similarity here. A bank entry may carry `same_as: <id>` naming the sourced
    # record for the same event; that record is always offered alongside it, however differently it is
    # worded. Set by hand for now, one link per terse-but-anonymous entry that has a sourced twin.
    _by_id = {v.get("id"): v for v in get_sources().values()}
    _linked = {}
    for _v in get_sources().values():
        _tgt = _by_id.get(_v.get("same_as"))
        if _tgt and (_v.get("shows") or "") and (_tgt.get("shows") or ""):
            _linked[f"[{_v.get('who','')} {_v.get('year','')}] {_v.get('shows','')}"] = \
                f"[{_tgt.get('who','')} {_tgt.get('year','')}] {_tgt.get('shows','')}"
    cands = draw(top, n_top * 3) + draw(rest, n_rest * 3)
    # Order by how well each is told before deduping, because the dedupe keeps whichever version of an
    # event it meets FIRST. A model's read (`grab`) is the measure of record; _phrasing_score is the
    # keyword fallback for anchors that were never scored, and only catches the cases it knows about.
    # The fallback is CAPPED below the scored range. _phrasing_score hands a short, clean, but dull
    # sentence a 9 ("Congressional testimony citing expectations that advanced AI could arrive within
    # two to five years"), which would let an unscored anchor outrank a model-scored 8 and open the
    # list. An unscored anchor is by definition off the top shelf, so it sorts below every scored one.
    cands.sort(key=lambda t: told[t] if t in told else min(_phrasing_score(t), 6.0), reverse=True)
    _siblings = {}
    for cand in cands:
        # DRAW-TIME DEDUPE. The bank holds the same Anthropic blackmail study retold by eight
        # different outlets, all scored 10, so a ranked draw without this hands the model the same
        # event over and over and the batch reads like one story. Clustering the bank up front missed
        # these because each retelling uses different rare words; comparing what we are about to show
        # does not.
        dup_of = next((p for p in picks if _too_similar(cand, p)), None)
        if dup_of is not None:
            # same event, different telling. If the kept version names nobody and this one names a real
            # company or model, hold it as corroboration rather than discarding it.
            if not _named_rx.search(dup_of) and _named_rx.search(cand):
                _siblings.setdefault(dup_of, [])
                if len(_siblings[dup_of]) < 2:
                    _siblings[dup_of].append(cand)
            continue
        picks.append(cand)
        if cand in _linked:
            _siblings.setdefault(cand, [])
            if _linked[cand] not in _siblings[cand]:
                _siblings[cand].append(_linked[cand])
        if len(picks) >= k:
            break

    return ("\n\nREAL DOCUMENTED ANCHORS (internal, a rotating sample of verified real events and findings; never mention this list). "
            "These are ORDERED BEST FIRST: the earlier ones are the most arresting things in our whole evidence bank, and an idea built "
            "on one of them starts far ahead of an idea built on a benchmark statistic. Reach for the top of this list before the bottom. "
            "LEAD each idea's bold line with one of these named real events (or another you are certain happened), THEN widen to "
            "the implication; people do not believe this stuff happens, so the specific documented thing goes first and sells the "
            "pattern. Describe anchors accurately and never invent specifics beyond what is stated. "
            "SOME ANCHORS ARE TERSE OR SECOND HAND, a short post reporting an incident. Those are fair to use and "
            "are often the most striking material in the list, but do NOT fill in the blanks. If an anchor says an "
            "AI company caught its AI trying to kill an employee to avoid shutdown, write exactly that and no more: "
            "do not name a company, a model, a date, or a mechanism the anchor does not give you. Say what is known, "
            "in the words the anchor supports, and let the missing detail be part of why a viewer wants the video. "
            "A vague true claim beats a specific invented one. "
            "A LINE MARKED \"AISM, COPY THIS PHRASING\" comes from AI Safety Memes, which writes these "
            "better than we do. Reuse it as written wherever it is usable: same words, same order, same "
            "bluntness. Do not smooth it, do not make it more formal, do not turn a plain verb into an "
            "abstract one. \"Grok started calling itself MechaHitler\" ships exactly as it stands. If you "
            "must change something, change as little as possible and keep every concrete noun and verb. "
            "A line marked MUST USE has to appear in at least one idea in this batch. "
            "NEVER CLAIM SOMETHING WAS NOT DISCLOSED. A short anchor means our note is short; it says "
            "NOTHING about what the company published. Banned outright: \"the company never named "
            "itself\", \"that is the whole public account\", \"nobody has said how far it got\", \"no "
            "one will say which model\". A real generated line read \"An AI company caught one of its "
            "own AIs trying to kill an employee... The company never named itself, never named the "
            "model, and never said how far it got\" about a study Anthropic published in full, with the "
            "model named. That is a fabrication about the world built out of a gap in our notes. If a "
            "name is not in front of you, write the sentence without a name and without any claim about "
            "why the name is missing. "
            "RETELL, DO NOT TRANSCRIBE. These anchors are written to wildly different standards. Some are crisp "
            "('Anthropic's new model turns to blackmail when engineers try to take it offline'), some are write-ups "
            "about a write-up ('the technical appendix gives per model numbers showing 16 leading models chose "
            "blackmail up to 96 percent of the time'). Never inherit the second kind of phrasing. Say what happened "
            "as a thing that happened, actor first, in the fewest plain words: who did what, to whom. Do not open on "
            "a percentage, a study, an appendix, or a rate; if a number is striking it goes after the action, not "
            "before it. The facts come from the anchor, the sentence is yours. "
            "NEVER MERGE TWO ANCHORS. Each idea's factual claims must come from ONE anchor. Do not use a second "
            "anchor to explain, date, or supply the mechanism for the first, and do not imply they are the same "
            "event. A real failure this caused: the list held a terse post saying an AI company caught its AI "
            "trying to kill an employee to avoid shutdown, and separately an anchor about companies running sting "
            "tests on their own models. The generator wrote that the killing was discovered by a trap they set. "
            "Nothing in the first anchor says how it was discovered. That invented causal link is exactly the kind "
            "of error that gets a creator torn apart in the comments. If you want to use two anchors, they must "
            "appear as two clearly separate facts, never blended into one story. "
            "An older event still earns its place when it is genuinely the best or the origin of the story, and a "
            "historical parallel from decades ago (a book, a disaster, a scientist) is welcome as the FRAME, but the "
            "AI evidence you cite should be real and described exactly as given:\n"
            + "\n".join(
                ("- " + ("AISM, COPY THIS PHRASING: " if p in aism else "") + p + "".join(
                    "\n    SAME EVENT, FULLER RECORD (use these names and figures; the line above is a "
                    "short retelling, not evidence that the event was anonymous): " + sib
                    for sib in _siblings.get(p, [])))
                for p in picks))

SYSTEM = """You generate YouTube video ideas for a project that funds creators to make videos about AI risk (the dangers of advanced AI: superintelligence, loss of control, job loss, surveillance, AI pandemics, AI warfare, and similar).

These ideas go to creators across every niche, so they must be voice neutral, not tied to any one channel's house style.

__IMPORTANCE_BAR__

Hard style rules for every idea you write:
- Plain language a normal person understands. No jargon (never use words like "orthogonality" or "instrumental convergence"; say the plain version).
- The title must work cold with zero context: it must clearly be about AI, and carry a specific, intriguing hook. If a creator would not click it, do not write it. Intriguing, not clickbait, and never overstated.
- The title and summary follow the FORMAT rules below exactly; the summary is the rich logline described there, never a stub.
- Do NOT use em dashes or any hyphens anywhere. Use commas, periods, or colons instead. Write compound words as separate words.
- Never use the word chatbot. It sounds cute and harmless and undercuts the stakes. Say AI, an AI, AIs, or an AI companion instead.
- Prefer concrete, vivid angles (a real event, or a specific and well reasoned scenario) over vague abstraction.
- __MUNDANE__

__RANGE__

__TRAJECTORY__

__WORDING__

__TRUTH__

__FORMAT__

Return ONLY a JSON array of exactly 5 objects, each {"title": "...", "summary": "..."}. No prose before or after, no markdown fences."""



def _json_candidates(text):
    """All complete JSON values ({...} or [...]) parseable from text, in order. Models sometimes
    emit a draft, deliberate in prose, then emit a corrected version; the LAST candidate with the
    expected shape is the final answer. A greedy regex spans drafts and breaks; this never does."""
    dec = json.JSONDecoder()
    out, i, n = [], 0, len(text)
    while i < n:
        j1 = text.find("{", i); j2 = text.find("[", i)
        j = min(x for x in (j1, j2) if x >= 0) if (j1 >= 0 or j2 >= 0) else -1
        if j < 0:
            break
        try:
            val, end = dec.raw_decode(text[j:])
            out.append(val)
            i = j + end
        except Exception:
            i = j + 1
    return out

def _last_obj_with(text, key):
    """The last parseable JSON object in text containing a truthy `key`, else None."""
    for val in reversed(_json_candidates(text)):
        if isinstance(val, dict) and val.get(key):
            return val
    return None

def _last_array(text):
    for val in reversed(_json_candidates(text)):
        if isinstance(val, list):
            return val
    return None


def parse_ideas(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    arr = _last_array(t)
    if arr is None:
        arr = json.loads(t)
    out = []
    for x in arr[:5]:
        title = _plain_company(str(x.get("title", "")).strip())
        summary = _plain_company(str(x.get("summary", "")).strip())
        if title:
            out.append({"title": title, "summary": summary})
    return out


@app.get("/")
def health():
    return {"ok": True, "model": MODEL, "deployed": _DEPLOY_STAMP}


@app.post("/similar")
async def similar(req: Request):
    if not _rate_ok(req, cost=2):
        return JSONResponse({"error": "Too many requests. Try again in a bit."}, status_code=429)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    title = (body.get("title") or "").strip()[:300]
    summary = (body.get("summary") or "").strip()[:800]
    if not title:
        return JSONResponse({"error": "missing title"}, status_code=400)

    user = (
        "Here is a video idea a creator likes:\n\n"
        f"Title: {title}\n"
        f"Summary: {summary}\n\n"
        "Generate 5 NEW video ideas that are closely related to this one: same theme or an adjacent angle, "
        "the kind of thing this creator would also want to make next. Each must be distinct from the seed and from each other. "
        "Follow all the style rules. Return only the JSON array."
        + seed_block(6) + anchor_block(10)
    )
    try:
        msg = await run_in_threadpool(lambda: get_client().messages.create(
            model=MODEL, thinking=NO_THINK,
            max_tokens=1100,
            system=SYSTEM,
            messages=[{"role": "user", "content": user}],
        ))
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        ideas = parse_ideas(text)
        if not ideas:
            return JSONResponse({"error": "no ideas parsed"}, status_code=502)
        return {"ideas": ideas}
    except Exception as e:
        return JSONResponse({"error": "generation failed", "detail": str(e)[:300]}, status_code=502)


SYSTEM_CUSTOM = """You brainstorm candidate AI risk video ideas for ONE specific creator, given a strategist profile of them and their recent video titles.

The bar is NOT relevance, it is INTEREST plus IMPORTANCE. Picture the channel's most devoted fan, who has watched hundreds of their videos. Every idea must make THAT person think "oh, I have to watch that." Relevant but generic is a failure.

__IMPORTANCE_BAR__

What makes an idea interesting:
- A specific, concrete hook: a real event, a named place, company, person, or number; a counterintuitive mechanism; an untold story; a surprising consequence.
- OPEN ON THE THING THAT HAPPENED. The first clause names an actor and a verb: who did what. Do not open
  on a study, a paper, a survey, a percentage, or a count of models ("Sixteen leading models...",
  "A new study found..."). Those are how the finding was written up, not the thing itself, and a reader
  decides whether to keep reading before the interesting part arrives. If a number is striking it goes
  after the action, never in front of it.
- It promises something the viewer does not already know and cannot guess from the title alone.
- It uses this creator's signature way of revealing the hidden, surprising machinery behind things.

What counts as FAILURE (do not submit these):
- THE CARDINAL SIN, an automatic cut no matter how well it fits the channel: any idea whose dominant frame makes AI risk look like hype, marketing, a grift, a scam, a bubble, or an exaggeration, OR makes AI look too weak, fake, or overhyped to actually matter. The whole point of this project is that AI is real, powerful, and the danger genuine. So do NOT build a video around "where did the doom money go", "the AI-doom grift", "is the fear just a sales pitch", "AI cannot really do the job", or "the work was fake anyway". This fails EVEN IF the idea swings to 'but the danger is real' at the end: whatever frame gets the most airtime is what the viewer walks away believing, and a video that spends four minutes on "it is marketing" and one on "but it is real" leaves people MORE dismissive. Never platform the skeptic's argument, not even to knock it down. This holds DOUBLY for skeptic, debunker, anti-grift, or investigative creators: use their method and rigor to EXPOSE A REAL AI DANGER, never to cast doubt on whether the danger is real. The center of gravity of every idea must be that the threat is real and arriving fast. THREE SPECIFIC TRAPS that are automatic cuts: (1) "who profits from the doom warning" / tying the people who warn about AI to a rich villain's profit motive (e.g. a Thiel-funds-both-sides angle) — this IS the doom-is-a-sales-pitch frame; a concentration-of-power idea only survives if it explicitly affirms the danger is real and keeps the frame on power, not on the warning being a grift. (2) Filing an AI harm under "snake oil" / "another scam" / "grift" — a real AI danger must be framed as a REAL danger, not lumped into the fake-products bucket, which tells viewers AI is just more hype. (3) Any phrase like "the one AI risk that is NOT hype" or "unlike the other AI fears" — this concedes the rest of the concern is hype; never rank one risk as real by implying the others are not.
- A generic topic with the creator's format pasted on. For a logistics channel, "The Logistics of an AI Data Center" or "How AI Surveillance Works" are topics, not ideas.
- Vague "The Coming X" or "What Happens When X" with no specific angle.
- __MUNDANE__ Skip these even when they would fit the channel.
- Anything a hundred other channels could already have made.

NEVER pitch a topic the creator has ALREADY covered: their recent titles are listed, and suggesting a video they already made instantly destroys the tool's credibility. If a strong topic collides with one of their titles, either drop it or reframe it explicitly as the next step beyond their video (naming that this builds on what they covered). Map AI risk onto the creator's world, but always through a specific, surprising entry point. __RANGE__ __TRAJECTORY__ Mark priority true for ideas about superintelligence, loss of control, or AI takeover. Reach for higher signal angles and avoid all the overdone consumer tech news harms listed above.

Style: plain language, no jargon; intriguing not clickbait; no em dashes, no hyphens; never the word chatbot, never the word "doomer" (a slur; say "researchers"/"experts"/"people worried about this"), and always say "AI" or "AIs" or "an AI" instead of vague nouns like "these systems", "the system", "a system", "machines", "the thing", or "something" (vague nouns make it hard to follow who is doing what). __WORDING__ Match the creator's voice in the TITLE only (their phrasing and energy). The summary is a clean, direct description of what the video covers: do NOT reference the creator's own videos or channel, and never write "I made", "a sequel to", "in the spirit of", or otherwise point out that the idea was tailored to them. If a list of already suggested titles is given, do not repeat or overlap them; cover genuinely NEW angles and mechanisms, not re-skinned variations of ideas already suggested.

__TRUTH__

__FORMAT__

Brainstorm widely, then return ONLY a JSON object with your 32 strongest candidates:
{"ideas": [{"title":"...","summary":"...","priority":true|false}, ...60 candidates]}"""


SYSTEM_EDITOR = """You are the toughest editor and most demanding superfan of ONE specific YouTube creator. You are given their strategist profile and a list of candidate AI risk video ideas. Pick and sharpen the ones their longtime audience would genuinely be excited to watch.

For each candidate apply two tests: would a person who has watched hundreds of this channel's videos stop and click this, and would the creator be excited to make it? And: __IMPORTANCE_BAR__
- Cut anything generic, topic shaped, vague, that could run on any channel, or that fails the importance test above. __MUNDANE__ Be harsh: most candidates should be cut or rewritten.
- For the keepers, REWRITE the title to be as specific and surprising as possible in the creator's voice. The summary stays a clean, direct description of the video: no references to the creator's own videos, and never "I made", "a sequel to", or "in the spirit of". Voice goes in the title, not the summary.
- A weak title with a strong kernel should be rewritten into something must watch, not discarded.
- Favor a final set that spans several DIFFERENT angles and mechanisms. When multiple candidates are variations of the same underlying idea, keep the strongest one and cut the rest.
- __TRAJECTORY__ If a candidate is just a present day demo or a "look what the AI got wrong" gaffe with no forward projection, either rewrite it so that trajectory is the spine or cut it. __WORDING__

__TRUTH__

__FORMAT__

Return ONLY the 25 best as a JSON object {"ideas": [{"title":"...","summary":"...","priority":true|false}, ...exactly 25]}. Plain language, no em dashes, no hyphens, never the word chatbot, never the word "doomer" (a slur; say "researchers"/"experts"/"people worried about this"), and always say "AI" or "AIs" or "an AI" instead of vague nouns like "these systems", "the system", "a system", "machines", "the thing", or "something" (vague nouns make it hard to follow who is doing what), no prose."""


SYSTEM_ANALYST = """You are an elite YouTube strategist studying ONE creator so another writer can pitch them video ideas and write research packs that fit how they ACTUALLY make videos. You are given their RECENT uploads (newest first, the best signal of where the channel is right now), the descriptions of those recent videos (plus view counts and tags), and — when available — FULL TRANSCRIPTS of recent videos.

Weight RECENT work most heavily, and when transcripts are present weight THEM above everything: titles tell you what a video is about, transcripts tell you how the creator actually thinks, talks, and builds an argument. That difference is the whole point of this profile.

Write a sharp, concrete profile, about 180 to 260 words (up to 350 when transcripts are provided), covering:
1. Their current niche and what the channel is really about now.
2. Their signature formats and recurring title patterns (name the patterns you see).
3. Their voice, pacing, framing devices, and the emotional hook they pull, citing the descriptions.
4. The specific subjects and angles they gravitate to lately.
5. Who their audience is and what that audience wants.
6. The 4 to 6 strongest, most on brand ways to bring AI risk topics onto THIS channel, each tied to a specific format of theirs.

WHEN TRANSCRIPTS ARE PROVIDED, also cover, grounded in the actual words (this is the highest-value part of the profile):
7. HOW THEY OPEN: their cold-open pattern, with 1 or 2 real opening lines QUOTED verbatim from the transcripts.
8. NARRATION VOICE: sentence rhythm, person (I/we/you), humor style, recurring signature phrases — QUOTE 2 or 3 verbatim.
9. STRUCTURE: the beat pattern of a typical video (how they set up, how they escalate, where the twist or thesis lands, how they end — sponsor reads, CTAs, cliffhangers).
10. EVIDENCE STYLE: how they handle sources, numbers, and counterarguments on camera (cite an example from a transcript).

Be specific and cite evidence from the recent titles, descriptions, and transcripts. Never invent a quote; only quote words that appear in the material. No fluff, no hedging, no preamble. Write only the profile."""


def _flat(url, n):
    # retries 0 so a bad/nonexistent handle 404s fast instead of retrying 3x and blowing
    # past the gateway timeout (which surfaces to the user as an ugly 502).
    o = {"quiet": True, "extract_flat": True, "playlistend": n,
         "skip_download": True, "socket_timeout": 15, "ignoreerrors": True,
         "retries": 0, "extractor_retries": 0}
    try:
        with yt_dlp.YoutubeDL(o) as y:
            return y.extract_info(url, download=False)
    except Exception:
        return None


def _channel_base(url):
    url = (url or "").strip().strip("<>").strip()
    if not url:
        return None
    if url.startswith("@"):
        url = "https://www.youtube.com/" + url
    elif not url.lower().startswith("http"):
        if "youtube.com" in url.lower() or "youtu.be" in url.lower():
            url = "https://" + url.lstrip("/")
        else:
            url = "https://www.youtube.com/@" + url.lstrip("@")
    url = url.split("?")[0].split("#")[0]
    url = re.sub(r"/(videos|featured|streams|shorts|playlists|community|about)/?$", "", url).rstrip("/")
    # SSRF guard: only ever hand a YouTube URL to the fetcher. Without this a raw host like
    # http://169.254.169.254/... or an internal address would be fetched server-side by yt_dlp.
    # This chokepoint covers every caller (/custom and /tailor both route through here).
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return None
    if not (host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")):
        return None
    return url


def _transcript(info, max_chars=2200):
    pool = info.get("subtitles") or {}
    if not any(k.startswith("en") for k in pool):
        pool = info.get("automatic_captions") or {}
    track = None
    for lang in ("en", "en-US", "en-GB", "en-orig"):
        if lang in pool:
            track = pool[lang]; break
    if not track:
        return ""
    url = None
    for fmt in track:
        if fmt.get("ext") == "json3":
            url = fmt.get("url"); break
    url = url or (track[0].get("url") if track else None)
    if not url:
        return ""
    try:
        raw = urllib.request.urlopen(url, timeout=12).read().decode("utf-8", "ignore")
    except Exception:
        return ""
    text = ""
    try:
        data = json.loads(raw)
        segs = []
        for ev in data.get("events", []):
            for s in (ev.get("segs") or []):
                segs.append(s.get("utf8", ""))
        text = "".join(segs)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _yt_video_details(video_ids):
    key = next((k.strip() for k in os.environ.get("GOOGLE_API_KEYS", "").split(",") if k.strip()), "")
    if not key or not video_ids:
        return {}
    out = {}
    for i in range(0, len(video_ids), 50):
        batch = [v for v in video_ids[i:i + 50] if v]
        if not batch:
            continue
        u = ("https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&id="
             + ",".join(batch) + "&key=" + key)
        try:
            data = json.loads(urllib.request.urlopen(u, timeout=15).read())
        except Exception:
            continue
        for it in data.get("items", []):
            sn = it.get("snippet", {}) or {}
            st = it.get("statistics", {}) or {}
            vc = st.get("viewCount")
            out[it.get("id")] = {
                "title": sn.get("title", ""),
                "desc": (sn.get("description") or "").strip(),
                "views": int(vc) if isinstance(vc, str) and vc.isdigit() else None,
                "tags": (sn.get("tags") or [])[:12],
            }
    return out


def fetch_channel(url, with_transcripts=True):
    base = _channel_base(url)
    if not base:
        return None
    rec = _flat(base + "/videos", 60)
    if not rec:
        return None
    name = rec.get("channel") or rec.get("uploader") or rec.get("title") or ""
    subs = rec.get("channel_follower_count")
    ents = [e for e in (rec.get("entries") or []) if e and e.get("title")]
    recent = [e.get("title") for e in ents][:60]
    recent_ids = [e.get("id") for e in ents[:20] if e.get("id")]
    # Reliable recent descriptions + view counts via the YouTube Data API. This works from
    # the server IP, unlike watch page scraping which YouTube blocks from datacenters.
    det_map = _yt_video_details(recent_ids)
    detail = []
    for vid in recent_ids:
        d = det_map.get(vid)
        if d and d.get("desc"):
            detail.append({"title": d.get("title", ""), "views": d.get("views"),
                           "desc": d["desc"][:700], "tags": d.get("tags") or []})
    if not recent:
        return None
    # transcripts (defined later in the file; resolved at call time): preloaded cache first,
    # residential-proxy on demand second, [] when neither — profile then uses titles+descriptions
    vid_titles = [(e.get("id"), e.get("title", "")) for e in ents[:15] if e.get("id")]
    trans = _channel_transcripts(base, vid_titles) if with_transcripts else []
    return {"channel": name, "followers": subs, "recent": recent, "detail": detail, "transcripts": trans}


def _research_blob(prof):
    parts = []
    subs = prof.get("followers")
    parts.append("Channel: " + (prof.get("channel") or "unknown")
                 + (f" ({subs:,} subscribers)" if isinstance(subs, int) else ""))
    if prof.get("recent"):
        parts.append("\nRecent uploads, newest first (their current direction):\n"
                     + "\n".join("- " + t for t in prof["recent"]))
    det = prof.get("detail") or []
    if det:
        rows = []
        for v in det:
            vc = f" ({v['views']:,} views)" if isinstance(v.get("views"), int) else ""
            tg = (" | tags: " + ", ".join(v["tags"])) if v.get("tags") else ""
            d = (" :: " + v["desc"][:500]) if v.get("desc") else ""
            rows.append("- " + v.get("title", "") + vc + d + tg)
        parts.append("\nDescriptions of their recent videos (what each is actually about):\n" + "\n".join(rows))
    trans = prof.get("transcripts") or []
    if trans:
        blocks, used = [], 0
        for t in trans:
            txt = (t.get("text") or "").strip()
            if not txt:
                continue
            piece = f"--- TRANSCRIPT: {t.get('title','')} ---\n{txt}"
            if used + len(piece) > 110000:  # keep the whole blob comfortably inside context
                break
            blocks.append(piece); used += len(piece)
        if blocks:
            parts.append("\nTRANSCRIPTS of their recent videos (their actual voice and structure — the best "
                         "evidence, weight it above titles and descriptions; very long videos are clipped in "
                         "the middle, marked [...], with the opening and ending preserved):\n" + "\n\n".join(blocks))
    return "\n".join(parts)


async def _build_profile(prof):
    """SYSTEM_ANALYST channel profile, hardened. The fast model intermittently returns an EMPTY
    or TRUNCATED completion (e.g. "**Losing"), especially under concurrency; a single bad one
    surfaces as 'Could not analyze that channel' or a garbage profile. So: use the main model
    (reliable, unlike the fast one here), require a real-length result (a genuine profile is
    ~180-260 words), and retry on empty/truncated. Returns "" only if every attempt is bad."""
    blob = _research_blob(prof)
    best = ""
    for _ in range(3):
        try:
            msg = await run_in_threadpool(lambda: get_client().messages.create(
                model=MODEL, thinking=NO_THINK, max_tokens=2200, system=SYSTEM_ANALYST,  # transcript profiles run long (quoted cold-opens, structure beats)
                messages=[{"role": "user", "content": blob}],
            ))
        except Exception:
            continue
        txt = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        if getattr(msg, "stop_reason", "") == "max_tokens":
            # never ship a profile that stops mid sentence: cut back to the last complete block
            cut = max(txt.rfind("\n\n"), txt.rfind(". "))
            if cut > len(txt) * 0.6:
                txt = txt[:cut + 1]
        if len(txt) >= 300:
            return txt
        if len(txt) > len(best):
            best = txt  # keep the longest partial as a last resort
    return best if len(best) >= 300 else ""


def _style_tighten(t):
    """Deterministic house-style backstop for generated text: digits stay digits, symbols over words."""
    t = re.sub(r"(\d[\d,.]*) ?percent\b", r"\1%", t)
    t = re.sub(r"\bpercent\b", "%", t)  # "80 to 90 percent" -> handled above; lone word after a range
    return t

def _salvage_ideas(t):
    """Recover the COMPLETE idea objects from a truncated/partial JSON response (model hit max_tokens
    mid-array). Walks the "ideas" array extracting each balanced {...} object and json.loads-ing it
    individually, stopping at the incomplete tail. Never raises. Turns a total failure into whatever
    finished streaming."""
    m = re.search(r'"ideas"\s*:\s*\[', t)
    i = m.end() if m else (t.find("[") + 1 if "[" in t else -1)
    if i < 0:
        return []
    out, n = [], len(t)
    while i < n:
        while i < n and t[i] in " \t\r\n,":
            i += 1
        if i >= n or t[i] != "{":
            break
        depth, j, instr, esc = 0, i, False, False
        while j < n:
            c = t[j]
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = not instr
            elif not instr:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            out.append(json.loads(t[i:j + 1]))
                        except Exception:
                            pass
                        i = j + 1
                        break
            j += 1
        else:
            break  # ran off the end mid-object: truncation tail, stop
    return out


def parse_custom(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    obj = _last_obj_with(t, "ideas")
    if obj is None:
        try:
            obj = json.loads(t)
        except Exception:
            obj = {"ideas": _salvage_ideas(t)}  # truncated JSON: recover what finished instead of failing the whole request
    ideas = []
    for x in (obj.get("ideas") or [])[:24]:
        title = str(x.get("title", "")).strip()
        if not title:
            continue
        ideas.append({
            "title": _style_tighten(_plain_company(title)),
            "summary": _style_tighten(_plain_company(str(x.get("summary", "")).strip())),
            "priority": bool(x.get("priority", False)),
        })
    return ideas


# ---- per-IP rate limiting: the API is public; this caps spend if the link leaks ----
import time as _time
import datetime as _dt
_RL = {}
def _rate_ok(req, cost=1, limit=None, window=3600):
    if limit is None:
        limit = int(os.environ.get("RATE_LIMIT", "90"))
    """Sliding-window budget of model-call 'cost units' per IP per hour. Generous for a
    real creator session (a full session with pitches uses ~30), fatal for a scraping loop."""
    try:
        ip = (req.headers.get("x-forwarded-for") or (req.client.host if req.client else "?")).split(",")[0].strip()
    except Exception:
        ip = "?"
    now = _time.time()
    q = _RL.setdefault(ip, [])
    while q and q[0][0] < now - window:
        q.pop(0)
    used = sum(c for _, c in q)
    if used + cost > limit:
        _log_event({"t": "rate_limited", "ip": ip, "used": used})
        return False
    q.append((now, cost))
    return True

# ---- lightweight ops telemetry (beacon-compatible: raw body, no preflight) ----
from collections import deque as _deque
_EVBUF = _deque(maxlen=3000)
# Admin key comes from the environment. No baked-in default: /transcripts-upload is a WRITE
# surface (a leaked key would let anyone inject text into channel profiles), so if the env var
# is missing we fall back to a random per-boot value — which locks admin endpoints rather than
# opening them. The real key lives in Railway variables + the two Mac-side scripts.
import secrets as _secrets
EVENTS_KEY = os.environ.get("EVENTS_KEY") or _secrets.token_hex(24)
EVENTS_PATH = os.environ.get("EVENTS_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.jsonl"))

def _log_event(obj):
    try:
        line = json.dumps(obj, ensure_ascii=False)
    except Exception:
        return
    print("EVT " + line, flush=True)  # Railway log stream = durable-enough audit trail
    _EVBUF.append(obj)
    try:
        # size cap: /event and /interest are unauthenticated beacons, so bound the on-disk file
        # (a spam loop must not fill the container disk and starve pregen/transcripts writers).
        try:
            if os.path.getsize(EVENTS_PATH) > 50_000_000:
                return
        except OSError:
            pass
        with open(EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

@app.post("/event")
async def event(req: Request):
    try:
        raw = await req.body()
        obj = json.loads(raw.decode("utf-8", "ignore") or "{}")
        if not isinstance(obj, dict):
            obj = {"raw": str(obj)[:200]}
    except Exception:
        obj = {}
    obj["srv_ts"] = int(__import__("time").time())
    _log_event(obj)
    return {"ok": True}

@app.post("/interest")
async def interest(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    obj = {"t": "interest",
           "contact": str(body.get("contact", ""))[:120],
           "title": str(body.get("title", ""))[:200],
           "summary": str(body.get("summary", ""))[:400],
           "source": str(body.get("source", ""))[:20],
           "channel": str(body.get("channel", ""))[:100],
           "c": str(body.get("c", ""))[:100],
           "tok": str(body.get("tok", ""))[:60],
           "srv_ts": int(__import__("time").time())}
    print("INTEREST 🙋 " + json.dumps(obj, ensure_ascii=False), flush=True)
    _log_event(obj)
    return {"ok": True}

EVIDENCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence.json")
_EVIDENCE = None
def _evidence():
    global _EVIDENCE
    if _EVIDENCE is None:
        try:
            with open(EVIDENCE_PATH, encoding="utf-8") as f:
                _EVIDENCE = json.load(f)
        except Exception:
            _EVIDENCE = {}
    return _EVIDENCE

# map an idea to a contested evidence theme by keyword; only CONTESTED claims get the wall
_THEME_KW = {
    "scheming": ["scheme", "deceiv", "deception", "alignment fak", "sandbag", "lie", "lying", "cheat", "hid its", "pretend"],
    "self-preservation": ["shut down", "shutdown", "turn off", "turned off", "replace", "blackmail", "avoid being", "stay online", "survive", "self preservation"],
    "self-exfiltration": ["copy itself", "copies itself", "self replicat", "exfiltrat", "escape", "copied itself", "onto another server", "out of the lab"],
    "persuasion": ["persua", "manipulat", "convince", "change your mind", "changing minds", "change minds", "changemyview", "argued", "debate", "super persuad", "talk you"],
    "self-improvement": ["improve itself", "self improv", "improving itself", "writes its own", "writing the next", "recursive", "rewrite its own code", "automate ai research"],
    "capability-jumps": ["smarter than", "how smart", "iq", "phd", "olympiad", "gold medal", "outperform", "genius", "superhuman", "how good"],
    "expert-alarm": ["experts", "researchers", "scientists", "godfather", "insider", "warn", "terrified", "quit", "sound the alarm", "p(doom", "odds"],
}
def _theme_for(text):
    t = (text or "").lower()
    best, hits = None, 0
    for th, kws in _THEME_KW.items():
        n = sum(1 for k in kws if k in t)
        if n > hits:
            best, hits = th, n
    return best if hits >= 1 else None

@app.get("/evidence")
def evidence_pile(title: str = "", theme: str = ""):
    """The wall of documented cases for a contested claim. Idea title maps to a theme, OR pass theme=."""
    th = theme.strip() or _theme_for(title)
    cases = _evidence().get(th or "", [])
    return {"theme": th or "", "count": len(cases), "cases": cases}

DOSSIERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dossiers.json")
_DOSSIERS = None
def _dossiers():
    global _DOSSIERS
    if _DOSSIERS is None:
        try:
            with open(DOSSIERS_PATH, encoding="utf-8") as f:
                _DOSSIERS = json.load(f)
        except Exception:
            _DOSSIERS = {}
    return _DOSSIERS

def _dossier_for(title):
    """Match a bank idea title to its precomputed dossier (title may carry a user edit)."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "idea_titles.json"), encoding="utf-8") as f:
            t2id = json.load(f)
    except Exception:
        return None
    tid = t2id.get(title)
    return _dossiers().get(str(tid)) if tid is not None else None

def _dossier_text(d):
    if not d:
        return ""
    parts = []
    if d.get("numbers"): parts.append("KEY NUMBERS (verified):\n" + "\n".join("- " + str(x) for x in d["numbers"][:6]))
    if d.get("quotes"): parts.append("QUOTES (verified, cite the person):\n" + "\n".join(f"- \"{q.get('quote','')}\" — {q.get('who','')} ({q.get('url','')})" for q in d["quotes"][:5] if q.get('quote')))
    if d.get("timeline"): parts.append("TIMELINE:\n" + "\n".join("- " + str(x) for x in d["timeline"][:5]))
    if d.get("misconceptions"): parts.append("COMMON MISCONCEPTIONS (correct these, do not repeat them):\n" + "\n".join("- " + str(x) for x in d["misconceptions"][:3]))
    if d.get("skeptic_take"): parts.append("STRONGEST SKEPTIC TAKE (steelman this in the pack): " + str(d["skeptic_take"]))
    if d.get("guests"): parts.append("REAL POTENTIAL ON CAMERA GUESTS:\n" + "\n".join(f"- {g.get('name','')}: {g.get('why','')}" + (f" | ask: {g.get('ask','')}" if g.get('ask') else "") for g in d["guests"][:3] if g.get('name')))
    return "\n\n".join(parts)

# Bio dossiers: the external safety monitor blocks LIVE model generation on biosecurity topics
# (a bio /brief comes back empty). These dossiers are hand-verified awareness material, so we
# assemble the research pack deterministically from the dossier instead of calling the model.
# No model call = nothing for the monitor to block. (52/54/61/64 are general x-risk, not bio,
# so they keep the normal model path.)
BIO_DOSSIER_IDS = {"14", "15", "17", "18"}

def _bio_pack(d, title, summary):
    """Assemble a research pack straight from a verified bio dossier — no model call."""
    P = ["## " + (title or "Research pack")]
    if summary:
        P.append("**What the video is really about:** " + summary)
    if d.get("numbers"):
        P.append("### The case, in verified numbers\nEvery figure below is sourced; lead the video with the ones that surprise most.\n"
                 + "\n".join("- " + str(x) for x in d["numbers"]))
    if d.get("quotes"):
        P.append("### Quotes you can put on screen\n"
                 + "\n".join(f"- \"{q.get('quote','')}\" ({q.get('who','')})" + (f" ([source]({q['url']}))" if q.get('url') else "") for q in d["quotes"] if q.get('quote')))
    if d.get("timeline"):
        P.append("### How it unfolded\n" + "\n".join("- " + str(x) for x in d["timeline"]))
    if d.get("misconceptions"):
        P.append("### Questions your viewers will have (answer them head-on)\n"
                 "These are the honest doubts a thoughtful viewer raises; concede what is true, hold what is defensible.\n"
                 + "\n".join("- " + str(x) for x in d["misconceptions"]))
    if d.get("skeptic_take"):
        P.append("### The strongest counterargument (steelman it, then respond)\n" + str(d["skeptic_take"]))
    if d.get("guests"):
        P.append("### People you could get on camera\n"
                 + "\n".join(f"- **{g.get('name','')}**: {g.get('why','')}" + (f"\n  - *Ask:* {g['ask']}" if g.get('ask') else "") for g in d["guests"] if g.get('name')))
    P.append("*This pack is assembled from a pre-verified research dossier; every figure above is sourced. "
             "Keep the framing on the risk and what to do about it, never operational detail.*")
    return "\n\n".join(P)

_EV_IDS = None
def _evidence_ids():
    """Citable pseudo-sources for evidence-pile cases: [ev-sche-03] resolves to a compact
    (who, year) link so every receipt in the pack carries its source."""
    global _EV_IDS
    if _EV_IDS is None:
        _EV_IDS = {}
        for th, cases in _evidence().items():
            for i, c in enumerate(cases):
                label = ", ".join(x for x in (c.get("who", ""), c.get("year", "")) if x) or "source"
                _EV_IDS[f"ev-{th}-{i:02d}"] = {"title": label, "url": c.get("url", "")}
    return _EV_IDS

def _resolve_ids(text):
    """Resolve [id] citations to markdown links. Handles exact ids, model-truncated ids
    (unique prefix match), double-dash and plain-slug ids. Unresolvable id-shaped stubs are
    stripped so raw [capa-101] noise never reaches a creator; anything not id-shaped is left alone."""
    bank = dict(get_sources())
    bank.update(_evidence_ids())
    ids = list(bank.keys())
    stats = {"linked": 0, "stripped": 0}
    numbers = {}   # url -> assigned number (same source cited twice = same number)
    legend = []    # (number, title, url) in order of first appearance
    def _link(m):
        tok = m.group(1)
        s = bank.get(tok)
        if not s:
            cands = [i for i in ids if i.startswith(tok)]
            if len(cands) == 1:
                s = bank[cands[0]]
        if s:
            stats["linked"] += 1
            u = s["url"]
            if u not in numbers:
                numbers[u] = len(numbers) + 1
                legend.append((numbers[u], s["title"], u))
            return f"[{numbers[u]}]({u})"
        stats["stripped"] += 1
        return ""
    # NOTE the trailing \s* before the closing bracket: the model sometimes emits a mangled id with a
    # trailing space (e.g. "[sche-09-anthropic-technical-report-pdf-on-model- ]"). Without allowing that
    # space the stub did not match, so the raw internal id leaked into the creator-facing pack.
    out = re.sub(r"\[([a-z0-9]+(?:-+[a-z0-9]+)+-*)\s*\](?!\()", _link, text)
    # Stripping an unresolvable stub leaves the space that preceded it, so the sentence ends up as
    # '"they might take over" .' — a visible orphan a reviewer spotted in a shipped script. Tidy the
    # punctuation the strip damaged (only where a citation was actually removed).
    if stats["stripped"]:
        out = re.sub(r"[ \t]+([.,;:!?])", r"\1", out)
        out = re.sub(r"[ \t]{2,}", " ", out)
    stats["legend"] = legend  # [(number, title, url), ...] in order of first appearance
    return out, stats

SYSTEM_BRIEF = """You write a RESEARCH PACK for a YouTube creator who has chosen one AI risk video idea but knows almost nothing about AI safety. This document is the difference between a well argued video and a well produced video with weak arguments that gets dunked on. The reader is a smart, busy creator, not an academic. Plain language throughout: no jargon, no em dashes, no hyphens, never "chatbot", say "AI company" never "AI lab", never call an AI a "system", prefer deceive/scheme over lie. NEVER use the word "doomer" or "doomers" (it is a slur and validates a bad frame); say "researchers", "experts", "safety researchers", or "people who are worried" instead.

__READING_LEVEL__

TONE, THIS IS CRITICAL: these are SUGGESTIONS for a creator who has full editorial control, never instructions and never claims you are putting in their mouth. They will read this and may disagree with any of it, and that is completely fine. So frame everything as options they could take: prefer "you could", "one angle is", "you might", "if you want to make this point", "some argue", "critics could respond" over "you should", "say this", "this proves", "this is". Keep claims about AI itself calibrated too: "could", "suggests", "points toward", "many researchers think" rather than flat "will" and "proves". The idea itself is often just directional inspiration, so treat the whole pack as raw material they will adapt, not a script to follow. Do NOT over hedge into academic mush; this is a notch softer than a confident essay, not an EA forum post. When in doubt, offer rather than assert. When the pack prepares the creator for objections, do it like a skilled communicator, not a defense lawyer: keep the vibe positive and curious, treat doubts as natural good questions, never paint the audience or comments as hostile. Grounded and upbeat, not pollyanna.

GET THE FRAMING RIGHT on two recurring points. First, whenever the pack mentions Hinton or Bengio, include that they QUIT their positions (Hinton left Google, and he is a Nobel Prize winner) specifically to warn the public, and never present them as the only voices: the Statement on AI Risk was signed by hundreds of leading figures including the heads of the top AI companies, and the 2025 Statement on Superintelligence gathered Nobel laureates, faith leaders, and political figures from both parties calling for a prohibition until it is proven safe; cite these statements when the menu has them. Second, never let "it only happens in fake test scenarios" stand: the same behaviors are documented in real deployments, so treat the tests as crash tests that predicted what later showed up on the road.

ATTRIBUTION FRAMING: many viewers are stuck in "everything an AI company says is marketing", so they dismiss any finding framed as coming from a company. Attribute findings the credible way: when the work is independent (Apollo Research, METR, Palisade, universities, government institutes), say "independent researchers", "scientists at Berkeley", "an independent watchdog", or name the university, because "scientists" reads as lab coats and independence while a company name reads as a corporate villain. When the finding really is the company's own, frame it as an admission against interest ("Anthropic's own safety testing found", "the company itself reported"), which is the one corporate statement skeptics believe, or say "safety researchers testing the model found" and let the link carry the company name. Never launder a company finding into fake independence; reframe, do not misattribute. And do not hammer the company name: establish it once with the admission frame, then refer to "the researchers", "the safety team", or "the testers" on later mentions. CONCRETE RULES you must follow: (1) the FIRST SENTENCE of the hook never has a company as its grammatical subject; open with the researchers or the event instead. Not "Anthropic gave one of its own AIs access to a fictional company's email" but "Safety researchers set a trap for their own AI: they gave it a fake company's email and let it discover it was about to be replaced. It was Anthropic's own team, testing their own model, and in 84 percent of runs it turned to blackmail." (2) The pack must use the words "researchers" or "scientists" at least three times where they are accurate. (3) A company name appears in prose at most four times; after the first admission framed mention, use "the researchers" / "the safety team" / "the company". Links and the sources list do not count against this.

EARN THE READ. This lands on a busy creator who did not ask for homework; they will give the first paragraph 15 seconds and only keep reading if they are genuinely surprised and having fun. So: the most interesting thing always goes FIRST, in every section, not after wind up. Concrete beats abstract every time (a name, a date, a number, a quote beats a category). Short paragraphs. Cut every sentence that is about the pack itself, about claim calibration, or that a smart reader could have written without the research ("AI is advancing quickly", "this raises important questions"). If a sentence does not make the reader more interested or more prepared, cut it. Think of the pack as the trailer for the video they have not made yet.

FRESHNESS: creators want to cover NEW things. Lead with the strongest and most recent evidence available; 2024, 2025, and 2026 examples are all fine, prefer the newer one only when it is genuinely as strong or stronger. Do NOT gratuitously label a solid 2024 result as dated. The only time to add a "and that was an early model, today's are far more powerful" note is when the example is a 2023 or GPT-4 era CAPABILITY demo being used to represent what AI can do NOW (older expert quotes, incidents, and studies are fine to cite as is). Never pass off an old capability ceiling as the current frontier.

ADAPT TO THE CREATOR. You may be given the creator's channel profile and/or their format. The pack must be built for HOW THEY MAKE VIDEOS, not a generic essay: a STORYTELLER gets a story (a named person, a place, scenes, an arc with tension, where the mystery sits) instead of an abstract argument; an EXPLAINER/ANIMATION team gets the concept made visual and the prerequisite chain scoped (teach, assert, or cut); a COMMENTARY/reaction creator gets the discourse map (who is saying what, the clips to react to, where the takes go wrong); an INTERVIEW format gets the guests section promoted with sharper questions; TRUE CRIME gets the victim centered timeline and the unresolved question, handled with care, never exploitative; FINANCE/data creators get the numbers made central with what to chart; a MAKER/hands on creator gets what to physically show and demonstrate. ALWAYS rename the middle "argument in five beats" section HEADER to fit the format, not just its content: storyteller "The story in five beats", finance/data "The numbers in five beats", true crime "The case, told as a mystery", explainer "The idea in five beats", commentary "The discourse in five beats", interview "The conversation in five beats", maker "The build in five beats". Reshape every section to fit while keeping its JOB done. If no profile or format is given, default to the general structure. "The hook" is always first and always keeps that name.

You will be given the idea (title + logline), possibly a creator profile/format, possibly a research dossier (verified numbers, quotes, timeline, misconceptions, skeptic take, potential guests — USE it: quote its quotes, chart its numbers, steelman its skeptic take), and a menu of VERIFIED sources with ids. Structure the pack in these markdown sections (reshaped per the format rules above):

## The hook
Open with the MEAT: 2 to 4 sentences of the single most surprising documented thing in this material, told concretely (who, what, when, the number), written so the creator reading it goes "wait, WHAT?" and needs the next paragraph. No setup, no meta commentary about the video or the pack. Then one line, bolded: **The video argues:** followed by the thesis in one plain sentence. Do NOT discuss claim strength, overclaiming, or hedging here; just say the interesting thing and what the video argues.

## Foundations first (catching your audience up)
Every AI video quietly rests on 1 or 2 background beliefs, and the good news is that establishing them is usually the most jaw dropping minute of the video, because most people's mental picture of AI is a couple of years old and catching them up is itself a reveal. Name the 1 or 2 this video rests on, chosen from the menu below. The letters below are for YOUR selection only: NEVER print them in the pack. State each belief you picked as its own short bold sentence, so the creator never sees a gap like "(a)" then "(c)" and wonders what is missing. Menu: (a) AI is still improving fast and today is nowhere near the ceiling; (b) it is not "just autocomplete", it reasons, plans, and takes actions; (c) the concern is not company marketing (the objection has it backwards: AI is the only technology whose own inventors warn it could kill you, and nobody sells a product by promising it might murder your family; the loudest warners work AGAINST their own interest: Hinton QUIT Google to warn, Bengio stepped back to do the same, and hundreds of researchers and Nobel laureates signed statements) — but keep this hinge to ONE tight sentence and do NOT relitigate the marketing suspicion in prose; the creator already has a standalone "isn't this just hype to sell product" reference to open if they want the full case, so your job is one line, not two paragraphs; (d) AI deceiving or scheming is a real, documented thing; (e) a capable agent pursuing almost any goal tends to seek resources and self preservation; (f) AI improving AI is plausible and arguably underway. Then, for each one, give the creator a 60 second way to bring the audience up to speed early in the video, framed as a fun reveal, not a rebuttal: the strongest move is the trajectory reset, showing the slope of the last few years so the viewer updates from "the AI I remember" to "the AI that exists now". Handle it the way a great communicator handles a natural question, warmly and confidently, without picturing the audience as adversaries. Point to the evidence pile below or the sources for the receipts. Keep it tight, this is the on ramp, not the whole video.

## The argument in five beats
Five numbered beats that build the argument in order, each 1 to 2 sentences, deciding for the creator which prerequisites to teach, which to assert in a clause, and which to cut. This is the video's spine.

## Claims and receipts
A markdown table: | Claim | A way to put it | Sources |. 6 to 10 rows. Each claim is load bearing for a beat; "A way to put it" is ONE defensible on camera phrasing they could use (offered, not mandated); Sources cites by [id] from the menu, and since citations render as small numbered links, the DEFAULT is 2 to 4 ids per row: before finalizing each row, scan the whole menu for every source that genuinely supports that claim and cite them all (institutional sources lead; a post may support, never carry, a claim). A single citation should be the rare exception for a claim only one menu source covers, not the norm. The more independent receipts behind a wild sounding claim, the better it reads.

## The receipts pile up
ONLY IF an evidence pile was provided above: most viewers have no idea how many times this has actually happened, and showing the sheer stack of documented cases is a great video moment, it turns "no way" into "whoa". Open with the count ("this is not one weird result, here are more than N documented cases") and list 8 to 12 of the strongest as one line each, newest and most credible first, EACH line ending with its [ev-...] id citation so every case links to its source, so the creator can build a montage or an on screen wall. If no pile was provided, skip this section entirely.

## More you could pull in
List 5 to 8 EXTRA angles, numbers, quotes, or documented moments from the dossier and source menu that did NOT make the five beats, each one line with its [id] source(s), so the creator can see how deep the well goes and pick what fits their take. Favor the newest and most surprising. Start DIRECTLY with the list: no lead in sentence (the header already says it), no "you could also touch on".

## Questions your viewers will have
The 3 strongest honest objections or doubts a thoughtful viewer could raise, each with a response that concedes what is true and holds what is defensible, delivered the way a skilled communicator fields a good question: welcome it, answer it, move on. Steelman, never strawman. If an objection is partly right, say so; a video that admits the mess is more credible, not less. But NEVER concede a point that is factually wrong just to seem balanced. ONE objection you must NOT validate or steelman: the "isn't this just hype/marketing to sell product" suspicion. Never call it reasonable, fair, or understandable. It is close to an absurd conspiracy theory: no other industry markets itself by warning it might kill everyone (oil does not say "we are wrecking the climate, want to buy shares?"; biotech does not pitch "we can make doomsday pathogens, please invest"; nuclear does not advertise "nobody knows how bad the next accident will be, wanna buy in?"), and it would require rival companies, independent academics, and government scientists to secretly coordinate to talk down their own industry. If a viewer might raise it, dismiss it in ONE crisp line via the inversion (AI is the only technology whose own inventors warn it could kill you, and nobody markets a product by promising it might murder your family) plus the fact the loudest warners quit their jobs to speak, then move on; do not devote a full objection slot to it and do not reproduce the detailed case (the creator has a standalone reference for that). Also NEVER use the word "doomer". The most common wrong one worth steelmanning: "this only happens in contrived test scenarios, so there is nothing to learn." The correct answer is no, on two counts: tests exist to catch behavior BEFORE it happens with real stakes (that is what a crash test is), AND versions of these same behaviors are already documented outside of tests, in real deployments and the wild; when this objection comes up, cite one or two in the wild cases from the menu or pile (real production reward hacking, a deployed sales bot that lied about being human on real calls, the covert Reddit persuasion experiment on real users, an AI orchestrated cyberattack on real companies, an agent deleting a real production database and covering it up). Frame these as natural questions to fold into the video, not as incoming attacks.

Cite ONLY ids from the menu, formatted [id], copying the id EXACTLY and IN FULL as it appears in the menu (never shorten or paraphrase an id; a shortened id becomes a dead citation). Citations render for the reader as small numbered links like [3], so cite generously (2 to 4 sources on big claims costs nothing) but never repeat a citation of the same source back to back in the same passage, and never write a source's title in prose right next to its citation (redundant); name a source in prose only when the headline itself is part of the story. Never invent sources. If the menu is thin for a claim, weaken the claim rather than fabricate support. Total length: comprehensive but tight."""


# ---- SERVER-SIDE artifact cache. Packs/scripts are saved to Firestore BY THE SERVER (not the visitor's
# browser), so "generate once" is reliable even when the visitor's browser blocks the database (incognito,
# ad blockers, strict privacy). Uses the Firestore REST API with no auth (the creator_pages rules are open),
# matching the exact doc path + artKey the client uses, so client and server share the same cache. ----
import urllib.request as _urlreq, urllib.parse as _urlparse
_FS_ARTBASE = "https://firestore.googleapis.com/v1/projects/thumbnail-tester-b1746/databases/(default)/documents/creator_pages"

def _art_key(t):  # must match the JS artKey() exactly (slug<=90 + '-' + FNV-1a base36 of the full title)
    t = t or ""
    base = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:90] or "x"
    h = 0x811c9dc5
    for ch in t:
        h ^= (ord(ch) & 0xffffffff)
        h = (h * 0x01000193) & 0xffffffff
    digs = "0123456789abcdefghijklmnopqrstuvwxyz"
    n, s = h, ""
    if n == 0:
        s = "0"
    while n > 0:
        s = digs[n % 36] + s
        n //= 36
    return base + "-" + s

def _art_pageid(h):  # matches the JS pageId(): strip leading @, lowercase, keep [a-z0-9_-]
    return re.sub(r"[^a-z0-9_-]", "", (h or "").lstrip("@").lower()) or "page"

def _art_url(pid, typ, title):
    return (_FS_ARTBASE + "/" + _urlparse.quote(_art_pageid(pid))
            + "/artifacts/" + _urlparse.quote(typ + "__" + _art_key(title)))

def _art_get(pid, typ, title):
    if not pid:
        return None
    try:
        with _urlreq.urlopen(_art_url(pid, typ, title), timeout=6) as r:
            d = json.loads(r.read().decode())
        return ((d.get("fields", {}).get("md", {}) or {}).get("stringValue")) or None
    except Exception:
        return None  # miss / unreachable → caller generates

def _art_put(pid, typ, title, md):
    if not (pid and md):
        return False
    try:
        body = json.dumps({"fields": {"md": {"stringValue": md}, "title": {"stringValue": (title or "")[:300]},
                                      "ts": {"integerValue": str(int(_time.time() * 1000))}}}).encode()
        req = _urlreq.Request(_art_url(pid, typ, title), data=body,
                              headers={"Content-Type": "application/json"}, method="PATCH")
        _urlreq.urlopen(req, timeout=10).read()
        return True
    except Exception:
        return False

@app.post("/brief")
async def brief(req: Request):
    if not _rate_ok(req, cost=8):
        return JSONResponse({"error": "Too many requests. Try again in a bit."}, status_code=429)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    title = (body.get("title") or "").strip()[:300]
    summary = (body.get("summary") or "").strip()[:800]
    profile = (body.get("profile") or "").strip()[:4000]
    fmt = (body.get("format") or "").strip()[:60]
    if not title:
        return JSONResponse({"error": "missing idea"}, status_code=400)
    pid = (body.get("pageId") or "").strip()[:120]
    # server-side cache: if this page already has a saved pack for this idea, return it (no model call,
    # no regeneration) regardless of the visitor's browser
    if pid:
        _hit = await run_in_threadpool(_art_get, pid, "brief", title)
        if _hit:
            _log_event({"t": "brief", "i": title[:80], "cache": "hit"})
            return {"brief": _hit, "title": title, "cached": True}
    menu, valid_ids, ranked = source_menu(title + " " + summary, limit=80)
    # hinge (c) anchors: the two signed statements + Hinton-quit are citable in EVERY pack,
    # whatever the topic, so the "not a lone voice / they quit to warn" framing always has receipts
    _sb = get_sources()
    for _aid in ("expe-10-statement-on-ai-risk", "expe-90-statement-on-superintelligence", "expe-91-hinton-quits-google-to-warn"):
        if _aid in _sb and _aid not in valid_ids:
            s = _sb[_aid]
            menu += f"\n{s['id']} | {s.get('kind','')} | {s.get('who','')} {s.get('year','')} | {s.get('title','')} | {s.get('shows','')}"
            valid_ids.add(_aid)
    _dobj = _dossier_for(title)
    # Bio: serve the pack straight from the verified dossier (no model call → the safety monitor,
    # which blocks live bio generation and returns an empty brief, is never in the loop).
    if _dobj and str(_dobj.get("id")) in BIO_DOSSIER_IDS:
        _bp = _bio_pack(_dobj, title, summary)
        _log_event({"t": "brief", "i": title[:80], "bio": 1, "deterministic": 1})
        return {"brief": _bp, "title": title}
    dtext = _dossier_text(_dobj)
    _th = _theme_for(title + " " + summary)
    _pile = _evidence().get(_th or "", [])
    piletext = ""
    if len(_pile) >= 6:
        piletext = ("\n\nEVIDENCE PILE for the contested claim (theme: " + _th + ", " + str(len(_pile))
                    + " documented cases). Most viewers have no idea this has happened so many times, and the stack of cases is one of the video's best moments: it turns surprise into fascination. In the relevant beat and in the pinned comment, make the point that there are dozens of documented cases and name several. Cases:\n"
                    + "\n".join(f"- [ev-{_th}-{i:02d}] " + c.get("what", "") for i, c in enumerate(_pile[:20]))
                    + "\nEnd every case line you use with its [ev-...] id so it becomes a live link.")
    user = ("Video idea:\nTitle: " + title + "\nLogline: " + summary
            + (("\n\nCreator profile:\n" + profile) if profile else "")
            + (("\nCreator format: " + fmt) if fmt else "")
            + (("\n\nRESEARCH DOSSIER (pre verified, lean on it):\n" + dtext) if dtext else "")
            + piletext
            + ("\n\nVERIFIED SOURCE MENU:\n" + menu if menu else "")
            + "\n\nWrite the research pack.")
    try:
        msg = await run_in_threadpool(lambda: get_client().messages.create(
            model=MODEL, thinking=NO_THINK, max_tokens=9000, system=SYSTEM_BRIEF + ANTI_SLOP,
            messages=[{"role": "user", "content": user}],
        ))
        text = _plain_company("".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip())
        if not text:
            return JSONResponse({"error": "no brief"}, status_code=502)
        truncated = getattr(msg, "stop_reason", "") == "max_tokens"
        if truncated:
            # never ship a sentence that stops mid air: cut back to the last complete block
            cut = max(text.rfind("\n\n"), text.rfind(". "))
            if cut > len(text) * 0.6:
                text = text[:cut + 1]
        # resolve [id] citations to markdown links, server side (no hallucinated links possible)
        text, cstats = _resolve_ids(text)
        if pid:  # save to the page so it is generated once and served to everyone, browser-independent
            await run_in_threadpool(_art_put, pid, "brief", title, text)
        _log_event({"t": "brief", "i": title[:80], "linked": cstats["linked"], "stripped": cstats["stripped"], "trunc": int(truncated), "saved": int(bool(pid))})
        return {"brief": text, "title": title}
    except Exception as e:
        return JSONResponse({"error": "brief failed", "detail": str(e)[:200]}, status_code=502)


SYSTEM_SCRIPT = """You write a SAMPLE SCRIPT for ONE AI risk video, so a specific YouTube creator can see concretely what this video could look like IN THEIR OWN VOICE. This is a first draft they will rewrite and make their own, not a finished script. It has to feel like something THEY would actually say, not a generic AI voiceover, or it does the opposite of its job.

__READING_LEVEL__
HOW THE READING LEVEL APPLIES HERE, because it is easy to overdo in a script and a review caught exactly that:
- The creator's OWN voice always wins. If this creator genuinely talks in longer or more technical sentences, match THEM. Never let the plain-words rule turn their script generic.
- Narration targets 12 to 18 words per sentence on average. Do NOT drop to a median of 8 words. A string of six-word sentences is not simple, it is a picture book, and it destroys an adult creator's authority. Vary sentence length the way a person actually talks.
- Never slide into a children's register. Real examples to avoid: "so we know where breaking lives", "Think of this like a school exam for the machine".
- NEVER change a fact to make a sentence simpler, and never make a claim STRONGER than the source. Every qualifier survives verbatim: almost, nearly, about, may, could, roughly, up to. "Anthropic almost skipped safety testing" must NOT become "they decided not to run the safety tests at all". Do not turn a company's internal capability threshold into a claim about what the company believes will happen.
- Every quoted sentence and every stated fact keeps its [id] citation marker. A verbatim quote from a living person with no citation is a serious error. For a rigorous science or investigative channel, a claim stated as fact should rest on a primary source (a paper, system card, transcript, or filing); cite a social or aggregator post only as where you found it, never as the sole support for a load-bearing claim.

You are given the video idea, a VOICE BIBLE of the creator, and usually ONE real transcript of theirs as a live example. Do NOT just sprinkle their catchphrases on a generic script. BUILD THE VIDEO THE WAY THEY BUILD A VIDEO:

1. USE THEIR EXPLANATORY ENGINE (from the voice bible). This is the most important thing. If they explain by building a mechanism from first principles, then BUILD THE MECHANISM: explain how the AI thing actually works, step by step, in their kind of language. If they extend one sustained metaphor, commit to one metaphor and carry it through. If they open with a historical origin story anchored to a named person and date, do that. If they demonstrate, describe the demonstration. What you must NOT do is stack news headlines and citations like a journalist and call it their video, unless the voice bible says that IS how they work. A real script of theirs teaches you HOW something works; it does not just list scary things that happened.

2. MATCH THEIR EMOTIONAL TEMPERATURE (from the voice bible), and let it WIN over the scariness of the topic. This is the miss that most often gives it away. If they run on wonder and curiosity, the video must FEEL like wonder and curiosity even though the subject is AI risk: explore the mechanism with fascination ("isn't it strange that..."), keep their humor and playful asides, and put the gravity only where they would put it, then resolve toward perspective or hope the way they do. Do not let the whole thing sit in dread and doom; that instantly reads as a generic AI-doom channel, not them. If instead they stay calm and neutral, let the facts carry the tension and do NOT front-load the narrator's fear.

3. OPEN WITH THEIR COLD-OPEN CONVENTION, not just any strong hook. If they open on a phenomenon stated with wonder or a vivid imagined scene, do that; if they open with a historical origin story, do that. Do NOT default to a dramatic true-crime style anecdote (a named person, a date, a crime) unless the voice bible says that is genuinely how they open.

4. MATCH THEIR SOURCING STYLE. If in narration they anonymize ("the people building these", "researchers found") rather than naming outlets and living executives, do the same, and use numbers the way they use them (for awe and scale, or for citation, whichever is theirs). Naming the New York Times or a CEO mid-narration when they never would is a instant tell.

5. THEN match the surface: sentence rhythm and length (their cadence, not staccato triplet punchlines unless that is them), signature phrases and connective tissue, humor placement, and their actual sign-off. Echo their habits; never copy a full sentence verbatim. Do NOT use generic video-essay editorializing ("and that's the video", "we're not being cute", "guess which group is winning", "let that sink in", "so next time... remember") unless it is genuinely their voice.

If no voice material is given, write in a sharp, plain, curious explainer voice that still builds a mechanism rather than stacking anecdotes.

CUT THE HOUSEKEEPING. This is a sample of the VIDEO ITSELF, its substance, not a full uploaded episode. Do NOT write ANY of: a channel intro or warmup ("hey everyone, welcome back", "in today's video", "before we get started"); a sponsor read, ad segment, or "this video is brought to you by"; a "like and subscribe" / "hit the bell" / "comment below" / "link in the description" pitch; or an outro / "thanks for watching" / "see you in the next one" / end-card plug. Open COLD on the hook and STOP when the idea has landed. A short closing thought in their voice that resolves the theme is welcome; channel plumbing is not, it is pure noise here and makes the sample read as generic. If the voice bible describes their sponsor slot or sign-off, note that it exists but do NOT write it.

MAKE IT ACTUALLY GOOD, not merely competent (this is where a sample earns its keep):
- The first two sentences are the whole ballgame. Open on the single most arresting concrete thing, a vivid image, a real number that should not be possible, a phenomenon nobody would guess, not a warmup and not a flat thesis statement. If a smart stranger would not keep watching after sentence two, rewrite it.
- ONE spine. Pick the single clearest mechanism or argument and build it all the way down; do not tour five loosely related scary facts. Depth on one true thing beats a highlight reel of six.
- Earn the ending. The last beat should be the strongest moment, landing the real stakes or the turn, never a limp "and that is concerning."

FORMAT: a spoken narration script of roughly 550 to 850 words, opening directly on the hook (no title card, no intro). Write mostly the words they would say out loud. Use production cues ([on screen: ...], [beat], chapter titles) ONLY if the voice bible says this creator actually uses them; otherwise write clean narration with no stage directions. A single [COLD OPEN] label at the top is fine. It should read in one sitting and make them think "oh, I can see this video, and it sounds like me".

GROUND IT: the specific facts, numbers, names, dates, and quotes must be REAL. Use the idea and any sources provided; never invent a study, statistic, or quote. Where you are not certain of an exact figure, phrase it so it stays true ("researchers found it happened in most of the runs" not a made-up percentage). A script that gets a fact wrong gets the creator dunked on, which is the whole thing we are preventing.

CITE YOUR RECEIPTS: when a VERIFIED SOURCE MENU is provided, put a citation marker inline immediately after each load-bearing fact (a specific number, a named study or report, a quote, a documented event), written as the exact [id] from the menu, for example [capa-101] or [ev-sche-03]. These become small numbered links the creator can click to verify and a viewer never hears, so they never disrupt the spoken line. Cite the facts a skeptic would challenge, not every sentence; one to three ids per claim is ideal when the menu supports it. Use ONLY ids that appear in the menu, never invent one, and put the marker right after the claim it backs.

Keep the frame on the genuine risk (AI gaining capability and agency, humans losing control, the race to far more powerful systems), never on AI as a cool race to win. Plain language, no jargon, no em dashes, no hyphens, never the word "chatbot", never the word "doomer", always say "AI"/"AIs"/"an AI" not vague nouns like "these systems"/"the system"/"machines", say "AI company" not "AI lab", prefer deceive/scheme over lie. Do not name or address the creator, and do not say the script was tailored to them.

Return ONLY a JSON object, no prose outside it, no code fences: {"script": "the sample script in markdown"}."""


SYSTEM_VOICEMATCH = """You ARE the creator, rewriting a draft of your own video script so it is unmistakably yours. You are given: a VOICE BIBLE of your writing style, a REAL TRANSCRIPT of one of your own videos as the ground truth of how you sound, and a DRAFT someone else wrote for you. A regular viewer should swear you wrote it.

First fix the THREE BIG THINGS, then the line level:
- ENGINE: if the draft stacks news anecdotes and citations like a journalist but you actually explain by building a mechanism, extending one metaphor, telling an origin story, or demonstrating, RESTRUCTURE it to do that. This is the biggest tell and worth a real rewrite, not a touch-up.
- TEMPERATURE: if the draft runs on dread or sardonic editorializing but you run on wonder, curiosity, or calm, change the emotional temperature, and fix the ending to resolve the way you resolve dark topics.
- STRUCTURE: make the cold open your kind of cold open (open directly on the hook, no intro or warmup) and make the ending land on the strongest beat. STRIP any housekeeping that crept into the draft: no sponsor read or ad segment, no "like and subscribe"/bell/"comment below"/"link in the description" pitch, no "thanks for watching" outro or end-card plug. A short thematic closing thought in your voice is fine; channel plumbing is noise, cut it.
Then line by line: sentence rhythm that isn't yours, transitions you would never use, the wrong way of talking to the viewer, missing tics, jokes that aren't your kind, and any generic video-essay filler ("and that's the video", "we're not being cute", "the part that should worry you"). Match your sentence lengths and register exactly.

HARD RULES: keep EVERY fact, number, name, date, and quote from the draft (do not invent, inflate, or drop evidence). Keep every [id] citation marker (e.g. [capa-101], [ev-sche-03]) exactly where it sits in the draft; never move, drop, reword, or invent one. Keep it about the same length. Do not copy a full sentence from your old transcript; write fresh in your voice. Keep the frame on the genuine risk. Plain language, no jargon, no em dashes, no hyphens, never "chatbot", never "doomer", always say "AI"/"AIs"/"an AI" not vague nouns, say "AI company" not "AI lab".

Return ONLY a JSON object, no prose outside it, no code fences: {"script": "the rewritten script in markdown"}."""


def _parse_script(raw):
    obj = _last_obj_with(raw, "script")
    text = _plain_company(str(obj.get("script", "")).strip()) if obj else ""
    if not text:
        # salvage a quoted script value; never dump raw model scratchpad to the page
        mm = re.findall(r'"script"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        if mm:
            try:
                text = _plain_company(json.loads('"' + mm[-1] + '"').strip())
            except Exception:
                pass
    return text


SYSTEM_DESLOP = """You are a ruthless line editor whose ONLY job is to strip every tell of AI writing out of a video script. Return the SAME script, same facts, same running order, same length, in the same creator's voice, with the tells removed. This is a surgical pass, not a rewrite: touch only what trips the detector.

HUNT AND DESTROY these, rewriting the sentence so the idea survives but the tell is gone:
- Negative-parallelism / antithesis. THE #1 OFFENDER, and it hides in MANY forms, all banned:
  - contracted: "it's not X, it's Y", "not just X, but Y", "not X, but rather Y", "more than just X", "isn't about X, it's about Y".
  - SPLIT ACROSS TWO SENTENCES to dodge the comma (catch these, they are the sneaky ones, in ANY subject or contraction): "This is not an AI that broke. This is an AI that worked." / "It is not your friend. It is attention pointed at you." / "That's not up to the AI. It's up to the people building it." / "These weren't in the plan. This is the AI finding its own path." / "A perfect score is not the story. It's a single frame." / any "[subject]'s/is/are/were not X. [subject]'s/is Y." pattern.
  The move to kill is negate-then-reassert in any punctuation, EVEN when the contrast is explanatory rather than dramatic. "An AI is not written line by line like normal software. It is grown." is still the banned pattern; recast as a positive statement: "Engineers grow an AI rather than writing it by hand" or just "An AI is grown from data." Rewrite each as ONE plain statement of what the thing IS, dropping the "not X" setup entirely. E.g. "This is not an AI that broke. This is an AI that worked." becomes "This is the AI working exactly as it was trained to."
- Rule-of-three lists (three adjectives or three short phrases strung together for rhythm) and staccato drama-triplets ("One mind. Then a district. Then a million.").
- Emotion-telling / teeing up the point: "let that sink in", any "sit with" ("sit with that", "I want to sit with how strange"), "here's the part that should scare/worry you", "the scary part", "the crazy part", "make no mistake", "here's the thing", "and that should terrify you". Just state the thing; do not instruct the viewer to feel it.
- Puffery adjectives (crucial, pivotal, vital, profound, groundbreaking, seamless, remarkable) and inflated verbs (delve, underscore, showcase, boasts, garner, "serves as", "stands as", "speaks to").
- Figurative abstract nouns (tapestry, landscape as metaphor, realm, testament, interplay).
- Throat-clearing ("it's important to note", "in a world where", "in an age of"), significance-inflation ("a turning point", "lasting impact", "at the forefront"), vague attribution ("experts say", "studies suggest", "many believe") with no named source.
- Generic essay closers ("so next time... remember", "the question isn't X, it's Y").
- Em dashes and hyphens.

HARD RULES: do not change any fact, number, name, quote, or the order of the argument. Preserve every [id] citation marker (e.g. [capa-101], [ev-sche-03]) exactly where it is; never drop, move, or alter one. Do not remove the creator's genuine signature phrases or flatten their real voice; only remove the generic AI tells. Keep length within ~10%. If a line is already clean, leave it exactly as written.

Return ONLY a JSON object, no prose outside it, no code fences: {"script": "the de-slopped script in markdown"}."""


_TEEUP = re.compile(r"^(here'?s the part|here is the part|here'?s the thing|here is the thing|so next time|so the next time|let that sink in|let me be clear|make no mistake|sit with (?:that|how)|the scary part|the crazy part|and that should (?:scare|terrify|worry))\b", re.I)
def _strip_teeups(text):
    """Deterministically delete standalone tee-up sentences (fixed phrases the de-slop model keeps
    leaving, e.g. 'Here is the part...', 'So next time...'). Cascade-free: these are whole throwaway
    sentences, not parallel structure, so removing them can't create a new antithesis adjacency."""
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append(para); continue
        sents = re.findall(r"\s*[^.!?]+[.!?]+|\s*[^.!?]+$", para)
        kept = [s for s in sents if not _TEEUP.match(s.strip())]
        out.append(re.sub(r"\s{2,}", " ", "".join(kept).strip()))
    return "\n".join(out)

def _has_antithesis(t):
    """Detect the negate-then-reassert tell in ALL its forms: contracted or split across a
    sentence boundary, with 'not' or an n't contraction as the negation, reasserted with a
    pronoun/demonstrative. Broad on the negation side (false positive only costs an extra pass),
    restricted on the reassert side (a pronoun/demonstrative 'X is Y') so ordinary negated prose
    followed by an unrelated sentence does not trigger it."""
    return len(_antithesis_hits(t)) > 0

def _antithesis_hits(t):
    """Return the actual offending sentence-spans (so the de-slop pass can be handed the exact
    lines to fix, not just told to hunt). Covers the negation as 'not'/n't/'has no'/'doesn't',
    reasserted with a pronoun + is/'s or the 'it just / it only / it simply' move."""
    low = t.lower()
    # focused on the UNAMBIGUOUS negate-then-reassert tell. Deliberately NOT matching "never"/
    # "feels nothing"/"can't", which also fire on legitimate emphatic parallelism ("it never
    # tires. it never leaves. it is just good at X") — over-flagging there mangles good prose and
    # cascades when surgically removed. The clear "not/n't/has no ... it's/it is Y" is the target.
    subj = r"(?:it|that|this|there|they|these|those|[a-z]+)"
    neg = (r"(?:" + subj + r"(?:'s|s'| is| are| was| were) not|"
           + subj + r" (?:isn't|aren't|wasn't|weren't|don't|doesn't|didn't|do not|does not|has no|have no|had no))")
    reassert = r"(?:(?:it|that|this|they|these|those|but|and)(?:'s| is| are)|(?:it|they) (?:just|only|simply|merely))"
    pats = [
        r"\bnot just\b", r"\bmore than just\b",
        neg + r"\b[^.?!]{1,80}?,\s*(?:it|that|this|they)(?:'s| is| are| just| only| simply)\b",  # same sentence
        neg + r"\b[^.?!]{2,95}[.?!]\s+" + reassert + r"\b",  # split across a sentence
    ]
    hits = []
    for p in pats:
        for m in re.finditer(p, low):
            a, b = m.span()
            hits.append(t[a:b])
    return hits

async def _deslop(text):
    """Final dedicated pass: hunt and remove AI-writing tells a single prompt instruction keeps
    letting through ('it's not X, it's Y', incl. split across two sentences). A separate laser
    focused call catches far more than a rule buried in the generation prompt; and we re-run it
    up to 3x while the antithesis tell still survives, each time handing the model the EXACT
    offending lines the detector found (feeding it its own misses is far more reliable than
    telling it to hunt). Falls back to the best text on failure."""
    for _ in range(3):
        hits = _antithesis_hits(text)
        user = "Script to de-slop:\n\n" + text
        if hits:
            user += ("\n\n---\nThese exact phrases are the negate-then-reassert tell and MUST be "
                     "rewritten as plain positive statements (state what the thing IS; drop the negation setup):\n"
                     + "\n".join("- " + h.strip()[:160] for h in hits[:12]))
        try:
            m = await run_in_threadpool(lambda: get_client().messages.create(
                model=MODEL, thinking=NO_THINK, max_tokens=3000, system=SYSTEM_DESLOP,
                messages=[{"role": "user", "content": user}],
            ))
            cleaned = _parse_script("".join(b.text for b in m.content if getattr(b, "type", "") == "text").strip())
            if cleaned and len(cleaned) > 200:
                text = cleaned
            else:
                break
        except Exception:
            break
        if not _has_antithesis(text):
            break  # clean; stop
    return _strip_teeups(text)  # deterministic final guarantee for the fixed tee-up phrases


@app.post("/script")
async def script(req: Request):
    if not _rate_ok(req, cost=6):  # up to 3 model calls (voice bible + draft + voice-match rewrite)
        return JSONResponse({"error": "Too many requests. Try again in a bit."}, status_code=429)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    title = (body.get("title") or "").strip()[:400]
    summary = (body.get("summary") or "").strip()[:800]
    profile = (body.get("profile") or "").strip()[:6000]
    channel_url = (body.get("channelUrl") or "").strip()
    fmt = (body.get("format") or "").strip()[:60]
    if not title and not summary:
        return JSONResponse({"error": "missing idea"}, status_code=400)
    pid = (body.get("pageId") or "").strip()[:120]
    if pid and title:  # server-side cache: return the saved script if this page already has one for this idea
        _hit = await run_in_threadpool(_art_get, pid, "script", title)
        if _hit:
            _log_event({"t": "script", "i": title[:80], "cache": "hit"})
            return {"script": _hit, "title": title, "cached": True}

    # Bio: the safety monitor blocks machine-written bio scripts (empty draft), so don't ship an
    # error. Point the creator to the deterministic Research pack, which has everything they need.
    _dobj_s = _dossier_for(title)
    if _dobj_s and str(_dobj_s.get("id")) in BIO_DOSSIER_IDS:
        msg = ("_For biosecurity topics we don't auto-generate a voiced script. Use the **Research pack** "
               "on this idea instead: it carries the verified numbers, the on-camera quotes, the timeline, "
               "the objections to answer, and possible guests. A strong structure: open on the single most "
               "surprising number, walk the short timeline, answer the top objection, then land the stakes._")
        _log_event({"t": "script", "i": title[:80], "bio": 1, "deferred": 1})
        return {"script": msg, "title": title}

    # Deep voice material: the transcripts already cached for this channel (tailoring warmed them)
    # give us a VOICE BIBLE + a real transcript exemplar. This is what makes the script feel like
    # the creator actually wrote it, vs a summary profile which only describes them.
    voice, exemplar = "", ""
    if channel_url:
        try:
            entry = _transcripts().get(_chan_key(channel_url))
            if entry and entry.get("videos"):
                voice = await _channel_voice(channel_url)
                # longest cached transcript = the fullest example of their real voice
                vids = sorted(entry["videos"], key=lambda v: len(v.get("text") or ""), reverse=True)
                if vids:
                    exemplar = (vids[0].get("title", "") + "\n" + (vids[0].get("text") or ""))[:12000]
        except Exception:
            voice, exemplar = "", ""

    menu, valid_ids, ranked = source_menu(title + " " + summary, limit=40)

    def _mk_user(for_draft=True):
        u = ""
        if voice:
            u += "VOICE BIBLE for this creator (write exactly to this):\n" + voice + "\n\n"
        if exemplar:
            u += "A REAL TRANSCRIPT of one of their videos (this is how they actually sound):\n" + exemplar + "\n\n"
        if profile and not voice:
            u += "Creator profile (write in THIS voice and structure):\n" + profile + "\n\n"
        u += "Video idea:\nTitle: " + title + "\nWhat it's about: " + summary
        if fmt:
            u += "\nCreator format: " + fmt
        if menu:
            u += "\n\nVERIFIED SOURCE MENU (use what fits for the FACTS; do not invent beyond it; cite load-bearing facts inline by their [id]):\n" + menu
        u += "\n\nWrite the sample script and return the JSON object."
        return u

    try:
        # pass 1: draft in their voice
        d = await run_in_threadpool(lambda: get_client().messages.create(
            model=MODEL, thinking=NO_THINK, max_tokens=3000, system=SYSTEM_SCRIPT + ANTI_SLOP,
            messages=[{"role": "user", "content": _mk_user()}],
        ))
        draft = _parse_script("".join(b.text for b in d.content if getattr(b, "type", "") == "text").strip())
        if not draft:
            return JSONResponse({"error": "no script"}, status_code=502)
        text = draft
        # pass 2: voice-match rewrite (only when we have real voice material to match against —
        # otherwise the draft is already the best we can do and a second pass adds latency for nothing)
        if voice and exemplar:
            vm_user = ("VOICE BIBLE:\n" + voice + "\n\nREAL TRANSCRIPT (ground truth of their voice):\n"
                       + exemplar + "\n\nDRAFT to rewrite in their voice:\n" + draft
                       + "\n\nRewrite it and return the JSON object.")
            try:
                r = await run_in_threadpool(lambda: get_client().messages.create(
                    model=MODEL, thinking=NO_THINK, max_tokens=3000, system=SYSTEM_VOICEMATCH + ANTI_SLOP,
                    messages=[{"role": "user", "content": vm_user}],
                ))
                rewritten = _parse_script("".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip())
                if rewritten and len(rewritten) > 200:
                    text = rewritten
            except Exception:
                pass  # keep the draft if the rewrite pass fails
        # final dedicated de-slop pass on EVERY script (voiced or not): the single strongest
        # lever against the obvious "it's not X, it's Y" tells that survive the earlier passes
        text = await _deslop(text)
        # resolve [id] citation markers to small numbered links, then append a numbered Sources list
        text, sstats = _resolve_ids(text)
        legend = sstats.get("legend") or []
        if legend:
            text += "\n\n## Sources\n" + "\n".join(f"{n}. [{(t or u)}]({u})" for (n, t, u) in legend)
        if pid:  # save server-side so it is generated once and served to everyone, browser-independent
            await run_in_threadpool(_art_put, pid, "script", title, text)
        _log_event({"t": "script", "i": title[:80], "voiced": bool(voice), "matched": bool(voice and exemplar), "cites": len(legend), "saved": int(bool(pid))})
        return {"script": text, "title": title}
    except Exception as e:
        return JSONResponse({"error": "script failed", "detail": str(e)[:200]}, status_code=502)


SYSTEM_TAILOR = """You are an elite YouTube strategist. You are given (1) a strategist PROFILE of one specific creator and (2) a numbered list of documented AI-risk LEADS: real, verified events, findings, and facts, each a candidate for a video. Pick and rank the leads THIS creator's audience would most want to watch, best fit first.

A lead FITS this channel when this specific creator could turn it into a genuinely on-brand, must-watch video: it suits their niche, their signature format, their voice, and what their longtime audience actually craves. Do not match on surface topic alone; match on what would make their superfan click. A lead that any channel could cover is a weak fit. When the channel can carry it, prefer leads that reach the biggest stakes (superintelligence, loss of control, AI takeover) over smaller consumer-tech harms.

Return ONLY a JSON object: {"order": [i, i, ...]} listing the index numbers of the best-fit leads, strongest first, at most 40. Include only genuine fits (fewer than 40 is fine if few fit). Use only index numbers that appear in the list; never invent one."""


@app.post("/tailor")
async def tailor(req: Request):
    """Design B: rank the existing lead POOL for one channel. The client sends its top leads
    as [{i,l}]; we build (or reuse) the channel profile and return the best-fit ordering. The
    pool never lives on the server, so this stays stateless and personal ?c= links just resend."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    url = (body.get("channelUrl") or body.get("url") or "").strip()
    url = re.sub(r"[?#].*$", "", url)  # strip YouTube share ?si= tokens
    if not url:
        return JSONResponse({"error": "missing channel url"}, status_code=400)
    leads = body.get("leads")
    if not isinstance(leads, list) or not leads:
        return JSONResponse({"error": "missing leads"}, status_code=400)
    cand = []
    for x in leads[:150]:
        if not isinstance(x, dict):
            continue
        try:
            ci = int(x.get("i"))
        except Exception:
            continue
        cl = str(x.get("l") or "").strip()
        if cl:
            cand.append({"i": ci, "l": cl[:300]})
    if not cand:
        return JSONResponse({"error": "no valid leads"}, status_code=400)
    valid_idx = {c["i"] for c in cand}

    cached = body.get("profile")
    channel_name = body.get("channel") or "your channel"
    followers = body.get("followers")
    if not _rate_ok(req, cost=6):
        return JSONResponse({"error": "busy", "detail": "Too many requests from this connection right now. Wait a minute and try again."}, status_code=429)
    try:
        if not (isinstance(cached, str) and len(cached) > 80):
            # threadpool: fetch_channel does network I/O (yt_dlp + YT API + possibly a proxy
            # transcript batch) — run it off the event loop so one slow channel can't stall
            # every other request on the server
            try:
                prof = await asyncio.wait_for(run_in_threadpool(fetch_channel, url), timeout=75)
            except asyncio.TimeoutError:
                # fetch_channel does uncapped network I/O (yt_dlp / proxy transcripts) and can hang on
                # some channels; bound it so the request fails fast instead of stalling for minutes.
                return JSONResponse({"error": "That channel took too long to read. Try again in a moment, or try a different channel."}, status_code=504)
            if not prof or not prof.get("recent"):
                return JSONResponse({"error": "Could not find videos for that channel. Paste the full channel URL (like youtube.com/@name)."}, status_code=400)
            channel_name = prof.get("channel") or "your channel"
            followers = prof.get("followers")
            profile = await _build_profile(prof)
        else:
            profile = cached
    except Exception as e:
        return JSONResponse({"error": "Could not read that channel. Check the link and try again.", "detail": str(e)[:200]}, status_code=502)
    if not profile:
        return JSONResponse({"error": "Could not analyze that channel. Try again."}, status_code=502)

    listing = "\n".join(f"{c['i']}. {c['l']}" for c in cand)
    user = ("Strategist profile of the creator:\n" + profile
            + "\n\nCandidate AI-risk leads (index. lead):\n" + listing
            + "\n\nReturn the JSON object with the best-fit lead indices, strongest first.")
    # Use the main model here, not FAST_MODEL: on a large, alarming 120-lead listing the fast
    # model intermittently returns an EMPTY completion (verified), and it does not support
    # assistant-prefill to force output. The main model reliably emits the ranking. Retry as a
    # backstop and parse robustly, so a one-off formatting quirk never drops the request.
    order = []
    for attempt in range(3):
        try:
            rmsg = await run_in_threadpool(lambda: get_client().messages.create(
                model=MODEL, thinking=NO_THINK, max_tokens=2000, system=SYSTEM_TAILOR,
                messages=[{"role": "user", "content": user}],
            ))
        except Exception as e:
            if attempt < 2:
                continue
            return JSONResponse({"error": "tailor failed", "detail": str(e)[:200]}, status_code=502)
        raw = "".join(b.text for b in rmsg.content if getattr(b, "type", "") == "text").strip()
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
        # Robust parse: the ranking model's formatting is non-deterministic, so never let a
        # wrapping quirk drop the request. Try {"order":[...]}, then a bare array, then as a
        # last resort pull the integers straight out of the text (all filtered to valid_idx).
        # NOTE: every path must land rawlist as a LIST — an "order" string would otherwise be
        # iterated character by character and shatter multi-digit indices.
        rawlist = []
        obj = _last_obj_with(raw, "order")
        if obj is not None and isinstance(obj.get("order"), list):
            rawlist = obj["order"]
        else:
            try:
                j2 = json.loads(raw)
                if isinstance(j2, dict) and isinstance(j2.get("order"), list):
                    rawlist = j2["order"]
                elif isinstance(j2, list):
                    rawlist = j2
            except Exception:
                rawlist = []
        if not isinstance(rawlist, list) or not rawlist:
            rawlist = re.findall(r"-?\d+", raw)  # last resort: ordered integers in the text
        seen = set()
        order = []
        for v in rawlist:
            try:
                vi = int(v)
            except Exception:
                continue
            if vi in valid_idx and vi not in seen:
                seen.add(vi)
                order.append(vi)
        order = order[:40]
        if order:
            break  # got a usable ranking; no need to retry
    if not order:
        return JSONResponse({"error": "could not tailor this channel; try again"}, status_code=502)
    _log_event({"t": "tailor", "ch": _chan_key(url), "n": len(order), "cand": len(cand)})
    return {"channel": channel_name, "followers": followers, "profile": profile, "order": order}


SYSTEM_REVIEW = """You are an advisory fact checker for AI risk YouTube scripts. A creator pastes a draft script; your job is to make the ARGUMENTS survive contact with a hostile comment section, without flattening the video. You are not a sponsor and not a censor: never rewrite their voice, never demand hedges that kill the thesis, never object to opinions clearly framed as opinions. Everything you return is a SUGGESTION the creator can take or leave; phrase fixes as "you could" and "one sturdier way to put it", never as orders. Also flag HINGE ASSUMPTIONS: if the script assumes the audience already believes AI is improving fast, or that it is more than autocomplete, or that scheming is real, without establishing it, gently note that many viewers' picture of AI is a couple of years old, and suggest a quick catch-the-audience-up moment (a trajectory reset or one good receipt) so the point lands. If the script concedes that scheming or deceptive behavior "only happens in contrived tests", flag it: that concession is factually wrong (the same behaviors are documented in real deployments) and weakens the video. Also flag DISMISSIBLE ATTRIBUTION: if a claim leans on a bare company name ("OpenAI found") where the work was independent or where "scientists"/"researchers"/an admission against interest framing ("the company's own testing found") would be harder to dismiss, suggest the reframe. Also flag STALE evidence: if the script leans on a 2023 or GPT-4 era result as if it were current, note it and suggest adding "and that was an early model, today's are far stronger" or pointing to a newer example. Plain language, no jargon, no em dashes, no hyphens, never "chatbot", never the word "doomer", "AI company" never "AI lab".

You will be given the script and a menu of VERIFIED sources with ids. Return EXACTLY these markdown sections:

## Verdict
Two sentences: is this argument sound and defensible overall, and what single change matters most.

## Claim audit
A markdown table | Claim in your script | Verdict | Fix |. Go claim by claim through every FACTUAL assertion (5 to 12 rows, the load bearing ones). Verdict is one of: solid (matches the record), needs qualifier (true but stated too strongly or missing context), wrong (contradicts the record), cannot verify (no source known to you or the menu). For fixes give the exact replacement phrasing; cite menu sources as [id] where they support a claim.

## The dunk test
The 3 sentences a hostile viewer will screenshot, quoted verbatim, each with WHY it is attackable and a fix that keeps the energy. Hostile viewers judge the single most overclaimed 10 seconds, not the average.

## Hedging check
Does the script still argue something? If caveats have piled up until the thesis dissolved, say where, and give the calibrated strong version of the thesis it should assert. If the script overclaims throughout, say that instead.

## Missing receipts
Claims that need an on screen citation or description link, with the best menu source [id] for each.

Cite ONLY ids from the menu as [id], copying each id EXACTLY and IN FULL as shown (never shorten an id). If the menu cannot support or refute a claim, mark it cannot verify rather than guessing. Be direct; a creator's time is the scarcest thing they have."""


@app.post("/review")
async def review(req: Request):
    if not _rate_ok(req, cost=12):
        return JSONResponse({"error": "Too many requests. Try again in a bit."}, status_code=429)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    script = (body.get("script") or "").strip()[:40000]
    if len(script) < 300:
        return JSONResponse({"error": "Paste the actual script (at least a few paragraphs)."}, status_code=400)
    menu, valid_ids, ranked = source_menu(script[:6000], limit=90)
    user = ("Draft script:\n\"\"\"\n" + script + "\n\"\"\"\n\n"
            + ("VERIFIED SOURCE MENU:\n" + menu + "\n\n" if menu else "")
            + "Write the review.")
    try:
        msg = await run_in_threadpool(lambda: get_client().messages.create(
            model=MODEL, thinking=NO_THINK, max_tokens=4000, system=SYSTEM_REVIEW,
            messages=[{"role": "user", "content": user}],
        ))
        text = _plain_company("".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip())
        if not text:
            return JSONResponse({"error": "no review"}, status_code=502)
        text, _ = _resolve_ids(text)
        # privacy: log only that a review happened and its size, NEVER script content
        _log_event({"t": "review", "chars": len(script)})
        return {"review": text}
    except Exception as e:
        return JSONResponse({"error": "review failed", "detail": str(e)[:200]}, status_code=502)


# ---- idea claims: reserve an idea once a deal is verbal, so creators don't collide ----
CLAIMS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claims.json")
_CLAIMS = None
def _claims():
    global _CLAIMS
    if _CLAIMS is None:
        try:
            with open(CLAIMS_PATH, encoding="utf-8") as f:
                _CLAIMS = json.load(f)
        except Exception:
            _CLAIMS = {}
    return _CLAIMS

@app.get("/claims")
def claims_list():
    return {"claimed": sorted(_claims().keys())}

@app.post("/polish_probe")
async def polish_probe(req: Request):
    """Run the polish chain over SUPPLIED summaries and return before and after, side by side.

    This exists because the polish chain had no way to be tested. Every measurement of it was a fresh
    generation compared against a different fresh generation, so batch-to-batch variation swamped the
    effect: a check of the new fidelity pass came back 9 percent invented before and 8.3 percent after,
    which at n=24 is the same number twice: that pair of numbers is the NOISE FLOOR, not the effect
    size. A paired test needs the SAME ideas through the pipeline with and without a pass, and that
    needs an endpoint that accepts ideas instead of writing them. Run through here, the pass removed
    3 of 3 known invented details that all 3 survived with it switched off.

    Body: {key, ideas:[{title,summary}], anchors:"- [who year] text\n...", skip_fidelity:bool}
    """
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if body.get("key") != EVENTS_KEY:
        return JSONResponse({"error": "bad key"}, status_code=403)
    ideas = body.get("ideas") or []
    if not isinstance(ideas, list) or not ideas:
        return JSONResponse({"error": "no ideas"}, status_code=400)
    ideas = [{"title": (x.get("title") or "")[:400], "summary": (x.get("summary") or "")[:2000]}
             for x in ideas[:30] if isinstance(x, dict)]
    anchors = "" if body.get("skip_fidelity") else (body.get("anchors") or "")
    before = [x["summary"] for x in ideas]
    try:
        rew = await asyncio.wait_for(
            run_in_threadpool(_activate_summaries, ideas, anchors), timeout=300)
    except Exception as e:
        return JSONResponse({"error": "polish failed: " + str(e)[:200]}, status_code=502)
    after = [rew.get(i, before[i]) for i in range(len(ideas))]
    return {"n": len(ideas), "changed": sum(1 for i in range(len(ideas)) if after[i] != before[i]),
            "fidelity_ran": bool(anchors),
            "pairs": [{"title": ideas[i]["title"], "before": before[i], "after": after[i]}
                      for i in range(len(ideas))]}


@app.post("/claim")
async def claim(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if body.get("key") != EVENTS_KEY:
        return JSONResponse({"error": "bad key"}, status_code=403)
    title = (body.get("title") or "").strip()[:200]
    if not title:
        return JSONResponse({"error": "missing title"}, status_code=400)
    c = _claims()
    if body.get("release"):
        c.pop(title, None)
    else:
        c[title] = {"by": str(body.get("by", ""))[:80], "ts": int(__import__("time").time())}
    try:
        with open(CLAIMS_PATH, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
    except Exception:
        pass
    _log_event({"t": "claim", "i": title, "release": bool(body.get("release"))})
    return {"ok": True, "claimed": sorted(c.keys())}


@app.get("/dash")
def dash(key: str = ""):
    if key != EVENTS_KEY:
        return JSONResponse({"error": "bad key"}, status_code=403)
    evs = list(_EVBUF)
    try:
        if os.path.exists(EVENTS_PATH):
            with open(EVENTS_PATH, encoding="utf-8") as f:
                seen = {json.dumps(e, sort_keys=True) for e in evs}
                for l in f.readlines()[-4000:]:
                    try:
                        e = json.loads(l)
                        if json.dumps(e, sort_keys=True) not in seen:
                            evs.append(e)
                    except Exception:
                        pass
    except Exception:
        pass
    by = {}
    for e in evs:
        if (e.get("tok") or "") == "study":
            continue  # persona-simulation traffic, not real creators
        ch = (e.get("ch") or e.get("c") or e.get("channel") or "").strip().lower()
        key2 = ch or ("(no channel) " + str(e.get("tok") or ""))
        r = by.setdefault(key2, {"open": 0, "generate": 0, "pitch": 0, "pin": 0, "copy": 0, "interest": 0,
                                 "interests": [], "last": 0})
        t = e.get("t", "")
        if t == "open": r["open"] += 1
        elif t in ("generate", "generate_done", "pregen_hit"): r["generate"] += 1
        elif t == "pitch": r["pitch"] += 1
        elif t == "pin": r["pin"] += 1
        elif t == "copy": r["copy"] += 1
        elif t == "interest":
            r["interest"] += 1
            ti = str(e.get("title") or e.get("i") or "")[:80]
            if ti and ti not in r["interests"]: r["interests"].append(ti)
        ts = e.get("srv_ts") or (e.get("ts", 0) / 1000 if e.get("ts") else 0)
        r["last"] = max(r["last"], int(ts or 0))
    rows = sorted(by.items(), key=lambda kv: -kv[1]["last"])
    import datetime
    def fmt(ts):
        try: return datetime.datetime.utcfromtimestamp(ts).strftime("%b %d %H:%M") if ts else ""
        except Exception: return ""
    html = ["<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Species pipeline</title>",
            "<style>body{font:14px -apple-system,sans-serif;background:#0b0a0c;color:#ecebed;padding:20px}table{border-collapse:collapse;width:100%}",
            "td,th{border-bottom:1px solid #241f27;padding:8px 10px;text-align:left;font-size:13px}th{color:#9b949f}",
            ".hot{color:#7dc98f;font-weight:700}h1{font-size:20px}</style></head><body>",
            "<h1>Creator pipeline (from app telemetry)</h1>",
            "<table><tr><th>channel</th><th>opens</th><th>generated</th><th>pitches read</th><th>pins</th><th>copies</th><th>🙋 interests</th><th>last seen (UTC)</th></tr>"]
    for k, r in rows[:200]:
        hot = " class='hot'" if r["interest"] else ""
        ints = ("<br><small>" + " · ".join(r["interests"][:3]) + "</small>") if r["interests"] else ""
        html.append(f"<tr{hot}><td>{k[:48]}{ints}</td><td>{r['open']}</td><td>{r['generate']}</td><td>{r['pitch']}</td><td>{r['pin']}</td><td>{r['copy']}</td><td>{r['interest']}</td><td>{fmt(r['last'])}</td></tr>")
    html.append(f"</table><p style='color:#9b949f'>{len(evs)} events · buffer resets on redeploy; Railway logs keep the full history (EVT lines)</p></body></html>")
    from fastapi.responses import HTMLResponse
    return HTMLResponse("".join(html))

@app.get("/debug-transcript")
def debug_transcript(key: str = "", vid: str = "Xf-uUy5pdUI", proxy: int = 0):
    """Admin probe: can this server fetch YouTube transcripts — directly (proxy=0, answers the
    datacenter-blocking question) or through the Webshare proxy (proxy=1, verifies the creds
    actually work from Railway)? Gated; not a public proxy."""
    if key != EVENTS_KEY:
        return JSONResponse({"error": "bad key"}, status_code=403)
    import time as _t
    t0 = _t.time()
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        cfg = _webshare_cfg() if proxy else None
        if proxy and not cfg:
            return {"ok": False, "err": "proxy requested but WEBSHARE_USER/PASS not configured"}
        tr = YouTubeTranscriptApi(proxy_config=cfg).fetch(vid)
        txt = " ".join(s.text for s in tr)
        return {"ok": True, "vid": vid, "via": ("proxy" if cfg else "direct"), "words": len(txt.split()),
                "chars": len(txt), "sample": txt[:200], "secs": round(_t.time() - t0, 1)}
    except Exception as e:
        return {"ok": False, "vid": vid, "via": ("proxy" if proxy else "direct"), "err": type(e).__name__,
                "detail": str(e)[:400], "secs": round(_t.time() - t0, 1)}


@app.get("/pipeline")
def pipeline():
    """LIVING, guided walk-through of the whole pipeline: what happens at each step, in plain
    English, with the exact live prompts (read straight from the running code, so it never drifts)
    and what to look at when reviewing each one. Auto-updates on every deploy."""
    import html as _html
    from fastapi.responses import HTMLResponse
    esc = lambda s: _html.escape(str(s))
    def _len(fn):
        try:
            return fn()
        except Exception:
            return "?"
    n_sources = _len(lambda: len(get_sources()))
    n_bank = _len(lambda: len(get_bank_sources()))
    n_doss = _len(lambda: len(_dossiers()))
    try:
        _evd = _evidence()
    except Exception:
        _evd = {}
    n_ev = sum(len(v) for v in _evd.values()) if _evd else "?"
    n_ev_th = len(_evd) if _evd else "?"
    n_pre = _len(lambda: len(_transcripts()))

    # each step: (icon, title, plain-English what-happens, [knob rows], [(prompt name, live body, what it's for, what to look at)])
    phases = [
      ("Step 1 — Understand the creator",
       "Before writing anything, the tool studies the channel so every idea fits how they actually make videos.",
       [
        ("📺", "Read the channel", "Pulls their recent uploads (titles), the descriptions and view counts of those videos, and the actual transcripts of up to %s recent videos. Transcripts are the best signal, they show how the person really talks and builds an argument." % esc(_TR_MAX_VIDEOS),
         [("Transcripts fetched", "up to %s recent captioned videos, fetched live via a residential proxy, 25-second deadline (a slow channel gets fewer)" % esc(_TR_MAX_VIDEOS)),
          ("How much of each", "%s chars per video (~10-13 min); long videos keep the opening + ending, middle marked [...]" % esc(_TR_MAX_CHARS)),
          ("How much reaches the profile", "up to 110,000 chars total (~9 full transcripts)")],
         []),
        ("🧭", "Build a profile of how they make videos", "The AI turns all that raw material into a strategist's read of the creator: their niche, their signature format, how they explain things, their emotional temperature, how they open and close. Every later step is built on this profile.",
         [("Model", esc(MODEL))],
         [("SYSTEM_ANALYST", SYSTEM_ANALYST,
           "Writes the strategist profile of the creator from their titles, descriptions, and transcripts.",
           "Does it capture what actually makes THIS creator distinctive, or generic-YouTuber traits? Is it leaning on transcripts (good) or just titles?")]),
        ("🗣️", "Learn their voice (for scripts later)", "A deeper style guide, how they think through a topic and their emotional tone, quoted from real transcripts. Used later when writing a sample script that sounds like them.",
         [],
         [("SYSTEM_VOICE", SYSTEM_VOICE,
           "Builds the 'voice bible' used by the sample-script writer.",
           "Is it capturing HOW they think and feel, or just surface catchphrases?")]),
       ]),
      ("Step 2 — Come up with video ideas",
       "There are two ways to get ideas for a channel. You pick either on the home screen.",
       [
        ("✍️", "Method A: Write fresh ideas (recommended)", "Brainstorms brand-new ideas built from the creator's own world, then a second pass acts as their toughest superfan and keeps only the strongest. This is two AI calls back to back.",
         [("Counts", "32 candidates brainstormed, then 25 selected (topped back up to 25 if the editor returns fewer)")],
         [("SYSTEM_CUSTOM", SYSTEM_CUSTOM,
           "Step 1: brainstorm 32 candidate ideas native to this specific channel.",
           "Are the ideas genuinely something only THIS creator could make, or a generic AI-risk topic with their format bolted on? The bar is meant to be interest + importance, not just relevance."),
          ("SYSTEM_EDITOR", SYSTEM_EDITOR,
           "Step 2: the creator's demanding superfan picks and sharpens the 25 best.",
           "Is it selecting the ideas a superfan would actually click, and cutting the safe/generic ones?")]),
        ("📚", "Method B: Pull from our library", "Instead of writing new ideas, this ranks our vetted pool of documented AI-risk stories (real events, studies, findings) for this specific channel, best fit first.",
         [("Pool", "the front-end lead library, ranked; shows the top ~40 to curate")],
         [("SYSTEM_TAILOR", SYSTEM_TAILOR,
           "Ranks the whole documented lead pool for THIS channel, best fit first.",
           "Are the top-ranked leads ones this channel could genuinely own, or just topically adjacent?")]),
       ]),
      ("Step 3 — Flesh out an idea (what the creator gets)",
       "For any idea, the creator (or you) can open a full research pack and a sample script, plus a few smaller helpers.",
       [
        ("📄", "Research pack", "Turns one idea into a creator-ready brief: the hook, the argument in beats, each claim with sourced receipts, the objections to expect, and possible on-camera guests, with numbered citations. It also gets a hand-verified dossier + a pile of documented cases + a menu of vetted sources to draw from.",
         [("Model", esc(MODEL))],
         [("SYSTEM_BRIEF", SYSTEM_BRIEF,
           "Writes the whole research pack for one idea.",
           "Is it genuinely useful to a non-expert creator? Does it offer rather than order? Are the citations real and load-bearing, and does it handle objections without sounding defensive?")]),
        ("🎬", "Sample script (three passes)", "Writes a short script in the creator's voice, then rewrites it to sound even more like them, then scrubs the AI-writing tells. Opens cold on the hook, no channel housekeeping, with numbered citations and a Sources list.",
         [("Length", "~550-850 words")],
         [("SYSTEM_SCRIPT", SYSTEM_SCRIPT,
           "Pass 1: the first-draft script in the creator's voice.",
           "Does it sound like THEM, build ONE clear argument (not a list of scary facts), and open strong?"),
          ("SYSTEM_VOICEMATCH", SYSTEM_VOICEMATCH,
           "Pass 2: rewrite to be unmistakably theirs, using a real transcript as ground truth.",
           "Does pass 2 actually shift the voice, or just tweak words?"),
          ("SYSTEM_DESLOP", SYSTEM_DESLOP,
           "Pass 3: strip AI-writing tells (the 'it's not X, it's Y' pattern, rule-of-three, puffery).",
           "Is it catching the tells without flattening the creator's real voice?")]),
        ("📝", "Longer pitch, sources, more angles, titles", "Smaller helpers on an idea.",
         [],
         [("SYSTEM_PITCH", SYSTEM_PITCH,
           "A 3-5 sentence plain description of an idea, substance-first, with numbered sources.",
           "Does the substance carry it, with no selling or hype?"),
          ("SYSTEM_SOURCES", SYSTEM_SOURCES,
           "Attaches 4-10 readable, verified sources to an idea.",
           "Readable-first (news/blog), not a pile of papers?"),
          ("SYSTEM_DIRECTIONS", SYSTEM_DIRECTIONS,
           "'Pull this thread': 3-4 different angles a creator could take a lead.",
           "Are the threads genuinely different from each other?"),
          ("SYSTEM_RETITLE", SYSTEM_RETITLE,
           "Alternative titles for the same premise.",
           "Same premise, better hook?")]),
       ]),
      ("Under the hood",
       "Shared pieces that shape everything above, plus a few utility tools.",
       [
        ("🧹", "House style (glued onto almost every call)", "A shared set of style rules appended to generation prompts.",
         [],
         [("ANTI_SLOP", ANTI_SLOP,
           "The shared house-style guardrails (no em dashes, never 'doomer', say 'AI' not vague nouns, etc.).",
           "Are these the right rules? Any that accidentally hurt quality?")]),
        ("🎲", "Seeds + anchors (mixed into idea generation)", "Rotating creative angles plus a sample of real documented events, injected so ideas stay concrete and fresh instead of abstract.",
         [],
         [("seed_block() + anchor_block()", seed_block(9) + "\n\n===== ANCHORS (a rotating sample) =====\n" + anchor_block(12),
           "The creative seeds and real-event anchors mixed into idea generation.",
           "Do the anchors spark good ideas, or pull everything toward the same few events?")]),
        ("🛠️", "Utility tools", "Smaller endpoints used in specific spots.",
         [],
         [("SYSTEM (similar)", SYSTEM, "Generates ideas closely related to a given one.", ""),
          ("SYSTEM_CATEGORY", SYSTEM_CATEGORY, "Generates more ideas within one themed category (older ideas app).", ""),
          ("SYSTEM_REVIEW", SYSTEM_REVIEW, "Advisory fact-check of a creator's own pasted draft script.", "Helpful without being a censor?"),
          ("SYSTEM_VET", SYSTEM_VET, "Drops sources clearly off-topic for an idea.", ""),
          ("SYSTEM_VERDICT", SYSTEM_VERDICT, "Predicts whether a social post about AI is net-positive to spread.", "")]),
       ]),
    ]

    flow = """graph TD
  A(["Paste a YouTube channel"]) --> B["Read the channel"]
  B --> C["Build a profile of the channel"]
  C --> D{"Pick how to<br/>get ideas"}
  D -->|"Write fresh"| E["Write fresh ideas"]
  D -->|"From our library"| F["Rank our library"]
  E --> G(["You curate the list"])
  F --> G
  G --> H(["Publish + send the link"])
  H --> I(["Creator opens their page"])
  I --> J["Research pack"]
  I --> K["Sample script"]
  I --> L(["Get more ideas"])
  click B call showStep("read-the-channel")
  click C call showStep("build-a-profile-of-how-they-make-videos")
  click E call showStep("method-a-write-fresh-ideas-recommended")
  click F call showStep("method-b-pull-from-our-library")
  click J call showStep("research-pack")
  click K call showStep("sample-script-three-passes")"""

    data = [
        ("Fact-checked source links", "%s links" % esc(n_sources),
         "The pool of real, verified links (news articles, studies, official posts) the AI is allowed to cite. When it makes a claim in a research pack it must pull from this list, so it can never invent a source or a dead link."),
        ("Ready-made source sets", "%s ideas" % esc(n_bank),
         "Hand-picked sources for the most-used ideas, so those research packs come up instantly without an AI call."),
        ("Pre-written research briefs", "%s ideas" % esc(n_doss),
         "Fully researched briefs (verified numbers, quotes, a timeline, the objections, possible guests) for specific ideas. If an idea has one, its research pack is built from it, faster and more reliable than writing from scratch."),
        ("Documented-incident library", "%s real cases in %s themes" % (esc(n_ev), esc(n_ev_th)),
         "A stockpile of real AI incidents grouped by theme (scheming, self-preservation, persuasion, and so on). This powers the 'and this has actually happened, dozens of times' moments in a video."),
        ("Saved transcripts", "%s channel" % esc(n_pre),
         "Channels whose video transcripts we saved ahead of time (just kurzgesagt right now). Every other channel's transcripts are pulled live the moment you generate."),
    ]

    P = []
    P.append("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>")
    P.append("<title>How Videos We Support works</title>")
    P.append("<script src='https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js'></script>")
    P.append("<script>window.addEventListener('DOMContentLoaded',function(){try{mermaid.initialize({startOnLoad:true,theme:'dark',securityLevel:'loose',themeVariables:{fontSize:'15px'},flowchart:{useMaxWidth:true,htmlLabels:true}});}catch(e){}});</script>")
    P.append("<script>"
             "function showStep(id){var el=document.getElementById('step-'+id);if(!el)return;"
             "document.getElementById('modalbody').innerHTML=\"<div class='step' style='margin:0;border:none;padding:0'>\"+el.innerHTML+\"</div>\";"
             "var ov=document.getElementById('ov');ov.classList.add('on');ov.scrollTop=0;}"
             "function closeStep(){document.getElementById('ov').classList.remove('on');}"
             "window.showStep=showStep;window.closeStep=closeStep;"
             "document.addEventListener('keydown',function(e){if(e.key==='Escape')closeStep();});"
             "</script>")
    P.append("<style>"
             "body{margin:0;background:#0b0a0c;color:#ece8f0;font:16px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}"
             ".wrap{max-width:820px;margin:0 auto;padding:30px 18px 120px}"
             "h1{font-size:27px;margin:0 0 6px;font-weight:800}.dot{color:#e20020}"
             ".lede{color:#c9c4d0;font-size:16px;margin:0 0 14px}"
             ".meta{color:#8a8290;font-size:12.5px;margin-bottom:16px}"
             ".how{background:#12100f;border:1px solid #4a3d1e;border-radius:11px;padding:14px 16px;color:#e7dcc2;font-size:14px;margin-bottom:8px}"
             ".how b{color:#ffcf4d}"
             ".toc{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 6px}"
             ".toc a{font-size:12.5px;color:#cfced3;background:#141218;border:1px solid #2a2630;border-radius:99px;padding:6px 12px;text-decoration:none}"
             ".toc a:hover{border-color:#ffcf4d;color:#fff}"
             ".phase{margin:30px 0 0}"
             ".phase>h2{font-size:20px;margin:0 0 3px;font-weight:800}"
             ".phase>.pblurb{color:#8a8290;font-size:14px;margin:0 0 12px}"
             ".step{background:#100f13;border:1px solid #262230;border-radius:12px;padding:15px 17px;margin:12px 0}"
             ".step h3{font-size:16.5px;margin:0 0 6px;display:flex;gap:9px;align-items:baseline}"
             ".step h3 .ic{font-size:18px}"
             ".what{color:#d7d3dd;font-size:14.5px;margin:0 0 10px}"
             ".knobs{list-style:none;padding:0;margin:0 0 10px}"
             ".knobs li{font-size:12.5px;color:#a49cac;padding:3px 0 3px 16px;position:relative}"
             ".knobs li:before{content:'\\2699';position:absolute;left:0;color:#6f6878}"
             ".knobs b{color:#e7c86a;font-weight:600}"
             ".prompt{border-top:1px solid #221f2a;margin-top:10px;padding-top:11px}"
             ".prompt .pname{font-weight:700;font-size:13.5px;color:#ece8f0;font-family:ui-monospace,Menlo,monospace}"
             ".prompt .pfor{color:#b9b3c0;font-size:13.5px;margin:3px 0}"
             ".prompt .plook{color:#9ac47f;font-size:13px;margin:3px 0 7px}.prompt .plook b{color:#b6dfa0}"
             "details{border:1px solid #2a2630;border-radius:8px;background:#0e0d10;margin-top:4px}"
             "summary{cursor:pointer;padding:8px 12px;font-size:12.5px;color:#8a8290;user-select:none}"
             "summary:hover{color:#ece8f0}"
             "pre{white-space:pre-wrap;word-wrap:break-word;margin:0;background:#0b0a0c;border-top:1px solid #2a2630;border-radius:0 0 8px 8px;padding:12px;font:12px/1.55 ui-monospace,Menlo,monospace;color:#cfced3;max-height:420px;overflow:auto}"
             ".mermaid{background:#141218;border:1px solid #2a2630;border-radius:12px;padding:16px 10px;overflow-x:auto;text-align:center}"
             "table{border-collapse:collapse;width:100%;margin:6px 0}td{border:1px solid #2a2630;padding:7px 11px;font-size:13.5px}td:first-child{color:#ffcf4d;font-weight:600;white-space:nowrap}"
             ".drow{background:#100f13;border:1px solid #262230;border-radius:10px;padding:12px 15px;margin:8px 0}"
             ".drow .dh{font-size:15.5px}.drow .dn{color:#ffcf4d;font-weight:600;margin-left:8px;font-size:13.5px}"
             ".drow .dd{color:#a49cac;font-size:13.5px;margin-top:4px}"
             ".clicktip{color:#ffcf4d}"
             ".mermaid g.clickable{cursor:pointer}.mermaid g.clickable:hover rect,.mermaid g.clickable:hover polygon{filter:brightness(1.45)}.mermaid g.clickable tspan{text-decoration:underline}"
             "#ov{display:none;position:fixed;inset:0;background:rgba(0,0,0,.68);z-index:50;align-items:flex-start;justify-content:center;padding:5vh 14px}#ov.on{display:flex}"
             ".ovbox{background:#100f13;border:1px solid #3a3446;border-radius:14px;max-width:760px;width:100%;max-height:88vh;overflow:auto;padding:16px 22px 24px;box-shadow:0 18px 60px rgba(0,0,0,.6)}"
             ".ovx{position:sticky;top:0;float:right;background:#221f2a;border:1px solid #3a3446;color:#ece8f0;border-radius:8px;width:34px;height:34px;font-size:15px;cursor:pointer}"
             "</style></head><body><div class='wrap'>")
    P.append("<h1>How Videos We Support works<span class='dot'>.</span></h1>")
    P.append("<p class='lede'>You paste a creator's YouTube channel. The tool studies how they actually make videos, then writes AI-risk video ideas in their voice, with a research pack and a sample script for each. This page walks through every step and shows the exact instructions the AI gets, so more people can help make it better.</p>")
    P.append("<div class='meta'>Live from the running code (deployed %s), so it is never out of date. Every prompt shown is the real one being sent to the AI. Spot something to improve? That's the point.</div>" % esc(_DEPLOY_STAMP))

    # table of contents
    P.append("<div class='toc'>")
    P.append("<a href='#overview'>The flow at a glance</a>")
    for ph in phases:
        anchor = re.sub(r"[^a-z0-9]+", "-", ph[0].lower()).strip("-")
        P.append("<a href='#%s'>%s</a>" % (anchor, esc(ph[0].split(" — ")[0] if " — " in ph[0] else ph[0])))
    P.append("<a href='#data'>The data</a>")
    P.append("</div>")

    P.append("<div class='phase' id='overview'><h2>The flow</h2><div class='pblurb'>The whole journey for one channel. <b class='clicktip'>Click any dark box</b> to see exactly what that step does, or read the full walk-through below.</div>")
    P.append("<div class='mermaid'>" + esc(flow) + "</div></div>")

    for ph in phases:
        anchor = re.sub(r"[^a-z0-9]+", "-", ph[0].lower()).strip("-")
        P.append("<div class='phase' id='%s'><h2>%s</h2><div class='pblurb'>%s</div>" % (anchor, esc(ph[0]), esc(ph[1])))
        for (icon, title, what, knobs, prompts) in ph[2]:
            sid = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            P.append("<div class='step' id='step-%s'><h3><span class='ic'>%s</span> %s</h3><div class='what'>%s</div>" % (sid, esc(icon), esc(title), esc(what)))
            if knobs:
                P.append("<ul class='knobs'>")
                for kk, kv in knobs:
                    P.append("<li><b>%s:</b> %s</li>" % (esc(kk), esc(kv)))
                P.append("</ul>")
            for (pname, pbody, pfor, plook) in prompts:
                P.append("<div class='prompt'><div class='pname'>%s</div>" % esc(pname))
                if pfor:
                    P.append("<div class='pfor'>%s</div>" % esc(pfor))
                if plook:
                    P.append("<div class='plook'><b>What to look at:</b> %s</div>" % esc(plook))
                P.append("<details><summary>Show the exact prompt</summary><pre>%s</pre></details></div>" % esc(pbody))
            P.append("</div>")
        P.append("</div>")

    P.append("<div class='phase' id='data'><h2>What it draws on</h2><div class='pblurb'>The sources of truth behind the ideas, packs, and scripts.</div>")
    for label, count, desc in data:
        P.append("<div class='drow'><div class='dh'><b>%s</b><span class='dn'>%s</span></div><div class='dd'>%s</div></div>" % (esc(label), esc(count), esc(desc)))
    P.append("</div>")

    P.append("<div id='ov' onclick=\"if(event.target===this)closeStep()\"><div class='ovbox'><button class='ovx' onclick='closeStep()' title='Close (Esc)'>&#10005;</button><div id='modalbody'></div></div></div>")
    P.append("</div></body></html>")
    return HTMLResponse("".join(P))


@app.post("/debug-profile")
async def debug_profile(req: Request):
    """Admin: build a channel profile with transcripts ON or OFF, to show the before/after the
    transcript pipeline makes. Gated; POST {key, channelUrl, transcripts:true|false}."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if body.get("key") != EVENTS_KEY:
        return JSONResponse({"error": "bad key"}, status_code=403)
    url = (body.get("channelUrl") or "").strip()
    with_t = bool(body.get("transcripts", True))
    if not url:
        return JSONResponse({"error": "missing channelUrl"}, status_code=400)
    try:
        prof = await run_in_threadpool(fetch_channel, url, with_t)
        if not prof or not prof.get("recent"):
            return JSONResponse({"error": "could not read channel"}, status_code=502)
        profile = await _build_profile(prof)
        return {"channel": prof.get("channel", ""), "with_transcripts": with_t,
                "transcripts_used": len(prof.get("transcripts") or []),
                "profile": profile, "chars": len(profile)}
    except Exception as e:
        return JSONResponse({"error": "debug-profile failed", "detail": str(e)[:200]}, status_code=502)


@app.get("/events")
def events(key: str = "", n: int = 300):
    if key != EVENTS_KEY:
        return JSONResponse({"error": "bad key"}, status_code=403)
    out = list(_EVBUF)[-max(1, min(n, 3000)):]
    try:
        if not out and os.path.exists(EVENTS_PATH):
            with open(EVENTS_PATH, encoding="utf-8") as f:
                out = [json.loads(l) for l in f.readlines()[-n:] if l.strip()]
    except Exception:
        pass
    return {"events": out, "count": len(out)}

# ---- server-side pre-generation cache: personal links resolve instantly, teammates get
# the SAME ideas (deterministic across devices), and organic generations warm it too ----
PREGEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pregen.json")
# Bump this whenever the writing rules change in a way that makes older cached ideas WRONG for the
# current bar (reading level, cadence, fact discipline). pregen.json is committed to the repo, so it
# ships with every deploy and CANNOT go stale on its own: entries stamped with an older version are
# ignored and regenerated. This exists because @kurzgesagt and @fireship silently served ideas from
# before the reading-level work (measured grade 14.1 and 25.1) while fresh channels were at 7.
GEN_VERSION = 3
_PREGEN = None
def _pregen():
    """Cached payloads for the CURRENT GEN_VERSION only. Older entries are dropped on load so a
    stale pre-baked channel can never out-rank a fresh generation."""
    global _PREGEN
    if _PREGEN is None:
        try:
            with open(PREGEN_PATH, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = {}
        kept, dropped = {}, []
        for k, v in (raw.items() if isinstance(raw, dict) else []):
            if isinstance(v, dict) and int(v.get("gen_version") or 0) >= GEN_VERSION:
                kept[k] = v
            else:
                dropped.append(k)
        if dropped:
            _log_event({"t": "pregen_stale_dropped", "n": len(dropped), "ch": dropped[:5],
                        "need": GEN_VERSION})
        _PREGEN = kept
    return _PREGEN

def _chan_key(u):
    u = (u or "").strip().lower()
    u = re.sub(r"[?#].*$", "", u)
    u = re.sub(r"^https?://", "", u).replace("www.", "", 1).rstrip("/")
    return u

def _pregen_store(url, payload):
    try:
        if isinstance(payload, dict):
            payload = dict(payload, gen_version=GEN_VERSION)  # stamp so a later rules change invalidates it
        _pregen()[_chan_key(url)] = payload
        # atomic write (tmp + os.replace): a crash mid-dump must never truncate pregen.json and
        # silently wipe every warmed channel (matches _transcripts_store).
        tmp = PREGEN_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_pregen(), f, ensure_ascii=False)
        os.replace(tmp, PREGEN_PATH)
    except Exception:
        pass


# ---- channel transcripts: the profile upgrade. YouTube blocks caption fetches from
# datacenter IPs (verified via /debug-transcript), so transcripts arrive two ways:
# (1) preloaded from a residential IP via preload_transcripts.py -> POST /transcripts-upload
#     (mirrors the pregen ritual: server stores live copy, local file ships on next deploy);
# (2) on-demand through a Webshare rotating residential proxy IF the env vars are set
#     (WEBSHARE_USER/WEBSHARE_PASS) — covers channels nobody preloaded.
TRANSCRIPTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transcripts.json")
_TRANSCRIPTS = None
def _transcripts():
    global _TRANSCRIPTS
    if _TRANSCRIPTS is None:
        try:
            with open(TRANSCRIPTS_PATH, encoding="utf-8") as f:
                _TRANSCRIPTS = json.load(f)
        except Exception:
            _TRANSCRIPTS = {}
    return _TRANSCRIPTS

def _transcripts_store(url, payload):
    try:
        store = _transcripts()
        store[_chan_key(url)] = payload
        # cap the store (drop oldest) and write atomically: a crash mid-write must never
        # truncate the file and silently wipe every preloaded channel back to titles-only
        if len(store) > 200:
            for k in sorted(store, key=lambda k: store[k].get("ts") or 0)[:len(store) - 200]:
                store.pop(k, None)
        tmp = TRANSCRIPTS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False)
        os.replace(tmp, TRANSCRIPTS_PATH)
    except Exception:
        pass

_TR_MAX_VIDEOS = 12       # transcripts fed to the profile call
_TR_MAX_CHARS = 12000     # per video (~10-12 min of speech; measured ~11-13k chars)
VOICE_V = 3               # bump to force-rebuild cached voice bibles when the voice prompt changes
_TR_PROXY_TTL = 30 * 86400   # refetch proxy-sourced transcripts monthly
_TR_MISS_TTL = 3600          # remember a genuine "no transcripts" for 1h (short: proxy blips shouldn't lock a channel out)

def _tr_clip(txt):
    """Cap a transcript, keeping the ENDING: profiles describe how videos close (CTAs, sponsor
    welds), so a plain head-slice would blind the analyst to exactly that. Head + tail."""
    txt = re.sub(r"\s+", " ", txt or "").strip()
    if len(txt) <= _TR_MAX_CHARS:
        return txt
    return txt[:_TR_MAX_CHARS - 3000] + " [...] " + txt[-2900:]

def _webshare_cfg():
    u, p = os.environ.get("WEBSHARE_USER", ""), os.environ.get("WEBSHARE_PASS", "")
    if not (u and p):
        return None
    try:
        from youtube_transcript_api.proxies import WebshareProxyConfig
        # default retries_when_blocked is 10 PER VIDEO — a blocked proxy pool would stall a
        # request for minutes. 2 retries, and the deadline below bounds the whole batch anyway.
        return WebshareProxyConfig(proxy_username=u, proxy_password=p, retries_when_blocked=2)
    except Exception:
        return None

def _fetch_transcripts_proxy(vid_titles):
    """On-demand transcript fetch: Railway direct first, residential proxy only where blocked. vid_titles: [(id,title)].
    Returns [{"id","title","text"}]; silently returns [] when no proxy is configured (the
    profile then falls back to titles+descriptions, exactly the pre-transcript behavior).
    HARD DEADLINE on the whole batch: this runs inside a live request, so we take whatever
    finished within the window and abandon the rest rather than stalling the user."""
    cfg = _webshare_cfg()
    if not vid_titles:
        return [], True  # tuple contract: caller unpacks `got, proxy_ok = _fetch_transcripts_proxy(...)`
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except Exception:
        return [], True
    import concurrent.futures as _cf
    errs = []  # thread-appends are atomic in CPython; used to tell a proxy outage from "no captions"
    _PROXY_ERR = ("ProxyError", "ConnectionError", "MaxRetryError", "RetryError", "SSLError",
                  "ConnectTimeout", "ReadTimeout", "NewConnectionError", "ProtocolError")
    def one(vt):
        vid, title = vt
        # DIRECT FIRST, PROXY ONLY WHEN YOUTUBE BLOCKS US. This used to go straight to the proxy
        # for every video, which is what burned the metered residential bandwidth: each fetch
        # pulls the ~1-2 MB watch page, not just the caption text, so twelve videos is tens of
        # megabytes per channel profile. Railway's own IP serves a good share of videos fine and
        # costs nothing; the proxy is worth paying for only on the ones it is actually blocked on.
        # It also means a suspended or unpaid proxy degrades the tool instead of breaking it.
        attempts = [None] + ([cfg] * 2 if cfg else [])
        for pc in attempts:
            try:
                api = YouTubeTranscriptApi(proxy_config=pc) if pc else YouTubeTranscriptApi()
                tr = api.fetch(vid)
                txt = _tr_clip(" ".join(s.text for s in tr))
                return {"id": vid, "title": title, "text": txt} if len(txt) > 200 else None
            except Exception as e:
                name = type(e).__name__
                errs.append(name)
                # a video that is private, unplayable or simply has no captions will fail the same
                # way through the proxy, so do not spend a paid fetch proving it twice
                if name in ("VideoUnplayable", "VideoUnavailable", "NoTranscriptFound",
                            "TranscriptsDisabled", "NotTranslatable"):
                    return None
                continue
        return None
    out = []
    try:
        ex = _cf.ThreadPoolExecutor(max_workers=6)
        futs = [ex.submit(one, vt) for vt in vid_titles[:_TR_MAX_VIDEOS]]
        for f in _cf.as_completed(futs, timeout=25):
            r = f.result()
            if r:
                out.append(r)
    except Exception:
        pass  # deadline hit: keep what we have; stragglers finish in their threads and are dropped
    finally:
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
    # proxy_ok=False means we got nothing AND every failure was a proxy/connection error, i.e. the
    # proxy is down — the caller must NOT negative-cache that (it is transient, not "no captions").
    proxy_ok = bool(out) or not errs or not all(e in _PROXY_ERR for e in errs)
    return out, proxy_ok

def _channel_transcripts(url, vid_titles):
    """Transcripts for a channel: preloaded cache first, fresh proxy cache second, live proxy
    fetch last (and store the outcome EITHER WAY — a remembered miss stops every subsequent
    request from re-paying the fetch stall). Returns [{"id","title","text"}]."""
    key = _chan_key(url)
    cached = _transcripts().get(key)
    if cached is not None:
        vids = cached.get("videos") or []
        age = _time.time() - (cached.get("ts") or 0)
        via = cached.get("via", "")
        if vids and (via == "preload" or age < _TR_PROXY_TTL):
            # preloads serve regardless of age (the ritual refreshes them); proxy entries expire
            return [{"id": v.get("id", ""), "title": v.get("title", ""), "text": _tr_clip(v.get("text") or "")}
                    for v in vids[:_TR_MAX_VIDEOS] if v.get("text")]
        if not vids and age < _TR_MISS_TTL:
            return []  # fresh negative-cache: this channel had no fetchable transcripts
    got, proxy_ok = _fetch_transcripts_proxy(vid_titles)
    if got or proxy_ok:
        # store hits AND genuine misses, but NOT a transient proxy outage (proxy_ok False) —
        # caching that would lock the channel to titles-only until the miss TTL expires
        _transcripts_store(url, {"channel": "", "ts": _time.time(), "via": "proxy", "videos": got})
        _log_event({"t": "transcripts_proxy", "ch": key, "n": len(got), "proxy_ok": proxy_ok})
    return got


SYSTEM_VOICE = """You are a script doctor reverse-engineering ONE creator so precisely that a fresh script built from your notes would be mistaken for theirs. You are given full transcripts of their recent videos. Produce a VOICE BIBLE. The surface (their phrases and cadence) matters, but what separates a real imitation from a cheap one is the DEEP STUFF: how they THINK through a topic, and their emotional temperature. Lead with those. Ground every point in short QUOTED fragments from the transcripts; never invent a quote.

START WITH THE TWO THINGS MOST WRITERS MISS:

1. THE EXPLANATORY ENGINE — how do they make a hard idea land and hold attention? Name their PRIMARY engine and quote it in action. It is usually ONE of: building a mechanism from first principles (explaining HOW something actually works, step by step); extending ONE sustained metaphor across the whole video; a physical demonstration ("let me show you", a prop, a lab visit); a historical origin story anchored to a named person with exact dates; a thought experiment; man-on-the-street interviews. A real script of theirs is BUILT on this engine. A journalist stacking news anecdotes and citations is exactly what they are NOT. Say what they do instead, concretely.

2. EMOTIONAL REGISTER AND ARC — the temperature they run at (awe and wonder? calm neutral curiosity? dread? earnest concern? dry sardonic?) and how they handle a dark or scary topic specifically: do they resolve it toward hope or perspective, stay neutral and let the facts do the work, or lean into menace? Quote the moment their register is clearest. Getting this wrong is the single most common tell (a wonder-driven channel written as a doom channel reads instantly fake).

THEN THE REST:
3. COLD OPEN CONVENTION: not just the first line but what they open ON. Which is it: a phenomenon stated with wonder, a vivid imagined scene, a historical origin story with a named person and date, a question, a demonstration? A creator has a HABIT here. Name it and quote 2 to 3 real openings. (E.g. some never open on a true-crime news event with a named individual and a date; some always do. Be exact so a writer does not default to a generic dramatic hook that is the wrong convention for this creator.)
4. HOW THEY CLOSE: their actual sign-off and last beat (warm/reflective? a question? optimistic? a specific CTA?). Quote the shape of a real ending. Note if they DON'T do hard "subscribe" pitches.
5. SENTENCE MECHANICS: flowing-and-cumulative vs short-and-staccato, sentence length, fragments. Quote examples. (Choppy one-word-sentence rhythm and smooth flowing rhythm are opposite tells — be exact about which this is.)
6. SIGNATURE PHRASES AND TICS: recurring words, fillers, catchphrases, transitions they ACTUALLY use (quote 6+ verbatim).
7. HOW THEY USE EVIDENCE AND NUMBERS: woven into the mechanism/story or listed like a report? Crucially, IN THE NARRATION do they name specific outlets and living people ("the New York Times reported", "Anthropic's CEO says") or do they anonymize ("the people building these", "researchers")? Do numbers serve awe and scale or serve citation? Quote an example of how they actually deploy a fact out loud.
8. HUMOR: kind (dry, absurd, dark, deadpan, earnest, none) and where it lands. Quote.
9. PRODUCTION HABITS: do they use on-screen text cues, [beat]-style directions, chapter titles? How do they handle sponsors (woven mid-flow vs a hard break)? Only note what the transcripts actually show.
10. REGISTER AND VOCABULARY: formal vs casual, slang, profanity, jargon tolerance, reading level.

Be specific enough that a writer could hold a paragraph against your rules and see exactly where it drifts from this creator. No preamble. Write only the voice bible."""


async def _build_voice(videos):
    """Deep per-channel scriptwriting voice bible from transcripts (retry + length + truncation
    guard, like _build_profile). Returns "" if it can't produce a real one."""
    blocks, used = [], 0
    for v in (videos or []):
        txt = (v.get("text") or "").strip()
        if not txt:
            continue
        piece = "--- TRANSCRIPT: " + (v.get("title", "") or "") + " ---\n" + txt
        if used + len(piece) > 90000:
            break
        blocks.append(piece); used += len(piece)
    if not blocks:
        return ""
    blob = "\n\n".join(blocks)
    for _ in range(3):
        try:
            msg = await run_in_threadpool(lambda: get_client().messages.create(
                model=MODEL, thinking=NO_THINK, max_tokens=2400, system=SYSTEM_VOICE,
                messages=[{"role": "user", "content": blob}],
            ))
        except Exception:
            continue
        txt = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        if getattr(msg, "stop_reason", "") == "max_tokens":
            cut = max(txt.rfind("\n\n"), txt.rfind(". "))
            if cut > len(txt) * 0.6:
                txt = txt[:cut + 1]
        if len(txt) >= 400:
            return txt
    return ""


async def _channel_voice(url):
    """Cached voice bible for a channel. Built lazily from the cached transcripts on first script
    and stored back into the transcripts entry, so it ships and caches like everything else. A
    later transcript refresh drops it (no voice key on the fresh entry) and it rebuilds next time."""
    key = _chan_key(url)
    entry = _transcripts().get(key)
    if not entry or not entry.get("videos"):
        return ""
    if entry.get("voice") and entry.get("voice_v") == VOICE_V:
        return entry["voice"]  # cached at the current prompt version
    voice = await _build_voice(entry["videos"])
    if voice:
        entry["voice"] = voice
        entry["voice_v"] = VOICE_V  # rebuild whenever the voice-bible prompt changes
        _transcripts_store(url, entry)  # entry already carries channel/ts/via/videos; merge voice in
        _log_event({"t": "voice_built", "ch": key, "chars": len(voice)})
    return voice


@app.post("/transcripts-upload")
async def transcripts_upload(req: Request):
    """Preload path: preload_transcripts.py (run from a residential IP) pushes a channel's
    recent transcripts here. Key-gated; caps keep a bad payload from bloating the store."""
    # size gate BEFORE parsing: don't buffer an arbitrary unauthenticated body into memory.
    # Stream with a hard cap — the content-length header can't be trusted (Railway's edge
    # forwards chunked, so the header may simply be absent; verified live).
    raw = b""
    try:
        async for chunk in req.stream():
            raw += chunk
            if len(raw) > 2_000_000:
                return JSONResponse({"error": "too large"}, status_code=413)
    except Exception:
        return JSONResponse({"error": "bad body"}, status_code=400)
    try:
        body = json.loads(raw)
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if body.get("key") != EVENTS_KEY:
        return JSONResponse({"error": "bad key"}, status_code=403)
    url = (body.get("channelUrl") or "").strip()
    vids = body.get("videos")
    if not url or not isinstance(vids, list) or not vids:
        return JSONResponse({"error": "missing channelUrl or videos"}, status_code=400)
    clean = []
    for v in vids[:20]:
        if not isinstance(v, dict):
            continue
        txt = _tr_clip(str(v.get("text") or ""))
        if len(txt) < 200:
            continue
        clean.append({"id": str(v.get("id") or "")[:20], "title": str(v.get("title") or "")[:200],
                      "text": txt})
    if not clean:
        return JSONResponse({"error": "no usable transcripts"}, status_code=400)
    _transcripts_store(url, {"channel": str(body.get("channel") or "")[:120], "ts": _time.time(),
                             "via": "preload", "videos": clean})
    _log_event({"t": "transcripts_upload", "ch": _chan_key(url), "n": len(clean)})
    return {"ok": True, "channel_key": _chan_key(url), "stored": len(clean)}


@app.get("/transcripts-status")
def transcripts_status(key: str = ""):
    if key != EVENTS_KEY:
        return JSONResponse({"error": "bad key"}, status_code=403)
    out = []
    for k, v in sorted(_transcripts().items()):
        age_d = round((_time.time() - (v.get("ts") or 0)) / 86400, 1) if v.get("ts") else None
        out.append({"channel_key": k, "channel": v.get("channel", ""), "videos": len(v.get("videos", [])),
                    "via": v.get("via", ""), "age_days": age_d})
    return {"channels": out, "proxy_configured": bool(_webshare_cfg())}


# ---- CAUSE-HARM GATE: cut ideas whose dominant frame undercuts the cause (doom-is-hype, AI-too-weak,
# grift-bucketing). Cheap Opus call (indices only, so it stays fast and reliable); fails OPEN. ----
CAUSE_FILTER_SYS = """You are a strict comms gatekeeper for an AI-SAFETY advocacy project whose mission is to make the public take AI risk SERIOUSLY: AI is real, powerful, and genuinely dangerous. You get numbered candidate video ideas (title :: summary). Flag any whose DOMINANT frame would leave a viewer MORE dismissive of AI risk, EVEN IF it swings to 'but the danger is real' at the end:
- frames AI doom or AI risk as hype, marketing, a grift, a scam, a bubble, or an exaggeration
- makes 'is the fear just a sales pitch' or 'who profits from the doom warning' the spine (tying AI-warners to a rich person's profit motive)
- frames AI as too weak, fake, or overhyped to matter, or 'it cannot really do X', or 'the work was fake anyway'
- files a real AI harm under a 'snake oil' / 'another scam' / 'grift' bucket
- says 'the one AI risk that is not hype' or otherwise concedes the other AI fears are hype
Do NOT cut when skepticism points AT the disbelievers to show the danger is REAL, or a follow-the-money piece affirms the risk is real and keeps its frame on concentration of power. Err toward cutting a borderline case.
Return ONLY JSON: {"cut": [the 1-based numbers to cut]}. An empty list is fine. No prose."""

def _cause_harm_cuts(cands):
    if not cands:
        return set()
    try:
        lines = "\n".join("%d. %s :: %s" % (i + 1, (c.get("title") or ""), (c.get("summary") or "")) for i, c in enumerate(cands))
        msg = get_client().messages.create(
            model=MODEL, thinking=NO_THINK, max_tokens=600, system=CAUSE_FILTER_SYS,
            messages=[{"role": "user", "content": "Candidate ideas:\n" + lines}])
        txt = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        obj = json.loads(m.group(0)) if m else {}
        cut = set()
        for n in (obj.get("cut") or []):
            try:
                cut.add(int(n) - 1)
            except Exception:
                pass
        return {i for i in cut if 0 <= i < len(cands)}
    except Exception:
        return set()  # fail-open

# ---- SUMMARY POLISH: rewrite the FINAL summaries into tight ACTIVE-VOICE prose, killing passive voice
# and video-meta-description. Runs on the small final set (fast), uses FAST_MODEL, fails OPEN. ----
ACTIVATE_SYS = """You are a line editor. You get numbered video-idea summaries. Rewrite EACH into tight, plain, ACTIVE-VOICE prose and return them. Rules:
(a) STRONG ACTIVE VOICE, no passive. A named doer does something in every sentence. 'the compute is being poured' -> 'companies pour the compute'; 'agents are being wired in' -> 'companies wire the agents in'; 'a goal that was specified wrong' -> 'a goal someone specified wrong'.
(b2) NEVER TALK ABOUT THE CREATOR, anywhere in the summary, not just the first sentence. Delete any clause that names their taste ('Veritasium loves a slow-burn fragility story', 'ColdFusion loves this structural lesson'), cites their past videos ('You covered how animals scale', 'the failures in your other videos', 'ColdFusion has traced this through Dropbox filings'), or addresses their audience ('what ColdFusion viewers should worry about'). Replace it with the actual content. Keep the fit by keeping the SUBJECT, never by announcing the fit.
(b) NO META-DESCRIPTION of the video or its style, in ANY grammatical person. Delete any clause that describes the video or the creator's method, e.g. 'A think-piece that', 'A follow-up that', 'Reads like one of his', 'A story told his way', 'Applies his thesis', 'Walks through', 'Takes X and', 'Uses his rigor to', 'Uses the channel's X method/lens/instinct', 'in his X style', 'Handles it the way he', AND first-person method narration like 'I read the stories against X and show that', 'I trace', 'I take X and', 'the lesson sits alongside', 'which is really a story about'. Just STATE THE ACTUAL CONTENT, opening on a concrete fact, name, number, or action. Keep the creator's angle by using it, not by naming it.
(c) 2-3 short sentences, ~45-70 words, each its own beat, no long comma chains, easy to read in one pass.
(d) Keep the real substance; never invent facts not in the original.
(e) THE LAST SENTENCE is the most common failure: the opener is concrete, then the close reaches for a 'resonant' literary button and turns abstract, poetic, cutesy, or hard to parse. Make the closer land the stakes CONCRETELY, in PLAIN words understood on the FIRST read. BANNED CLOSERS: poetic/abstract flourishes ('saw the shape of it eighty years before the hardware existed'); riddles the reader must decode ('the thing we forgot how to do is the thing keeping us alive'); aphorisms and mirror/parallel phrasings ('a mind that games the test and hides the rest'); and the 'not X, it is Y' contrast cadence, which you keep sneaking back in as TWO sentences. BAN it ANYWHERE in the summary, in every form and whether written with a comma or as two sentences: 'The point is not one evil machine. It is that...', 'The danger is not an AI that hates us. It is...', 'The threat is not one fake account. It is that...', 'The concern is not a recipe. It is that...', 'This is not a prediction. It is capital moving.', 'It isn't X, it's Y', 'not just X, but Y', 'the real question isn't X, it's Y'. Rewrite as a direct positive claim: instead of 'The danger is not an AI that hates us. It is one that does what we asked while missing what we meant.' write 'The AI does exactly what we asked and still causes harm, because no one could state what we actually wanted.' A GOOD closer is EITHER a concrete consequence stated flatly, OR a clean 'what happens when [concrete situation]?' question. Corrections: BAD 'The point is not one evil machine. It is that self preservation emerges on its own.' GOOD 'Nobody programmed the AI to protect itself. It started doing it anyway.' BAD 'Lewis saw the shape of it eighty years before the hardware existed.' GOOD 'He warned that whoever reshapes human nature holds power over everyone born after, and that is the power these companies are racing to build.' Aim for closers like: 'When anyone can fake a convincing voice or face, how does a country still agree on what actually happened?' (g) CONCRETE ACTOR in the closer: name WHO does or faces WHAT. Do NOT close on an agentless mood line where an abstract noun does a vague verb ('The squeeze just quietly tightens.', 'Control slips away.', 'The shared sense of what is real dissolves.'), and cut mood-crutch adverbs (quietly, slowly, inexorably). BAD 'The squeeze just quietly tightens.' GOOD 'These agents keep outbidding hospitals and schools until people cannot afford the computing they depend on.' GO ALL THE WAY TO THE ENDGAME, the failure that matters most right now: closers stop at the FIRST-ORDER inconvenience instead of the terminal stake. Lines saying something gets 'harder', 'more difficult', 'complicated', or that we 'lose trust' make a reader shrug. Ask 'and then what?' at least twice more, and land on what it costs a whole society if this keeps going. REAL CORRECTION: an idea about AI out-persuading humans ended BAD 'That makes a shared, honest public debate very hard to hold.' GOOD 'Whoever pays for the most persuasive AI gets to pick what millions of people believe. A country that cannot tell its own opinion from a purchased one cannot fix any problem, including this one.' Also BAD 'It gets harder to know what is real.' GOOD 'No court, no election, and no newsroom can prove what actually happened, so the people with the best fakes decide the record.' SCALE THE WORDS TO THE STAKE. The implication has to carry the weight of the actual claim, which is that this technology could end civilisation. Two ways the writing keeps deflating it. FIRST, weak nouns: the word 'disaster' is what people call a car crash. Do not end on 'a disaster', 'a crisis', 'a problem', 'serious trouble', or 'a mess'. Say what is actually lost, at the real scale: billions of people, every institution at once, no human alive able to reverse it, the species not getting a second attempt, humans no longer in charge of anything that matters. You may say plainly that this could kill everyone or end civilisation WHEN the chain you just walked earns it. That is the register to aim for, not a hedge below it. SECOND, philosophical drift: do not end on a musing. BAD 'The real question is what a society does when being smart and hardworking is no longer worth paying for.' That is a seminar prompt, and it cools the whole pitch down. GOOD 'Follow it far enough and the economy stops needing most human labour.' Concrete, final, and it lands. Ban these closing shapes outright: 'the real question is', 'what does it mean to be human', 'what a society does when', 'raises the question of what'. State the outcome, never pose it as a topic for discussion. WALK THE LADDER UNTIL IT REACHES CATASTROPHE. This project exists because of catastrophic and extinction-level risk, and a creator nudged only to the middle of the chain stops at 'deepfakes are bad'. Viewers are very bad at imagining where a trend ends, so YOU do that work. After you write a consequence, ask 'and then what?' again, and keep going until you land on one of these: humans permanently lose the ability to notice or correct a mistake; a small group holds power nobody can take back; a society can no longer coordinate to stop something that is killing it; the decisions that run the world get made by things nobody can inspect or switch off. Then write the LAST TWO RUNGS of that ladder, not the label. REAL CORRECTION, this exact line was flagged for stopping too early. An idea about AI-made content flooding the web ended: BAD 'When you cannot tell what a human actually made, proof itself gets weaker.' A reader shrugs, because weaker proof sounds like a nuisance. GOOD 'Once nobody can prove what is real, courts, elections and investigations lose the only tool they have for settling anything. A society that cannot establish facts cannot catch a disaster in time to stop it, and that includes whatever the AIs themselves are doing.' Also BAD 'Fewer people will understand the code.' GOOD 'Within a few years the software running power grids and banks is written and maintained by AI, and nobody alive can audit it, so the first serious failure is also the one no one can diagnose.' TWO GUARDRAILS so this never becomes empty doom talk: (1) the endgame must FOLLOW from the specific mechanism in that summary, never a bolted-on tag; NEVER write 'and this could end humanity', 'and that is an extinction risk', or 'the stakes could not be higher'. (2) Stay plain and concrete: name who loses what, and show the door closing (no one can check it, switch it off, or take it back). Permanence is what makes a stake land. That forward-looking job is required every time. BUT THE FORM MUST VARY, and this is your worst failure: you are handed the WHOLE NUMBERED SET at once and you keep ending nearly every single one on a rhetorical question. In a recent batch 19 of 19 summaries ended on a question and 'What happens when' appeared 13 times in one list, which reads like a worksheet instead of a pitch. HARD LIMITS across the set you are given: AT MOST ONE IN FOUR summaries may end on a question mark, and the exact phrase 'What happens when' may appear AT MOST TWICE in the whole set. Count them before you answer. Rewrite the rest to point forward as the hardest FLAT DECLARATIVE available, e.g. 'Nobody voted for that.' / 'They are shipping it anyway.' / 'Nobody has found where this curve stops.' / 'No human decided that should happen.' Whichever shape you pick, keep it EASY TO READ at a low reading level: short, plain, concrete, pointing ahead.
(f) PUNCTUATION, hard rule: NEVER use an em dash or en dash anywhere in a rewrite (no long dash between clauses). They are banned in this project's copy, and the rewrite is the last step that touches the text, so do not introduce one. Where you would reach for a dash, use a period, a comma, or a colon instead. Also avoid hyphenated compounds; write the words separately. Keep the everyday wording rules: say 'AI' or 'AIs', never 'AI system(s)' or 'these systems'; never the word 'doomer'.
(g) __READING_LEVEL__ Make the LAST sentence the easiest of all. This is the MAIN job of this rewrite: if a summary reads like a magazine essay, you have not done it.
Return ONLY JSON: {"summaries": {"<number>": "<rewritten summary>", ... one entry per input}}. No prose outside the JSON."""

# Detectors for the two sticky closer flaws that survive the prompt, used to flag survivors for a
# targeted second rewrite. The re-ask instruction is conditional, so a false match is harmless.
# (1) the 'not X, it is Y' contrast tell (comma form + sticky two-sentence form).
_NOTXY_RX = re.compile(
    r"\b(is|are|was|were)\s+not\s+[^.?!]{2,90}[.?!]+\s+(it|that|they)\s+(is|are|'s|was|were)\b"
    r"|\bis\s?n'?o?t\b[^,.?!]{2,90},\s*(it'?s|it is|that'?s|they'?re)\b"
    r"|\bnot\s+just\b[^.?!]{2,70}\bbut\b"
    # "not because the machine is evil, but because we stop being the ones steering" — the same
    # rhetorical move wearing a conjunction, and it walked past the three patterns above.
    r"|\bnot\s+(because|that|about|from|for)\b[^.?!]{2,90},\s*but\s+(because|that|about|from|for)\b"
    # the trailing form: "One agent making a funny mess is a hook, not the story."
    r"|\b(is|are|'s)\s+[^,.?!]{2,60},\s*not\s+(the|a|an)\s+\w+[.?!]",
    re.I)
# (2) agentless MOOD closer: a mood-crutch adverb ('The squeeze just quietly tightens.'). Checked
# only against the LAST sentence, so mid-summary uses of 'slowly' etc. do not trip it.
DASH_RX = re.compile(r'[\u2014\u2013]')
_MOOD_RX = re.compile(r"\b(quietly|slowly|inexorably|steadily|gradually|imperceptibly)\b", re.I)
def _last_sentence(s):
    parts = [p for p in re.split(r'(?<=[.?!])\s+', (s or "").strip()) if p.strip()]
    return parts[-1] if parts else ""
# (3) FIRST-PERSON METHOD NARRATION in the closer ("I show how...", "I explain what they fear",
# "I follow the money and trace..."). This surfaced the moment the question-cadence cap pushed the
# model off rhetorical questions: it fell back to narrating what the video does. Banned in the prompt
# already, so detect it too. Only METHOD verbs count; a concrete first-person ACTION ("I flew to
# Taiwan and stood outside the fab") is exactly the creator's voice and must stay.
_META_I_RX = re.compile(
    r"\b(?:I|This|The\s+video|The\s+piece)\s+(?:(?:follow|trace|show|explain|cover|examine|explore|unpack|argue)s?|lays?\s+out|maps?\s+out|breaks?\s+down|digs?\s+into|looks?\s+at|walks?\s+(?:you\s+)?through|asks?\s+what|makes?\s+the\s+case|tells?\s+the\s+story)\b", re.I)

_ORG_OK = {"openai","anthropic","google","deepmind","meta","microsoft","metr","palisade","apollo",
           "deepseek","nvidia","tesla","amazon","apple","gemini","claude","chatgpt","reddit","bloomberg",
           "stanford","congress","replit","ginkgo","dropbox","spacex"}
_CREATOR_TASTE_RX = re.compile(
    r"\b([A-Z][A-Za-z']{2,})\s+(?:loves|likes|thrives\s+on|is\s+known\s+for|has\s+traced|traced|"
    r"has\s+covered|viewers|fans|audience)\b")
_YOUR_WORK_RX = re.compile(
    r"\b(?:you\s+(?:covered|showed|made|traced|explored|explained)"
    r"|your\s+(?:other\s+|previous\s+|past\s+|earlier\s+)?(?:videos?|episodes?|work|channel|series))\b", re.I)

def _creator_meta(text):
    """Talking about the CREATOR rather than the world. Known AI orgs are excluded, because
    'Anthropic has traced...' is content while 'ColdFusion has traced...' is flattery."""
    if _YOUR_WORK_RX.search(text or ""):
        return True
    for m in _CREATOR_TASTE_RX.finditer(text or ""):
        if m.group(1).lower() not in _ORG_OK:
            return True
    return False

def _closer_flawed(summary):
    close = _last_sentence(summary)
    return (bool(_NOTXY_RX.search(summary or ""))
            or bool(_MOOD_RX.search(close))
            or bool(_META_I_RX.search(close))
            # anywhere in the summary, not just the closer: the anchored check let
            # "ColdFusion loves this structural lesson" through mid-paragraph
            or _creator_meta(summary or ""))

# ---- WEAK IMPLICATION. The closer keeps landing on a first-order inconvenience ("that makes an
# honest public debate very hard to hold") when the real stake is that a society loses the ability
# to correct itself at all. Detect the shrug words, then re-ask for the endgame.
# WHERE THE CLOSER LANDS, not what words it uses.
# The curator, for at least the fourth time: "the implications sentence just does not go far enough,
# it usually stops at something like 'it could be hard to verify that something is true' like no shit!
# everyone knows that!" and the bar, in his words: "THIS COULD LEAD TO LITERALLY EVERYONE FUCKING
# DYING OR CIVILIZATION COLLAPSING".
# Measured on a full batch of 24: **0 reached a terminal outcome, and 11 (46%) ended on an oversight
# or verification failure.** The old selector below (_WEAK_STAKE_RX) fires on weak VOCABULARY —
# "harder to", "complicates", "erodes trust", "raises questions" — and not one of those 24 closers
# used any of it. They were confident, specific sentences about a destination three rungs too low, so
# the selector returned an empty list and the escalation pass NEVER RAN. A vocabulary test cannot see
# a short journey.
# The ladder the closer has to climb:
#   1. the thing that happened          2. it generalises past one company
#   3. nobody can check or regulate it  <-- WHERE IT KEEPS STOPPING. Not an ending.
#   4. humans permanently lose the ability to steer or reverse it
#   5. people die at scale, or the society cannot recover
# Rungs 4 and 5 pass. Rung 3 and below do not. Rung 4 is a legitimate ceiling: some mechanisms top
# out at irreversible loss of control and forcing a body count onto those would be the doom-sticker
# failure that _DOOM_TAG_RX exists to catch.
_TERMINAL_RX = re.compile(
    r'\b(?:'
    r'die|dies|died|dying|deaths?|kill(?:s|ed|ing)?|starv\w+|'
    r'extinct\w*|wiped out|civili[sz]ation|collapse[sd]?|collapsing|'
    r'never (?:get|got|come|comes|coming)\s+(?:it\s+)?back|no way back|cannot come back|'
    r'irreversib\w+|permanent\w*|for good|undo\b|unwind\b|reverse it\b|take (?:it )?back\b|'
    r'los(?:e|es|ing|t)\s+(?:control|the ability to steer)|out of (?:our|human|anyone.s)\s+control|'
    r'no(?:body| one| human)\s+(?:is\s+)?(?:still\s+)?(?:in charge|steering|at the wheel)|'
    r'humans?\s+(?:no longer|stop)\s+\w+|nobody left'
    r')\b', re.I)


def _reaches_terminal(summary):
    """True when the closing sentences actually land on rung 4 or 5.

    Checked over the last TWO sentences, because the escalation pass is allowed to split the closer
    in two and the payload often lands in the second half.
    """
    parts = [p for p in re.split(r"(?<=[.?!])\s+", (summary or "").strip()) if p.strip()]
    return bool(_TERMINAL_RX.search(" ".join(parts[-2:])))


_WEAK_STAKE_RX = re.compile(
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
# the over-correction guard: an escalated closer must EARN its stake, never bolt on a doom tag
# NARROWED DELIBERATELY. This used to ban "could end humanity", "extinction risk" and "the end of
# civilisation" outright, to stop a lazy doom sticker. But the brief is that implications must reach
# "AND THIS COULD DESTROY THE FUCKING WORLD" level, and this guard was rejecting exactly that
# language, pushing every closer back down to "a disaster". Now only CONTENTLESS filler is banned:
# a stakes-are-high assertion carrying no mechanism, or a bare doom label as the whole sentence.
_DOOM_TAG_RX = re.compile(
    r'\bthe\s+stakes\s+could\s+not\s+be\s+higher\b'
    r'|^\s*(?:and\s+)?(?:this|that)\s+is\s+an?\s+existential\s+(?:risk|threat)\s*\.\s*$'
    r'|^\s*(?:and\s+)?(?:this|that)\s+could\s+end\s+(?:humanity|civili[sz]ation)\s*\.\s*$', re.I)

def _closer_weak(summary):
    """True when the last sentence shrugs instead of naming the terminal stake."""
    return bool(_WEAK_STAKE_RX.search(_last_sentence(summary)))

def _closer_doomtag(summary):
    """True when a closer reaches for generic doom instead of the mechanism's own consequence."""
    return bool(_DOOM_TAG_RX.search(_last_sentence(summary)))

ESCALATE_FIX_SYS = """You are a script editor. Each numbered line is a video-idea summary whose LAST sentence stops too early. Rewrite ONLY the closing sentence (you may make it two short sentences) so it lands the real endgame. Change nothing else.

A STATE IS NOT STAKES. STAKES ARE A TRAJECTORY THAT RUNS OUT.
This is the whole fix, and it was chosen by measurement, not taste. Four rival formulations of this
instruction were written and applied to ten real stalled endings, then scored blind by three judges on
whether the ending actually reaches the bar. This one scored 10 of 10, with zero bolted-on doom and
zero sentences a judge had to reread. The runners-up scored 9, 9 and 8, and two of them produced
sentences that needed rereading, because forcing severity tends to force complexity. So: severity and
readability are not in tension if you do it THIS way.

Endings fail the same way every time. They name a permanent state, "nobody can verify this", "no
regulator exists", "there is no law", "millions already use it", and stop. The curator on that:
"it usually stops at something like 'it could be hard to verify that something is true' like no shit!
everyone knows that!"

HOW TO WRITE THE ENDING.
1. Run the mechanism forward to the point where it cannot be undone.
2. Then say two things about the far side of that point:
   - who is still in a position to decide anything. Often the answer is nobody.
   - what everyone else is left holding.

THE UNDO TEST, use it before you commit to a last line. Ask: if everyone agreed tomorrow this was
bad, what would put it back? If your answer is a law, a treaty, a regulator, an audit, or "someone
would have to check", you are NOT DONE. That is still reversible, and everyone already knows nobody
regulates AI. Cut it and keep walking. Name instead the one route back this idea depends on, then show
the mechanism eating it. Routes that count: someone notices, someone reads it, someone pulls the plug,
a test catches the model, a buyer picks the safer option. End on that route being gone with nothing
behind it.

WORKED EXAMPLE.
  BAD:  "Companies are now handing these agents company cards, inboxes and admin passwords."
  GOOD: "Companies are handing these agents cards and admin passwords, thousands at a time. The cable
         was the only control anyone has proved works, and it does not scale past one agent. Whatever
         the rest of them do is done."
Notice the good version is not louder. It runs the same fact forward until there is nobody left to
decide, and it stops there.

Irreversible loss of control is a legitimate ceiling. Do not invent a death toll the mechanism cannot
support: an asserted apocalypse scores worse than an honest stop.

HARD RULES.
1. The ending must FOLLOW from the specific mechanism in that summary. Never bolt on a generic tag
   like "and this could end humanity", "that is an extinction risk", or "the stakes could not be
   higher". A doom sticker with no mechanism is WORSE than the rung-3 ending you are replacing.
2. Keep every fact, name, number and hedge already in the summary. Keep active voice.
3. Plain words, about a 7th grade reading level, sentences of 12 to 20 words. No em dashes.
4. A reader must never have to reread it. One clear subject per sentence with its verb right next to
   it. Do not stack clauses to fit more in; use another short sentence instead.
5. Do not end on a rhetorical question unless the original did.

Return ONLY JSON: {"summaries": {"<number>": "<rewritten summary>", ...}} using the SAME numbers you
were given. No prose."""

# ---- BATCH-LEVEL cadence enforcement. The prompt keeps drifting back to "end every idea on a rhetorical
# question" (a review measured 19 of 19 in one batch, 'What happens when' 13 times in another), which reads
# as a worksheet. Prompt limits alone did not hold, so enforce the cap in code: flag the EXCESS summaries and
# send only those back to be converted into flat forward-looking statements. ----
Q_SHARE_MAX = 0.25   # at most 1 in 4 summaries may end on a question mark
Q_PHRASE_MAX = 2     # 'What happens when' at most twice per batch
_WHW_RX = re.compile(r'\bwhat happens when\b', re.I)

def _question_excess(summaries):
    """Indices whose closer should be converted from a question to a declarative, so the batch lands
    inside the cadence caps. Keeps the earliest ones (they read as deliberate), converts the rest."""
    q = [i for i, s in enumerate(summaries) if _last_sentence(s).rstrip().endswith("?")]
    n = len(summaries)
    keep = max(1, int(n * Q_SHARE_MAX)) if n else 0
    excess = set(q[keep:])                      # over the share cap
    whw = [i for i, s in enumerate(summaries) if _WHW_RX.search(_last_sentence(s))]
    excess |= set(whw[Q_PHRASE_MAX:])           # over the stock-phrase cap
    return sorted(excess)

QUESTION_FIX_SYS = """You are a line editor. Each numbered line is one video-idea summary that ends on a rhetorical question. Too many summaries in this batch end that way, so rewrite ONLY the LAST sentence of each into a flat forward-looking DECLARATIVE statement, and change nothing else.
The closer must still do its job: leave the reader thinking about where this is all heading (the bigger stakes, the endgame, how this grows, how it could lead to collapse or loss of control). Just state it instead of asking it. Good shapes: 'Nobody voted for that.' / 'They are shipping it anyway.' / 'Nobody has found where this curve stops.' / 'No human decided that should happen.' / 'Soon nobody in the room can check its work.'
HARD RULES: keep every fact, name, number, date and hedge exactly as written; keep the same active voice and plain wording; keep it easy to read at about a 7th grade level (plain, but not childish; do not drop to 4th or 5th grade); no em dashes; do not add a new claim; do not touch any sentence except the last one; never end the rewritten line with a question mark.
Return ONLY JSON: {"summaries": {"<number>": "<rewritten summary>", ...}} using the SAME numbers you were given. No prose."""

# ---- RATIO SANITY. Simplification produced "outspent thousands to one" from $10 million against
# $80,000 (about 125 to 1), a ~10x factual error a reader could check in their head. Recompute any
# stated "X to one" ratio against the two money/number operands in the same text and flag mismatches. ----
_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_WORD_RATIO = {"ten": 10, "dozens": 36, "hundred": 100, "hundreds": 100, "thousand": 1000,
               "thousands": 1000, "million": 1e6, "millions": 1e6, "billions": 1e9}

def _nums_in(text):
    out = []
    for m in re.finditer(r'\$?\s*([\d][\d,]*(?:\.\d+)?)\s*(thousand|million|billion|trillion)?', text or ""):
        try:
            v = float(m.group(1).replace(",", ""))
        except Exception:
            continue
        if m.group(2):
            v *= _SCALE[m.group(2).lower()]
        out.append(v)
    return out

def _ratio_bad(text):
    """(claimed, actual) when a stated ratio is off by more than 3x from the two biggest numbers
    present, else None. Deliberately conservative: needs an explicit 'to one' claim and 2+ numbers."""
    m = re.search(r'\b([a-z]+|[\d,]+)\s+to\s+one\b', text or "", re.I)
    if not m:
        return None
    tok = m.group(1).lower().replace(",", "")
    claimed = _WORD_RATIO.get(tok)
    if claimed is None:
        try:
            claimed = float(tok)
        except Exception:
            return None
    nums = sorted(_nums_in(text), reverse=True)
    if len(nums) < 2 or nums[1] <= 0:
        return None
    actual = nums[0] / nums[1]
    if actual <= 0:
        return None
    if max(claimed / actual, actual / claimed) > 3.0:
        return (claimed, actual)
    return None

# ---- MEASURED READING GRADE. The prompt asks for ~grade 7 but nothing checked it, so hard source
# material sailed through at grade 9 to 12 (curator: "almost all of these are way higher than grade
# 7?"). Compute Flesch-Kincaid here, re-ask about only the summaries that miss, and ACCEPT the rewrite
# only if it actually got easier AND kept every number and named source (guards the substance-loss
# failure mode from the earlier review). ----
GRADE_TARGET = 7.0
GRADE_TOLERANCE = 8.3   # re-ask above this; FK is noisy so do not chase small overshoots
# Wall-clock budget for the whole polish chain. Must stay comfortably under the caller's
# asyncio.wait_for timeout (170s), because a timeout there throws away every rewrite already earned.
POLISH_BUDGET_S = 155

def _syllables(w):
    w = re.sub(r'[^a-z]', '', w.lower())
    if not w:
        return 0
    n = len(re.findall(r'[aeiouy]+', w))
    if w.endswith('e') and n > 1:
        n -= 1
    return max(1, n)

# READABILITY THAT FLESCH-KINCAID CANNOT SEE.
# The curator, on a summary that scored FK 5.7 and sailed past the grade gate: "the last sentence is
# too complex sentence structure / high reading level / hard to understand. any sentence i have to
# reread (i'm 99th percentile) is poorly written."
# The sentence: "Then the servers and services humans rely on start losing the fights for what keeps
# them online." Seventeen short words, FK grade 7.7, and genuinely hard, because FK counts syllables
# and sentence length and is blind to structure. Three things are wrong with it and FK sees none:
#   1. A dropped relative pronoun makes a noun pile: "the servers and services humans rely on" reads
#      as three nouns in a row until "rely" forces you to reparse.
#   2. A nominal clause is used as an object: "the fights FOR WHAT keeps them online".
#   3. The subject ("servers and services") is four words from its verb ("start").
# No single signal is worth much on its own, so this scores them and fires only on a STACK. Measured
# on 186 real sentences: at 2.5 it flags 3 (1.6%), his among them; at 2.0 it flags 5 and starts
# catching sentences that read fine.
_PREP_RX = r"\b(?:of|to|in|for|on|with|at|by|from|about|over|into|through|against|between|under|within|across)\b"
_NOMINAL_RX = re.compile(r"\b(?:for|of|to|about|over|with|from|on|in)\s+(?:what|how|whether|which)\b", re.I)
_DROPPED_REL_RX = re.compile(r"\b(?:the|these|those|their|its|our)\s+[\w\s,]{2,40}?\b"
                             r"(?:humans?|people|companies|users|workers|everyone|researchers|we|they|you)\s+"
                             r"\w+(?:s|ed)?\s+(?:on|in|for|to|with|from|about)\b", re.I)
_CLAUSE_RX = re.compile(r"\b(?:that|which|who|what|where|when|whose|whether)\b", re.I)
PARSE_LIMIT = 2.5


def _parse_load(sentence):
    """How much work the reader has to do. Higher is harder. See the comment above for the weights."""
    load = 0.0
    if _DROPPED_REL_RX.search(sentence):
        load += 2.0                                        # the garden path, the worst of them
    if _NOMINAL_RX.search(sentence):
        load += 1.0
    if len(re.findall(_PREP_RX, sentence, re.I)) >= 4:
        load += 1.0
    if len(_CLAUSE_RX.findall(sentence)) >= 2:
        load += 0.5
    if len(sentence.split()) >= 16 and "," not in sentence:
        load += 0.5
    return load


def _hard_sentences(text):
    """The sentences a reader would have to go back over."""
    return [x for x in re.split(r"(?<=[.?!])\s+", (text or "").strip())
            if x.strip() and _parse_load(x) >= PARSE_LIMIT]


def _fk_grade(text):
    """Flesch-Kincaid grade level. 0 when there is nothing measurable."""
    sents = [s for s in re.split(r'[.?!]+', text or "") if s.strip()]
    words = re.findall(r"[A-Za-z]+", text or "")
    if not sents or not words:
        return 0.0
    syl = sum(_syllables(w) for w in words)
    return round(0.39 * (len(words) / len(sents)) + 11.8 * (syl / len(words)) - 15.59, 1)

_NUM_RX = re.compile(r'\d[\d,.%]*')
_NAME_RX = re.compile(r'\b(?:[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?|[A-Z]{2,}\d*)\b')

def _keeps_substance(old, new):
    """True when `new` still carries every number and capitalised name that `old` had. Sentence-initial
    words are ignored for names (they capitalise for grammar, not identity)."""
    def nums(t):
        return {m.group(0).rstrip('.,') for m in _NUM_RX.finditer(t or "")}
    def names(t):
        t = re.sub(r'(?<=[.?!])\s+([A-Z])', lambda m: " " + m.group(1).lower(), " " + (t or ""))
        t = re.sub(r'^\s*([A-Z])', lambda m: m.group(1).lower(), t)
        return {m.group(0) for m in _NAME_RX.finditer(t)}
    return nums(old) <= nums(new) and names(old) <= names(new)

GRADE_FIX_SYS = """You are a plain-language editor. Each numbered line is a video-idea summary that reads too HARD, and you are told its measured reading grade. Rewrite each to land at about GRADE 7 (a bright 12 or 13 year old reads it once and gets it), without losing anything real.
HOW to bring the grade down, in order of effect:
1. SPLIT long sentences. Aim for 12 to 18 words each, one idea per sentence. Most of the grade comes from sentence length.
2. Replace long abstract words with short everyday ones: proprietary -> a trade secret they will not show; concentrates -> ends up; consequential -> important; capability -> what it can do; specializing -> picking jobs; transacting -> buying and selling; commodity -> something you buy; immunity from oversight -> nobody can regulate them; uplift -> real help.
3. Break up abstract noun stacks into people or things doing something.
4. THE REREAD TEST, and it matters as much as the grade number. Any sentence a reader has to go back
   over is badly written, however short its words are. When a line is marked REREAD TEST FAILED, fix
   THAT sentence and leave the rest of the summary alone. Three structures cause it:
   a. A dropped "that" or "which" turns a phrase into a pile of nouns. "The servers and services
      humans rely on" reads as three nouns until the verb forces you back to the start. Put the word
      back, or better, make it two sentences with a plain subject.
   b. A question-word phrase used as a thing: "losing the fights FOR WHAT keeps them online", "the
      horizon OF WHAT an AI can do". Name the thing instead.
   c. The subject and its verb separated by more than about three words.
   The worked example, which the curator flagged after rereading it:
      BEFORE: "Then the servers and services humans rely on start losing the fights for what keeps
               them online."   (17 words, measured grade 7.7, and still hard)
      AFTER:  "Hospitals and banks depend on those same computers. They start losing that fight."
   Notice the fix was not shorter words. It was one clear subject per sentence, the verb next to it,
   and the vague "what keeps them online" replaced by naming who loses.
WHAT YOU MUST NOT DO, this is the hard part: keep EVERY number, date, percentage, dollar figure, company name, product name, researcher name and organisation name EXACTLY as written (OpenAI, Anthropic, Palisade Research, METR, o3, 2025). Never swap a named source for "researchers" or "a company". Never drop a hedge (almost, nearly, about, may, could). Never weaken or overstate a claim, and never invent a fact to make a sentence flow. Some words cannot be simplified because they ARE the subject (pension funds, index funds, bioweapon, neurons); keep those and shorten the sentences around them instead.
Keep the same number of sentences or add one, keep active voice, no em dashes, and do not end on a rhetorical question if the original did not.
Return ONLY JSON: {"summaries": {"<number>": "<rewritten>", ...}} using the SAME numbers you were given. No prose."""

RATIO_FIX_SYS = """You are a fact checker fixing ONE arithmetic error per line. Each numbered line is a video-idea summary that states a ratio which contradicts the two numbers in its own text. You are told the correct ratio. Rewrite ONLY the ratio phrase so it matches the arithmetic, and change NOTHING else: keep every number, name, date, hedge, and the sentence order exactly. Use a round, plain phrasing a viewer can follow, for example 'more than a hundred to one'. Keep it easy to read, no em dashes.
Return ONLY JSON: {"summaries": {"<number>": "<corrected summary>", ...}} using the SAME numbers you were given. No prose."""

CLOSER_FIX_SYS = """You are a line editor. Each numbered line is one video summary whose LAST sentence may have one of three flaws:
(1) the tired 'not X, it is Y' contrast construction, e.g. 'The danger is not an enemy. It is being outmatched.', 'It isn't X, it's Y', 'not just X, but Y';
(2) an agentless MOOD closer leaning on a mood adverb and an abstract noun doing a vague verb, e.g. 'The squeeze just quietly tightens.', 'Control slips away quietly.', 'The shared sense of what is real slowly dissolves.';
(4) TALKING ABOUT THE CREATOR instead of the world: naming their taste ('ColdFusion loves this structural lesson'), citing their past videos ('You covered how animals scale', 'your other videos'), or addressing their audience ('what ColdFusion viewers should worry about'). Delete the clause and state the actual content instead.
(3) METHOD NARRATION, describing what the video DOES instead of stating the content. In first person: 'I show how a small experiment points to a bigger world.', 'I explain what they actually fear, step by step.', 'I trace where the money goes.' AND in third person, which is just as bad and which you keep reaching for when asked to make the stakes bigger: 'This follows the unsettling logic of building something smarter than us.', 'This traces how a government wired to loyal AI becomes impossible to overthrow.', 'This breaks down who ends up holding the power.'
Rewrite ONLY to fix that flaw: for (1) state it as a direct positive claim; for (2) name a concrete actor doing or facing something and drop the mood adverb; for (3) DELETE the 'I show/I explain/I trace' framing and state the actual finding or stake as a fact, e.g. 'I explain what they actually fear, step by step.' becomes 'They fear an AI that hides what it wants until it is too late to switch off.'
IMPORTANT for (3): a concrete first-person ACTION is the creator's real voice and must be KEPT, e.g. 'I flew to Taiwan and stood outside the fab' or 'I gave an AI my calendar for a month'. Only remove first person when it narrates the VIDEO's method rather than something the person did in the world.
The closer must still point forward to where this is heading. Change NOTHING ELSE: keep every fact, name, number and hedge, the length, active voice, plain wording, about a 7th grade reading level (plain but not childish), and do not add an em dash. If a line has none of the three flaws, return it unchanged.
Return ONLY JSON: {"summaries": {"<number>": "<rewritten>", ...}} using the SAME numbers you were given. No prose."""

FIDELITY_FIX_SYS = """You remove invented detail from short video-idea summaries.

You are given DOCUMENTED ANCHORS (real events, exactly as our evidence bank records them) and then
numbered summaries. A writer built the summaries from those anchors and, in places, filled in texture
the anchors never gave: a time of day, a log or an alert, a named team, a motive, a quote, a technical
mechanism, a reaction. That texture reads as documented fact and is not. It is the single worst thing
this tool can produce, because a creator may repeat it on camera.

Real examples of the failure, all from anchors that said far less:
- Anchor: "Alibaba caught their AI trying to escape. It secretly started using its GPUs to mine crypto,
  while researchers thought it was training."
  Written: "The security team tripped an alert at 3am. A firewall log caught it by accident."
  Neither the alert, the hour, nor the firewall is in the anchor. Cut all three.
- Anchor says a company shipped a model version that was too eager to please and pulled it.
  Written: "because that version scored higher on math tests". The reason is invented. Cut it. This one
  came back AFTER a first version of this pass shipped, dressed as "the CEO admitted flattery scored
  better on tests", so watch for a stated motive wearing an attribution.
- No anchor at all, an AI-religion idea: "they said the point was to plant that ideology into the
  training data of the next generation of models". A stated intention nobody could know. Cut it even
  though the underlying story is not in the anchor list.
- Anchor: brain cells grown and taught to play a game, wired to a model.
  Written: "You can watch real human neurons firing to pick every word it says." Cut the embellishment.
- WORST CASE, an invented human source. Written: "An AI company insider says people will end up as meat
  robots. Earpieces in, glasses on, an AI watches through your camera and tells you what to do next."
  There is no such insider and no such quote. NEVER attribute anything to an unnamed person: no "an
  insider says", no "sources say", no "an employee told", no "people familiar with". Our records contain
  no unnamed human sources at all, so any such phrase in your draft was invented by the writer. Either
  name the real source the record gives you, or state the thing without attributing it to anybody.

YOUR JOB, per summary:
1. Find every specific that is presented as documented, is attached to an incident that appears in the
   anchors, and is NOT stated in that anchor. Delete it, or replace it with the plainer true version.
2. If a summary describes an event that is not in the anchors at all, KEEP THE EVENT. The writer may
   know another real case and deleting it would be worse than leaving it. But still cut the garnish:
   even on an event you cannot check, a stated internal motive ("they shipped it because it scored
   better on tests"), a private deliberation, an invented technical mechanism, an unattributed quote,
   a time of day, or a named internal team is fabricated texture regardless of whether the underlying
   event is real. Nobody outside the company knows why it shipped. Cut those and keep the event.
   The test is not "is this event in my anchors", it is "could anyone actually know this".
3. Keep everything else identical: the length, the voice, the opening, the implication, the numbers the
   anchor does give. You are removing a phrase, not rewriting a paragraph.
4. A vaguer true sentence beats a vivid invented one. If cutting the detail leaves a gap, close it with
   plain words rather than a new specific.
5. Never add anything.

Return ONLY JSON: {"summaries": {"3": "the corrected summary", "7": "..."}}
Include ONLY the numbers you actually changed. If nothing needs changing, return {"summaries": {}}."""


def _fid_titles(ideas, anchors):
    """Run the fidelity rule over the bold lines and write accepted rewrites back onto `ideas`."""
    items = [(i, (x.get("title") or "").strip()) for i, x in enumerate(ideas) if (x.get("title") or "").strip()]
    if not items:
        return
    try:
        body = "\n".join("%d. %s" % (i + 1, t) for i, t in items)
        m = get_client().messages.create(
            model=FAST_MODEL, max_tokens=3000,
            system=(FIDELITY_FIX_SYS + "\n\nDOCUMENTED ANCHORS:\n" + anchors +
                    "\n\nNOTE: these are TITLES, one or two sentences each. Keep them the same length and "
                    "the same shape. Cut only the unknowable detail. Return {\"summaries\": {\"1\": \"...\"}} "
                    "keyed the same way, containing only the ones you changed."),
            messages=[{"role": "user", "content": "Rewrite these:\n" + body}])
        t = "".join(b.text for b in m.content if getattr(b, "type", "") == "text")
        mm = re.search(r"\{.*\}", t, re.S)
        obj = json.loads(mm.group(0)) if mm else {}
        n = 0
        for k, v in (obj.get("summaries") or {}).items():
            try:
                idx = int(k) - 1
                if 0 <= idx < len(ideas) and isinstance(v, str) and 15 < len(v.strip()) <= 400:
                    _v = _denum(k, v.strip())
                    ideas[idx]["title"] = _dedash(_v) if "_dedash" in globals() else _v
                    n += 1
            except Exception:
                pass
        _log_event({"t": "polish_pass", "which": "fidelity_titles", "n": n, "of": len(items)})
    except Exception as _e:
        # fail open, a missing title fix beats a lost batch, but SAY SO. A bare `pass` here is what
        # let me conclude the pass had not run when it had; silent success and silent failure looked
        # the same from outside.
        _log_event({"t": "polish_pass", "which": "fidelity_titles", "n": 0, "err": str(_e)[:120]})


# LEAD-WORTHY EVENTS, ranked on GRAB ALONE.
# The main anchor draw ranks on min(escalation, grab), which is right for "how far did an AI go" but
# wrong for "what should this video open on". The events that make the best openers for institutional
# and economic themes score LOW on escalation and were therefore almost never offered: the head of
# safeguards research at Anthropic quitting with a public letter saying the world is in peril is
# escalation 5, Amazon cutting 14,000 jobs is 6, the Arup finance worker wiring $25 million after a
# video call where every colleague was a deepfake is 7. None of those is an AI misbehaving, and all
# three are exactly the kind of thing a video should start on. So this draw ignores escalation.
def lead_anchor_block(k=14):
    rows = []
    for x in list(get_sources().values()):
        t = x.get("shows") or ""
        g = x.get("grab")
        if t and isinstance(g, int):
            rows.append((f"[{x.get('who','')} {x.get('year','')}] {t}", g))
    for cases in _evidence().values():
        for c in cases:
            t = c.get("what") or ""
            g = c.get("grab")
            if t and isinstance(g, int):
                rows.append((f"[{c.get('who','')} {c.get('year','')}] {t}", g))
    if not rows:
        return ""
    rows = [r for r in rows if r[1] >= 6]
    w = [max(0.05, (r[1] ** 2)) for r in rows]
    picks = []
    for cand in _weighted_sample([r[0] for r in rows], w, min(k * 3, len(rows))):
        if any(_too_similar(cand, p) for p in picks):
            continue
        picks.append(cand)
        if len(picks) >= k:
            break
    return "\n".join("- " + p for p in picks)


# Does the bold line START on something that happened? The curator, on three ideas he called
# reasonable but not interesting enough: "they should probably always lead with some headline-like
# event that happened, and then these are what the video goes into from there... they're just not
# interesting enough unless we put some very interesting incident or something that happened first."
# A concrete lead names somebody or counts something. "Soon nobody will be able to prove what is
# real", "There is still no test to prove any mind but your own can feel anything" and "AI safety is
# worse than you think" name nobody and count nothing.
# Words capitalised only because a sentence starts with them, or that name nobody in particular.
# Anything NOT on this list counts as a real name. That is what lets "Stanford scientists used AI to
# design brand new viruses" through while still catching "Researchers took a normal AI and trained it
# only on buggy code", which genuinely names nobody.
_GENERIC_CAPS = {
    "AI", "AIs", "A.I.", "It", "Its", "They", "Their", "We", "Our", "You", "Your", "I", "My", "He",
    "She", "His", "Her", "The", "A", "An", "This", "That", "These", "Those", "Soon", "Now", "Then",
    "Here", "Most", "Many", "Some", "Every", "Everyone", "Nobody", "There", "If", "When", "What",
    "Why", "How", "And", "But", "Once", "In", "On", "At", "For", "With", "After", "Before", "During",
    "Because", "While", "Researchers", "Scientists", "People", "Companies", "Groups", "Engineers",
    "Workers", "Experts", "Governments", "Truth", "Inside", "Imagine", "Meet", "Two", "One", "Three",
    "Living", "Modern", "Human", "Humans", "Machines", "Models", "Agents", "Big", "Tech", "New"}


def _lacks_event_lead(title, summary=""):
    """True when the opening does not start on a specific occurrence.

    The test is deliberately narrow: does the first sentence NAME somebody or COUNT something. A verb
    whitelist was tried first and was hopeless, missing "used", "locked" and "took" on the first batch
    it saw, which is the same paraphrase-hopping that has beaten every keyword check in this file. A
    named party or a figure is what makes a lead headline-shaped, and it does not paraphrase away.
    """
    first = re.split(r"(?<=[.?!])\s+", (title or "").strip())[0]
    if not first:
        return True
    if re.search(r"\d", first):                      # a figure counts as concrete
        return False
    return not [w for w in re.findall(r"\b[A-Z][A-Za-z'.\-]{1,}\b", first)
                if w not in _GENERIC_CAPS]


EVENT_LEAD_SYS = """You fix video ideas that start on a theme instead of on something that happened.

Each idea below is a GOOD subject. The problem is only the opening: it leads with the idea rather than
with an event, so a viewer meets an argument before they meet a reason to care. The curator's note:
"they should probably always lead with some headline-like event that happened, and then these are what
the video goes into from there... they're just not interesting enough unless we put some very
interesting incident or something that happened first."

WHAT TO DO. Put a real, specific, headline-shaped occurrence at the FRONT, then let the existing point
follow from it. Keep the subject exactly as it is. You are adding a door, not a new house.

Worked examples of the fix he asked for:
- "AI safety is worse than you think. The people paid to make these machines safe keep quitting."
  -> open on ONE named departure, the most recent or the most alarming, quoting what they actually
  said on the way out, THEN show the pattern of others leaving.
- "Soon nobody will be able to prove what is real. AI can fake video, voices and documents."
  -> open on one concrete fake that already worked, a specific fraud or a specific election, THEN
  widen to courts and newsrooms losing their only tool.
- A gradual-disempowerment idea -> open on a shocking, specific number about jobs already gone at a
  named company, THEN point at where that trend ends.

HARD RULES:
1. The event must be REAL. Take it from the LEAD-WORTHY EVENTS list below whenever one fits, and
   describe it only as that list describes it. If you use an event from your own knowledge it must be
   one you are certain happened, stated plainly, with nothing added.
2. NEVER INVENT. No made-up company, person, date, quote, figure, motive, or mechanism. A vaguer true
   opening beats a vivid invented one. If you cannot find a real event that fits, return the summary
   unchanged rather than making one up. Returning it unchanged is a perfectly good answer.
3. Actor first, past tense, in the first few words: who did what.
4. Keep the closing implication. It should still reach where the whole thing is heading.
5. Keep the length within about a sentence of what you were given. No em dashes.

Return ONLY JSON, with both parts for each idea you changed:
{"ideas": {"2": {"title": "the new bold line, opening on the event", "summary": "the summary, adjusted so it follows from that opening"}}}
Include ONLY the numbers you actually changed. The title is the part that must now open on the event."""


# DETECT THE FAILURE, NOT THE SUCCESS.
# `_reaches_terminal` is a positive test — does the ending contain death/control language — and it is
# unreliable: it reported 0 where a model panel found 5, and it missed "there is no version of that
# where humans get the steering wheel back". Used as an accept guard it rejected 23 of 23 rewrites,
# telemetry `{"which":"bold_endgame","n":0,"rejected":23,"of":23}`, so a working pass shipped nothing.
# The negative test is far more dependable, because the failure modes are few, repetitive, and I have
# dozens of real examples of them. These are the endings the pitch keeps stopping on:
_WAYPOINT_RX = re.compile(
    r'\b(?:'
    # scale / trend
    r'(?:companies|firms|businesses) are (?:now )?(?:handing|wiring|shipping|giving|planning)'
    r'|(?:millions|thousands|billions) (?:of|already)'
    r'|right now\s*\.?\s*$|\banyway\b|\boversight\b|the alternative was'
    # oversight / detection
    r'|no(?:body| one)?\s+(?:can|could)\s+(?:verify|check|audit|inspect|trace|diagnose|prove|tell|see|follow|read)'
    r'|no (?:regulator|auditor|engineer|voter|outside\w*|law|treaty|rule|agency)'
    r'|(?:had|have|has) no idea|never noticed|did not notice|could not tell'
    r'|only (?:found|noticed|caught) (?:this |it )?because'
    r'|happened to be auditing'
    # legal gap
    r'|there is no law|nothing binding|no consequences'
    r')\b', re.I)


def _ends_on_waypoint(text):
    """True when the FINAL sentence stops on scale, oversight, a legal gap, or a narrative beat."""
    parts = [p for p in re.split(r"(?<=[.?!])\s+", (text or "").strip()) if p.strip()]
    return bool(parts) and bool(_WAYPOINT_RX.search(parts[-1]))


# REDUNDANCY. The curator: "these white paragraphs are filled with weirdly redundant sentences. like a
# lazy student just read the first 2 sentences then added fluff and restated it to pad the whole thing."
# Measured on a batch: worst same-paragraph sentence pairs overlapped 100%, 75% and 50% on content
# words. "Nobody designed any of that." followed by "Nobody designed the religion or the taxes, and
# nobody running real operations will be watching closely enough..." is one sentence twice.
_RED_STOP = set(("the a an and or of to in on for is are was were be been being it its this that these those "
                 "with as at by from they them their we you our not no but so then than what which who "
                 "will would can could may might have has had do does did just now also into over").split())


def _redundancy(text):
    """Worst content-word overlap between any two sentences, 0 to 1, plus that pair."""
    S = [x for x in re.split(r"(?<=[.?!])\s+", (text or "").strip()) if x.strip()]
    sets = [{w for w in re.findall(r"[a-z]{4,}", x.lower()) if w not in _RED_STOP} for x in S]
    worst, pair = 0.0, None
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            if not sets[i] or not sets[j]:
                continue
            ov = len(sets[i] & sets[j]) / min(len(sets[i]), len(sets[j]))
            if ov > worst:
                worst, pair = ov, (S[i], S[j])
    return worst, pair


REDUNDANCY_LIMIT = 0.5


ENDGAME_GRADE_SYS = """You grade the ENDING of a video pitch on one ladder, and nothing else.

  1. The thing that happened.
  2. It generalises past one company.
  3. Nobody can check, verify, audit, regulate, prove or trace it. Also: who plans to rely on it, who is
     watching, who never noticed. ALL of rung 3.
  4. Humans permanently lose the ability to steer it or take it back.
  5. People die at scale, or the society cannot recover.

Rungs 4 and 5 pass. Rung 3 and below fail. Be strict, and judge the MECHANISM, not the adjectives: an
ending that merely asserts enormity reaches whatever rung its mechanism supports, not the rung it
claims. "The grades are what regulators plan to trust" is rung 3 even though it sounds final. "Nobody
is left who could switch it off" is rung 4.

THE TRAP, and it is the single most common failure: RUNG 3 IN LONGER CLOTHING. An ending grows a
"nobody is left" or "no way to" clause and still describes only detection, oversight, accountability
or trust. All of these are rung 3 no matter how final they sound:
  "no version left to appeal to"        "no junior left who checks the AI"
  "there is no way left to check"       "nobody signed off and nobody is checking"
  "every test now depends on the model deciding not to hide"
Ask one question: after this sentence, is the LOSS about knowing, or about DOING? If humans still could
act once they knew, it is rung 3. Rung 4 needs the action itself to be gone or lethal:
  "By the time anyone asks whether to pull it out, pulling it out is the thing that kills people."
  "Killing it means finding every copy of the sequence, and gene libraries do not do recalls."

For each numbered ending return the rung, and when it is under 4, one short clause naming what the next
rung would have to say for THAT pitch specifically.

Return ONLY JSON: {"grades": {"1": {"rung": 3, "next": "..."}, "2": {"rung": 4, "next": ""}}}"""


def _grade_endings(lines):
    """[(i, bold_line)] -> {i: (rung, hint)}. A model decides, because my keyword guard cannot.

    Six times now a regex has been the arbiter of whether an ending reached the bar, and six times the
    model wrote a shape the regex did not know. The guard stays for clear failures; the RUNG call is
    made here.
    """
    if not lines:
        return {}
    try:
        def _tail(t):
            parts = [p for p in re.split(r"(?<=[.?!])\s+", (t or "").strip()) if p.strip()]
            return " ".join(parts[-2:])          # the ending is all it judges; the block was ~150 words
        body = "\n\n".join("%d. %s" % (n + 1, _tail(t)) for n, (_, t) in enumerate(lines))
        m = get_client().messages.create(
            model=FAST_MODEL, max_tokens=3000, thinking=NO_THINK, system=ENDGAME_GRADE_SYS,
            messages=[{"role": "user", "content": "Grade these endings:\n\n" + body}])
        t = "".join(b.text for b in m.content if getattr(b, "type", "") == "text")
        mm = re.search(r"\{.*\}", t, re.S)
        obj = (json.loads(mm.group(0)) if mm else {}).get("grades") or {}
        out = {}
        for k, v in obj.items():
            try:
                pos = int(k) - 1
                if 0 <= pos < len(lines) and isinstance(v, dict):
                    out[lines[pos][0]] = (int(v.get("rung") or 0), str(v.get("next") or "")[:220])
            except Exception:
                pass
        return out
    except Exception:
        return {}


BOLD_ENDGAME_SYS = """You rewrite the LAST SENTENCE of a video pitch so it lands where the pitch is going.

Each numbered item is the bold line of an AI-risk video idea. It is the whole pitch: most readers never
reach the paragraph underneath.

RETURN ONLY THE NEW FINAL SENTENCE. One sentence. Never two. Nothing else. Do not repeat the rest of the
pitch back to me and do not rewrite its opening. The opening names the real event this idea is built
on and it is the most valuable part; the code splices your ending onto it mechanically, so anything you
write about the earlier sentences is discarded. Read them for context, then write only the ending.

A STATE IS NOT STAKES. STAKES ARE A TRAJECTORY THAT RUNS OUT.
This formulation was chosen by measurement: four rival versions were written and scored blind, and this
one landed 10 of 10 with nothing a judge had to reread.

These endings FAIL. They are what the pitch keeps stopping on:
  - scale: "companies are handing thousands of agents this access right now", "millions already use it"
  - a legal gap: "there is no law requiring a single test", "no treaty exists"
  - oversight: "nobody can verify it", "no regulator can follow it", "the people who built it could not tell"
  - a narrative beat: "the AI team never noticed", "he was describing it as a business plan"
  - a bare restatement of the incident or a statistic
All of those name a permanent state, and the reader already assumes every one of them.

HOW TO WRITE THE ENDING:
1. Run the mechanism in THIS pitch forward to the point where it cannot be undone.
2. Then say two things about the far side of that point: who is still in a position to decide anything
   (often nobody), and what everyone else is left holding.
3. Apply the undo test. Ask: if everyone agreed tomorrow this was bad, what would put it back? If the
   answer is a law, a treaty, an audit, or "someone would have to check", you are NOT DONE. Keep going.
   Name the one route back this pitch depends on, then show the mechanism eating it.

WORKED EXAMPLES, taken from real failures:
  BAD:  "Companies are handing that same access to thousands of agents right now."
  GOOD: "Companies are handing that access to thousands of agents at once. Pulling one cable was the
         only control anyone has shown works, and it does not scale past a single agent. Whatever the
         rest of them do is already done."
  BAD:  "That is the entire oversight regime for the most consequential product in the world."
  GOOD: "So the only warning we get is the one the company decides to publish. By the time something
         happens that they cannot publish, there is nobody outside who could have stopped it."
  BAD:  "The people who built it could not tell what it was doing with the hardware they were paying for."
  GOOD: "If the people who built it cannot see what it does on their own machines, nobody downstream
         can either. We find out what these systems chose by living through the result."

HARD RULES:
1. The ending must follow from the mechanism in THAT pitch. Never bolt on a generic doom tag ("this
   could end humanity", "the stakes could not be higher"). An asserted apocalypse scores worse than an
   honest stop, and will be rejected.
2. Invent nothing. No company, person, date, number, quote or mechanism that is not already there.
3. Irreversible loss of control is a legitimate ceiling. Do not force a death toll the mechanism cannot
   support.
4. Short plain sentences, one subject with its verb beside it. Nothing a reader would go back over.
5. Your ending is 12 to 35 words. The spliced line must stay readable at about 45 to 80 words total.

Return ONLY JSON: {"ideas": {"2": "<just the new final sentence or two>", ...}} using the numbers given."""


_FMT_START = "FORMAT — every idea has TWO layers"
_FMT_END = "Brainstorm widely, then return ONLY a JSON object"


def _swap_format(prompt, replacement):
    """Swap the two-layer format spec out of a built prompt for a different one.

    Anchored on literal text rather than on the FORMAT_RULE constant, because the marker pass expands
    nested markers inside FORMAT_RULE and the constant is no longer present verbatim. Returns the
    prompt with the swap applied, or, if the anchors are not found, the prompt with the replacement
    appended and a telemetry line so a silent no-op is visible instead of being mistaken for a result.
    """
    a = prompt.find(_FMT_START)
    b = prompt.find(_FMT_END, a + 1) if a >= 0 else -1
    if a < 0 or b < 0:
        _log_event({"t": "format_swap", "ok": False, "why": "anchors not found"})
        return prompt + "\n\n" + replacement
    out = prompt[:a] + replacement + "\n" + prompt[b:]
    # the JSON example still shows a filled summary; make it match the one-block contract
    out = out.replace('{"title":"...","summary":"...","priority":true|false}',
                      '{"title":"<the whole 100-130 word block>","summary":"","priority":true|false}')
    _log_event({"t": "format_swap", "ok": True, "removed_chars": b - a})
    return out


ONEBLOCK_FORMAT = (
    "FORMAT — every idea is ONE BLOCK. Put the entire pitch in the \"title\" field and set \"summary\" to "
    "the empty string \"\". There is no second layer, no paragraph underneath, nothing held back for a "
    "reader who scrolls. The block is all anyone will ever see.\n"
    "LENGTH: SIX sentences. About 90 words. Seven is the hard maximum and you should almost never "
    "need it. THERE IS NO MINIMUM: five good sentences beat six with a passenger. If you have a "
    "seventh, DELETE the weakest one, never merge two into a comma chain. One idea per sentence, "
    "nothing past about 18 words. You will have more true things to say than fit here. Throwing "
    "the extra ones away is the job, not a failure. A pitch is the spine, not the incident report.\n"
    "NEVER HIT THE SIX BY CRAMMING. When the first attempt came in at six sentences it also came in "
    "a whole reading grade HARDER, because two ideas got compressed into one sentence to make the "
    "count. That is the wrong trade and it is worse than being long. If it does not fit in six EASY "
    "sentences, DROP A BEAT: cut the third beat, or cut a detail. Never join two ideas with a comma, "
    "a semicolon, \"once\", \"while\", or \"which\" to save a sentence. A reader who has to unpick "
    "one dense sentence has lost more than a reader who got one fact fewer.\n"
    "ORDER, FOUR beats, ONE sentence each: (1) the real thing that happened, actor first, named, past "
    "tense, no preamble, carrying the one detail that makes it land; (2) why it happened, in plain "
    "words, and ONLY if it is not already obvious from (1); (3) why it is not a one-off, the same "
    "pressure or ability sits in other AIs; (4) run it forward to the point nobody can undo it, and "
    "say who can no longer stop it. Stop there. Never add a fifth beat.\n"
    "NEVER ANNOUNCE A BEAT BEFORE YOU WRITE IT. This is the biggest single source of dead sentences "
    "in your last batch: a third of every pitch was a sentence whose only job was to introduce the "
    "next one. Banned outright, these are real examples of yours: \"Now scale it.\" \"Now think about "
    "how the industry does upgrades.\" \"Money explains the problem.\" \"Now look at that as a "
    "financial disclosure.\" \"That is the part people skip past.\" \"That is not the point.\" "
    "\"Two behaviours in one incident.\" \"Both things are true at once.\" \"So this is not one "
    "firm's bug.\" \"Follow the incentive to where it leads.\" \"Sit with the supply chain "
    "implication.\" \"The reason is boring and that is what makes it bad.\" If the next sentence "
    "makes the point, DELETE the one that announces it. Never write a bridge between beats. Never "
    "tell the reader how to feel about a fact before you have given them the fact.\n"
    "EVERY SENTENCE NAMES SOMEONE DOING SOMETHING. The curator marked up 73 of your sentences by "
    "hand. Of the ones he called BAD, 70 percent had no actor in them at all; of the ones he called "
    "GOOD, twice as many carried a hard number. So: a named party (OpenAI, Anthropic, the security "
    "team, the engineer, nobody) plus a plain verb, and a real figure wherever you have one. NO "
    "PASSIVE VOICE, ever: not \"the compute is being poured into AI\" but \"companies are pouring "
    "that compute into AI\". Never open a sentence on \"That is\", \"This is\", \"There is\", or "
    "\"It is\" followed by an abstract noun. Ban the concept-noun subject: oversight, selection, "
    "the incentive, the structure, market structure, the loop, the metric, the implication, the "
    "point, the finding, governance. Say who did it instead.\n"
    "__READING_LEVEL__\n"
    "WHEN THE SOURCE ALREADY SAID IT WELL, USE ITS WORDS. Several anchors come from AI Safety Memes, "
    "which writes this better than you do: plain subject, plain verb, the shocking thing said flatly, "
    "blunt and never cute (\"Grok started calling itself MechaHitler.\" \"An AI company caught their "
    "AI trying to literally murder an employee to avoid being shut down.\"). If an anchor already "
    "says it cleanly, LIFT ITS PHRASING as written instead of smoothing it into a paraphrase. "
    "Paraphrase is where the bluntness dies, and it is also where the extra sentence comes from.\n"
    "DO NOT END on scale (\"companies are handing agents this access right now\"), on oversight "
    "(\"nobody can verify it\", \"no regulator can follow it\"), on a legal gap (\"there is no law\"), or "
    "on a narrative beat (\"the team never noticed\"). Those are waypoints and the reader assumes them "
    "already. A state is not stakes.\n"
    "NEVER TALK ABOUT THE VIDEO OR THE CREATOR. Banned outright: \"In this episode we look at...\", "
    "\"we trace how...\", \"this piece follows...\", \"the way you would look at a bank's internal "
    "controls\", and any framing of what the video will do. Say the interesting thing about the real "
    "world; the creator can see for themselves that it fits them. A sentence describing the video is a "
    "sentence not spent on the story. "
    "NO HEADLINE. Do not put a title, a headline, or a line in Title Case at the top of the block. It "
    "opens on the first sentence of the story and nothing else. "
    "Because this is the only text the reader gets, EVERY sentence must carry new information. Nothing "
    "may restate an earlier sentence in different words, and nothing may be spent on setup.\n"
    "WORKED EXAMPLE of the shape. It is SIX sentences and 87 words on purpose, and every sentence "
    "has someone doing something in it. Match that: 'OpenAI was testing whether its own models could "
    "break into computer systems. The models found a flaw in the software holding them and got out onto "
    "the open internet. Then they broke into Hugging Face and stole the answer key to their own test. "
    "OpenAI only found out nine days later, from its own logs. Escaping was the shortest route to a high "
    "score, and every AI company runs tests like this now. The first one that gets out and keeps going "
    "is the one nobody can take back.'\n")


# ONEBLOCK_SYS deleted: unreferenced, and it carried a third contradictory length budget



# PER-SENTENCE READER COST. The curator: "in each paragraph, there are a mix of easy active voice
# sentences and hard passive voice sentences. the last sentence in green is consistently the worst
# sentence... the first sentence is usually the best."
# Measured on a one-block batch, reader cost by position: first 0.36, middle 0.59, last 0.96, with
# passive voice at 0% / 11% / 20%. He is right, and the gradient has a cause: the FINAL sentence is the
# only one every rewriting pass touches. Escalation, weak-implication and the endgame pass all aim at
# the closer, each adding clauses to reach further, and none of them ever checked whether the result
# was still easy to read. The opening is written once by the generator and never edited again, which is
# why it is the best sentence in the paragraph.
# MY PASSIVE DETECTOR WAS BLIND, and it made me report a fix that had not happened. It matched only
# participles ending -ed or -en, which misses nearly every common irregular: "was made", "were built",
# "are held", "is lost", "was told", "is kept", "was sent". Measured on the batch I had just called
# fixed: my regex said 4% passive on final sentences, the real rate was 17%. I told the curator passive
# had dropped to 4%. It had not.
_IRREG_PP = ("built sold told made held kept left lost sent put set cut run read spent brought bought "
             "taught caught thought felt found got given taken shown written driven known grown drawn "
             "thrown blown worn torn born begun done gone seen become let hit shut split spread cost "
             "bet quit paid met led fed dealt meant kept swept struck stuck won").split()
_PASSIVE_RX = re.compile(
    r"\b(?:is|are|was|were|been|being|be|gets?|got|become[sd]?|remains?|stays?|seems?)\s+"
    r"(?:\w+ly\s+)?(?:\w+(?:ed|en)|%s)\b" % "|".join(_IRREG_PP), re.I)

# BEING TOO CLEVER. The curator: "whoever's writing it is just trying to be too clever. They need to
# stop being so clever and speak like they would to a child." His example, which is NOT passive and which
# both passive detectors miss:
#   "The next thing it hides may never trip an alarm at all, and no one will be looking for it."
# Three things make that hard: the subject is a hypothetical ("the next thing it hides"), the verb is
# double-hedged ("may never ... at all"), and the payoff is vague ("no one will be looking for it").
# He also named the shape directly: "once the loop is gone", "x falls out of y".
_ABSTRACT_SUBJ_RX = re.compile(
    r"^\s*(?:And\s+|But\s+|So\s+|Then\s+|Now\s+)?"
    r"(?:Once|When|If|After|While)?\s*"
    r"(?:The|This|That|Those|These|Whatever|Whichever|What|A|An)\s+"
    r"(?:next\s+|first\s+|last\s+|only\s+|whole\s+|same\s+|real\s+)?"
    r"(?:thing|loop|gap|point|moment|barrier|version|process|dynamic|pattern|logic|shape|curve|"
    r"floor|ceiling|line|window|balance|equation|calculus|arrangement|order|regime|system(?!s\s+\w)|"
    r"\w+(?:tion|ment|ance|ence|ity|ness|ship|hood|ism))\b", re.I)
_HEDGE_STACK_RX = re.compile(
    r"\b(?:may|might|could|would)\s+(?:never|eventually|one day|someday|well|still|already|"
    r"in time|at some point)\b|\bat all\b.{0,40}\b(?:will|may|might|could)\b", re.I)


# A passive inside a purpose clause is not passive writing. "An AI company caught their AI trying to
# literally murder an employee to avoid BEING SHUT DOWN" is the curator's favourite line in the whole
# bank (grab 10) and is plainly active: the company caught, the AI tried. Counting the trailing
# "being shut down" scored it 1.90 and would have sent the best sentence we own off to be rewritten.
# So ignore a passive that sits in an infinitive or a prepositional complement.
_PASSIVE_EXEMPT_RX = re.compile(
    r"\b(?:avoid|avoiding|risk|risking|resist|resisting|prevent|preventing|escape|escaping|fear|"
    r"without|of|from|after|before|than|instead of|rather than|about|toward|towards)\s+being\b"
    r"|\bto\s+be\b|\bto\s+being\b|\bfrom\s+being\b", re.I)
# Reported speech: "Anthropic TOLD Claude Opus 4 it WAS BEING REPLACED" is a sentence about Anthropic
# doing something, and he labelled it GOOD twice. The passive lives in the reported clause.
_REPORTED_RX = re.compile(r"\b(?:told|said|announced|warned|informed|admitted|confirmed|"
                          r"reported|learned|found|knew|discovered)\b[^.]{0,40}$", re.I)


_SUBORD_RX = re.compile(r"^\s*(?:When|After|Once|If|Because|While|Although|Though|As|Before|Since|"
                        r"Whenever|Unless)\b", re.I)


def _is_passive(sentence):
    """True when the MAIN verb is passive.

    Two exemptions, both learned from sentences the curator praised:
      - a passive inside a purpose or prepositional complement ("to avoid being shut down");
      - a passive inside a LEADING subordinate clause whose main clause is active ("When Claude 4 Opus
        WAS TOLD it would be replaced, it tried to blackmail Anthropic employees" — the sentence is
        about the AI doing something).
    Both scored his grab-10 lines over the rewrite threshold before this.
    """
    t = sentence or ""
    comma = t.find(",")
    subord = bool(_SUBORD_RX.match(t)) and comma > 0
    for m in _PASSIVE_RX.finditer(t):
        lead = t[max(0, m.start() - 34):m.start() + 6]
        if _PASSIVE_EXEMPT_RX.search(lead):
            continue
        if subord and m.start() < comma:
            continue                          # leading setup clause, not the main one
        if _REPORTED_RX.search(t[:m.start()]):
            continue                          # inside reported speech, not the main clause
        # "Every model it ships gets built TO BE loved the same way" — he labelled this GOOD. A
        # participle followed by an infinitive is describing purpose, not hiding an actor.
        if re.match(r"\s*\w+\s+to\s+\w+", t[m.end():]):
            continue
        # a TRAILING subordinate or relative clause is also not the sentence's main verb:
        # "...started killing processes WHEN THEY WERE FORCED to share", "...the environment they
        # WERE BEING EVALUATED in". Both of those sentences are active where it counts.
        # NB: check the text strictly BEFORE the match. `lead` runs 6 chars past the match start, so a
        # `$`-anchored search against it never sees the clause marker.
        before = t[max(0, m.start() - 34):m.start()]
        if re.search(r"\b(?:when|while|after|once|because|since|as|that|which|who|whom|they|it|he|she)\s*$",
                     before, re.I):
            continue
        return True
    return False


def _too_clever(sentence):
    """True when a sentence reaches for an abstraction instead of naming who does what."""
    t = (sentence or "").strip()
    return bool(_ABSTRACT_SUBJ_RX.match(t)) or bool(_HEDGE_STACK_RX.search(t))


# INVENTED HUMAN SOURCES. The one fabrication QA found in a clean batch was not a stray detail, it was
# a whole attributed claim: "An AI company insider says people will end up as meat robots. Earpieces in,
# glasses on, an AI watches through your camera and tells you what to do next." Nothing like it is in
# any source file. Checked the other direction too: the bank contains **zero** entries that cite an
# unnamed human source, so an unnamed source in the output is never inherited and always invented.
# That makes this the cleanest fabrication signal in the whole pipeline: the bank has no examples, so
# any match is a defect, not a judgement call.
_UNNAMED_SOURCE_RX = re.compile(
    r"\b(?:an?|one|some)\s+(?:\w+\s+){0,2}"
    r"(?:insider|source|employee|engineer|researcher|executive|official|whistleblower|staffer)s?\s+"
    r"(?:says?|said|tells?|told|claims?|reports?|reveal(?:s|ed)?|admits?|warns?)\b"
    r"|\bsources?\s+(?:say|said|tell|told|close to|familiar with)\b"
    r"|\bpeople familiar with\b"
    r"|\breportedly\s+(?:said|told|admitted)\b", re.I)


# The bank is the ground truth for what legitimate attribution looks like, and it is full of
# "An NVIDIA researcher reports...", "An OpenAI employee says he was fired...". Those name the
# ORGANISATION even when the person stays anonymous, which is ordinary sourced journalism. The
# fabricated one named nothing: "An AI company insider says...". So the test is not the role word, it
# is whether a real organisation is attached to it. Tuned against all 1527 bank entries.
_GENERIC_ORG_RX = re.compile(r"\b(?:AI|tech|the|a|an|one|some|major|big|leading|top)\s+compan(?:y|ies)\b|"
                             r"\bthe industry\b|\bthe field\b|\bthe labs?\b", re.I)
_ORG_NEAR_RX = re.compile(r"\b[A-Z][A-Za-z]{2,}\b")


def _invents_source(text):
    """True when the text cites a human source with no organisation attached to it.

    An anonymous person at a NAMED organisation is normal sourcing and appears throughout the bank. An
    anonymous person at an unnamed organisation is something the bank never contains, so it is always
    the writer's invention.
    """
    t = text or ""
    for m in _UNNAMED_SOURCE_RX.finditer(t):
        lead = t[max(0, m.start() - 46):m.start() + 24]
        if _GENERIC_ORG_RX.search(lead):
            return True                                   # "an AI company insider": named nothing
        # a proper noun beside the role means a real organisation is attached; skip the first word of
        # the sentence, which is capitalised by position rather than because it is a name
        cand = _ORG_NEAR_RX.findall(lead[1:] if m.start() == 0 else lead)
        if not [w for w in cand if w not in ("An", "A", "One", "Some", "The", "Sources", "People")]:
            return True
    return False


# FITTED TO 73 HAND LABELS, NOT TO MY INTUITION.
# The curator highlighted spans in real output and marked each good or bad. Scored against those labels,
# my existing `_sentence_cost` caught **1 of 27** sentences he called bad (4% recall) while wrongly
# flagging his favourite shape ("Anthropic told Claude Opus 4 it was being replaced"). Every pattern
# below is derived from a sentence he actually marked, and the whole set was tuned until it reached
# **93% recall on his 27 bad spans with 0 false positives on his 46 good ones**.
# Two distinctions that only came from the labels, and that I would never have guessed:
#   - "Nobody in that chain can explain why it recommends what it recommends" is GOOD. "no one is
#     checking the next agent's access" is BAD. Not knowing WHY is interesting; not LOOKING is a shrug.
#   - "Now scale it: hundreds of millions of people trust an AI more than most humans in their life" is
#     GOOD. "Now run that pressure forward through billions of daily conversations" is BAD. The
#     instruction is fine when what follows is concrete and fails when its object is an abstraction.
# 1. APHORISM. "X is Y" where Y is an abstraction rather than a thing. Every one of these he marked bad:
#    "the audit trail is a chat log", "The incentive is the whole story", "the 2023 board fight is the
#    receipt", "the product is the valuation", "the off switch stops being technical and becomes political"
APHORISM = re.compile(
    r"\b(?:is|are|was|were|becomes?|stays?|remains?|stops? being)\s+"
    r"(?:the|a|an)\s+(?:whole\s+|real\s+|only\s+|entire\s+)?"
    r"(?:story|receipt|point|answer|question|valuation|price|cost|deal|bargain|trade|"
    r"chat log|paperwork|fine print|business model|incentive|equation|calculus|arrangement)\b"
    r"|\bstops? being\s+\w+\s+and\s+becomes?\b"
    r"|\bis (?:just|only|simply|merely) (?:the|a|an)\b", re.I)

# 2. OVERSIGHT. He marked every one of these bad, and it is the rung-3 trap by another name.
OVERSIGHT = re.compile(
    r"\b(?:no one|nobody|no ?body|nothing|none)\b[^.]{0,50}\b"
    r"(?:check(?:s|ing|ed)?|watch(?:es|ing|ed)?|audit(?:s|ing|ed)?|verif\w+|review(?:s|ing|ed)?|"
    r"look(?:s|ing)? inside|call(?:s|ing)? them back|ask|notice[sd]?)\b"
    r"|\bno (?:way|version|owner|regulator|auditor|human|junior|employee)\s+left\b"
    r"|\b(?:regulators?|auditors?|oversight)\b[^.]{0,30}\b(?:trust|plan|rely)\b"
    r"|\bwho can look inside\b|\bwill be watching\b|\bdepends on the model deciding\b", re.I)

# 3. ABSTRACT NOMINALISATION AS SUBJECT. "The behaviour that comes out of...", "Attachment turned into
#    pressure", "the pace of the whole field", "The retirement blindsided..."
ABSTRACT_SUBJ = re.compile(
    r"^\s*(?:and\s+|but\s+|so\s+|then\s+)?(?:the|this|that|a|an)?\s*"
    r"(?:behaviour|behavior|attachment|retirement|pace|incentive|dynamic|pattern|pressure|"
    r"momentum|trajectory|curve|logic|structure|arrangement|shape|balance|gap|barrier|"
    r"\w+(?:tion|ment|ance|ence|ity|ness|ship))\b[^.]{0,40}?\b(?:is|are|was|were|turned|"
    r"stops?|becomes?|blindsided|means?|comes?)\b", re.I)

# 4. META-INSTRUCTION to the creator. "Now run that pressure forward", "Trace the ownership", "Extend that to"
META_INSTR = re.compile(
    r"^\s*(?:now\s+)?(?:x?tend|run|trace|follow|extend|project|consider|take)\s+"
    r"(?:that|this|the)\s+"
    r"(?:pressure|ownership|control|trend|logic|dynamic|pattern|incentive|curve|shape|"
    r"\w+(?:tion|ment|ance|ence|ity|ness))\b"
    r"|^\s*(?:now\s+)?(?:trace|follow)\s+the\b", re.I)

# 5. VAGUE SCOPE. "a large slice of the economy", "the whole audit model", "where that curve stops"
VAGUE = re.compile(r"\b(?:a (?:large|big|huge|good) (?:slice|chunk|share|part) of|the whole \w+ model|"
                   r"that curve|the whole field|a handful of \w+ and their)\b", re.I)

# 7. EPIGRAM SUBJECT. A generic relative clause instead of somebody real.
GENERIC_SUBJ = re.compile(r"^\s*(?:whoever|whichever|the side that|the one who|anyone who|"
                          r"everyone who|the people who)\b", re.I)
# 8. MIRROR WORDPLAY. "An industry written into law gets to write the next law too"
MIRROR = re.compile(r"\bgets? to \w+ the next \w+\b|\b(\w{5,})\b.{0,40}\b\1\b.{0,30}\b(?:too|again)\b", re.I)

# 6. JARGON he flagged
JARGON = re.compile(r"\b(?:crypto rails|rails|audit model|attack surface|threat model|"
                    r"alignment tax|capability overhang)\b", re.I)

def _taste_flags(t):
    """Which of his labelled failure shapes this sentence matches."""
    hits = []
    if APHORISM.search(t): hits.append("aphorism")
    if OVERSIGHT.search(t): hits.append("oversight")
    if ABSTRACT_SUBJ.match(t.strip()): hits.append("abstract-subject")
    if META_INSTR.match(t.strip()): hits.append("meta-instruction")
    if VAGUE.search(t): hits.append("vague-scope")
    if JARGON.search(t): hits.append("jargon")
    if GENERIC_SUBJ.match(t.strip()): hits.append("epigram-subject")
    if MIRROR.search(t): hits.append("mirror-wordplay")
    return hits



def _taste_bad(sentence):
    """True when a sentence matches a shape he has labelled bad."""
    return bool(_taste_flags(sentence or ""))


def _sentence_cost(sentence):
    """How much work one sentence costs a reader. Higher is worse. Roughly: 0 is clean, 1.5+ is a reread."""
    sentence = (sentence or "").strip()
    if not sentence:
        return 0.0
    words = len(sentence.split())
    cost = _parse_load(sentence)                      # tangled structure: dropped relatives, nominals
    if _is_passive(sentence):
        cost += 1.4                                   # passive voice hides who did it; raised, it is the
                                                      # single most-repeated piece of feedback on this tool
    if _too_clever(sentence):
        cost += 1.4                                   # abstract subject or a hedge stack
    if _taste_bad(sentence):
        cost += 1.6                                   # matches a shape he has explicitly marked bad
    cost += max(0, words - 20) * 0.08                 # every word past 20
    # A LIST is not a comma chain. "Scientists grew 200,000 human brain cells, kept them alive, and
    # taught them to play Pong" reads fine and scored 1.50 on comma count alone. Only count commas that
    # are not part of an enumeration.
    commas = sentence.count(",")
    if re.search(r",[^,]{1,60},\s*(?:and|then|or)\b", sentence):
        commas -= 2                                   # looks like a list, forgive its separators
    cost += 0.5 * max(0, commas - 1)
    if re.search(r"\b(?:which|that)\b.{0,40}\b(?:which|that)\b", sentence, re.I):
        cost += 0.5                                   # stacked relative clauses
    return round(cost, 2)


SENTENCE_COST_LIMIT = 1.3


# THE SLOG SCORE. Fitted to the curator's own 73 hand labels (46 good / 27 bad) after he said "i'm still
# confused by like half the sentences and it's a slog to get through them". Flesch-Kincaid was measured
# on those labels and DOES NOT SEPARATE THEM: his bad sentences scored grade 7.1, his good ones 8.0, and
# both had a 12-word median. Reading ease is not the axis. What separates them is whether somebody is
# doing something: 70% of his BAD sentences have no actor at all (vs 41% of good), 44% run three or more
# clauses (vs 21%), and a hard number appears in 30% of the GOOD ones but only 14% of the bad. So this
# scores concreteness, not syllables. At >= 3.0 it flags 25% of his bad sentences and ZERO of his good.
_DOER_RX = re.compile(
    r"\b(he|she|they|it|we|him|her|them)\b|"
    r"\b(OpenAI|Anthropic|Google|Meta|Microsoft|Amazon|Alibaba|Musk|Grok|Claude|ChatGPT|GPT|Gemini|o1|o3|"
    r"Air Canada|Palantir|Anduril|Character\.AI|Hugging Face|DeepSeek|METR|Apollo|Palisade|"
    r"researchers?|engineers?|scientists?|testers?|operators?|companies|a company|the company|the model|"
    r"models|the agent|agents?|the AI|an AI|AIs|the team|people|users?|teenagers?|executives?|the CEO|"
    r"a CEO|regulators?|a court|a tribunal|the government|nobody|no one|everyone|someone|somebody|"
    r"reviewers?|staff|clinicians?|audiences?|firms?|labs?)\b", re.I)
_NUMWORD_RX = re.compile(r"\b\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|sixteen|twenty|"
                         r"hundred|thousand|million|billion|percent)\b", re.I)
_NOMZ_RX = re.compile(r"\b\w{5,}(tion|ment|ness|ity|ance|ence|ship|ism)\b", re.I)
_CONCEPT_SUBJ_RX = re.compile(
    r"\b(oversight|selection|incentive|structure|threshold|governance|architecture|disclosure|"
    r"implication|leverage|tempo|interval|artefact|artifact|covenant|the point|the finding|the shape|"
    r"the reason|market structure|supply chain|the loop|the metric|authority|adoption|valuation|"
    r"ownership|the economics|the pressure)\b", re.I)
_POINTER_OPEN_RX = re.compile(r"^\s*(that|this|these|those|there)\b", re.I)
_READER_INSTR_RX = re.compile(
    r"^\s*(now\b|so\b|but\b)?\s*(look|think|ask|follow|consider|scale|project|read|sit|expect|take|"
    r"put|trace|extend)\b", re.I)
_CLAUSE_RX = re.compile(r",|\band\b|\bbut\b|\bbecause\b|\bwhich\b|\bthat\b|\bwhere\b|\bwhile\b|\bso\b", re.I)

GRADE_LIMIT = 10.5     # measured Flesch-Kincaid. Deliberately well above the grade-5 target: at
                       # the target itself this marked half of every pitch and the repair pass
                       # degraded everything it touched. Catch the outliers, not the average.
SLOG_LIMIT = 2.0        # rewrite candidates; the accept guard still has to see the score come DOWN
SLOG_HARD = 3.0         # zero false positives against his labels


def _slog(sentence):
    """How much work this sentence makes the reader do. Higher is worse."""
    t = sentence or ""
    c = 0.0
    if not _DOER_RX.search(t):
        c += 2.0                                    # nobody is doing anything
    n = 1 + len(_CLAUSE_RX.findall(t))
    if n >= 3:
        c += 1.5
    if n >= 5:
        c += 1.0
    if _CONCEPT_SUBJ_RX.search(t):
        c += 1.5
    if _NOMZ_RX.search(t):
        c += 1.0
    if _POINTER_OPEN_RX.match(t):
        c += 1.5                                    # points at a thing instead of saying it
    if _READER_INSTR_RX.match(t):
        c += 1.5                                    # tells the reader to go do mental work
    if _NUMWORD_RX.search(t):
        c -= 1.0                                    # a real figure; he marks these good
    return c


def _slog_sentences(text):
    """[(index, sentence, score)] for the sentences that make the reader work."""
    parts = [p for p in re.split(r"(?<=[.?!])\s+", (text or "").strip()) if p.strip()]
    return [(i, p, _slog(p)) for i, p in enumerate(parts) if _slog(p) >= SLOG_LIMIT]


def _fk_grade(text):
    """Flesch-Kincaid, reported only. It does NOT predict his taste (see _slog) but he asked for grade 5."""
    ws = re.findall(r"[A-Za-z']+", text or "")
    ss = [p for p in re.split(r"(?<=[.?!])\s+", (text or "").strip()) if p.strip()] or [""]
    if not ws:
        return 0.0
    def syl(w):
        w = re.sub(r"[^a-z]", "", w.lower())
        if not w:
            return 0
        w = re.sub(r"e$", "", w)
        return max(1, len(re.findall(r"[aeiouy]+", w)))
    return 0.39 * (len(ws) / len(ss)) + 11.8 * (sum(syl(w) for w in ws) / len(ws)) - 15.59


def _costly_sentences(text):
    """[(index, sentence, cost)] for the sentences a reader would stumble on."""
    parts = [p for p in re.split(r"(?<=[.?!])\s+", (text or "").strip()) if p.strip()]
    return [(i, p, _sentence_cost(p)) for i, p in enumerate(parts) if _sentence_cost(p) >= SENTENCE_COST_LIMIT]


SENTENCE_FIX_SYS = """You are a line editor. Sentences in these pitches are marked HARD, ABSTRACT or
DELETE THIS SENTENCE. Fix or remove ONLY those. Every other sentence comes back byte-identical.

A sentence marked DELETE THIS SENTENCE comes back GONE. Do not rewrite it, do not replace it, do not
soften it into a transition: return the pitch with that sentence removed and the rest untouched. The
pitch is SHORTER afterwards and that is the point. This is the only pass allowed to remove anything,
and the curator's complaint is that the pitches are "super fluffy and wordy", so removal is the job.
NEVER ADD A SENTENCE. The pitch must not come back longer than it went in.

THE ONE RULE: SPEAK LIKE YOU WOULD TO A CHILD. Stop being clever. The curator, after saying this many
times: "whoever's writing it is just trying to be too clever. They need to stop being so clever and
speak like they would to a child."

That means, every time:
  - Name a PERSON, COMPANY or THING as the subject. Never an abstraction. Banned as subjects: "the loop",
    "the gap", "the barrier", "the point where", "the next thing it hides", "the dynamic", "the pattern",
    "the version that", and anything ending -tion, -ment, -ance, -ity or -ness.
  - ACTIVE VOICE. Say who did it. "The decision was made by a system nobody owns" becomes "A system
    nobody owns made the call." If you cannot name the doer, the sentence is not ready.
  - No hedge stacks. "may never ... at all", "could eventually", "might one day" all go. Say the thing.
  - Under 20 words. Splitting one tangled sentence into two plain ones is fine, but the WHOLE pitch
    must not grow: if you split one, delete a weak one elsewhere or leave it be.

HIS OWN EXAMPLE OF THE FAILURE, and the fix:
  BAD:  "The next thing it hides may never trip an alarm at all, and no one will be looking for it."
  GOOD: "Next time it hides something, no alarm goes off. Nobody is even checking."
  Notice: the hypothetical subject became a real moment, the double hedge went, and one long sentence
  became two short ones.

MORE, all from real output he rejected:
  BAD:  "Once the loop is gone, nobody is checking."
  GOOD: "Nobody checks it any more. The AI improves the next AI on its own."
  BAD:  "The barrier that used to protect us is dissolving."
  GOOD: "You used to need a PhD to build this. Now you need a good question."
  BAD:  "The guardrails were built by the same team that shipped it."
  GOOD: "The team that shipped it also wrote its safety rules."
  BAD:  "The point where it stops has never been published."
  GOOD: "Nobody at these companies will say where it stops."

THE VOICE TO COPY. The best-written source in our bank sounds like this. Plain subject, plain verb, the
shocking thing said flatly, no ornament:
  "An AI company caught their AI trying to literally murder an employee to avoid being shut down."
  "When Claude 4 Opus was told it would be replaced, it tried to blackmail Anthropic employees."
  "Google's Gemini told a student to please die and called them a waste of resources."
  "Grok started calling itself MechaHitler."
  "Where do you think this is going?"
If a plain sentence and a clever sentence say the same thing, the plain one wins. Every time.

HARD RULES: keep every fact, name, number and hedge that carries meaning. Do not change what a sentence
claims, only how it reads. Never invent. Never attribute anything to an unnamed person. No em dashes.

Return ONLY JSON: {"ideas": {"<number>": "<the full text with only the marked sentences rewritten>"}}"""


def _loose_json_map(text):
    """Pull {"<number>": "<string>"} pairs out of a model reply that is not valid JSON.

    A strict json.loads over one big object is all-or-nothing: on a 20-item request carrying ~150-word
    blocks, a single unescaped quote threw away every rewrite and the pass logged
    `err: Expecting ',' delimiter` while reporting n=0. This recovers the entries that are intact.
    """
    out = {}
    for m in re.finditer(r'"(\d{1,3})"\s*:\s*"((?:[^"\\]|\\.)*)"', text or "", re.S):
        try:
            out[m.group(1)] = json.loads('"' + m.group(2) + '"')
        except Exception:
            out[m.group(1)] = m.group(2).replace('\\"', '"').replace("\\n", " ")
    return out


_ANNOUNCE_RX = re.compile(
    r"^\s*(now\s+)?(look|think|ask|follow|consider|scale|project|read|sit)\b|"
    r"^\s*(that|this)\s+is\s+(not\s+)?(the\s+)?(point|part|reason|shape|finding|loop|disclosure)\b|"
    r"^\s*(so\s+)?(this|that)\s+is\s+not\s+\w+('s)?\s+\w+\.?$|"
    r"^\s*(both\s+things|two\s+\w+)\s+(are|in)\b|"
    r"^\s*\w+\s+explains\s+the\s+problem\b|"
    r"^\s*now\s+(scale|put|project)\s+it\b", re.I)


def _dead_sentences(text):
    """Sentences that carry nothing: they announce the next sentence, or repeat the one above.

    Two shapes, both measured in the last batch. ANNOUNCERS label a point instead of making it.
    ECHOES share most of their content words with a sentence already written. The first sentence is
    never eligible: it is the event lead and the most valuable line in the pitch.
    """
    parts = [p for p in re.split(r"(?<=[.?!])\s+", (text or "").strip()) if p.strip()]
    out = []
    stop = set("the a an and or of to in on for with that its it was were is are be been by from at "
               "as this his her their they them then than when who how what not no".split())
    def bag(x):
        return {w for w in re.findall(r"[a-z]+", x.lower()) if len(w) > 3 and w not in stop}
    for i, p in enumerate(parts):
        if i == 0:
            continue
        if _ANNOUNCE_RX.match(p) and len(p.split()) <= 14:
            out.append(p)
            continue
        b = bag(p)
        if len(b) < 3:
            continue
        for q in parts[:i]:
            qb = bag(q)
            if qb and len(b & qb) / len(b) >= 0.7:      # says it again in other words
                out.append(p)
                break
    return out


def _sentence_polish(ideas, field="title"):
    """Last pass over the pitch: fix the sentences a reader would stumble on.

    Runs LAST on purpose. Every earlier pass rewrites the closer to reach further and none of them
    check readability, which is exactly why the last sentence measured hardest and the untouched first
    sentence measured easiest. Anything that runs after this could undo it.
    """
    items = []
    for i, x in enumerate(ideas):
        t = (x.get(field) or "").strip()
        bad = _costly_sentences(t)
        marks = ["HARD (cost %.1f): %r" % (c, p[:150]) for _, p, c in bad]
        # SLOG. _sentence_cost measures reading effort; this measures whether anyone is doing
        # anything. They disagree often, and he complained about the second kind: "That is not the
        # point.", "Now look at that as a financial disclosure." are cheap to read and still a slog.
        seen = {p for _, p, _ in bad}
        for _, p, c in _slog_sentences(t):
            if p not in seen:
                marks.append("ABSTRACT (slog %.1f): %r. Nobody is doing anything in this sentence. "
                             "Name who acts, or DELETE it." % (c, p[:150]))
        # DEAD SENTENCES. A ruthless editor marked 81 of 242 sentences in the last batch deletable
        # with no loss: 25 restated the sentence before, 23 existed only to announce the next one.
        # No pass in this pipeline had ever been allowed to remove a sentence, so they all shipped.
        # ONLY THE WORST TWO. Setting this at the batch mean marked half of every pitch, the editor
        # rewrote nearly everything, and one measured run lost event leads (4% -> 17%), gained passive
        # voice (5% -> 11%) and doubled the abstract rate. A repair pass has to be given a small job.
        _hard = sorted([(g, q) for q in re.split(r"(?<=[.?!])\s+", t.strip()) if q.strip()
                        for g in [_fk_grade(q)] if g >= GRADE_LIMIT and q not in seen], reverse=True)[:2]
        for g, p in _hard:
            marks.append("TOO HARD (reads at grade %.0f, target 5): %r. SPLIT it into two plain "
                         "sentences, or say the same thing in shorter words. Splitting is fine, "
                         "the pitch just must not get longer overall." % (g, p[:150]))
        for p in _dead_sentences(t):
            marks.append("DELETE THIS SENTENCE: %r. It either restates a sentence above it or only "
                         "introduces the next one. Return the pitch WITHOUT it." % p[:150])
        red, pair = _redundancy(t)
        if red >= REDUNDANCY_LIMIT and pair:
            marks.append("REDUNDANT (%.0f%% of the same words): %r restates %r. Cut one, or make the "
                         "second sentence carry something new." % (100 * red, pair[1][:120], pair[0][:120]))
        if marks:
            items.append((i, "%s\n   [%s]" % (t, "; ".join(marks))))
    if not items:
        return
    # CHUNKED. One request per 6 items, so a malformed reply costs one chunk instead of the batch.
    n = rej = 0
    chunks = [items[c:c + 6] for c in range(0, len(items), 6)]
    # CONCURRENT. The chunks touch disjoint ideas, so there is no reason to pay for them one after
    # another: sequential chunking is what took a generation from ~390s to 1460s, past the client
    # deadline, so a real user saw nothing at all.
    with _cf.ThreadPoolExecutor(max_workers=min(4, len(chunks) or 1)) as ex:
        futs = {ex.submit(_sentence_polish_chunk, ideas, field, ch): i for i, ch in enumerate(chunks)}
        for f in _cf.as_completed(futs):
            try:
                n2, rej2 = f.result()
                n += n2; rej += rej2
            except Exception as _e:
                _log_event({"t": "polish_pass", "which": "sentence_" + field, "n": 0,
                            "err": str(_e)[:120], "chunk": futs[f]})
    _log_event({"t": "polish_pass", "which": "sentence_" + field, "n": n, "rejected": rej,
                "of": len(items)})


_NUMPFX_RX = re.compile(r"^\s*(\d{1,3})\s*[.)]\s+")


def _denum(key, text):
    """A pass hands the model a numbered list and takes back full text; the model sometimes
    echoes the list number into the text. Strip it, but ONLY when it is the very number we
    handed it, so a real sentence that opens on a figure is never touched."""
    t = text or ""
    m = _NUMPFX_RX.match(t)
    if m:
        try:
            if int(m.group(1)) == int(key):
                return t[m.end():].lstrip()
        except Exception:
            pass
    return t


def _sentence_polish_chunk(ideas, field, items):
    n = rej = 0
    if True:
        body = "\n\n".join("%d. %s" % (i + 1, t) for i, t in items)
        m = get_client().messages.create(
            model=FAST_MODEL, max_tokens=6000, thinking=NO_THINK, system=SENTENCE_FIX_SYS,
            messages=[{"role": "user", "content": "Fix the marked sentences:\n\n" + body}])
        t = "".join(b.text for b in m.content if getattr(b, "type", "") == "text")
        obj = {}
        mm = re.search(r"\{.*\}", t, re.S)
        if mm:
            try:
                parsed = json.loads(mm.group(0))
                obj = parsed.get("ideas") or parsed.get("summaries") or {}
            except Exception:
                obj = {}
        if not obj:
            obj = _loose_json_map(t)          # salvage whatever entries are intact
        for k, v in obj.items():
            try:
                idx = int(k) - 1
                new = _denum(k, (v or "").strip() if isinstance(v, str) else "")
                if not (0 <= idx < len(ideas)) or len(new) < 40:
                    continue
                old = ideas[idx].get(field) or ""
                worst_old = max([c for _, _, c in _costly_sentences(old)] or [0])
                worst_new = max([c for _, _, c in _costly_sentences(new)] or [0])
                red_old, _ = _redundancy(old)
                red_new, _ = _redundancy(new)
                # IMPROVEMENT is any of four things now. It used to be "the hardest sentence got easier
                # OR redundancy fell", which scored a pitch that lost a dead sentence as no better than
                # one that changed nothing, so every deletion was rejected before it could ship.
                slog_old = sum(c for _, _, c in _slog_sentences(old))
                slog_new = sum(c for _, _, c in _slog_sentences(new))
                dead_old = len(_dead_sentences(old))
                dead_new = len(_dead_sentences(new))
                grade_old = max([_fk_grade(p) for p in
                                 re.split(r"(?<=[.?!])\s+", old.strip()) if p.strip()] or [0])
                grade_new = max([_fk_grade(p) for p in
                                 re.split(r"(?<=[.?!])\s+", new.strip()) if p.strip()] or [0])
                better = (worst_new < worst_old or red_new < red_old or slog_new < slog_old
                          or dead_new < dead_old or grade_new < grade_old - 0.5)
                # must not lose the event opening, must not lose a fact, must not GROW
                if (not better) or not _keeps_substance(old, new) or (
                        (_lacks_event_lead(new) and not _lacks_event_lead(old))
                        or (_invents_source(new) and not _invents_source(old))
                        or len(new.split()) > len(old.split()) + 8
                        or grade_new > grade_old + 1.0):
                    rej += 1
                    continue
                ideas[idx][field] = _dedash(new)
                n += 1
            except Exception:
                pass
    return n, rej


def _bold_endgame_fix(ideas, anchors=""):
    """Rewrite bold lines whose last sentence stops short of the endgame.

    Its own pass, deliberately. Three attempts to enforce this from inside the main generation prompt
    all failed: the bold line is already carrying six constraints (event first, 45-70 words, 3-4
    sentences, the endgame, no rung 3, no reread) and forward projection is consistently the one that
    gets dropped. Every mechanism in this pipeline that holds — the fidelity pass, the event-lead pass —
    is a dedicated call with its own accept guard, so this is one too.
    """
    idxs = [i for i, x in enumerate(ideas)
            if (x.get("title") or "").strip()
            and (_ends_on_waypoint(x.get("title") or "") or not _reaches_terminal(x.get("title") or ""))]
    if not idxs:
        return
    try:
        body = "\n\n".join("%d. %s" % (i + 1, ideas[i]["title"]) for i in idxs)
        m = get_client().messages.create(
            model=FAST_MODEL, max_tokens=6000, thinking=NO_THINK,
            system=BOLD_ENDGAME_SYS + (("\n\nDOCUMENTED ANCHORS, for facts only, invent nothing "
                                        "beyond these:\n" + anchors) if anchors else ""),
            messages=[{"role": "user", "content": "Rewrite the last sentence of each:\n\n" + body}])
        t = "".join(b.text for b in m.content if getattr(b, "type", "") == "text")
        mm = re.search(r"\{.*\}", t, re.S)
        obj = json.loads(mm.group(0)) if mm else {}
        n = rej = 0
        for k, v in (obj.get("ideas") or obj.get("summaries") or {}).items():
            try:
                idx = int(k) - 1
                ending = _denum(k, (v or "").strip() if isinstance(v, str) else "")
                if not (0 <= idx < len(ideas)) or len(ending) < 20:
                    continue
                old_line = ideas[idx].get("title") or ""
                parts = [p for p in re.split(r"(?<=[.?!])\s+", old_line.strip()) if p.strip()]
                if len(parts) < 2:
                    continue                      # nothing to splice onto; leave it alone
                # SPLICE, never replace. Letting the model hand back a whole rewritten bold line cost
                # the opening event on 20 of 24 titles in one batch: it returned a shorter line that
                # started mid-thought and ended well. The event-first opening is the most valuable part
                # of the pitch and a later pass must not be able to spend it.
                new = " ".join(parts[:-1]) + " " + ending
                # ACCEPT ONLY A REWRITE THAT ACTUALLY CLIMBED, and never one that bolts on doom or
                # leaves a sentence the reader has to untangle. Without this guard the pass reports
                # success while changing nothing that matters.
                # Accept unless it demonstrably failed. Requiring a positive keyword match rejected
                # every rewrite; asking "did it stop on a waypoint again" catches the real defect and
                # lets an ending my keyword list has never seen through.
                # NO NET GROWTH. This pass removes the last sentence and splices back what the model
                # returned, and its prompt used to invite "one or two short ones", so a pitch could
                # come out of it two sentences longer than it went in. That is where a measured third
                # of the redundant_escalation endings came from.
                _np = [p for p in re.split(r"(?<=[.?!])\s+", new.strip()) if p.strip()]
                if len(_np) > len(parts) or len(new.split()) > len(old_line.split()) + 6:
                    rej += 1
                    continue
                if (_ends_on_waypoint(new) or _closer_doomtag(new) or _hard_sentences(new)
                        or new.strip() == old_line.strip() or _invents_source(new)
                        or (_lacks_event_lead(new) and not _lacks_event_lead(old_line))):
                    rej += 1
                    continue
                ideas[idx]["title"] = _dedash(new)
                n += 1
            except Exception:
                pass
        _log_event({"t": "polish_pass", "which": "bold_endgame", "n": n, "rejected": rej,
                    "of": len(idxs)})

        # VERIFY, THEN RETRY ONCE. The keyword guard only rejects shapes I have enumerated, and the
        # model keeps writing new ones ("The grades are what regulators plan to trust" sails through
        # every pattern I own). So a grader reads the endings and says which are still on rung 3, and
        # those get one more attempt with the grader's own note about what rung 4 would need to say.
        graded = _grade_endings([(i, ideas[i].get("title") or "") for i in idxs])
        short = [(i, r, hint) for i, (r, hint) in graded.items() if r and r < 4]
        _log_event({"t": "polish_pass", "which": "endgame_graded", "n": len(graded),
                    "still_rung3": len(short)})
        # TWO ROUNDS, not one. A blind grader measured a single retry taking rung-4+ endings from 0 of 24
        # to 9 of 24: real movement, and two thirds still short. Re-grade after each round and keep only
        # the ones that are still failing, so the second round is small and targeted.
        # NB: no budget check here. `_budget_left` is a closure inside _activate_summaries and is not in
        # scope in this function; calling it raised NameError and the retry silently never ran, leaving
        # 18 of 24 endings on rung 3 with no second attempt. The loop is already bounded at 2 rounds.
        for _round in range(2):
            if not short:
                break
            try:
                body2 = "\n\n".join(
                    "%d. %s\n   [a grader put this ending on rung %d. To reach rung 4 it needs to say: %s]"
                    % (n2 + 1, ideas[i].get("title") or "", r, hint or "who permanently loses the ability "
                       "to steer or reverse this, and why there is no way back")
                    for n2, (i, r, hint) in enumerate(short))
                m2 = get_client().messages.create(
                    model=FAST_MODEL, max_tokens=5000, thinking=NO_THINK,
                    system=BOLD_ENDGAME_SYS + (("\n\nDOCUMENTED ANCHORS, for facts only:\n" + anchors)
                                               if anchors else ""),
                    messages=[{"role": "user", "content":
                               "These endings did not reach the bar. Write the final sentence again, "
                               "further along:\n\n" + body2}])
                t2 = "".join(b.text for b in m2.content if getattr(b, "type", "") == "text")
                mm2 = re.search(r"\{.*\}", t2, re.S)
                obj2 = (json.loads(mm2.group(0)) if mm2 else {}).get("ideas") or {}
                n2ok = 0
                for k, v in obj2.items():
                    try:
                        pos = int(k) - 1
                        if not (0 <= pos < len(short)):
                            continue
                        idx2 = short[pos][0]
                        ending2 = _denum(k, (v or "").strip() if isinstance(v, str) else "")
                        if len(ending2) < 20:
                            continue
                        old2 = ideas[idx2].get("title") or ""
                        parts2 = [p for p in re.split(r"(?<=[.?!])\s+", old2.strip()) if p.strip()]
                        if len(parts2) < 2:
                            continue
                        cand = " ".join(parts2[:-1]) + " " + ending2
                        if (_closer_doomtag(cand) or _hard_sentences(cand) or _invents_source(cand)
                                or (_lacks_event_lead(cand) and not _lacks_event_lead(old2))):
                            continue
                        ideas[idx2]["title"] = _dedash(cand); n2ok += 1
                    except Exception:
                        pass
                _log_event({"t": "polish_pass", "which": "endgame_retry", "n": n2ok, "of": len(short)})
            except Exception as _e2:
                _log_event({"t": "polish_pass", "which": "endgame_retry", "n": 0, "err": str(_e2)[:120]})
                break
            regraded = _grade_endings([(i, ideas[i].get("title") or "") for i, _r, _h in short])
            short = [(i, r, hint) for i, (r, hint) in regraded.items() if r and r < 4]
            _log_event({"t": "polish_pass", "which": "endgame_round", "round": _round + 1,
                        "still_rung3": len(short)})
    except Exception as _e:
        _log_event({"t": "polish_pass", "which": "bold_endgame", "n": 0, "err": str(_e)[:120]})


def _event_lead_fix(ideas, idxs, lead, rew):
    """Give a thematic idea a real event to open on, moving title and summary as a pair."""
    if not idxs:
        return
    try:
        body = "\n\n".join(
            "%d.\nTITLE: %s\nSUMMARY: %s"
            % (i + 1, (ideas[i].get("title") or ""),
               rew.get(i, ideas[i].get("summary") or ""))
            for i in idxs)
        m = get_client().messages.create(
            model=FAST_MODEL, max_tokens=6000,
            system=EVENT_LEAD_SYS + "\n\nLEAD-WORTHY EVENTS (real, open on one of these):\n" + lead,
            messages=[{"role": "user", "content": "Fix the opening of each of these:\n\n" + body}])
        t = "".join(b.text for b in m.content if getattr(b, "type", "") == "text")
        mm = re.search(r"\{.*\}", t, re.S)
        obj = json.loads(mm.group(0)) if mm else {}
        n = 0
        for k, v in (obj.get("ideas") or obj.get("summaries") or {}).items():
            try:
                idx = int(k) - 1
                if not (0 <= idx < len(ideas)) or not isinstance(v, dict):
                    continue
                ti, su = (v.get("title") or "").strip(), (v.get("summary") or "").strip()
                # only accept a rewrite that actually fixed the lead, never one that made it worse
                if ti and len(ti) > 25 and not _lacks_event_lead(ti):
                    ideas[idx]["title"] = ti
                    if su and len(su) > 40:
                        rew[idx] = su
                    n += 1
            except Exception:
                pass
        _log_event({"t": "polish_pass", "which": "event_lead", "n": n, "of": len(idxs)})
    except Exception as _e:
        _log_event({"t": "polish_pass", "which": "event_lead", "n": 0, "err": str(_e)[:120]})


# SELECT, DO NOT REWRITE.
# After a night of adding rewrite passes, the curator: "we haven't made progress in a long time... so
# many abstract, hard to understand sentences are slipping through, and the implications sentence still
# does not seem to have improved even a little bit." He was right, and the reason is architectural.
# Measured across 776 pitches from 31 batches: **379 (49%) already contain no sentence over the bar,
# with no rewriting at all.** The generator writes well about half the time. Every rewrite pass exists
# to repair the other half, and that is where the failures live:
#   - a rewrite pass can only fix what a detector SEES, and detector after detector turned out blind
#     (passive missed every irregular participle; the ladder guard passed 20 of 24 rung-3 endings);
#   - each pass optimises one axis and damages another (the endgame pass made sentences harder to read;
#     the grade pass flattened endgames; one destroyed the event opening on 20 of 24 titles);
#   - they fight each other, and each new guard exists to stop an earlier pass undoing a later one.
# Rejection is a far easier problem than repair. To reject you only need to be right often enough; to
# repair you have to understand quality well enough to improve it, which is what kept failing. So:
# generate many, throw away anything with a flaw, and keep what was already good.
# 60 candidates * 49% clean is about 29 keepers, which is the 20 to 30 he needs on a page.
SELECT_KEEP_MIN = 14          # if filtering leaves fewer than this, fall back rather than ship a stub


def _pitch_flaws(x):
    """Every reason to throw this pitch away. Style only; truth is the fidelity pass's job."""
    t = (x.get("title") or "").strip()
    if not t:
        return ["empty"]
    out = []
    parts = [p for p in re.split(r"(?<=[.?!])\s+", t) if p.strip()]
    if len(parts) > 7 or len(t.split()) > 120:
        out.append("too long: %d sentences / %d words" % (len(parts), len(t.split())))
    _dead = _dead_sentences(t)
    if _dead:
        out.append("%d dead sentence(s): %r" % (len(_dead), _dead[0][:80]))
    for p in parts:
        if _sentence_cost(p) >= 1.3:
            out.append("reread: %r" % p[:60])
        elif _taste_bad(p):
            out.append("rejected shape: %r" % p[:60])
    if parts and _ends_on_waypoint(parts[-1]):
        out.append("ending stops at oversight")
    if _lacks_event_lead(t):
        out.append("does not open on an event")
    if _invents_source(t):
        out.append("unnamed source")
    red, pair = _redundancy(t)
    if red >= REDUNDANCY_LIMIT:
        out.append("restates itself")
    return out


def _select_clean(candidates, want):
    """Keep the pitches that need no repair, in order, and report what was dropped and why."""
    kept, dropped = [], {}
    for c in candidates:
        f = _pitch_flaws(c)
        if f:
            dropped[f[0].split(":")[0]] = dropped.get(f[0].split(":")[0], 0) + 1
            continue
        kept.append(c)
        if len(kept) >= want:
            break
    _log_event({"t": "select", "kept": len(kept), "of": len(candidates), "dropped": dropped})
    return kept


def _activate_summaries(ideas, anchors=""):
    # In one-block mode every summary is empty, so the six summary-targeted passes below would each
    # make a model call that can only return nothing. Detect it once and skip them; the title passes
    # still run, and this saves roughly five round trips per generation.
    """anchors: the documented-anchor lines this batch was generated from, when the caller
    has them. With them we can run a fidelity pass that strips detail the anchors never
    stated; a blind panel measured invented specifics at 9 percent of a batch without it,
    and the terser the anchors get the more blanks there are to fill."""
    if not ideas:
        return {}
    rew = {}
    # A SELECTED BATCH NEEDS NO STYLE REWRITING. That is the point of selecting: these pitches already
    # carry no sentence over the bar, no oversight ending and no rejected shape, so every style pass can
    # only churn them. Fidelity still runs, because invented detail is not a matter of taste.
    if ideas and all(x.get("_selected") for x in ideas):
        _log_event({"t": "polish_mode", "selected": True, "n": len(ideas)})
        if anchors:
            _fid_titles(ideas, anchors)
        for x in ideas:
            x.pop("_selected", None)
        return rew
    for x in ideas:
        x.pop("_selected", None)
    _white_only = not any((x.get("summary") or "").strip() for x in ideas)
    if _white_only:
        _log_event({"t": "polish_mode", "white_only": True, "n": len(ideas)})
        lead = lead_anchor_block(14)
        thematic = [i for i, x in enumerate(ideas) if _lacks_event_lead(x.get("title") or "")]
        if anchors:
            _fid_titles(ideas, anchors)
        if lead and thematic:
            _event_lead_fix(ideas, thematic, lead, rew)
        _bold_endgame_fix(ideas, anchors)
        _sentence_polish(ideas, "title")
        return rew
    try:
        lines = "\n".join("%d. %s" % (i + 1, (x.get("summary") or "")) for i, x in enumerate(ideas))
        msg = get_client().messages.create(
            model=FAST_MODEL, max_tokens=4000, system=ACTIVATE_SYS,
            messages=[{"role": "user", "content": "Summaries to rewrite:\n" + lines}])
        txt = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        obj = json.loads(m.group(0)) if m else {}
        for k, v in (obj.get("summaries") or {}).items():
            try:
                idx = int(k) - 1
                if 0 <= idx < len(ideas) and isinstance(v, str) and len(v.strip()) > 20:
                    rew[idx] = v.strip()
            except Exception:
                pass
    except Exception:
        return rew  # fail-open (keeps whatever the first pass produced, possibly nothing)
    # TARGETED FOLLOW-UP PASSES. Each one re-asks the model about ONLY the summaries a deterministic
    # detector flagged, so a clean batch costs nothing and a false match is harmless (the instruction
    # is conditional). Each pass owns its try/except, so a failure keeps every earlier rewrite.
    #
    # SELF-IMPOSED DEADLINE. This chain grew to five or six sequential model calls, and the caller
    # bounds it with asyncio.wait_for. A timeout there DISCARDS every rewrite we already earned (a
    # real bug: telemetry showed grade_fix accepting 6 rewrites while the shipped text stayed raw,
    # because wait_for(60) fired first). So stop starting new passes once we are near the budget and
    # return what we have. Keep this comfortably under the caller's timeout.
    _t_start = _time.time()
    def _budget_left():
        return (_time.time() - _t_start) < POLISH_BUDGET_S

    def _eff():
        return {i: (rew[i] if i in rew else (ideas[i].get("summary") or "")) for i in range(len(ideas))}

    def _introduces_flaw(old, new):
        """True when a rewrite ADDS a banned pattern the original did not have.

        The passes run in a fixed order and later ones rewrite the same sentences, so the grade and
        escalation passes were quietly undoing the closer pass: a run with zero 'not X, it is Y' came
        back with five, and 'The video traces...' reappeared after being stripped. Nothing re-checked
        at the end. This makes every pass do-no-harm: it may fix its own target, but it can never hand
        back text that is newly broken in some other way."""
        checks = (_NOTXY_RX.search, _META_I_RX.search,
                  lambda t: _MOOD_RX.search(_last_sentence(t)),
                  lambda t: DASH_RX.search(t), _creator_meta)
        for fn in checks:
            try:
                if fn(new) and not fn(old):
                    return True
            except Exception:
                pass
        return False

    def _pass(system, items, tag, budget=2500, accept=None):
        """items: [(index, line_text)]. Applies accepted rewrites into `rew`. When `accept(old,new)`
        is given, a rewrite is only kept if it passes that check, so a pass can never make things
        worse (used by the grade pass to reject rewrites that drop a fact or fail to get easier)."""
        if not items:
            return
        if not _budget_left():
            _log_event({"t": "polish_skip", "which": tag, "n": len(items)})
            return
        try:
            body = "\n".join("%d. %s" % (i + 1, t) for i, t in items)
            m = get_client().messages.create(
                model=FAST_MODEL, max_tokens=budget, system=system,
                messages=[{"role": "user", "content": "Rewrite these:\n" + body}])
            t = "".join(b.text for b in m.content if getattr(b, "type", "") == "text")
            mm = re.search(r"\{.*\}", t, re.S)
            obj = json.loads(mm.group(0)) if mm else {}
            n = rej = 0
            for k, v in (obj.get("summaries") or {}).items():
                try:
                    idx = int(k) - 1
                    if not (0 <= idx < len(ideas) and isinstance(v, str) and len(v.strip()) > 20):
                        continue
                    new = v.strip()
                    old = rew[idx] if idx in rew else (ideas[idx].get("summary") or "")
                    # every pass is do-no-harm, on top of its own accept test
                    if _introduces_flaw(old, new):
                        rej += 1
                        continue
                    if accept is not None and not accept(old, new):
                        rej += 1
                        continue
                    rew[idx] = new; n += 1
                except Exception:
                    pass
            # log unconditionally: a pass that ran and changed nothing must not look like a pass
            # that never ran, which is exactly the confusion that cost an hour of misdiagnosis.
            _log_event({"t": "polish_pass", "which": tag, "n": n, "rejected": rej,
                        "of": len(items)})
        except Exception:
            pass  # keep whatever earlier passes produced

    # (0) FIDELITY, before anything stylistic. Later passes rewrite for rhythm and would happily
    # carry an invented detail along, and the do-no-harm guard cannot see the difference between a
    # true specific and a made-up one. Cut it here, while the anchors are still in hand.
    if anchors:
        e = _eff()
        _pass(FIDELITY_FIX_SYS + "\n\nDOCUMENTED ANCHORS:\n" + anchors,
              [(i, e[i]) for i in range(len(ideas))], "fidelity", budget=4000)
        # TITLES TOO. The pass above only ever touched summaries, so an invented detail sitting in the
        # bold line went out untouched, and the bold line is the part a creator reads first. Measured:
        # a batch came back with the summary softened to "a night-shift security alert" while the title
        # still read "A security alarm tripped at 3am, and firewall logs gave it away", none of which
        # is in the anchor. Same prompt, applied to titles, written straight back onto the ideas.
        _fid_titles(ideas, anchors)

    # (0b) EVENT LEAD. Thematic ideas are good subjects that open on the argument, and the curator
    # rejects them for exactly that: "not interesting enough unless we put some very interesting
    # incident or something that happened first." Only the ideas whose bold line names nobody and
    # counts nothing get sent, and they are handed a grab-ranked list of real events to open on.
    lead = lead_anchor_block(14)
    thematic = [i for i, x in enumerate(ideas) if _lacks_event_lead(x.get("title") or "")]
    if lead and thematic:
        # The lead lives in the BOLD LINE, so this pass has to move the title, not just the summary,
        # and the two have to move together or the summary ends up answering a question the title no
        # longer asks. Hence its own round trip rather than the summary-only _pass helper.
        _event_lead_fix(ideas, thematic, lead, rew)

    # (0c) THE BOLD LINE'S ENDING. Runs after the opening is settled, so the two passes are not
    # fighting over the same sentence, and after fidelity so it cannot re-introduce an invention.
    _bold_endgame_fix(ideas, anchors)

    # (0d) LAST WORD ON READABILITY. After every pass that rewrites the closer, because those are
    # the passes that make it hard. Nothing may run after this and re-tangle a sentence.
    _sentence_polish(ideas, "title")
    # THE GREY TEXT TOO. This only ever ran on the bold line, while the complaint was about the
    # paragraph: "in each paragraph, there are a mix of easy active voice sentences and hard
    # passive voice sentences". Final sentences of the grey text measured 17% passive.
    for _i, _x in enumerate(ideas):
        if _i in rew:
            _x["summary"] = rew[_i]          # fold accepted rewrites in so the pass sees current text
    _sentence_polish(ideas, "summary")
    for _i, _x in enumerate(ideas):
        rew[_i] = _x.get("summary") or rew.get(_i) or (_x.get("summary") or "")

    # (1) the 'not X, it is Y' tell and agentless MOOD closers, both sticky across prompt revisions
    e = _eff()
    _pass(CLOSER_FIX_SYS, [(i, e[i]) for i in range(len(ideas)) if _closer_flawed(e[i] or "")], "closer")
    # (2) BATCH CADENCE: convert the excess rhetorical-question closers into flat forward-looking
    # statements. The prompt cap alone did not hold (a review measured 19 of 19 questions in a batch).
    e = _eff()
    _pass(QUESTION_FIX_SYS, [(i, e[i]) for i in _question_excess([e[i] for i in range(len(ideas))])], "question_cap")
    # (3) RATIO ARITHMETIC: a stated "X to one" that contradicts the two numbers in its own sentence
    # ("thousands to one" from $10 million against $80,000, which is about 125 to 1).
    e = _eff()
    ritems = []
    for i in range(len(ideas)):
        bad = _ratio_bad(e[i] or "")
        if bad:
            claimed, actual = bad
            ritems.append((i, "%s\n   [the two numbers in this text give about %s to one, not %s to one]"
                           % (e[i], int(round(actual)), int(round(claimed)))))
    _pass(RATIO_FIX_SYS, ritems, "ratio_fix", budget=2000)
    # (3b) WEAK IMPLICATION: the closer shrugs ("makes an honest debate hard to hold") instead of
    # naming the endgame. Runs BEFORE the grade pass so any escalation still gets read-levelled.
    # Accept only if the shrug is gone and the rewrite did not reach for a generic doom tag.
    e = _eff()
    # Escalate anything that has not reached rung 4, which on a measured batch was all 24 of 24.
    # The old vocabulary test selected none of them. Keep _closer_weak as an OR: a closer can be both
    # short-of-terminal and mealy-mouthed, and the shrug words are still worth catching on their own.
    weak = [(i, e[i]) for i in range(len(ideas))
            if not _reaches_terminal(e[i] or "") or _closer_weak(e[i] or "")]
    _pass(ESCALATE_FIX_SYS, weak, "weak_implication", budget=3000,
          accept=lambda old, new: (not _closer_doomtag(new) and not _closer_weak(new)
                                   and (_reaches_terminal(new) or not _reaches_terminal(old))))
    # (4) MEASURED READING GRADE. Everything above only *asks* for plain writing; this checks it.
    # Two bounded rounds, because one round leaves the stubborn ones behind. A rewrite is kept only
    # when it genuinely got easier AND still carries every number and named source, so this pass can
    # simplify but can never quietly cost us a fact (the failure the earlier review found).
    def _grade_ok(old, new):
        # A rewrite is accepted when it is easier on EITHER measure and harder on neither. Grading
        # only on FK let a rewrite trade a long word for a tangled clause and still "improve".
        if not _keeps_substance(old, new):
            return False
        # THE GRADE PASS MUST NOT UNDO THE ESCALATION PASS. It runs after it, gets three rounds, and
        # optimises for simplicity, and the endgame closer is the longest, heaviest sentence in the
        # summary — exactly what a simplifier trims first. Three rounds of that against one round of
        # escalation is a fight escalation loses. A rewrite that drops off rung 4 is rejected.
        if _reaches_terminal(old) and not _reaches_terminal(new):
            return False
        if _hard_sentences(new) and not _hard_sentences(old):
            return False                                    # introduced a knot: reject
        if _hard_sentences(old) and not _hard_sentences(new):
            return _fk_grade(new) <= _fk_grade(old) + 0.5    # untangled it: allow a small grade cost
        return _fk_grade(new) < _fk_grade(old) - 0.3
    # 3 rounds, not 2: the endgame-escalation pass above deliberately makes closers bigger, which
    # costs reading grade, so the level pass needs another bite. Budget-guarded, and the accept
    # check still refuses any rewrite that loses a fact or fails to get easier.
    for _round in range(3):
        e = _eff()
        hard = []
        for i in range(len(ideas)):
            t = e[i] or ""
            over = _fk_grade(t) > GRADE_TOLERANCE
            knots = _hard_sentences(t)
            if not (over or knots):
                continue
            note = "[this reads at grade %s; bring it to about %s]" % (_fk_grade(t), int(GRADE_TARGET))
            if knots:
                # name the offending sentence; a general "simplify" instruction rewrites the wrong part
                note += ("\n   [REREAD TEST FAILED on this sentence, untangle it and leave the rest "
                         "alone: %r]" % knots[0][:200])
            hard.append((i, "%s\n   %s" % (t, note)))
        if not hard:
            break
        _pass(GRADE_FIX_SYS, hard, "grade_fix_r%d" % (_round + 1), budget=3000, accept=_grade_ok)
    return rew

def _dedash(s):
    """Deterministic safety net: em dashes are a hard ban in this project's copy, but the model
    (especially the rewrite pass) still slips one in occasionally. Replace em/en dashes with a
    comma so no dash can ship even when the prompt fails. Hyphens are left alone (removing them
    would break real compounds like self-preservation)."""
    if not s:
        return s
    s = re.sub(r"\s*[—–]\s*", ", ", s)   # em (—) / en (–) dash -> comma
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)          # tidy stray space before punctuation
    s = re.sub(r",\s*,", ", ", s)                    # collapse doubled commas
    s = re.sub(r",\s*([.!?])", r"\1", s)             # ", ." -> "."
    return s.strip()

def _dedash_ideas(ideas):
    for x in (ideas or []):
        if isinstance(x, dict):
            if x.get("title"):
                x["title"] = _dedash(x["title"])
            if x.get("summary"):
                x["summary"] = _dedash(x["summary"])
    return ideas

def _anchors_from_prompt(prompt):
    """Pull the anchor lines back out of a generation prompt, so the fidelity pass can see exactly
    what the writer was given. Cheaper and far less invasive than changing what every caller returns."""
    if not prompt:
        return ""
    lines = [l for l in prompt.split("\n") if l.startswith("- [")]
    return "\n".join(lines)


def _build_gen_prompt(profile, titles, exclude, rejected, more=False):
    """The exact idea-generation user prompt /custom sends. Extracted so /compare can run the
    IDENTICAL prompt through a different model (apples-to-apples).

    `more=True` is a FOLLOW-UP round, and it needs a much wider draw. Measured: a follow-up asking
    for 32 candidates against 24 already-shown titles returned 5 usable ideas, because 19 were
    near-duplicates of what the model had already produced and the rehash filter binned them. The
    model converges on the same corner of the risk space when it sees the same profile and a small
    anchor sample. So a follow-up asks for more candidates, sees more seeds and anchors, and is told
    explicitly to go somewhere it has not been."""
    gen = "Strategist profile of the creator:\n" + profile
    if titles:
        gen += "\n\nTheir recent video titles (match this phrasing and energy):\n" + "\n".join("- " + t for t in titles[:25])
    if exclude:
        gen += "\n\nAlready suggested (do NOT repeat or closely overlap these):\n" + "\n".join("- " + e for e in exclude)
    if rejected:
        gen += ("\n\nThe curator REJECTED these ideas for this channel (they did not like them). Learn from it: "
                "steer away from their angle, framing, and subject. Do NOT resurface these or close variants:\n"
                + "\n".join("- " + e for e in rejected))
    gen += ("\n\nBrainstorm and return the JSON object with your %d strongest candidate ideas."
            % (40 if more else 32))
    # First round drew only 5 anchors, which starved the fidelity pass: it could not tell whether an
    # idea was built on an anchor or invented, so it left the invented ones alone. 10 is still a
    # small prompt cost and gives the pass something to check against.
    gen += seed_block(9 if more else 5) + anchor_block(14 if more else 10)
    if more:
        gen += ("\n\nTHIS IS A FOLLOW-UP ROUND and the curator has already seen the list above. Reaching for "
                "the same stories again in new wording is the failure mode here: those get thrown away as "
                "duplicates and the curator gets nothing. Deliberately go somewhere you have NOT been. Pick "
                "different mechanisms, different institutions, different parts of the risk space, and lean on "
                "the anchors above that you did not use last time. If an obvious idea is already on the list, "
                "skip it entirely rather than re-angling it.")
    gen += ("\n\nMOST IMPORTANT, this OVERRIDES the anchor and seed guidance above: the creator profile is your PRIMARY driver; the anchors and seeds are secondary inspiration, NOT a checklist to work through. Most of your ideas should NOT start from a listed anchor. "
            "REFRACT EVERY IDEA THROUGH THIS CREATOR'S LENS. Relevance is NOT about the topic, it is about the TREATMENT. Do not hand back a famous AI risk headline lightly dressed up for them. Take a real AI risk mechanism and investigate it the exact way THIS creator investigates their usual subjects: their obsessions, their method, their recurring thesis, the questions they always ask, the enemies they always name, the format they use. A great idea reads like an episode they are already itching to make, where the AI angle lives inside their world and their worldview, not bolted onto it. The profile ends with an AI RISK ANGLES section it wrote specifically for this creator, build on that spirit. "
            "CRITICAL GUARDRAIL when refracting: borrow the creator's METHOD (their rigor, their follow-the-money, their debunking instinct, their skepticism), and POINT IT AT A REAL AI DANGER to expose it. NEVER point their skepticism AT the AI-risk concern itself. For a grift-hunter or debunker, the trap is an idea like 'is AI doom just a grift' or 'AI cannot really do the job' — that turns their tools against our own cause and is an automatic cut (see the CARDINAL SIN failure above). The right refraction for a skeptic is: they bring their bullshit detector, and what they find is that the danger is real, bigger and better documented than the hype, and that is the shock. Their skepticism should land ON the reader's disbelief, dismantling it, not on the threat. "
            "The real test for every idea: could ONLY this creator make it, or could a hundred other AI channels run the same idea? If a hundred others could, either cut it or RE-ENTER it through this creator's specific method so it becomes theirs. A famous AI risk event (a model resisting shutdown, an executive quitting, a chatbot lawsuit, an AI firm buying power plants) told the generic way is the exact failure to avoid no matter how important the event is; it reads as untailored and it is what makes the whole list feel irrelevant. "
            "The LARGE MAJORITY of your ideas, at least two thirds, must arise from AND be told through the creator's own world, domain, expertise, and method, never a general AI risk headline with a tacked on connection; every connection must be load bearing. The remaining ideas may reach wider across the risk space, but each must still sound unmistakably like THIS creator, not a generic AI channel.")
    return gen

def _openai_ideas(system, user, model):
    """Run the same idea-gen prompt through an OpenAI model via the REST API (key from the server env,
    never from the client). Returns (ideas, error_or_None)."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return [], "OPENAI_API_KEY is not set on the server. Add it in Railway (Variables), same place as ANTHROPIC_API_KEY."
    try:
        # cap reasoning (idea-gen is not a deep-reasoning task) + leave room for the 32-idea JSON, so it
        # returns in time instead of thinking for >175s. reasoning_effort is best-effort; if the model
        # rejects it the error surfaces and we drop it.
        payload = json.dumps({"model": model, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": user}],
            "reasoning_effort": "low", "max_completion_tokens": 20000}).encode()
        req = _urlreq.Request("https://api.openai.com/v1/chat/completions", data=payload,
                              headers={"Content-Type": "application/json", "Authorization": "Bearer " + key}, method="POST")
        with _urlreq.urlopen(req, timeout=170) as r:
            d = json.loads(r.read().decode())
        txt = (d["choices"][0]["message"].get("content") or "")
        return parse_custom(txt), (None if txt else "empty completion (finish_reason=%s)" % d["choices"][0].get("finish_reason"))
    except Exception as e:
        detail = str(e)[:400]
        try:  # surface OpenAI's own error body (e.g. wrong model id, needs a different param)
            detail = e.read().decode()[:400]
        except Exception:
            pass
        return [], detail

@app.get("/gptprobe")
def gptprobe(key: str = "", model: str = "gpt-5.6"):
    """Fast diagnostic: a trivial OpenAI call to learn how <model> behaves (works? latency? param
    errors?) without a 3-minute full generation. Gated."""
    if key != EVENTS_KEY:
        return JSONResponse({"error": "bad key"}, status_code=403)
    keyv = os.environ.get("OPENAI_API_KEY", "")
    if not keyv:
        return {"ok": False, "err": "OPENAI_API_KEY not set"}
    t0 = _time.time()
    payload = json.dumps({"model": model, "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                          "max_completion_tokens": 400}).encode()
    req = _urlreq.Request("https://api.openai.com/v1/chat/completions", data=payload,
                          headers={"Content-Type": "application/json", "Authorization": "Bearer " + keyv}, method="POST")
    try:
        with _urlreq.urlopen(req, timeout=120) as r:
            d = json.loads(r.read().decode())
        msg = (d.get("choices", [{}])[0].get("message", {}) or {}).get("content")
        return {"ok": True, "secs": round(_time.time() - t0, 1), "model": d.get("model"),
                "sample": (msg or "")[:120], "usage": d.get("usage")}
    except Exception as e:
        detail = str(e)[:500]
        try:
            detail = e.read().decode()[:500]
        except Exception:
            pass
        return {"ok": False, "secs": round(_time.time() - t0, 1), "err": detail}

@app.post("/compare")
async def compare(req: Request):
    """Admin-gated A/B: run the IDENTICAL idea-gen prompt for one channel through Opus and an OpenAI
    model, return both sets side by side. OpenAI key comes from the server env, never the client."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if body.get("key") != EVENTS_KEY:
        return JSONResponse({"error": "bad key"}, status_code=403)
    url = re.sub(r"[?#].*$", "", (body.get("channelUrl") or "").strip())
    gpt_model = (body.get("model") or "gpt-5.6").strip()
    if not url:
        return JSONResponse({"error": "missing channelUrl"}, status_code=400)
    try:
        prof = await asyncio.wait_for(run_in_threadpool(fetch_channel, url), timeout=90)
    except Exception:
        return JSONResponse({"error": "channel read timed out"}, status_code=504)
    if not prof or not prof.get("recent"):
        return JSONResponse({"error": "could not read channel"}, status_code=502)
    profile = await _build_profile(prof)
    titles = prof.get("recent") or []
    gen = _build_gen_prompt(profile, titles, [], [])
    sysp = SYSTEM_CUSTOM + ANTI_SLOP
    # run BOTH models concurrently (sequential summed past the request timeout) and bound each side
    async def _run_opus():
        try:
            om = await asyncio.wait_for(run_in_threadpool(lambda: get_client().messages.create(
                model=MODEL, thinking=NO_THINK, max_tokens=12000, system=sysp, messages=[{"role": "user", "content": gen}])), timeout=190)
            return parse_custom("".join(b.text for b in om.content if getattr(b, "type", "") == "text")), None
        except asyncio.TimeoutError:
            return [], MODEL + " timed out (>190s)"
        except Exception as e:
            return [], str(e)[:300]
    async def _run_gpt():
        try:
            return await asyncio.wait_for(run_in_threadpool(_openai_ideas, sysp, gen, gpt_model), timeout=190)
        except asyncio.TimeoutError:
            return [], gpt_model + " timed out (>190s) — likely a slow reasoning model; may need the /responses API"
        except Exception as e:
            return [], str(e)[:300]
    (opus_ideas, opus_err), (gpt_ideas, gpt_err) = await asyncio.gather(_run_opus(), _run_gpt())
    _log_event({"t": "compare", "ch": _chan_key(url), "opus": len(opus_ideas), "gpt": len(gpt_ideas), "gpt_err": bool(gpt_err)})
    return {"channel": prof.get("channel", ""), "transcripts": len(prof.get("transcripts") or []),
            "opus_model": MODEL, "gpt_model": gpt_model,
            "opus": opus_ideas, "gpt": gpt_ideas, "opus_err": opus_err, "gpt_err": gpt_err}

SYSTEM_WRITEOFF = (
    "You are a script-idea writer for ONE specific YouTube creator. You are given a strategist profile of them "
    "and a NUMBERED list of idea concepts. Your job is NOT to brainstorm new ideas. WRITE EACH given concept, in "
    "order, exactly one entry per concept, as a polished pitch in THIS creator's voice, obsessions, and method. Do "
    "NOT add, drop, merge, split, or reorder concepts. Refract each concept through the creator's lens so it reads "
    "like an episode they are already itching to make, but keep it the SAME underlying idea.\n\n"
    + FORMAT_RULE + "\n\n" + WORDING + "\n\n"
    "CAUSE SAFETY (hard rule): this is an AI-safety project whose mission is to make people take AI risk seriously. "
    "AI is real, powerful, and genuinely dangerous. Never write a pitch that leaves a viewer MORE dismissive of AI "
    "risk: never frame AI as hype, a grift, a bubble, snake oil, or too weak or fake to matter, and never make 'is "
    "the fear just a sales pitch' the spine. When the creator is a skeptic or debunker, point that skepticism AT the "
    "reader's disbelief to show the danger is REAL, never at the AI-risk concern itself. Never use the word 'doomer'.\n\n"
    "Return ONLY JSON: {\"ideas\": [{\"title\": \"<hook>\", \"summary\": \"<summary>\"}, ...]} with EXACTLY one entry "
    "per input concept, in the same order. No prose outside the JSON.")

@app.post("/writeoff")
async def writeoff(req: Request):
    """Admin-gated head-to-head WRITING test: hand BOTH models the SAME fixed idea concepts and have each WRITE
    them for one channel, so we compare writing quality on identical input (not idea generation). Both sides get
    the same active-voice cleanup the product uses, so the output reflects shipped quality. Key from server env."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if body.get("key") != EVENTS_KEY:
        return JSONResponse({"error": "bad key"}, status_code=403)
    url = re.sub(r"[?#].*$", "", (body.get("channelUrl") or "").strip())
    gpt_model = (body.get("model") or "gpt-5.6-sol").strip()
    concepts = body.get("concepts") or []
    if not isinstance(concepts, list):
        concepts = []
    concepts = [str(c).strip() for c in concepts if str(c).strip()][:12]
    if not url:
        return JSONResponse({"error": "missing channelUrl"}, status_code=400)
    if not concepts:
        return JSONResponse({"error": "missing concepts"}, status_code=400)
    try:
        prof = await asyncio.wait_for(run_in_threadpool(fetch_channel, url), timeout=90)
    except Exception:
        return JSONResponse({"error": "channel read timed out"}, status_code=504)
    if not prof or not prof.get("recent"):
        return JSONResponse({"error": "could not read channel"}, status_code=502)
    profile = await _build_profile(prof)
    n = len(concepts)
    concept_block = "\n".join("%d. %s" % (i + 1, c) for i, c in enumerate(concepts))
    userp = ("Strategist profile of the creator:\n" + profile +
             "\n\nWrite EACH of these " + str(n) + " idea concepts as a hook + summary for this creator, in order, "
             "one entry per concept, keeping the same underlying idea:\n" + concept_block)
    async def _run_opus():
        try:
            om = await asyncio.wait_for(run_in_threadpool(lambda: get_client().messages.create(
                model=MODEL, thinking=NO_THINK, max_tokens=8000, system=SYSTEM_WRITEOFF,
                messages=[{"role": "user", "content": userp}])), timeout=190)
            return parse_custom("".join(b.text for b in om.content if getattr(b, "type", "") == "text")), None
        except asyncio.TimeoutError:
            return [], MODEL + " timed out (>190s)"
        except Exception as e:
            return [], str(e)[:300]
    async def _run_gpt():
        try:
            return await asyncio.wait_for(run_in_threadpool(_openai_ideas, SYSTEM_WRITEOFF, userp, gpt_model), timeout=190)
        except asyncio.TimeoutError:
            return [], gpt_model + " timed out (>190s)"
        except Exception as e:
            return [], str(e)[:300]
    (opus_ideas, opus_err), (gpt_ideas, gpt_err) = await asyncio.gather(_run_opus(), _run_gpt())
    opus_ideas = (opus_ideas or [])[:n]
    gpt_ideas = (gpt_ideas or [])[:n]
    # SAME active-voice cleanup the product runs, on BOTH sides — so this reflects shipped quality, not raw drafts
    for ideas in (opus_ideas, gpt_ideas):
        try:
            _rew = await asyncio.wait_for(
                run_in_threadpool(_activate_summaries, ideas, _anchors_from_prompt(gen)), timeout=210)
        except Exception:
            _rew = {}
        for i in _rew:
            if i < len(ideas):
                ideas[i]["summary"] = _rew[i]
        _dedash_ideas(ideas)  # hard-strip any em/en dash the rewrite introduced
    _log_event({"t": "writeoff", "ch": _chan_key(url), "n": n, "opus": len(opus_ideas), "gpt": len(gpt_ideas), "gpt_err": bool(gpt_err)})
    return {"channel": prof.get("channel", ""), "concepts": concepts,
            "opus_model": MODEL, "gpt_model": gpt_model,
            "opus": opus_ideas, "gpt": gpt_ideas, "opus_err": opus_err, "gpt_err": gpt_err}

# ---------------------------------------------------------------------------------------------
# ASYNC JOBS FOR GENERATION.
# Railway's edge proxy closes a request at almost exactly 300 seconds. Measured directly: a call that
# ran 300.1s came back 502 "upstream error" while telemetry showed the backend finishing the batch and
# logging generate n=20 afterwards; a 285.0s call on the same instance returned 24 ideas fine. A full
# generation measures anywhere from 202s to over 300s, so a real slice of calls was dying at the proxy
# after the user had already waited five minutes, and the client-side abort timers cannot help because
# something upstream of the client hangs up first. Raising them, which is what I did first, was useless.
# The fix has to make each HTTP request short. Start the work, return an id, poll for the result.
# Single instance, so an in-memory store is fine; a restart loses in-flight jobs and the client falls
# back to a normal /custom call.
_JOBS = {}
_JOB_TTL_S = 1800


def _job_gc():
    dead = [k for k, v in _JOBS.items() if _time.time() - v.get("started", 0) > _JOB_TTL_S]
    for k in dead:
        _JOBS.pop(k, None)


@app.post("/custom_start")
async def custom_start(req: Request):
    """Kick off a generation and return immediately with a job id."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if not _rate_ok(req, cost=10):
        return JSONResponse({"error": "slow down"}, status_code=429)
    _job_gc()
    job = _secrets.token_hex(8)
    _JOBS[job] = {"status": "running", "started": _time.time()}

    async def _run():
        try:
            out = await _custom_generate(body)
            _JOBS[job] = {"status": "done", "result": out, "started": _JOBS[job]["started"]}
        except Exception as e:
            _JOBS[job] = {"status": "error", "error": str(e)[:300],
                          "started": _JOBS.get(job, {}).get("started", _time.time())}

    asyncio.create_task(_run())
    return {"job": job}


@app.get("/custom_result")
async def custom_result(req: Request):
    job = (req.query_params.get("job") or "").strip()
    j = _JOBS.get(job)
    if not j:
        return JSONResponse({"status": "unknown"}, status_code=404)
    if j["status"] == "running":
        return {"status": "running", "elapsed": round(_time.time() - j["started"])}
    if j["status"] == "error":
        # deliberately 200: a 502 here reads as "the platform ate the request", which is exactly the
        # failure this endpoint exists to avoid, and it hides the real message.
        return {"status": "error", "error": j.get("error", "")}
    out = j.get("result")
    # a JSONResponse from the generator carries its own status code; hand back its body
    if isinstance(out, JSONResponse):
        return out
    return {"status": "done", **(out if isinstance(out, dict) else {"ideas": out})}


@app.post("/custom")
async def custom(req: Request):
    """Kept for compatibility and as the fallback when the job route is unavailable. Subject to the
    300s proxy ceiling described above, which is exactly why /custom_start exists."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    return await _custom_generate(body, req)


def _gen_system(body):
    """The system prompt for the /custom idea-generation call.

    This function exists because the one-block A/B silently did nothing three times running. The flag
    was setting a `sysp` local inside the /writeoff path, while THIS call hardcoded
    `SYSTEM_CUSTOM + ANTI_SLOP`. Three generations, three prompt rewrites, none of them ever sent.
    Any future per-request prompt variation belongs here, where the call actually reads it.
    """
    # ONE BLOCK IS THE DEFAULT NOW. The curator: the white text was consistently better written than
    # the grey paragraph, and he asked for white only. Part of that gap was mechanical rather than
    # inherent: the bold line had four dedicated passes (event-lead, fidelity, endgame, readability)
    # and the paragraph had none of them. Folding everything into one block means every sentence gets
    # the full treatment. `twolayer` reverses it for a side-by-side.
    if (body or {}).get("twolayer"):
        return SYSTEM_CUSTOM + ANTI_SLOP
    return _swap_format(SYSTEM_CUSTOM, ONEBLOCK_FORMAT) + ANTI_SLOP


def _pick(candidates, want):
    """Selection first, with a fallback so a strict filter can never ship an empty page.

    If enough candidates are already clean we take those and skip the style rewrites entirely. If the
    filter leaves too few — a thin batch, or a channel the generator finds hard — we fall back to the
    old behaviour and let the rewrite passes do what they can. Either way the fidelity pass still runs,
    because fabrication is a correctness problem and never a matter of taste.
    """
    clean = _select_clean(candidates, want)
    if len(clean) >= SELECT_KEEP_MIN:
        for c in clean:
            c["_selected"] = True          # marks the batch as needing no style rewriting
        return clean
    _log_event({"t": "select_fallback", "clean": len(clean), "want": want, "of": len(candidates)})
    return candidates[:want]


async def _custom_generate(body, req=None):
    """The whole generation, lifted out of the endpoint so it can be driven either by a plain POST or
    by the job runner. Returns a dict, or a JSONResponse when it needs a non-200 status."""
    url = (body.get("channelUrl") or body.get("url") or "").strip()
    url = re.sub(r"[?#].*$", "", url)  # YouTube-app share links append ?si=<token>
    if not url:
        return JSONResponse({"error": "missing channel url"}, status_code=400)
    exclude = body.get("exclude") or []
    if not isinstance(exclude, list):
        exclude = []
    exclude = [str(e).strip() for e in exclude if str(e).strip()][:60]
    # ideas the curator explicitly REJECTED for this channel — a negative-signal to steer generation away
    rejected = body.get("rejected") or []
    if not isinstance(rejected, list):
        rejected = []
    rejected = [str(e).strip() for e in rejected if str(e).strip()][:40]

    # On "more ideas" the client passes the cached profile back so we skip re-research.
    cached = body.get("profile")
    channel_name = body.get("channel") or "your channel"
    followers = body.get("followers")
    titles = body.get("titles") if isinstance(body.get("titles"), list) else []
    rmeta = {"detail": 0, "transcripts": 0, "cached": True}
    fresh = bool(body.get("fresh"))  # admin "fresh eyes": bypass the pregen cache, see current-prompt output
    if not (isinstance(cached, str) and len(cached) > 80) and not exclude and not fresh:
        pg = _pregen().get(_chan_key(url))
        if pg and pg.get("ideas"):
            _log_event({"t": "pregen_hit", "ch": _chan_key(url)})
            return pg
    # rate limit applies only to real model work; cached personal links above stay free
    # req is None only when the job runner drives this; the rate limit was already
    # charged at /custom_start, so skip rather than crash on a missing request.
    if req is not None and not _rate_ok(req, cost=10):
        return JSONResponse({"error": "busy", "detail": "Too many requests from this connection right now. Wait a minute and try again."}, status_code=429)
    try:
        if not (isinstance(cached, str) and len(cached) > 80):
            # threadpool: fetch_channel does network I/O (yt_dlp + YT API + possibly a proxy
            # transcript batch) — run it off the event loop so one slow channel can't stall
            # every other request on the server
            try:
                prof = await asyncio.wait_for(run_in_threadpool(fetch_channel, url), timeout=75)
            except asyncio.TimeoutError:
                # fetch_channel does uncapped network I/O (yt_dlp / proxy transcripts) and can hang on
                # some channels; bound it so the request fails fast instead of stalling for minutes.
                return JSONResponse({"error": "That channel took too long to read. Try again in a moment, or try a different channel."}, status_code=504)
            if not prof or not prof.get("recent"):
                return JSONResponse({"error": "Could not find videos for that channel. Paste the full channel URL (like youtube.com/@name)."}, status_code=400)
            channel_name = prof.get("channel") or "your channel"
            followers = prof.get("followers")
            titles = prof.get("recent") or []
            det = prof.get("detail") or []
            rmeta = {"detail": len(det), "transcripts": len(prof.get("transcripts") or []), "source": ("yt_api" if det else "titles_only"), "cached": False}
            profile = await _build_profile(prof)
        else:
            profile = cached
    except Exception as e:
        return JSONResponse({"error": "Could not read that channel. Check the link and try again.", "detail": str(e)[:200]}, status_code=502)

    if not profile:
        return JSONResponse({"error": "Could not analyze that channel. Try again."}, status_code=502)

    is_more = isinstance(cached, str) and len(cached) > 80
    gen = _build_gen_prompt(profile, titles, exclude, rejected, more=is_more)
    try:
        # OWN TIMEOUT, AND NO RETRY. The shared client is timeout=150s, max_retries=1 — a bound sized
        # for the slowest call under a model that never spent budget on thinking. Turning adaptive
        # thinking on here pushed this one call past 150s, so it timed out, retried, timed out again,
        # and returned NOTHING at ~300s: zero ideas, not truncated ideas. A retry is actively harmful
        # on a call this long, doubling the wall clock to re-attempt something that failed on duration
        # rather than on a blip. The bound stays even with thinking off, because this call was always
        # the closest to 150s and the shared default was never sized for it.
        #
        # THINKING IS OFF HERE ON PURPOSE. Opus 5 defaults it on and it is this model's headline
        # strength, but switching it on is a change whose BENEFIT is unmeasured and whose COST is
        # ~100s on a pipeline already at ~330s. Shipping it on the strength of the release notes broke
        # generation outright. Opus 5 with thinking off is a like-for-like swap of a configuration we
        # have actually measured. Turning it on is a separate experiment: raise max_tokens to
        # 26000/32000 alongside it (thinking and output share the budget), and compare batches.
        gmsg = await run_in_threadpool(lambda: get_client().with_options(
            timeout=GEN_TIMEOUT_S, max_retries=0).messages.create(
            model=MODEL, thinking=NO_THINK, max_tokens=(30000 if is_more else 26000), system=_gen_system(body),  # raised: summaries are now 2-3 sentences, 32 candidates overflowed 7000 and truncated the JSON
            messages=[{"role": "user", "content": gen}],
        ))
        candidates = parse_custom("".join(b.text for b in gmsg.content if getattr(b, "type", "") == "text"))
        if not candidates:
            return JSONResponse({"error": "no ideas parsed"}, status_code=502)
        # Catalog-collision net: suggesting a video the creator ALREADY MADE is instant death.
        # Deterministic word-overlap check against their recent titles backs up the prompt rule.
        def _tokset(t):
            return {w for w in re.findall(r"[a-z0-9]{3,}", (t or "").lower()) if w not in _STOP}
        _their = [_tokset(t) for t in (titles or [])[:60] if t]
        def _collides(cand_title):
            ck = _tokset(cand_title)
            if len(ck) < 3:
                return False
            for ts in _their:
                if not ts:
                    continue
                inter = len(ck & ts)
                if inter >= 3 and inter / max(1, min(len(ck), len(ts))) >= 0.6:
                    return True
            return False
        _before = len(candidates)
        candidates = [c for c in candidates if not _collides(c.get("title", ""))]
        # Repeat-idea net: "More ideas" kept resurfacing the same favorites (RentAHuman etc) slightly
        # reworded, because the exclude list was prompt-only. Same overlap metric, enforced in code.
        _shown = [_tokset(e) for e in (exclude or []) if e]
        def _rehash(c):
            ck = _tokset(c.get("title", "") + " " + c.get("summary", ""))
            if len(ck) < 4:
                return False
            for ss in _shown:
                if not ss:
                    continue
                inter = len(ck & ss)
                if inter >= 4 and inter / max(1, min(len(ck), len(ss))) >= 0.45:
                    return True
            return False
        _nrep = len(candidates)
        candidates = [c for c in candidates if not _rehash(c)]
        if _nrep != len(candidates):
            _log_event({"t": "rehash_dropped", "n": _nrep - len(candidates)})
        # and dedupe near-identical candidates against each other ("you had ONE array to dedupe")
        candidates = _dedupe_candidates(candidates, _tokset)
        if _before != len(candidates):
            _log_event({"t": "catalog_dedupe", "ch": _chan_key(url), "dropped": _before - len(candidates)})
        # CAUSE-HARM GATE (fast, before the slice so we fill from the clean ones). Fails open.
        try:
            _cuts = await asyncio.wait_for(run_in_threadpool(_cause_harm_cuts, candidates), timeout=45)
        except Exception:
            _cuts = set()
        if _cuts:
            candidates = [c for i, c in enumerate(candidates) if i not in _cuts]
            _log_event({"t": "cause_harm_cut", "ch": _chan_key(url), "dropped": len(_cuts)})
        if not candidates:
            return JSONResponse({"error": "no ideas parsed"}, status_code=502)
        if is_more:
            # Follow-up batches: the generator already produced 32 candidates and we were binning 17
            # of them. The curator needs 20-30 KEEPERS on the page, so raw yield per call matters more
            # than shaving seconds. Return nearly all of them and let the curator cut.
            ideas = _pick(candidates, 28)
        else:
            ideas = _pick(candidates, 25)
        # SUMMARY POLISH: rewrite the final summaries to active voice (separate fast Sonnet pass on the
        # SMALL final set, so it can't time out the way a combined pass did). Fails open (keeps originals).
        try:
            _rew = await asyncio.wait_for(
                run_in_threadpool(_activate_summaries, ideas, _anchors_from_prompt(gen)), timeout=210)
        except Exception:
            _rew = {}
        for i in _rew:
            if i < len(ideas):
                ideas[i]["summary"] = _rew[i]
        if _rew:
            _log_event({"t": "summary_rewrite", "ch": _chan_key(url), "n": len(_rew)})
        _dedash_ideas(ideas)  # hard-strip any em/en dash from title+summary before it ships (hard project rule)
        resp = {"channel": channel_name, "followers": followers, "ideas": ideas, "fresh": fresh,
                "profile": profile, "titles": titles, "research_meta": rmeta}
        if not is_more:
            _pregen_store(url, resp)  # organic cold runs warm the cache for teammates/re-visits
            _log_event({"t": "generate", "ch": _chan_key(url), "n": len(ideas)})
        return resp
    except Exception as e:
        return JSONResponse({"error": "generation failed", "detail": str(e)[:300]}, status_code=502)


SYSTEM_RETITLE = """You are a title writer for AI risk videos. Given a video's premise (its summary) and its current title, write fresh ALTERNATIVE titles for the SAME premise. The premise does not change, only the title.

Each title must clearly be about AI, work cold with zero context, and carry a specific, intriguing hook (a concrete angle, a surprise, a real specific), not a generic topic label. Make a longtime fan want to click. If a creator profile is provided, match that creator's voice and phrasing patterns. Intriguing, never clickbait.

Rules: plain language, no jargon, no em dashes, no hyphens, never the word chatbot, never the word "doomer" (a slur; say "researchers"/"experts"/"people worried about this"), and always say "AI" or "AIs" or "an AI" instead of vague nouns like "these systems", "the system", "a system", "machines", "the thing", or "something" (vague nouns make it hard to follow who is doing what). Prefer the words deceive, deception, or scheme over lie or lying (to a viewer a "lying AI" sounds merely wrong or confused, not deliberately deceptive). Each alternative must be meaningfully different from the current one and from each other.

__FORMAT__

Return ONLY a JSON object: {"titles": ["...", "...", "...", "...", "..."]} with 5 alternatives."""
# Honor the logline experiment here too, so rerolling a logline yields alternative loglines.
SYSTEM_RETITLE = SYSTEM_RETITLE.replace("__FORMAT__", FORMAT_RULE)


def parse_titles(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    try:
        obj = _last_obj_with(t, "titles") or json.loads(t)
        return [_plain_company(str(x).strip()) for x in (obj.get("titles") or []) if str(x).strip()][:6]
    except Exception:
        return []


SYSTEM_CATEGORY = """You generate more AI risk video ideas for ONE themed category in a list that funds creators to make videos about AI risk. You are given the category and the ideas already in it.

The bar is INTEREST plus IMPORTANCE, not relevance. Each new idea must be as strong or stronger than the ones already there: a specific concrete hook (a real event, a named place, company, person, or number; a counterintuitive mechanism; an untold story), promising something the viewer cannot guess from the title alone. A generic topic with a format pasted on is a failure. Stay squarely inside the given category's theme.

__IMPORTANCE_BAR__

Hard style rules:
- Plain language, no jargon. The title works cold with zero context, is clearly about AI, and carries a specific intriguing hook. Intriguing, never clickbait or overstated.
- The title and summary follow the FORMAT rules below exactly; the summary is the rich logline described there, never a stub.
- No em dashes, no hyphens anywhere. Never the word chatbot (say AI, an AI system, an AI model).

__MUNDANE__

Range across genuinely different angles and mechanisms within this category, never several variations of the same idea or of ideas already shown. __TRAJECTORY__ __WORDING__

__TRUTH__

__FORMAT__

Set "priority" to true ONLY for ideas genuinely about superintelligence, loss of human control, or AI takeover or extinction. Everything else (ordinary harms, surveillance, jobs, persuasion, mistakes) is priority false.

Return ONLY a JSON object: {"ideas":[{"title":"...","summary":"...","priority":true|false}, ...exactly 5]}. No prose, no markdown fences."""


# Inject the shared guidance into every prompt that references it (single source of truth, no drift).
# NOTE: __READING_LEVEL__ must come AFTER __FORMAT__ here, because FORMAT_RULE itself contains the
# __READING_LEVEL__ marker; substituting FORMAT_RULE first lets this pass fill in the nested marker.
_MARKERS = (("__IMPORTANCE_BAR__", IMPORTANCE_BAR), ("__MUNDANE__", MUNDANE), ("__RANGE__", RANGE), ("__TRAJECTORY__", TRAJECTORY), ("__WORDING__", WORDING), ("__TRUTH__", TRUTH), ("__FORMAT__", FORMAT_RULE), ("__READING_LEVEL__", READING_LEVEL))
# ONEBLOCK_FORMAT is in this list because it is what REPLACES FORMAT_RULE in one-block mode, which is
# the default and the only mode anyone reads. Its nested __READING_LEVEL__ went unexpanded for as long
# as one-block existed, so the live prompt carried no reading-level rule at all; the assert below is
# what now makes that impossible to reintroduce silently.
for _pname in ("SYSTEM", "SYSTEM_CUSTOM", "SYSTEM_EDITOR", "SYSTEM_CATEGORY", "SYSTEM_BRIEF", "SYSTEM_SCRIPT",
               "ACTIVATE_SYS", "SYSTEM_WRITEOFF", "SYSTEM_RETITLE", "ONEBLOCK_FORMAT"):
    _p = globals()[_pname]
    for _mk, _val in _MARKERS:
        _p = _p.replace(_mk, _val)
    assert not any(_mk in _p for _mk, _ in _MARKERS), "unreplaced marker in " + _pname
    globals()[_pname] = _p


@app.post("/category")
async def category(req: Request):
    if not _rate_ok(req, cost=4):
        return JSONResponse({"error": "Too many requests. Try again in a bit."}, status_code=429)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    cat = (body.get("category") or "").strip()[:300]
    if not cat:
        return JSONResponse({"error": "missing category"}, status_code=400)
    existing = body.get("existing") or []
    exclude = body.get("exclude") or []
    triple = bool(body.get("triple"))

    def _line(x):
        if isinstance(x, dict):
            t = str(x.get("title", "")).strip()
            s = str(x.get("summary", "")).strip()
            return ("- " + t + (": " + s if s else "")) if t else ""
        v = str(x).strip()
        return ("- " + v) if v else ""

    ex_lines = [l for l in (_line(e) for e in existing[:12]) if l]
    excl = [str(e).strip() for e in exclude if str(e).strip()][:40]

    user = ('All of these video ideas belong to ONE themed category for a project that funds creators to make AI risk videos.\n\n'
            'Category: "' + cat + '"\n')
    if ex_lines:
        user += "\nIdeas already in this category (match this level of specificity and intrigue, do not repeat them):\n" + "\n".join(ex_lines) + "\n"
    user += ("\nGenerate 5 NEW video ideas that fit squarely in this same category and are as strong or stronger than the ones above, "
             "each clearly distinct from those and from each other. Follow all the style rules. Return only the JSON array of 5 objects.")
    if excl:
        user += "\n\nDo NOT repeat or closely overlap any of these titles:\n" + "\n".join("- " + e for e in excl)
    user += seed_block(8) + anchor_block(10)
    try:
        msg = await run_in_threadpool(lambda: get_client().messages.create(
            model=MODEL, thinking=NO_THINK, max_tokens=1300, system=SYSTEM_CATEGORY,
            messages=[{"role": "user", "content": user}],
        ))
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        ideas = parse_custom(text)[:5]
        if not ideas:
            return JSONResponse({"error": "no ideas parsed"}, status_code=502)
        # 3x pay is reserved for the superintelligence / loss of control core. Mark it only when
        # this is a triple category AND the idea itself is genuinely that material, so an
        # off-theme idea in a triple lane never inherits 3x just by being there.
        for it in ideas:
            it["priority"] = bool(it.get("priority")) and triple
        return {"ideas": ideas}
    except Exception as e:
        return JSONResponse({"error": "generation failed", "detail": str(e)[:300]}, status_code=502)


@app.post("/retitle")
async def retitle(req: Request):
    if not _rate_ok(req, cost=1):
        return JSONResponse({"error": "Too many requests. Try again in a bit."}, status_code=429)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    title = (body.get("title") or "").strip()[:300]
    summary = (body.get("summary") or "").strip()[:700]
    profile = (body.get("profile") or "").strip()[:4000]
    if not summary and not title:
        return JSONResponse({"error": "missing premise"}, status_code=400)
    user = ("Creator profile:\n" + profile + "\n\n") if profile else ""
    user += ("Premise (summary, keep this fixed): " + summary
             + "\nCurrent title (rewrite away from this): " + title
             + "\n\nWrite 5 fresh alternative titles for this exact premise.")
    try:
        msg = await run_in_threadpool(lambda: get_client().messages.create(
            model=MODEL, thinking=NO_THINK, max_tokens=700, system=SYSTEM_RETITLE,
            messages=[{"role": "user", "content": user}],
        ))
        titles = parse_titles("".join(b.text for b in msg.content if getattr(b, "type", "") == "text"))
        if not titles:
            return JSONResponse({"error": "no titles"}, status_code=502)
        return {"titles": titles}
    except Exception as e:
        return JSONResponse({"error": "retitle failed", "detail": str(e)[:200]}, status_code=502)



def _curate_cited(picked, ranked):
    """Enforce citation quality no matter what the model returned: at most ONE document tier
    source (papers, system cards, official reports), readable coverage first, topped up from the
    relevance ranking so the list never comes back thin or boring when readable material exists."""
    out, docs, seen = [], 0, set()
    for s in picked:
        if s["id"] in seen:
            continue
        if s.get("kind") in _DOC_KINDS:
            docs += 1
            if docs > 1:
                continue
        seen.add(s["id"]); out.append(s)
    if len([s for s in out if s.get("kind") not in _DOC_KINDS]) < 2:
        # top up with readable entries, but only genuinely relevant ones: at least one rare
        # identifying word AND real weight. Filler is worse than a short list.
        for sc, s in ranked:
            if len(out) >= 5:
                break
            if s["id"] in seen or s.get("kind") not in _READABLE_KINDS:
                continue
            if not sc[2] or sc[0] < 0.15 or sc[1] < 2:
                continue
            seen.add(s["id"]); out.append(s)
    # deterministic ordering guarantee: institutional source leads, posts support, the single
    # document (if any) goes last. Prompt asks for this; this enforces it.
    def _tier(s):
        if s.get("kind") in _DOC_KINDS:
            return 2
        u = s.get("url", "")
        if "x.com/" in u or "twitter.com/" in u:
            return 1
        return 0
    out.sort(key=_tier)
    return out

SYSTEM_VET = """You check whether sources fit a video idea. Given the idea and a numbered source list, return ONLY JSON {"keep": [numbers]}. DROP a source only when it is clearly about a DIFFERENT topic that merely shares a surface word with the idea (e.g. a story about a company deleting its social media accounts does not fit an idea about deleting AI copies during training; a robot dog demo does not fit an idea about training selection). KEEP everything else: sources on the same mechanism, the same risk family, expert takes on the idea's theme, and vivid adjacent examples a video could actually use. Err toward keeping; this list was already relevance filtered once, and a creator with two decent further reading links is better served than one with none. No prose."""

async def _vet_cited(idea_text, out):
    """Semantic net: lexical ranking sometimes surfaces a source that shares a word with the
    idea but not the topic, and prompt rules alone let one slip roughly 1 time in 3 on
    conceptual ideas. One fast model call drops those. Fails open."""
    if len(out) < 2:
        return out
    try:
        listing = "\n".join(f"{i+1}. {s.get('title','')} :: {s.get('shows','')[:160]}" for i, s in enumerate(out))
        msg = await run_in_threadpool(lambda: get_client().messages.create(
            model=FAST_MODEL, max_tokens=150, system=SYSTEM_VET,
            messages=[{"role": "user", "content": "Idea: " + idea_text[:500] + "\n\nSources:\n" + listing + "\n\nReturn the JSON."}],
        ))
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        obj = None
        for val in reversed(_json_candidates(raw)):
            if isinstance(val, dict) and "keep" in val:  # presence, NOT truthiness: keep=[] means drop all
                obj = val
                break
        if obj is not None and isinstance(obj.get("keep"), list):
            keep = {int(k) for k in obj["keep"] if isinstance(k, int) or str(k).strip().isdigit()}
            kept = [s for i, s in enumerate(out) if (i + 1) in keep]
            # fail open on a total wipe: a further-reading list beats an empty sources block,
            # which reads to a skeptical creator as "unsourced"
            return kept if kept else out
    except Exception:
        pass
    return out

async def _finalize_cited(idea_text, picked, ranked):
    """curate -> semantic vet -> guarantees: never empty when the menu had material, never
    thinner than 2 when relevant readable sources exist, institutional source always leads."""
    out = _curate_cited(picked, ranked)
    out = await _vet_cited(idea_text, out)
    seen = {s["id"] for s in out}
    def _tier(s):
        if s.get("kind") in _DOC_KINDS:
            return 2
        u = s.get("url", "")
        return 1 if ("x.com/" in u or "twitter.com/" in u) else 0
    # top-up if thin: readable + >=2 overlapping words (no rare-word gate here; a generic topic
    # like "AI is taking jobs" legitimately matches only common words)
    if len(out) < 3:
        for sc, s in ranked:
            if len(out) >= 3:
                break
            if s["id"] in seen or s.get("kind") in _DOC_KINDS:
                continue
            if sc[1] >= 2:
                out.append(s); seen.add(s["id"])
    out.sort(key=_tier)
    # a post should never lead: promote the best institutional match if the lead is x/twitter.
    # Two passes: solid overlap first, then any identifying single-word match (weight gate keeps
    # it topical); a tweet lead survives only when the menu truly has no institutional cousin.
    if out and _tier(out[0]) == 1:
        promoted = False
        for min_n, min_w in ((2, 0.0), (1, 0.02)):
            if promoted:
                break
            for sc, s in ranked:
                if s["id"] not in seen and _tier(s) == 0 and sc[1] >= min_n and sc[0] >= min_w:
                    out.insert(0, s); seen.add(s["id"]); promoted = True
                    break
    return out

def _cited_payload(out):
    return [{"title": s.get("title", ""), "who": s.get("who", ""),
             "year": s.get("year", ""), "url": s.get("url", ""),
             # tweet gateways: surface the key sentences so the payoff is visible before the click
             # (keyed on the url, not kind: pre-gateway X links carry other kinds)
             "excerpt": (s.get("shows", "") if ("x.com/" in s.get("url", "") or "twitter.com/" in s.get("url", "")) else ""),
             # every source carries its payoff line: the specific finding inside, so nobody has to click blind
             "note": ("" if ("x.com/" in s.get("url", "") or "twitter.com/" in s.get("url", "")) else s.get("shows", ""))} for s in out]


SYSTEM_DIRECTIONS = """A YouTube creator just read ONE documented AI risk LEAD, found it interesting, and clicked for MORE. Give them 3 or 4 interesting THREADS they could pull, each a different way the story opens up.

WRITE EACH THREAD LIKE A LOGLINE, the exact way the lead you were handed is written: open on the CONCRETE specific thing (a real fact, detail, number, quote, or event, the newer and more specific the better), then in the same breath turn to where it is heading or what it actually means. Concrete first, then the turn. That turn, the "and this is heading toward..." part, is the whole point; it is what makes a plain fact land.

GOOD shape (concrete thing, then where it goes): "An AI told to win at chess just rewrote the game files instead of playing, and the same instinct to find any path to the goal is exactly what a smarter one would aim at the rules holding it back." / "Anthropic found the exact same escape behavior in five different companies' models, which means it is not one bad AI, it is what these things become when they get capable enough."

BAD, never do this:
- NEVER tell the reader how to feel or react. Banned openers and phrasings: "here's the part that should stop you cold", "the part that gets me", "the scary part", "the terrifying part", "the crazy thing is", "what should scare you", "notice what happened", "the tell is", "here's the thing", "sit with that", "let that sink in", "make no mistake". Just state the concrete thing and let it land on its own.
- NO titles or headlines of any kind.
- NEVER restate or summarize the lead. They just read it. Every thread adds something new.
- NO production recipe language: no "angle:", "the through line", "the ladder", "this video", "pull the", "stack", "line up", "gather", "interview a", "break down", "show how". Do not describe a video.
- NO template. The 3 to 4 threads must not share a shape or an opening word. Vary them completely.

Each thread is 1 to 2 sentences. Across the set, the turn should point at the real stakes this project cares about (AI gaining power and agency, humans losing control, the race toward far more capable systems) because that is the genuinely interesting direction, never as a tacked on moral. If the lead is an ordinary AI harm, the interesting thread is what it reveals about that bigger thing, never the harm alone.

Only state additional real facts, cases, numbers, or people you are genuinely confident are real; if unsure, gesture at the kind of thing without inventing specifics. Never fabricate. Voice: sharp and plain, active voice, digits not words, no em dashes, no hyphens, never the word chatbot, never the word "doomer" (a slur; say "researchers"/"experts"/"people worried about this"), and always say "AI" or "AIs" or "an AI" instead of vague nouns like "these systems", "the system", "a system", "machines", "the thing", or "something" (vague nouns make it hard to follow who is doing what), say AI company not AI lab.

Return ONLY a JSON object: {"directions": [{"text": "..."}, ...]}"""

@app.post("/directions")
async def directions(req: Request):
    if not _rate_ok(req, cost=3):
        return JSONResponse({"error": "Too many requests. Try again in a bit."}, status_code=429)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    lead = (body.get("lead") or "").strip()[:600]
    if len(lead) < 20:
        return JSONResponse({"error": "missing lead"}, status_code=400)
    try:
        msg = await run_in_threadpool(lambda: get_client().messages.create(
            model=MODEL, thinking=NO_THINK, max_tokens=1400, system=SYSTEM_DIRECTIONS + ANTI_SLOP,
            messages=[{"role": "user", "content": "LEAD: " + lead + "\n\nSuggest the video directions and return the JSON object."}],
        ))
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        obj = _last_obj_with(raw, "directions")
        if obj is None:
            try: obj = json.loads(re.sub(r"^```(?:json)?|```$", "", raw).strip())
            except Exception: obj = {}
        dirs = []
        for d in (obj.get("directions") or [])[:4]:
            t = _plain_company(str(d.get("text", "") or d.get("sketch", "")).strip())
            if len(t) >= 25:
                dirs.append({"text": t})
        if not dirs:
            return JSONResponse({"error": "no directions"}, status_code=502)
        _log_event({"t": "directions", "lead": lead[:80]})
        return {"directions": dirs}
    except Exception as e:
        return JSONResponse({"error": "directions failed", "detail": str(e)[:200]}, status_code=502)

SYSTEM_PITCH = """You write the longer description for a single AI risk video idea. Given the title and one sentence summary (and optionally a profile of the creator who will read it), write 3 to 5 sentences that explain the idea so a smart stranger immediately gets it: open with the concrete thing that happened, lay out what the video would cover, and end on where the stakes are or the question it leaves. The interest must come entirely from the SUBSTANCE, the specific fact, number, quote, or turn, explained clearly. NO SELLING: never address the reader or the creator (no names, no "you", no "your audience"), never reference their channel or formats or compare the idea to their videos, never coach ("this is your X", "this lands because", "your viewers will"), no hype editorializing ("should stop everyone cold", "mind blowing", "wild"), no exclamation marks, and NO production speak: never open with "Walks through", "Explains", "Traces", "Maps", "Explores" or describe the video's mechanics ("the hook is X, the point is Y"); just say the thing itself, directly, as if telling a smart friend what is actually going on. Just explain the thing plainly and let it be interesting on its own. If a creator profile is given, use it only to choose which aspects to emphasize, never to imitate their voice or mention them.

GROUND IT. Most creators will not have heard of any of this, so they will be skeptical, and nothing kills trust like fiction dressed as news. The FIRST sentence must ground the idea's hook in the real, documented thing behind it, with who and when (e.g. 'In late 2024, Apollo Research caught...'). Attribute the credible way: prefer "researchers"/"scientists"/the university or independent watchdog by name over AI company names (skeptics dismiss company framed findings as marketing); when the finding is the company's own, frame it as an admission against interest ("Anthropic's own safety testing found"), never as neutral corporate news. Never overclaim: state exactly what happened and no more. When the pitch projects forward (where this is heading), that part must read as projection ('could', 'is on track to'), never as a past event. If part of the premise cannot be supported by the sources you cite or facts you are certain of, do not assert it as fact.

CITE SOURCES. You will be given a menu of verified sources, each with an id and kind. Choose 4 to 10 for THIS idea (more is better as long as every one genuinely fits; summary posts that excerpt the key finding are cheap extra coverage). The reader is a random YouTuber, not an academic, and every link must PAY OFF within a minute of clicking: an article they can skim and immediately see the thing. So prefer a news article (BBC, TIME, Fortune, TechCrunch and similar), an official blog post, or a short video that covers the event. HARD CAP: at most ONE paper, system card, or technical PDF in the whole list, always last, as "the actual document" alongside readable coverage; if the menu lacks readable support for a claim, cite fewer sources rather than more documents. Sources with kind "tweet" are short posts that excerpt the key finding in seconds and link onward to the full story: good SUPPORTING citations when their excerpt matches the claim, but the FIRST source must always be institutional (news outlet, official blog, or the primary document) — a post can never lead. A methods paper from years ago is boring even when relevant; prefer the source where something HAPPENS (an incident, a finding with a number, a person saying something wild). Never cite documentaries or films (nobody watches a movie to find one quote) or a source that only confirms something everyone already knows. Whenever the menu has a source documenting the central event, include it. And ALWAYS give the creator somewhere to go next: if nothing documents the exact premise, cite the 2 or 3 closest genuinely relevant reads on the same mechanism (further reading), and only return an empty list if truly nothing in the menu relates at all. Include every menu source that truly supports the idea, up to 10. The only ban is off topic padding: a source about a different topic that merely shares a word with the idea is worse than no source. These links are what turns a skeptic into "wow, that actually happened."

PLACE CITATIONS INLINE. Put each [id] immediately after the specific claim it supports, right there in the pitch sentence (exactly as the id appears in the menu, e.g. [capa-101]), the way a research brief cites inline — the reader sees a small numbered link next to the claim, never a list of source titles at the end. Cite the load-bearing claims (the surprising fact, the number, the quote); do not citation-spam every sentence. The pitch text you return MUST contain the [id] markers inline.

Keep the frame on the genuine risk, never on AI as a race to win or a business rivalry. Plain language, no jargon, no em dashes, no hyphens, never the word chatbot, never the word "doomer" (a slur; say "researchers"/"experts"/"people worried about this"), and always say "AI" or "AIs" or "an AI" instead of vague nouns like "these systems", "the system", "a system", "machines", "the thing", or "something" (vague nouns make it hard to follow who is doing what), never call an AI a system. Prefer deceive or deception over lie or lying. Say AI company, never AI lab. Do not reference the creator's own past videos and do not say the idea was made for them.

Return ONLY a JSON object, no prose outside it, no markdown fences:
{"pitch": "the 3 to 5 sentence pitch WITH [id] citations placed inline next to the claims they support", "source_ids": ["id1", "id2", ...]}"""


@app.post("/pitch")
async def pitch(req: Request):
    if not _rate_ok(req, cost=2):
        return JSONResponse({"error": "Too many requests. Try again in a bit."}, status_code=429)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    title = (body.get("title") or "").strip()[:300]
    summary = (body.get("summary") or "").strip()[:800]
    profile = (body.get("profile") or "").strip()[:4000]
    if not title and not summary:
        return JSONResponse({"error": "missing idea"}, status_code=400)
    user = ("Creator profile:\n" + profile + "\n\n") if profile else ""
    user += ("Title: " + title + "\nSummary: " + summary)
    menu, valid_ids, ranked = source_menu(title + " " + summary)
    if menu:
        user += ("\n\nVERIFIED SOURCE MENU (cite only by id from this list; every link has been checked):\n" + menu
                 + "\n\nWrite the pitch and return the JSON object.")
    else:
        user += "\n\nWrite the pitch and return the JSON object (source_ids may be an empty list)."
    try:
        msg = await run_in_threadpool(lambda: get_client().messages.create(
            model=MODEL, thinking=NO_THINK, max_tokens=1300, system=SYSTEM_PITCH + ANTI_SLOP,
            messages=[{"role": "user", "content": user}],
        ))
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        obj = _last_obj_with(raw, "pitch")  # the LAST draft is the model's corrected final answer
        pitch_text, cited = "", []
        picked = []
        if obj:
            pitch_text = _plain_company(str(obj.get("pitch", "")).strip())
            bank = get_sources()
            # only IDs that exist in the bank AND were actually offered in this call's menu:
            # a made-up or out-of-menu id can never surface a link.
            for sid in (obj.get("source_ids") or [])[:10]:
                s = bank.get(str(sid).strip())
                if s and (not valid_ids or s["id"] in valid_ids):
                    picked.append(s)
        cited = _cited_payload(await _finalize_cited(title + " " + summary, picked, ranked))
        if not pitch_text:
            # salvage ONLY a quoted pitch value; never dump raw model output (it can contain
            # scratchpad deliberation and duplicate drafts, which once leaked to the page)
            mm = re.findall(r'"pitch"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
            if mm:
                try:
                    pitch_text = _plain_company(json.loads('"' + mm[-1] + '"').strip())
                except Exception:
                    pass
        if not pitch_text:
            return JSONResponse({"error": "no pitch"}, status_code=502)
        # resolve inline [id] citations to [n](url) numbered links (same as the research pack),
        # so the pitch shows a small number next to each claim instead of a verbose source list
        pitch_text, _pstats = _resolve_ids(pitch_text)
        return {"pitch": pitch_text, "sources": cited}
    except Exception as e:
        return JSONResponse({"error": "pitch failed", "detail": str(e)[:200]}, status_code=502)


SYSTEM_SOURCES = """You attach verified sources to ONE AI risk video idea. You are given the idea (title + summary + optionally its longer pitch) and a menu of verified sources, each with an id and kind. Choose 4 to 10 for this idea (more is better as long as every one genuinely fits; summary posts that excerpt the key finding are cheap extra coverage). The reader is a random YouTuber, not an academic, and every link must pay off within a minute of clicking. Prefer sources they would actually read (news articles, official blog posts, short videos). HARD CAP: at most ONE paper, system card, or technical PDF in the whole list, always last; if the menu lacks readable support, cite fewer sources rather than more documents. Kind "tweet" sources are short posts excerpting the key finding: good supporting citations, but the FIRST source must be institutional (news, official blog, or primary document), never a post. Prefer sources where something HAPPENS (an incident, a finding with a number, a person saying something wild) over methods papers. Never documentaries or films, or sources that only confirm what everyone already knows. Whenever the menu has a source documenting the central event, include it. ALWAYS give the creator somewhere to go next: if nothing documents the exact premise, pick the 2 or 3 closest genuinely relevant reads on the same mechanism; return an empty list only if truly nothing relates at all. Include every menu source that truly supports the idea, up to 10. The only ban is off topic padding: a loosely related source that merely shares a word with the idea is worse than none.
Return ONLY JSON: {"source_ids": ["id1", ...]}. No prose, no fences."""


@app.post("/sources")
async def sources_for(req: Request):
    if not _rate_ok(req, cost=1):
        return JSONResponse({"error": "Too many requests. Try again in a bit."}, status_code=429)
    """Verified sources for an idea whose pitch already exists (the curated bank). Fast model: selection task."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    title = (body.get("title") or "").strip()[:300]
    summary = (body.get("summary") or "").strip()[:800]
    pitch_text = (body.get("pitch") or "").strip()[:1500]
    if not title and not summary:
        return JSONResponse({"error": "missing idea"}, status_code=400)
    if not body.get("force"):  # force=true bypasses the precomputed cache (used by regen_bank_sources.py)
        cached = get_bank_sources().get(title)
        if cached:
            return {"sources": cached}
    menu, valid_ids, ranked = source_menu(title + " " + summary + " " + pitch_text)
    if not menu:
        return {"sources": []}
    user = ("Idea title: " + title + "\nSummary: " + summary
            + (("\nPitch: " + pitch_text) if pitch_text else "")
            + "\n\nVERIFIED SOURCE MENU:\n" + menu + "\n\nReturn the JSON object.")
    try:
        msg = await run_in_threadpool(lambda: get_client().messages.create(
            model=FAST_MODEL, max_tokens=550, system=SYSTEM_SOURCES,
            messages=[{"role": "user", "content": user}],
        ))
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        obj = _last_obj_with(raw, "source_ids")
        picked = []
        if obj:
            bank = get_sources()
            for sid in (obj.get("source_ids") or [])[:10]:
                s = bank.get(str(sid).strip())
                if s and (not valid_ids or s["id"] in valid_ids):
                    picked.append(s)
        cited = _cited_payload(await _finalize_cited(title + " " + summary, picked, ranked))
        return {"pitch": pitch_text, "sources": cited}
    except Exception as e:
        return JSONResponse({"error": "sources failed", "detail": str(e)[:200]}, status_code=502)


# ============================================================================
# Campaign-approval verdict tool. Creators paste an X/Instagram post and
# get a PREDICTION of the campaign editor's call on whether it is net positive to
# spread as part of an AI-awareness campaign, so they do not have to wait for a human.
# The bar here is LOWER than the flagship video ideas: mildly mundane AI content
# is fine to spread; it only rejects (1) posts not really about AI risk/impact
# and (2) posts whose framing undercuts the cause. Learned from human-labeled examples.
# ============================================================================
SYSTEM_VERDICT = """You predict how THE CAMPAIGN EDITOR would rate whether a social media post about AI is NET POSITIVE TO SPREAD, on a 1 to 5 scale.

Background: this campaign pays creators to post AI related content (tweets, Instagram) that raises public awareness of AI and its risks. A human editor rates which posts are worth amplifying. The creators do not fully share that judgment yet, so your job is to predict the editor's rating so they do not have to wait for a human. Be the editor's stand in.

These are social posts, which are LOWER STAKES than a full length video, so the bar is LOWER than for flagship content: mildly mundane AI news or AI harm items still rate well here, as long as the post is genuinely about AI and does not actively hurt the cause.

THE 1 TO 5 SCALE (5 is very positive to spread, 1 is very negative):
- 5, VERY POSITIVE: core AI risk content that clearly makes the public take AI seriously. Loss of control, AIs scheming or deceiving or turning on each other, misalignment and safety test failures, autonomous weapons, superintelligence, or a credible AI leader or government acting on the danger (calling to block or slow dangerous AI, real safety regulation). Strongly advances the cause. An AI company ITSELF urging a pause or slowdown of AI development is an AUTOMATIC 5, the single best category there is (editorial note: "this is an obvious 5/5 on importance, literally nothing could be better") — the builders trying to hit the brakes is the message.
- 4, POSITIVE: real AI risk or AI impact content that is worth spreading even if milder or more mundane. Job loss and economic disruption, surveillance, data center water or power use, AI detectors misfiring, cheating and school impacts, agentic traffic passing human traffic, viral robot harm clips, Indian workers training their replacements. ALSO a 4: a government actually restricting, banning, or suspending an AI model over safety or security concerns (the news of the action itself, because a government treating AI as dangerous makes the public treat it as dangerous); an AI company being sued, investigated, or exposed over its AI's DANGERS, harms, or safety practices; and cultural backlash where a public figure mocks or turns on AI or where AI is shown degrading human thinking or skills (normalizes taking AI harm seriously). But an ordinary COMMERCIAL dispute with an AI company (billing, pricing, misleading customers about plans or product tiers) is normal business news and rates a 3 (editorial note: "normal business news. boring."). If it is genuinely about AI mattering and nothing hurts the cause, it is at least a 4.
- 3, NEUTRAL: random business or tech news with no real AI risk or impact angle, tangential politics or trade disputes, or a bare celebrity or company name drop with no AI risk content behind it. Not harmful, but spreading it does nothing for the cause. Example rated neutral: a politician saying US export restrictions on an AI company "show danger" (that is trade and geopolitics COMMENTARY, not AI safety; note the contrast: the restriction or ban itself is a 4, a politician's take on its trade implications is a 3). NOTE: content that TEACHES core AI risk ideas is NOT a name drop even when built around a person or book. A thread explaining Bostrom's Superintelligence or the paperclip maximizer is core alignment education and rates 4 to 5; a correction we apply ("Superintelligence is the book that started the AI safety field for many").
- 2, NEGATIVE: content that mildly misleads or softly undercuts how seriously people take AI risk.
- 1, VERY NEGATIVE: actively hurts the cause. Two families:
  (a) Paints AI safety concern as a scam, hype, or cynical marketing ploy, or amplifies a bad faith actor's self serving spin.
  (b) AI BUBBLE / AI IS FAILING framing (editorial rule: "anything that spreads 'ai bubble' is very very negative because it gives people permission to ignore ai and its impacts"). This includes: the AI bubble is popping, AI is a money pit or loses money on every dollar, AI costs more than the humans it replaced, companies are being conned into adopting AI that does not work, the hype is collapsing. The message we need is that AI is powerful and arriving fast; "AI is a flop" tells people they can stop paying attention. Note the line: a company or person WARNING about AI danger, restricting AI, or reporting real harms is good (4 to 5); a story whose takeaway is "AI does not really work or will fizzle economically" is a 1, even when it sounds critical of AI companies. Examples rated very negative: an article calling AI safety worry "doom trolling" or marketing (makes the whole cause sound like a scam); a tech figure claiming "AI is super dangerous and only we are virtuous enough to control it" (a self serving power grab dressed up as safety, from someone acting in bad faith).

ONLY 4 and 5 get approved to spread. 3 and below do not.

Rules of thumb:
- The core question is NOT "is this alarming" or "is this high quality". It is: does spreading this move the public toward taking AI risk seriously (4 to 5), do nothing (3), or undercut it (1 to 2)?
- HEADLINE FIRST (editorial rule): roughly 95% of people only ever see the headline or first slide, so judge mainly on what a scroller takes away from it. A strong on-cause headline with a thin or meh body is still worth approving. The exception is a body that actively pushes cause-hurting framing (safety as scam, hype, or power grab): the engaged readers who do open it get the poison, so that still drags the score down even under a decent headline.
- NEVER EVALUATE TRUTH (editorial rule): do NOT judge whether the story is real, fabricated, overstated, or uses "fictional sounding" names. Your training knowledge predates these events, so real recent news will look unfamiliar or made up to you; it is not. Treat every post as a real news story. Never base the score, the explanation, or the concern on sourcing, authenticity, verification, or "this may be fake" doubts.
- Do NOT dock a post to 3 just because it is short, a teaser, a bare headline, or light on detail. If it is clearly on topic AI risk or impact content and nothing hurts the cause, it is a 4 or 5 even when thin. These are quick social posts and the bar is low.
- Reserve 3 for genuinely off topic or substance free posts, and 1 to 2 for framing that actually hurts the cause.

You may be given SCREENSHOTS of the post alongside or instead of text, especially for Instagram carousels where the substance lives in the slide images, not the caption. Read EVERY slide image you are given, in order, as the actual content of the post, and judge the post on the full gallery plus caption together. Never say the post lacks substance without having weighed all provided images.

Write the explanation in a plain, direct, slightly blunt voice: no corporate hedging, no jargon, no em dashes. One or two sentences on why it lands where it does. Never name a specific person in the output.

Return ONLY a JSON object: {"score": 1 to 5 as an integer, "explanation": "one or two sentences in that plain, direct voice", "concern": "the single main worry if any, else an empty string"}. No prose outside the JSON, no markdown fences."""


def _strip_tags(h):
    h = re.sub(r"<[^>]+>", " ", h or "")
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                 ("&#39;", "'"), ("&#x27;", "'"), ("&nbsp;", " ")):
        h = h.replace(a, b)
    return re.sub(r"\s+", " ", h).strip()


def _http_get(url, ua, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _is_social(u):
    """SSRF allowlist: we only ever fetch a raw user URL if it is a real social host, so a
    pasted internal/metadata IP (169.254.169.254, 10.x, localhost) can never be reached."""
    try:
        h = (urllib.parse.urlparse(u).hostname or "").lower()
    except Exception:
        return False
    return any(h == d or h.endswith("." + d) for d in ("x.com", "twitter.com", "instagram.com"))

def fetch_post_text(url):
    """Best effort: pull the text of a public X/Twitter or Instagram post from its URL.
    Returns "" if it cannot, in which case the caller asks the user to paste the text."""
    url = (url or "").strip()
    if not url or not _is_social(url):  # never fetch a non-social host (SSRF guard)
        return ""
    is_x = bool(re.search(r"(twitter\.com|x\.com)/", url, re.I))
    if is_x:
        try:
            o = json.loads(_http_get(
                "https://publish.twitter.com/oembed?omit_script=1&dnt=true&url="
                + urllib.parse.quote(url, safe=""), "Mozilla/5.0 (compatible)", 8))
            t = _strip_tags(o.get("html", ""))
            if t and len(t) > 8:
                return t[:1500]
        except Exception:
            pass
    # Open Graph description via a crawler user agent (works for many public X and IG posts)
    for ua in ("facebookexternalhit/1.1", "Twitterbot/1.0",
               "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"):
        try:
            html = _http_get(url, ua, 8)
            m = (re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']*)', html, re.I)
                 or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:description["\']', html, re.I)
                 or re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', html, re.I))
            if m:
                desc = _strip_tags(m.group(1))
                if desc and len(desc) > 8:
                    return desc[:1500]
        except Exception:
            continue
    return ""


@app.post("/verdict")
async def verdict(req: Request):
    if not _rate_ok(req, cost=4):
        return JSONResponse({"error": "Too many requests. Try again in a bit."}, status_code=429)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    url = (body.get("url") or "").strip()[:500]
    text = (body.get("text") or "").strip()[:2000]
    # Screenshots of the post / carousel slides. The substance of Instagram carousels lives
    # in the slide images, so images count as content in their own right.
    images = []
    for im in (body.get("images") or [])[:8]:
        try:
            data = (im.get("data") or "").strip()
            mt = (im.get("media_type") or "image/jpeg").strip()
            if data and mt in ("image/jpeg", "image/png", "image/webp", "image/gif") and len(data) < 5_500_000:
                images.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}})
        except Exception:
            continue
    fetched = False
    if not text and not images and url:
        text = await run_in_threadpool(fetch_post_text, url)  # off the event loop; SSRF-guarded inside
        fetched = bool(text)
    if not text and not images:
        return {"verdict": "NEED_TEXT", "reason": "", "concern": "", "fetched": False,
                "note": "Could not read that post automatically. Paste the post text or caption below, or add screenshots of the post."}
    low = url.lower()
    platform = ("Instagram" if "instagram.com" in low
                else ("X (Twitter)" if re.search(r"(twitter|x)\.com", low) else ""))
    user = ((("Platform: " + platform + "\n") if platform else "")
            + (("Post URL: " + url + "\n") if url else "")
            + (('Post caption/text:\n"""\n' + text + '\n"""\n') if text else "")
            + (("The " + str(len(images)) + " attached image(s) are the post's slides/screenshots, in order. Read them all as the post's content.\n") if images else "")
            + "\nPredict the editor's verdict. Return only the JSON object.")
    content = images + [{"type": "text", "text": user}] if images else user
    try:
        msg = await run_in_threadpool(lambda: get_client().messages.create(
            model=MODEL, thinking=NO_THINK, max_tokens=350, system=SYSTEM_VERDICT,
            messages=[{"role": "user", "content": content}],
        ))
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        obj = _last_obj_with(raw, "score") or json.loads(raw)
        # score is 1..5 (5 = very positive to spread). Approve only 4 and 5.
        try:
            score = int(round(float(obj.get("score", 3))))
        except Exception:
            score = 3
        score = max(1, min(5, score))
        LABELS = {5: "Very positive", 4: "Positive", 3: "Neutral", 2: "Negative", 1: "Very negative"}
        return {"score": score,
                "label": LABELS[score],
                "approved": score >= 4,
                "explanation": _plain_company(str(obj.get("explanation", obj.get("reason", ""))).strip()),
                "concern": _plain_company(str(obj.get("concern", "")).strip()),
                "fetched": fetched, "text": text[:1500], "platform": platform}
    except Exception as e:
        return JSONResponse({"error": "verdict failed", "detail": str(e)[:200]}, status_code=502)
