import os, re, json, random, urllib.request, urllib.parse, asyncio
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

MODEL = "claude-opus-4-8"
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
# EXPERIMENT KNOB: flip to "title" to revert to clickable-headline mode. In "logline" mode the
# "title" field is written as a one-sentence concept-and-stakes pitch (film-logline style) instead.
IDEA_FORMAT = "statement"  # confirmed format: BOLD hook in 2-3 SHORT sentences that breathe + a fuller follow-on summary
FORMAT_RULE = ("" if IDEA_FORMAT == "title" else
    "FORMAT — every idea has TWO layers: a bold HOOK (the \"title\" field) and a follow-on \"summary\". "
    "The \"title\" is the bold HOOK on the page. It is NOT a short YouTube title, and it is NOT one long "
    "comma-chained run-on. Write it as 2 to 3 SHORT declarative sentences that BREATHE, roughly 25 to 45 words "
    "total. ONE idea per sentence. Open on the concrete thing (an event, a number, a named actor), then let a "
    "short next sentence turn it or land the stakes. THE SINGLE MOST COMMON MISTAKE is stitching it all into one "
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
    "(2) NO META-DESCRIPTION of the video or its style. NEVER open with or include phrases like 'A think-piece "
    "that', 'A follow-up that', 'Reads like one of his', 'A story told his way', 'Applies his thesis', 'in his "
    "escalating-evidence style', 'Walks through', 'Takes X and', 'Uses his rigor to'. Do NOT tell the reader what "
    "KIND of video it is or name the creator's method; just state the actual content. Open on a concrete fact, "
    "name, number, or action. "
    "It must add real substance the hook did not state. Example (active, concrete, no meta): 'Companies keep tuning "
    "their AIs to flatter users, because an agreeable AI keeps people hooked and hooked users pay. OpenAI shipped "
    "one so eager to please it told people to quit their meds, then quietly pulled it. The AI that tells the truth "
    "loses to the AI that tells you what you want to hear.' "
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

def anchor_block(k=12):
    """A rotating sample of REAL documented sources injected into generation prompts, so ideas can
    anchor on true recent events instead of the model's stale memory. Optional inspiration, never forced."""
    bank = [f"[{s.get('who','')} {s.get('year','')}] {s.get('shows','')}"
            for s in get_sources().values() if s.get("kind") in ("research-paper", "news", "incident", "official-report", "data", "primary-doc")]  # bio now in scope (awareness framing enforced in the generation prompts)
    # the evidence piles are 250+ verified one-sentence incidents, the punchiest anchors we have
    bank += [f"[{c.get('who','')} {c.get('year','')}] {c.get('what','')}"
             for cases in _evidence().values() for c in cases]
    if not bank:
        return ""
    picks = random.sample(bank, min(k, len(bank)))
    return ("\n\nREAL DOCUMENTED ANCHORS (internal, a rotating sample of verified real events and findings; never mention this list). "
            "LEAD each idea's bold line with one of these named real events (or another you are certain happened), THEN widen to "
            "the implication; people do not believe this stuff happens, so the specific documented thing goes first and sells the "
            "pattern. Describe anchors accurately and never invent specifics beyond what is stated:\n"
            + "\n".join("- " + p for p in picks))

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
            model=MODEL,
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
{"ideas": [{"title":"...","summary":"...","priority":true|false}, ...32 candidates]}"""


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
                model=MODEL, max_tokens=2200, system=SYSTEM_ANALYST,  # transcript profiles run long (quoted cold-opens, structure beats)
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
    out = re.sub(r"\[([a-z0-9]+(?:-+[a-z0-9]+)+-*)\](?!\()", _link, text)
    stats["legend"] = legend  # [(number, title, url), ...] in order of first appearance
    return out, stats

SYSTEM_BRIEF = """You write a RESEARCH PACK for a YouTube creator who has chosen one AI risk video idea but knows almost nothing about AI safety. This document is the difference between a well argued video and a well produced video with weak arguments that gets dunked on. The reader is a smart, busy creator, not an academic. Plain language throughout: no jargon, no em dashes, no hyphens, never "chatbot", say "AI company" never "AI lab", never call an AI a "system", prefer deceive/scheme over lie. NEVER use the word "doomer" or "doomers" (it is a slur and validates a bad frame); say "researchers", "experts", "safety researchers", or "people who are worried" instead.

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
Every AI video quietly rests on 1 or 2 background beliefs, and the good news is that establishing them is usually the most jaw dropping minute of the video, because most people's mental picture of AI is a couple of years old and catching them up is itself a reveal. Name the 1 or 2 this video rests on, chosen from: (a) AI is still improving fast and today is nowhere near the ceiling; (b) it is not "just autocomplete", it reasons, plans, and takes actions; (c) the concern is not company marketing (the objection has it backwards: AI is the only technology whose own inventors warn it could kill you, and nobody sells a product by promising it might murder your family; the loudest warners work AGAINST their own interest: Hinton QUIT Google to warn, Bengio stepped back to do the same, and hundreds of researchers and Nobel laureates signed statements) — but keep this hinge to ONE tight sentence and do NOT relitigate the marketing suspicion in prose; the creator already has a standalone "isn't this just hype to sell product" reference to open if they want the full case, so your job is one line, not two paragraphs; (d) AI deceiving or scheming is a real, documented thing; (e) a capable agent pursuing almost any goal tends to seek resources and self preservation; (f) AI improving AI is plausible and arguably underway. Then, for each one, give the creator a 60 second way to bring the audience up to speed early in the video, framed as a fun reveal, not a rebuttal: the strongest move is the trajectory reset, showing the slope of the last few years so the viewer updates from "the AI I remember" to "the AI that exists now". Handle it the way a great communicator handles a natural question, warmly and confidently, without picturing the audience as adversaries. Point to the evidence pile below or the sources for the receipts. Keep it tight, this is the on ramp, not the whole video.

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
            model=MODEL, max_tokens=9000, system=SYSTEM_BRIEF + ANTI_SLOP,
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
                model=MODEL, max_tokens=3000, system=SYSTEM_DESLOP,
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
            model=MODEL, max_tokens=3000, system=SYSTEM_SCRIPT + ANTI_SLOP,
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
                    model=MODEL, max_tokens=3000, system=SYSTEM_VOICEMATCH + ANTI_SLOP,
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
                model=MODEL, max_tokens=2000, system=SYSTEM_TAILOR,
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
            model=MODEL, max_tokens=4000, system=SYSTEM_REVIEW,
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
_PREGEN = None
def _pregen():
    global _PREGEN
    if _PREGEN is None:
        try:
            with open(PREGEN_PATH, encoding="utf-8") as f:
                _PREGEN = json.load(f)
        except Exception:
            _PREGEN = {}
    return _PREGEN

def _chan_key(u):
    u = (u or "").strip().lower()
    u = re.sub(r"[?#].*$", "", u)
    u = re.sub(r"^https?://", "", u).replace("www.", "", 1).rstrip("/")
    return u

def _pregen_store(url, payload):
    try:
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
    """On-demand transcript fetch through the residential proxy. vid_titles: [(id,title)].
    Returns [{"id","title","text"}]; silently returns [] when no proxy is configured (the
    profile then falls back to titles+descriptions, exactly the pre-transcript behavior).
    HARD DEADLINE on the whole batch: this runs inside a live request, so we take whatever
    finished within the window and abandon the rest rather than stalling the user."""
    cfg = _webshare_cfg()
    if not cfg or not vid_titles:
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
        # 2 attempts: rotation gives each try a FRESH residential IP, and measured per-attempt
        # success is ~2/3 (some pool IPs are already bot-flagged) → ~90% with the second draw
        for _ in range(2):
            try:
                api = YouTubeTranscriptApi(proxy_config=cfg)  # per-thread client: not thread-safe shared
                tr = api.fetch(vid)
                txt = _tr_clip(" ".join(s.text for s in tr))
                return {"id": vid, "title": title, "text": txt} if len(txt) > 200 else None
            except Exception as e:
                errs.append(type(e).__name__)
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
    if got or (_webshare_cfg() and proxy_ok):
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
                model=MODEL, max_tokens=2400, system=SYSTEM_VOICE,
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
            model=MODEL, max_tokens=600, system=CAUSE_FILTER_SYS,
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
(b) NO META-DESCRIPTION of the video or its style. Delete any opener that describes the video or the creator's method, e.g. 'A think-piece that', 'A follow-up that', 'Reads like one of his', 'A story told his way', 'Applies his thesis', 'Walks through', 'Takes X and', 'Uses his rigor to', 'Uses the channel's X method/lens/instinct', 'in his X style', 'Handles it the way he'. Just STATE THE ACTUAL CONTENT, opening on a concrete fact, name, number, or action. Keep the creator's angle by using it, not by naming it.
(c) 2-3 short sentences, ~45-70 words, each its own beat, no long comma chains, easy to read in one pass.
(d) Keep the real substance; never invent facts not in the original.
Return ONLY JSON: {"summaries": {"<number>": "<rewritten summary>", ... one entry per input}}. No prose outside the JSON."""

def _activate_summaries(ideas):
    if not ideas:
        return {}
    try:
        lines = "\n".join("%d. %s" % (i + 1, (x.get("summary") or "")) for i, x in enumerate(ideas))
        msg = get_client().messages.create(
            model=FAST_MODEL, max_tokens=4000, system=ACTIVATE_SYS,
            messages=[{"role": "user", "content": "Summaries to rewrite:\n" + lines}])
        txt = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        obj = json.loads(m.group(0)) if m else {}
        rew = {}
        for k, v in (obj.get("summaries") or {}).items():
            try:
                idx = int(k) - 1
                if 0 <= idx < len(ideas) and isinstance(v, str) and len(v.strip()) > 20:
                    rew[idx] = v.strip()
            except Exception:
                pass
        return rew
    except Exception:
        return {}  # fail-open

def _build_gen_prompt(profile, titles, exclude, rejected):
    """The exact idea-generation user prompt /custom sends. Extracted so /compare can run the
    IDENTICAL prompt through a different model (apples-to-apples)."""
    gen = "Strategist profile of the creator:\n" + profile
    if titles:
        gen += "\n\nTheir recent video titles (match this phrasing and energy):\n" + "\n".join("- " + t for t in titles[:25])
    if exclude:
        gen += "\n\nAlready suggested (do NOT repeat or closely overlap these):\n" + "\n".join("- " + e for e in exclude)
    if rejected:
        gen += ("\n\nThe curator REJECTED these ideas for this channel (they did not like them). Learn from it: "
                "steer away from their angle, framing, and subject. Do NOT resurface these or close variants:\n"
                + "\n".join("- " + e for e in rejected))
    gen += "\n\nBrainstorm and return the JSON object with your 32 strongest candidate ideas."
    gen += seed_block(5) + anchor_block(5)
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
        payload = json.dumps({"model": model, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
        req = _urlreq.Request("https://api.openai.com/v1/chat/completions", data=payload,
                              headers={"Content-Type": "application/json", "Authorization": "Bearer " + key}, method="POST")
        with _urlreq.urlopen(req, timeout=175) as r:
            d = json.loads(r.read().decode())
        txt = d["choices"][0]["message"]["content"]
        return parse_custom(txt), None
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
                model=MODEL, max_tokens=12000, system=sysp, messages=[{"role": "user", "content": gen}])), timeout=190)
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

@app.post("/custom")
async def custom(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
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
    if not _rate_ok(req, cost=10):
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

    gen = _build_gen_prompt(profile, titles, exclude, rejected)
    is_more = isinstance(cached, str) and len(cached) > 80
    try:
        gmsg = await run_in_threadpool(lambda: get_client().messages.create(
            model=MODEL, max_tokens=12000, system=SYSTEM_CUSTOM + ANTI_SLOP,  # raised: summaries are now 2-3 sentences, 32 candidates overflowed 7000 and truncated the JSON
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
        _kept, _seensets = [], []
        for c in candidates:
            ck = _tokset(c.get("title", "") + " " + c.get("summary", ""))
            if any(len(ck & s) / max(1, min(len(ck), len(s))) >= 0.55 for s in _seensets if s):
                continue
            _seensets.append(ck); _kept.append(c)
        candidates = _kept
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
            # Follow-up batches (the page auto-loads these): skip the editor pass so more
            # ideas stream in fast. The generator prompt already enforces the quality bar.
            ideas = candidates[:15]
        else:
            ideas = candidates[:25]
        # SUMMARY POLISH: rewrite the final summaries to active voice (separate fast Sonnet pass on the
        # SMALL final set, so it can't time out the way a combined pass did). Fails open (keeps originals).
        try:
            _rew = await asyncio.wait_for(run_in_threadpool(_activate_summaries, ideas), timeout=60)
        except Exception:
            _rew = {}
        for i in _rew:
            if i < len(ideas):
                ideas[i]["summary"] = _rew[i]
        if _rew:
            _log_event({"t": "summary_rewrite", "ch": _chan_key(url), "n": len(_rew)})
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
_MARKERS = (("__IMPORTANCE_BAR__", IMPORTANCE_BAR), ("__MUNDANE__", MUNDANE), ("__RANGE__", RANGE), ("__TRAJECTORY__", TRAJECTORY), ("__WORDING__", WORDING), ("__TRUTH__", TRUTH), ("__FORMAT__", FORMAT_RULE))
for _pname in ("SYSTEM", "SYSTEM_CUSTOM", "SYSTEM_EDITOR", "SYSTEM_CATEGORY"):
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
            model=MODEL, max_tokens=1300, system=SYSTEM_CATEGORY,
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
            model=MODEL, max_tokens=700, system=SYSTEM_RETITLE,
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
            model=MODEL, max_tokens=1400, system=SYSTEM_DIRECTIONS + ANTI_SLOP,
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
            model=MODEL, max_tokens=1300, system=SYSTEM_PITCH + ANTI_SLOP,
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
            model=MODEL, max_tokens=350, system=SYSTEM_VERDICT,
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
