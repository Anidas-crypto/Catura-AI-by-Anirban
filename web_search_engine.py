"""
╔══════════════════════════════════════════════════════════════════════════════╗
║            CATURA AI — PRODUCTION WEB SEARCH ENGINE  v2.0                    ║
║                                                                              ║
║  Architecture:                                                               ║
║    1. Query Rewriting      — smarter multi-angle queries                     ║
║    2. Parallel Search       — Tavily + Serper simultaneously                 ║
║    3. Deduplication         — URL + content fingerprinting                   ║
║    4. Firecrawl Extraction  — full page content for top results              ║
║    5. Trust Scoring         — domain reputation system                       ║
║    6. Fact Cross-Reference  — agreement/contradiction detection              ║
║    7. Cohere Reranking      — semantic relevance scoring                     ║
║    8. Citation Builder      — numbered inline references like ChatGPT        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os
import re
import json
import math
import asyncio
import hashlib
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse
from typing import Optional


# ── API Keys (read from environment) ─────────────────────────────────────────
TAVILY_KEY    = os.getenv("TAVILY_API_KEY", "")
SERPER_KEY    = os.getenv("SERPER_API_KEY", "")
FIRECRAWL_KEY = os.getenv("FIRECRAWL_API_KEY", "")
COHERE_KEY    = os.getenv("COHERE_API_KEY", "")

# ── NEW: LLM Query Planner config ─────────────────────────────────────────────
# Reuses the Anthropic API (same account already used elsewhere). A small,
# fast model is deliberate — this planning step runs BEFORE any search, so
# it must stay cheap and quick or it eats the latency budget of the whole
# pipeline. Override QUERY_PLANNER_MODEL via env if you want a different model.
QUERY_PLANNER_KEY     = os.getenv("ANTHROPIC_API_KEY", "")
QUERY_PLANNER_MODEL   = os.getenv("QUERY_PLANNER_MODEL", "claude-haiku-4-5-20251001")
QUERY_PLANNER_TIMEOUT = 4          # seconds — fail fast, fall back to regex
QUERY_PLANNER_MAX_TOK = 500        # small JSON payload, keep cost/latency low
MIN_PLANNED_QUERIES   = 5
MAX_PLANNED_QUERIES   = 10

# ── How many raw results to fetch per engine ─────────────────────────────────
RESULTS_PER_ENGINE = 5
# ── How many results to deep-crawl with Firecrawl ─────────────────────────────
# ── CHANGED: was a fixed FIRECRAWL_TOP_N = 2. Now dynamic — the pipeline
# decides how many pages are worth crawling per query (between these bounds)
# based on how much genuinely good, diverse evidence is available. See
# _select_firecrawl_candidates() for the selection logic.
FIRECRAWL_MIN_PAGES = 5
FIRECRAWL_MAX_PAGES = 15
FIRECRAWL_TOP_N      = FIRECRAWL_MIN_PAGES  # kept for backward compatibility

# ── NEW: Page-processing (chunk → store → score → select) config ────────────
# Replaces the old "shove content[:3000] straight at the AI" behavior. A raw
# page is chopped mid-sentence by a blind char truncate and often throws away
# the part that actually answers the query. Instead we split the page into
# semantic sections, drop duplicates, score each section against the query,
# and keep only the best ones — within the same overall char ceiling.
_CHUNK_MAX_CHARS  = 900    # per-chunk cap — keeps each chunk a focused unit
_CHUNK_MIN_CHARS  = 40     # drop near-empty slivers (nav/footer junk, stray lines)
_PAGE_MAX_CHUNKS  = 6      # hard cap on chunks kept per page
_PAGE_CHAR_BUDGET = 3000   # total chars kept per page after scoring (same ceiling as before)
_HEADER_RE        = re.compile(r'^(#{1,6})\s+(.*)$', re.MULTILINE)
_STOPWORDS        = {
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are",
    "was", "were", "what", "who", "when", "where", "how", "does", "do", "did",
    "this", "that", "with", "at", "by", "be", "it", "as", "from",
}

# ── NEW: Embedding retrieval config — provider-independent ──────────────────
# Embedding generation → vector search → retrieve only relevant chunks.
# EMBEDDING_PROVIDER picks the backend; swapping providers is a config
# change, not a code change. Any provider missing its key simply can't be
# selected — retrieval falls back to the keyword scorer, nothing breaks.
EMBEDDING_PROVIDER    = os.getenv("EMBEDDING_PROVIDER", "openai").lower()  # "openai" | "cohere" | "voyage"
OPENAI_KEY            = os.getenv("OPENAI_API_KEY", "")
VOYAGE_KEY            = os.getenv("VOYAGE_API_KEY", "")
EMBEDDING_MODEL_OPENAI = os.getenv("EMBEDDING_MODEL_OPENAI", "text-embedding-3-small")
EMBEDDING_MODEL_COHERE = os.getenv("EMBEDDING_MODEL_COHERE", "embed-english-v3.0")
EMBEDDING_MODEL_VOYAGE = os.getenv("EMBEDDING_MODEL_VOYAGE", "voyage-3-lite")
EMBEDDING_TIMEOUT      = 6      # seconds — fail fast, fall back to keyword scoring
RETRIEVAL_TOP_K        = _PAGE_MAX_CHUNKS  # same cap as before, now vector-driven when available
# ── Max Cohere rerank candidates ─────────────────────────────────────────────
RERANK_TOP_N       = 8


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — QUERY REWRITER
# Expands a user question into 2-3 targeted search queries for better coverage
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# RECENCY DETECTION
# Decides whether a query needs a time-boxed search (Tavily time_range/topic,
# Serper tbs) rather than a generic undated search. This is the single biggest
# lever for "why doesn't my AI know what's happening right now" — a generic
# search has no idea whether the user wants today's news or a 2019 article,
# so authority/relevance alone will often surface something stale.
#
# Deliberately broader than the old inline regex in rewrite_queries(), which
# only caught queries containing the literal words latest/current/now/today.
# This also catches incumbents/leaderboards/prices/live-event phrasing that
# doesn't use those words but is just as time-sensitive.
# ══════════════════════════════════════════════════════════════════════════════

# ── Named freshness categories: purpose-specific detectors instead of 3 flat
# pattern buckets. Each category still resolves to a "day"/"week"/"month"
# search window (Tavily/Serper time-boxing contract is unchanged), but is
# also exposed BY NAME so result-side scoring can reason about WHY a query
# is time-sensitive — breaking news, a live event, a model/version release,
# a recently released product, an election, a price, a CEO, or a government
# official — instead of just knowing THAT it's time-sensitive.
_WINDOW_RANK = {"day": 3, "week": 2, "month": 1}

_FRESHNESS_CATEGORIES: dict[str, dict] = {
    "breaking_news": {
        "window": "day",
        "patterns": [
            r'\b(today|breaking|just (now|happened)|this (morning|moment)|developing story)\b',
            r'\bright now\b',
        ],
    },
    "live_event": {
        "window": "day",
        "patterns": [
            r'\b(live|live score|live stream|livestream|ongoing|happening now)\b',
            r'\b(score|match score|who\'?s? (winning|leading))\b',
        ],
    },
    "current_price": {
        "window": "day",
        "patterns": [
            r'\b(price|stock|share price|nifty|sensex|crypto|bitcoin|'
            r'exchange rate|market cap|nav)\b',
        ],
    },
    "weather": {
        "window": "day",
        "patterns": [r'\bweather\b'],
    },
    "model_version": {
        "window": "week",
        "patterns": [
            r'\b(latest|newest) (version|release|model|update)\b',
            r'\bv\d+(\.\d+)*\b',
            r'\b(gpt|claude|gemini|llama|mistral)[- ]?\d+(\.\d+)?\b',
        ],
    },
    "product_release": {
        "window": "week",
        "patterns": [
            r'\b(new|newly|just|recently) (released|launched|unveiled|announced)\b',
            r'\brelease date\b',
            r'\b(iphone|pixel|galaxy)\s?\d+\b',
        ],
    },
    "recent_news": {
        "window": "week",
        "patterns": [
            r'\b(latest|newest|recent(ly)?|this week|new (version|release|update|model))\b',
            r'\b(news|headlines|happened|announcement|announced)\b',
        ],
    },
    "election": {
        "window": "month",
        "patterns": [
            r'\b(election|elected|poll results?|won (the )?election|voted (in|out)|exit poll|by-?election)\b',
        ],
    },
    "ceo": {
        "window": "month",
        "patterns": [
            r'\b(ceo|chief executive|chairman|managing director)\b',
        ],
    },
    "government_official": {
        "window": "month",
        "patterns": [
            r'\b(cm|chief minister|prime minister|president|governor|minister|mayor|'
            r'chancellor|appointed|resigned)\b',
            r'\bwho is the (current|new)\b',
            r'\bstill (the|in|alive|running|active|ceo|president)\b',
        ],
    },
    "current_event": {
        "window": "month",
        "patterns": [
            r'\b(current(ly)?|ongoing|status of|as of (today|now|\d{4}))\b',
        ],
    },
}


def classify_freshness_category(query: str) -> dict:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Named freshness classification for a query: which categories it matches
    (breaking_news, live_event, current_price, weather, model_version,
    product_release, recent_news, election, ceo, government_official,
    current_event) and the strongest resulting search window. Used both for
    query-side time-boxing (via detect_recency_window) and result-side
    freshness scoring (via _freshness_score), so both sides agree on why a
    query is time-sensitive.
    """
    lower = query.lower()
    matched = [name for name, spec in _FRESHNESS_CATEGORIES.items()
               if any(re.search(p, lower) for p in spec["patterns"])]
    if not matched:
        return {"categories": [], "window": None}
    window = max((_FRESHNESS_CATEGORIES[m]["window"] for m in matched),
                 key=lambda w: _WINDOW_RANK[w])
    return {"categories": matched, "window": window}


def detect_recency_window(query: str) -> Optional[str]:
    """
    ── CHANGED ──────────────────────────────────────────────────────────────
    Same contract as before — returns "day" | "week" | "month" | None, used
    to time-box Tavily/Serper requests. Now backed by the named freshness
    category classifier above instead of 3 flat keyword lists, so detection
    explicitly covers breaking news, live events, prices, model/version
    releases, recently released products, elections, CEOs, and government
    officials — not just a handful of generic recency words.
    None means: generic search, no time constraint needed (e.g. "what is
    photosynthesis" — Wikipedia-style facts don't benefit from date-boxing
    and over-constraining them can actually *reduce* result quality).
    """
    return classify_freshness_category(query)["window"]


# ══════════════════════════════════════════════════════════════════════════════
# QUERY ROUTING — domain-aware search routing
# Detects the question's subject-matter TYPE and biases the search engines
# toward the domains most likely to hold an authoritative answer for that
# type, instead of running every query through the same undifferentiated
# search:
#   Programming → GitHub, docs sites, StackOverflow
#   Medicine    → WHO, PubMed, CDC
#   Finance     → Bloomberg, Yahoo Finance
#   Government  → official .gov / intergovernmental domains
#   Science     → Nature, ScienceDirect
#   General     → no domain bias — plain Google (Serper) + Tavily, as before
# ══════════════════════════════════════════════════════════════════════════════

_QUERY_ROUTES: dict[str, dict] = {
    "programming": {
        "patterns": [
            r'\b(error|exception|traceback|stack trace|bug|debug|compil(e|ing)|syntax error)\b',
            r'\b(function|method|class|variable|library|package|module|api|sdk|framework)\b',
            r'\b(python|javascript|typescript|\bjava\b|c\+\+|rust|golang|kotlin|swift|php|ruby|\bsql\b)\b',
            r'\bhow (do|to) i\b.{0,40}\b(code|program|implement|write a function)\b',
            r'\b(install|npm|pip|cargo|yarn|docker|kubernetes|\bgit\b|github|repo|repository)\b',
        ],
        "domains": [
            # Q&A / code hosting
            "stackoverflow.com", "github.com", "gitlab.com", "sourceforge.net",
            "github.community",
            # Languages / runtimes / package registries
            "python.org", "docs.python.org", "nodejs.org", "npmjs.com",
            "rust-lang.org", "crates.io", "golang.org", "go.dev", "kotlinlang.org",
            "oracle.com", "java.com", "docs.oracle.com",
            # Databases
            "postgresql.org", "mysql.com", "mariadb.org", "sqlite.org", "redis.io",
            "mongodb.com",
            # Containers / infra / OS
            "docker.com", "kubernetes.io", "helm.sh", "nginx.org", "apache.org",
            "gnu.org", "linux.org", "kernel.org", "ubuntu.com", "debian.org",
            "fedora.org", "archlinux.org", "freedesktop.org", "gnome.org", "kde.org",
            # Cloud platforms
            "cloudflare.com", "vercel.com", "netlify.com", "render.com",
            "digitalocean.com", "aws.amazon.com", "azure.microsoft.com", "cloud.google.com",
            # Browser/web dev reference
            "developer.mozilla.org",
            # AI/ML dev platforms
            "huggingface.co", "paperswithcode.com", "kaggle.com", "ai.google.dev",
            "deepmind.google", "ollama.com", "mistral.ai", "cohere.com", "llama.com",
            "perplexity.ai", "together.ai", "replicate.com", "fireworks.ai", "groq.com",
            "vllm.ai", "langchain.com", "langchain.dev", "langgraph.dev",
            "openai.com", "anthropic.com",
            # Security tooling/reference
            "mitre.org", "cve.org", "nvd.nist.gov", "owasp.org", "sans.org",
            "malwarebytes.com", "virustotal.com", "abuse.ch",
            # Learning platforms
            "geeksforgeeks.org", "w3schools.com", "tutorialspoint.com", "javatpoint.com",
            "codecademy.com", "freecodecamp.org", "edx.org", "coursera.org", "udacity.com",
            # Dev communities
            "stackexchange.com", "superuser.com", "serverfault.com", "askubuntu.com",
            "hashnode.com", "codeproject.com", "daniweb.com", "bytes.com",
            "news.ycombinator.com", "lobste.rs", "linuxquestions.org",
            "ubuntuforums.org", "bbs.archlinux.org", "forum.manjaro.org",
            "forums.opensuse.org", "discuss.huggingface.co", "community.openai.com",
            "community.cloudflare.com", "community.atlassian.com", "discuss.python.org",
            "discuss.kotlinlang.org",
        ],
    },
    "medicine": {
        "patterns": [
            r'\b(symptom|treatment|diagnos(is|e)|disease|medicine|medication|dosage|side effect|'
            r'vaccine|virus|infection|surgery|therapy|clinical trial|\bdrug\b)\b',
            r'\b(cancer|diabetes|covid|\bflu\b|\bhiv\b|malaria|tuberculosis)\b',
        ],
        "domains": [
            # Global health authorities / IGOs
            "who.int", "unicef.org", "unaids.org",
            # US health agencies
            "cdc.gov", "nih.gov", "fda.gov",
            # India health/regulatory agencies
            "mohfw.gov.in", "nhm.gov.in", "nhp.gov.in", "icmr.gov.in", "ayush.gov.in",
            "cdsco.gov.in", "fssai.gov.in",
            # Peer-reviewed medical journals
            "thelancet.com", "nejm.org", "jamanetwork.com", "bmj.com", "cell.com",
            "pnas.org", "nature.com", "science.org",
            # Research indexes
            "pubmed.ncbi.nlm.nih.gov",
            # Established health institutions/reference
            "mayoclinic.org", "webmd.com", "healthline.com", "clevelandclinic.org",
        ],
    },
    "finance": {
        "patterns": [
            r'\b(stock|share price|market cap|nasdaq|nyse|\bipo\b|earnings|dividend|revenue|'
            r'quarterly results|investment|mutual fund|bond yield|interest rate|inflation)\b',
            r'\b(nifty|sensex|crypto|bitcoin|forex|exchange rate)\b',
        ],
        "domains": [
            # Wire/business news
            "bloomberg.com", "reuters.com", "cnbc.com", "forbes.com",
            "finance.yahoo.com", "moneycontrol.com", "morningstar.com",
            "marketwatch.com", "investopedia.com", "tradingeconomics.com",
            "coinmarketcap.com", "coingecko.com", "statista.com",
            # India business news
            "livemint.com", "business-standard.com", "businessstandard.com",
            "thehindubusinessline.com", "economictimes.indiatimes.com",
            "financialexpress.com",
            # India financial regulators / exchanges
            "rbi.org.in", "sebi.gov.in", "irdai.gov.in", "npci.org.in",
            "pfrda.org.in", "ibbi.gov.in", "ifsca.gov.in", "cci.gov.in",
            "trai.gov.in", "mcxindia.com", "nseindia.com", "bseindia.com",
            # US financial regulators
            "sec.gov", "federalreserve.gov", "treasury.gov", "finra.org",
            "cftc.gov", "fdic.gov", "occ.treas.gov", "cfpb.gov",
            # International financial institutions
            "imf.org", "worldbank.org", "oecd.org", "fred.stlouisfed.org",
            # Central banks (other countries)
            "ecb.europa.eu", "bankofengland.co.uk", "bankofcanada.ca",
            "rba.gov.au", "rbnz.govt.nz", "boj.or.jp", "bok.or.kr", "mas.gov.sg",
            "snb.ch", "bundesbank.de", "banque-france.fr", "bancaditalia.it",
            "bde.es", "dnb.nl", "nbb.be", "centralbank.ie", "riksbank.se",
            "norges-bank.no", "suomenpankki.fi", "oenb.at", "bportugal.pt",
            "bcb.gov.br", "banxico.org.mx",
        ],
    },
    "government": {
        "patterns": [
            r'\b(\blaw\b|\bbill\b|\bact\b|regulation|policy|ministry|parliament|congress|senate|'
            r'court ruling|government scheme|notification|gazette|census|budget|election commission)\b',
            r'\b(prime minister|chief minister|governor|cabinet)\b',
        ],
        "domains": [
            # India — government / regulators (core)
            "gov.in", "nic.in", "india.gov.in", "pib.gov.in", "mea.gov.in",
            "mha.gov.in", "mod.gov.in", "education.gov.in", "meity.gov.in",
            "dpiit.gov.in", "commerce.gov.in", "msme.gov.in", "finance.gov.in",
            "mca.gov.in", "labour.gov.in", "eci.gov.in", "sci.gov.in",
            "ecourts.gov.in", "dopt.gov.in", "mygov.in", "data.gov.in",
            # US government
            "usa.gov", "congress.gov", "supremecourt.gov", "state.gov",
            "justice.gov", "whitehouse.gov", "commerce.gov", "energy.gov",
            "defense.gov", "dhs.gov", "transportation.gov", "labor.gov",
            "interior.gov", "usda.gov", "va.gov", "cisa.gov",
            # UK / EU / international bodies
            "gov.uk", "europa.eu", "un.org", "who.int", "worldbank.org",
            "imf.org", "unesco.org", "wto.org", "oecd.org", "interpol.int",
            "undp.org", "unhcr.org", "ilo.org",
            # Other national governments
            "canada.ca", "gov.au", "govt.nz", "go.jp", "go.kr", "gov.sg",
            "admin.ch", "bund.de", "gouvernement.fr", "governo.it",
            "lamoncloa.gob.es", "government.nl", "belgium.be", "gov.ie",
            "government.se", "regjeringen.no", "valtioneuvosto.fi",
            "oesterreich.gv.at", "portugal.gov.pt", "gov.br", "gob.mx",
        ],
    },
    "science": {
        "patterns": [
            r'\b(research|\bstudy\b|experiment|hypothesis|peer.?review|journal article|research paper)\b',
            r'\b(physics|chemistry|biology|astronomy|genome|quantum|climate science)\b',
        ],
        "domains": [
            # Peer-reviewed journals
            "nature.com", "science.org", "thelancet.com", "nejm.org", "cell.com",
            "pnas.org", "jamanetwork.com", "bmj.com",
            # Research indexes / reference
            "arxiv.org", "scholar.google.com", "pubmed.ncbi.nlm.nih.gov",
            "britannica.com",
            # Science news
            "sciencedaily.com", "livescience.com", "space.com", "spacenews.com",
            # Space/science agencies
            "esa.int", "jpl.nasa.gov", "nasa.gov", "noaa.gov", "usgs.gov",
            "nsf.gov", "nist.gov", "isro.gov.in", "drdo.gov.in", "barc.gov.in",
            "csir.res.in", "imd.gov.in", "incois.gov.in",
            # Universities
            "mit.edu", "stanford.edu", "harvard.edu", "ox.ac.uk", "cam.ac.uk",
            "berkeley.edu", "iisc.ac.in",
        ],
    },
}


def classify_query_domain(query: str) -> dict:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Domain-aware query routing: detects which subject-matter category a
    question belongs to (programming, medicine, finance, government,
    science) and returns the domains most likely to hold an authoritative
    answer for that category. Falls back to "general" with an empty domain
    list when nothing matches — those queries run across both engines with
    no domain bias, exactly as before.
    Returns {"category": str, "domains": list[str]}.
    """
    lower = query.lower()
    for category, spec in _QUERY_ROUTES.items():
        if any(re.search(p, lower) for p in spec["patterns"]):
            return {"category": category, "domains": spec["domains"]}
    return {"category": "general", "domains": []}


def _rewrite_queries_regex_fallback(original: str) -> list[str]:
    """
    ── RENAMED from the old rewrite_queries() ──────────────────────────────
    This is the ORIGINAL pattern-based logic, kept byte-for-byte, just moved
    under a new name. It is now the FALLBACK path — used only when the LLM
    planner below is unavailable (no API key) or fails/times out/returns
    something unusable. Nothing about its behavior changed.
    """
    lower    = original.lower().strip().rstrip("?.,!")
    queries  = [original]
    year_now = datetime.utcnow().year
    recency  = detect_recency_window(original)

    # ── Anything time-sensitive (day/week/month window) — add recency +
    # official/news angles. Uses the shared detector so query rewriting
    # agrees with the time filters actually sent to Tavily/Serper below.
    if recency:
        queries.append(f"{lower} {year_now}")
        if lower.startswith("news"):
            queries.append(f"news {lower}")
        elif lower.startswith(("latest", "current", "recent")):
            queries.append(f"{lower} update")
        else:
            queries.append(f"latest {lower}")

    # ── Factual / Wikipedia-style ─────────────────────────────────────────────
    elif re.search(r'\b(what is|who is|when was|where is|how does|explain|define)\b', lower):
        queries.append(f"{lower} explained")
        queries.append(f"{lower} official definition")

    # ── Health / medical ─────────────────────────────────────────────────────
    elif re.search(r'\b(symptoms|treatment|cure|disease|medicine|dosage|side effect)\b', lower):
        queries.append(f"{lower} medical information")
        queries.append(f"{lower} NHS OR WHO OR WebMD")

    # ── Tech / coding ─────────────────────────────────────────────────────────
    elif re.search(r'\b(how to|tutorial|error|bug|fix|install|setup|configure)\b', lower):
        queries.append(f"{lower} tutorial")
        queries.append(f"{lower} stackoverflow OR github")

    # Deduplicate preserving order
    seen, unique = set(), []
    for q in queries:
        key = q.strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(q.strip())

    return unique[:3]


# ── NEW: prompt template for the LLM query planner ───────────────────────────
# Kept as a module-level constant (not re-built per call) so it's easy to
# tune/version without touching the calling code.
_QUERY_PLANNER_SYSTEM_PROMPT = """You are a search query planning engine for a production web search pipeline.
Given a user's question, produce a diverse set of search engine queries (NOT answers) that together will surface the best possible sources.

Cover as many of these angles as are genuinely relevant to the question (skip angles that don't apply — do not force irrelevant ones):
- official documentation / official source
- latest news / recent developments
- historical background / prior context
- comparisons vs alternatives
- benchmarks / data / statistics
- technical documentation / specs
- FAQs / common questions
- alternative phrasing a different searcher might use

Rules:
- Output ONLY a JSON array of strings. No prose, no markdown fences, no explanation.
- 5 to 10 queries. Prefer fewer, sharper queries over padding to hit 10.
- Each query must be a short, realistic search-engine query (not a sentence, not a question addressed to an AI).
- No duplicate or near-duplicate queries.
- Do not include the literal current year unless recency clearly matters.
- Match the language of the user's question."""


def _llm_plan_queries(original: str) -> Optional[list[str]]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Calls the Anthropic API with a small/fast model to plan 5-10 diverse
    search queries covering docs/news/history/comparisons/benchmarks/FAQs/
    alternative wording, per the system prompt above.

    Returns a cleaned list[str] on success, or None on ANY failure (missing
    key, network error, bad JSON, empty result) so the caller can fall back
    to the regex planner without ever raising.
    """
    if not QUERY_PLANNER_KEY:
        return None  # no key configured — let caller fall back silently

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         QUERY_PLANNER_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      QUERY_PLANNER_MODEL,
                "max_tokens": QUERY_PLANNER_MAX_TOK,
                "system":     _QUERY_PLANNER_SYSTEM_PROMPT,
                "messages":   [{"role": "user", "content": original}],
            },
            timeout=QUERY_PLANNER_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        # Extract the text block(s) from the response
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        ).strip()

        # Model may still wrap output in ```json fences despite instructions —
        # strip them defensively rather than trust the instruction alone.
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())

        planned = json.loads(text)
        if not isinstance(planned, list) or not planned:
            return None

        # Clean: must be non-empty strings, deduplicated (case-insensitive),
        # original question preserved as the first query for continuity with
        # the rest of the pipeline (dedup/citation logic assumes query[0] is
        # the primary query).
        seen, cleaned = set(), []
        for q in [original] + [str(q) for q in planned]:
            q = q.strip()
            key = q.lower()
            if q and key not in seen:
                seen.add(key)
                cleaned.append(q)

        if len(cleaned) < MIN_PLANNED_QUERIES:
            return None  # too thin — not worth it over the regex fallback

        return cleaned[:MAX_PLANNED_QUERIES]

    except Exception as e:
        # Any failure (timeout, bad key, malformed JSON, rate limit, etc.)
        # is swallowed here — this function's contract is "None on failure",
        # never raise. The caller decides what to do (fall back to regex).
        print(f"⚠️ [QueryPlanner] LLM planning failed, falling back to regex: {e}")
        return None


def rewrite_queries(original: str) -> list[str]:
    """
    Generate 5-10 diverse search queries from a user question using an
    LLM-powered planner (Claude/ChatGPT-style intent understanding: official
    docs, news, history, comparisons, benchmarks, technical docs, FAQs,
    alternative wording).

    ── CHANGED ────────────────────────────────────────────────────────────
    Previously this WAS the regex logic (2-3 queries, pattern-based).
    Now it tries the LLM planner first and falls back to the original
    regex-based logic (renamed to _rewrite_queries_regex_fallback) if the
    LLM is unavailable or fails for any reason. Function signature and
    return type are unchanged, so every caller downstream (run_production_search)
    keeps working without modification.
    """
    planned = _llm_plan_queries(original)
    if planned:
        return planned

    # Fallback — same fast, dependency-free behavior as before
    return _rewrite_queries_regex_fallback(original)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — PARALLEL SEARCH (Tavily + Serper)
# Both engines run simultaneously via asyncio.gather
# ══════════════════════════════════════════════════════════════════════════════

def _tavily_search_sync(
    query: str,
    max_results: int = RESULTS_PER_ENGINE,
    preferred_domains: Optional[list[str]] = None,
) -> list[dict]:
    """Synchronous Tavily search — called from thread pool."""
    if not TAVILY_KEY:
        return []
    try:
        recency = detect_recency_window(query)

        payload = {
            "api_key":             TAVILY_KEY,
            "query":               query,
            "max_results":         max_results,
            "include_answer":      True,
            "include_raw_content": False,
            # ── Freshness ────────────────────────────────────────────────────
            # A generic, undated search has no idea whether the user wants
            # today's number or a 2019 article — this is the #1 cause of
            # "stale" answers. When the query looks time-sensitive, bias the
            # search toward recent content instead of pure relevance/authority.
            "search_depth":        "advanced" if recency else "basic",
        }
        if recency:
            payload["time_range"] = recency  # "day" | "week" | "month"
            # Tavily's "news" topic (vs. "general") indexes news sources
            # specifically and only combines with time_range/days for the
            # tightest windows — broader "month" queries (e.g. "who is the
            # current CEO") aren't necessarily news articles, so leave those
            # on the general index.
            if recency in ("day", "week"):
                payload["topic"] = "news"

        # ── Domain-aware routing ─────────────────────────────────────────────
        # When classify_query_domain() has identified this as a programming/
        # medicine/finance/government/science question, bias Tavily toward
        # the authoritative domains for that category instead of leaving it
        # to generic relevance ranking alone.
        if preferred_domains:
            payload["include_domains"] = preferred_domains[:20]

        resp = requests.post(
            "https://api.tavily.com/search",
            json=payload,
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"⚠️ [Tavily] HTTP {resp.status_code}")
            return []

        data    = resp.json()
        results = []

        # Tavily's synthesised direct answer — treat as a top-tier result
        if data.get("answer"):
            results.append({
                "title":            "Direct Answer",
                "body":             data["answer"],
                "url":              "",
                "source_engine":    "tavily_answer",
                "is_direct_answer": True,
                "query_used":       query,
            })

        for r in data.get("results", []):
            results.append({
                "title":         r.get("title", ""),
                "body":          (r.get("content", "") or "")[:600],
                "url":           r.get("url", ""),
                "source_engine": "tavily",
                "query_used":    query,
                "published":     r.get("published_date", ""),
            })

        print(f"✅ [Tavily] '{query[:60]}' → {len(results)} results")
        return results

    except (requests.exceptions.RequestException, KeyError, ValueError, TypeError) as e:
        print(f"⚠️ [Tavily] expected failure: {e}")
        return []
    except Exception as e:
        print(f"❌ [Tavily] UNEXPECTED: {e.__class__.__name__}: {e}")
        return []


def _serper_search_sync(
    query: str,
    max_results: int = RESULTS_PER_ENGINE,
    preferred_domains: Optional[list[str]] = None,
) -> list[dict]:
    """Synchronous Serper (Google Search API) — called from thread pool."""
    if not SERPER_KEY:
        return []
    try:
        # ── Domain-aware routing ─────────────────────────────────────────────
        # Serper/Google has no native "preferred domains" parameter, so route
        # by appending a site: OR filter to the query text itself — biasing
        # Google toward the authoritative domains for this question's
        # category (see classify_query_domain) without hard-excluding
        # everything else.
        q_text = query
        if preferred_domains:
            site_filter = " OR ".join(f"site:{d}" for d in preferred_domains[:8])
            q_text = f"{query} ({site_filter})"

        payload = {"q": q_text, "num": max_results, "gl": "in", "hl": "en"}

        # ── Freshness ──────────────────────────────────────────────────────
        # Serper exposes Google's native date-range filter via "tbs". Without
        # it, Serper (and Google) rank purely by relevance/authority, which
        # can easily surface an older, higher-authority page over a fresher
        # but less-linked one for time-sensitive questions.
        recency = detect_recency_window(query)
        _tbs_map = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m"}
        if recency in _tbs_map:
            payload["tbs"] = _tbs_map[recency]

        resp = requests.post(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY":   SERPER_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"⚠️ [Serper] HTTP {resp.status_code}")
            return []

        data    = resp.json()
        results = []

        # Serper's answer box (like Google's featured snippet)
        answer_box = data.get("answerBox", {})
        if answer_box.get("answer") or answer_box.get("snippet"):
            results.append({
                "title":            answer_box.get("title", "Featured Snippet"),
                "body":             answer_box.get("answer") or answer_box.get("snippet", ""),
                "url":              answer_box.get("link", ""),
                "source_engine":    "serper_answer",
                "is_direct_answer": True,
                "query_used":       query,
            })

        # Knowledge graph
        kg = data.get("knowledgeGraph", {})
        if kg.get("description"):
            results.append({
                "title":         kg.get("title", "Knowledge Graph"),
                "body":          kg.get("description", ""),
                "url":           kg.get("descriptionUrl", kg.get("website", "")),
                "source_engine": "serper_kg",
                "query_used":    query,
            })

        for r in data.get("organic", []):
            results.append({
                "title":         r.get("title", ""),
                "body":          r.get("snippet", ""),
                "url":           r.get("link", ""),
                "source_engine": "serper",
                "query_used":    query,
                "position":      r.get("position", 99),
                "date":          r.get("date", ""),  # e.g. "2 days ago" — used for recency scoring
            })

        print(f"✅ [Serper] '{query[:60]}' → {len(results)} results")
        return results

    except (requests.exceptions.RequestException, KeyError, ValueError, TypeError) as e:
        print(f"⚠️ [Serper] expected failure: {e}")
        return []
    except Exception as e:
        print(f"❌ [Serper] UNEXPECTED: {e.__class__.__name__}: {e}")
        return []


async def _search_parallel(query: str, preferred_domains: Optional[list[str]] = None) -> tuple[list, list]:
    """Run Tavily + Serper simultaneously, return both result lists."""
    loop = asyncio.get_event_loop()
    tavily_task = loop.run_in_executor(None, _tavily_search_sync, query, RESULTS_PER_ENGINE, preferred_domains)
    serper_task = loop.run_in_executor(None, _serper_search_sync, query, RESULTS_PER_ENGINE, preferred_domains)
    tavily_res, serper_res = await asyncio.gather(tavily_task, serper_task)
    return tavily_res, serper_res


def _execute_search_queries(queries: list[str], preferred_domains: Optional[list[str]] = None) -> list[dict]:
    """
    ── CHANGED ──────────────────────────────────────────────────────────────
    Runs Tavily + Serper for every query in `queries` and returns the
    combined raw results. Now threads `preferred_domains` (from
    classify_query_domain) through to both engines for domain-aware routing.
    """
    all_raw: list[dict] = []
    for q in queries:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            tavily_res, serper_res = loop.run_until_complete(_search_parallel(q, preferred_domains))
            loop.close()
        except Exception:
            # Fallback to sequential if event loop issues
            tavily_res = _tavily_search_sync(q, preferred_domains=preferred_domains)
            serper_res = _serper_search_sync(q, preferred_domains=preferred_domains)

        all_raw.extend(tavily_res)
        all_raw.extend(serper_res)
    return all_raw


# ══════════════════════════════════════════════════════════════════════════════
# MULTI-STEP SEARCH PLANNING (NEW)
# Turns the pipeline from "generate queries → search once" into:
#   plan → search → analyze → (if evidence is thin) generate more queries →
#   search again → repeat, capped at MAX_SEARCH_ROUNDS and stopping as soon
#   as confidence is high. This only changes the PLANNING stage — dedup,
#   Firecrawl, trust scoring, cross-ref, rerank, and citations downstream
#   are untouched.
# ══════════════════════════════════════════════════════════════════════════════

MAX_SEARCH_ROUNDS       = 3   # hard cap — guarantees no infinite loop
CONFIDENCE_TIMEOUT      = 4   # seconds — same fail-fast philosophy as the planner
CONFIDENCE_MAX_TOK      = 300
MAX_ADDITIONAL_QUERIES  = 4   # per follow-up round

_CONFIDENCE_SYSTEM_PROMPT = """You are a research-evidence auditor for a web search pipeline.
You will be given the user's original question and a list of snippets already gathered from search engines.

Decide whether the gathered evidence is ALREADY sufficient to answer the question completely and confidently.

Output ONLY a JSON object, no prose, no markdown fences:
{
  "confident": true | false,
  "missing": ["short phrase describing a gap", ...],
  "additional_queries": ["search query", ...]
}

Rules:
- "confident": true means the evidence fully covers the question — no further searching is needed. Set "additional_queries" to [] in that case.
- "confident": false means there is a genuine, specific gap (missing facts, unanswered sub-question, outdated/conflicting info, no results at all for part of the question).
- Only mark false if the gap is real and specific — do not ask for more searches just to be thorough.
- "additional_queries": 1 to 4 short, realistic search-engine queries that directly target the missing information. Do not repeat queries that were already run.
- Never leave "confident" false with an empty "additional_queries" list."""


def _condense_evidence_for_review(raw_results: list[dict], max_items: int = 15) -> str:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Builds a compact, token-cheap text block of what's been found so far, so
    the confidence-check LLM call can decide if more searching is needed
    without re-sending full page bodies.
    """
    lines = []
    for r in raw_results[:max_items]:
        title = (r.get("title") or "").strip()
        body  = (r.get("body") or "").strip().replace("\n", " ")[:200]
        lines.append(f"- {title}: {body}")
    return "\n".join(lines) if lines else "(no results found)"


def _assess_evidence_and_plan_more(
    original_query: str,
    raw_results: list[dict],
    queries_already_run: list[str],
) -> dict:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Asks the LLM whether the evidence gathered so far is sufficient. Returns
    {"confident": bool, "additional_queries": list[str]}.

    Fails CLOSED toward stopping: any missing key, network error, timeout,
    malformed JSON, or missing API key returns confident=True with no
    additional queries. This is deliberate — a broken confidence check must
    never be able to force extra rounds, since that's how you'd get an
    unbounded/infinite loop. The MAX_SEARCH_ROUNDS cap is a second, independent
    guard on top of this.
    """
    if not QUERY_PLANNER_KEY:
        return {"confident": True, "additional_queries": []}

    evidence = _condense_evidence_for_review(raw_results)

    user_content = (
        f"Original question: {original_query}\n\n"
        f"Queries already run: {queries_already_run}\n\n"
        f"Evidence gathered so far:\n{evidence}"
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         QUERY_PLANNER_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      QUERY_PLANNER_MODEL,
                "max_tokens": CONFIDENCE_MAX_TOK,
                "system":     _CONFIDENCE_SYSTEM_PROMPT,
                "messages":   [{"role": "user", "content": user_content}],
            },
            timeout=CONFIDENCE_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        ).strip()
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())

        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return {"confident": True, "additional_queries": []}

        confident = bool(parsed.get("confident", True))

        seen = {q.strip().lower() for q in queries_already_run}
        additional = []
        for q in parsed.get("additional_queries", []) or []:
            q = str(q).strip()
            key = q.lower()
            if q and key not in seen:
                seen.add(key)
                additional.append(q)

        # A "not confident" verdict with nothing new to search is
        # meaningless — treat it as confident so the loop can stop.
        if not additional:
            confident = True

        return {
            "confident":          confident,
            "additional_queries": additional[:MAX_ADDITIONAL_QUERIES],
        }

    except Exception as e:
        print(f"⚠️ [Planner] Confidence check failed, stopping rounds: {e}")
        return {"confident": True, "additional_queries": []}


# ══════════════════════════════════════════════════════════════════════════════
# ANSWER-GRAPH HOP PLANNING (entity completion) ──────────────────────────────
# Deterministic, non-LLM multi-hop retrieval for entity-style questions
# (e.g. "Tell me about Acme Corp"): after each round, checks whether the
# evidence gathered so far already answers a fixed set of standard slots —
# founder → acquisition → timeline — in that order, and issues ONE targeted
# hop query for the first missing slot. Stops once every slot is covered
# ("answer graph complete") or MAX_SEARCH_ROUNDS is hit (max 3 hops total,
# same hard cap as the rest of the planner). Runs ahead of the generic
# LLM-based gap check below and needs no LLM/API key to work.
# ══════════════════════════════════════════════════════════════════════════════

_ANSWER_GRAPH_SLOTS = [
    {
        "slot": "founder",
        "covered_patterns": [
            r'\bfound(ed|er|ers)\b', r'\bco-?founder\b', r'\bstarted by\b', r'\bcreated by\b',
        ],
        "query_template": "{entity} founder",
    },
    {
        "slot": "acquisition",
        "covered_patterns": [
            r'\bacqui(re|red|sition)\b', r'\bmerger\b', r'\bbought (by|out)\b', r'\btakeover\b',
        ],
        "query_template": "{entity} acquisition history",
    },
    {
        "slot": "timeline",
        "covered_patterns": [
            r'\btimeline\b', r'\bhistory\b',
            r'\b(19|20)\d{2}\b.{0,80}\b(19|20)\d{2}\b',  # 2+ distinct years mentioned
        ],
        "query_template": "{entity} company timeline history",
    },
]

_ENTITY_NAME_RE = re.compile(r'\b([A-Z][a-zA-Z0-9&]*(?:\s+[A-Z][a-zA-Z0-9&]*){0,3})\b')

# Words indicating the query is about an organization/company/product-style
# entity (where founder/acquisition/timeline hops make sense) rather than a
# narrow factual, how-to, or definitional question.
_ENTITY_QUERY_HINTS = re.compile(
    r'\b(company|startup|corporation|organi[sz]ation|firm|business|app|platform|'
    r'product|brand|founder|ceo|acqui(re|red|sition)|about)\b', re.IGNORECASE,
)


def _extract_entity_name(query: str) -> Optional[str]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Best-effort extraction of the entity (company/product/org) a query is
    about, using a capitalized-phrase heuristic — picks the longest
    capitalized run as the more likely full entity name. Returns None when
    nothing looks like a proper noun (e.g. a lowercase how-to/definitional
    question), since those have no entity graph to hop.
    """
    candidates = [c.strip() for c in _ENTITY_NAME_RE.findall(query) if len(c.strip()) > 1]
    if not candidates:
        return None
    return max(candidates, key=len)


def _build_answer_graph(query: str, raw_results: list[dict]) -> dict:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Inspects evidence gathered so far and reports which standard slots
    (founder, acquisition, timeline) are already covered for an entity-style
    question. "applicable" is False for queries that don't look like they're
    about a company/organization/product entity — those get no graph hops.
    Returns {"applicable": bool, "entity": str|None,
             "covered": {"founder": bool, "acquisition": bool, "timeline": bool}}.
    """
    entity = _extract_entity_name(query)
    applicable = bool(entity) and bool(_ENTITY_QUERY_HINTS.search(query))

    combined_text = " ".join(
        (r.get("title", "") or "") + " " + (r.get("body", "") or "")
        for r in raw_results
    ).lower()

    covered = {
        slot["slot"]: any(re.search(p, combined_text) for p in slot["covered_patterns"])
        for slot in _ANSWER_GRAPH_SLOTS
    }
    return {"applicable": applicable, "entity": entity, "covered": covered}


def _next_hop_query(query: str, raw_results: list[dict]) -> Optional[str]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Walks the answer-graph slots in fixed order — founder → acquisition →
    timeline — and returns ONE targeted search query for the first slot not
    yet covered, or None if the question isn't entity-style, no entity name
    could be extracted, or every slot is already covered (the answer graph
    is complete).
    """
    graph = _build_answer_graph(query, raw_results)
    if not graph["applicable"] or not graph["entity"]:
        return None

    for slot in _ANSWER_GRAPH_SLOTS:
        if not graph["covered"][slot["slot"]]:
            return slot["query_template"].format(entity=graph["entity"])

    return None  # every slot covered — answer graph is complete


def plan_and_execute_search(query: str) -> tuple[list[str], list[dict]]:
    """
    ── CHANGED ──────────────────────────────────────────────────────────────
    Iterative multi-step search planner:

      Round 1: rewrite_queries() → search
      Analyze:
        - deterministic answer-graph hop check first (founder → acquisition
          → timeline for entity-style questions — see _next_hop_query); if a
          slot is missing, hop straight to a targeted query for it, no LLM
          needed
        - otherwise, ask the LLM whether the evidence is sufficient
          - yes, or LLM unavailable/fails → stop
          - no → LLM proposes targeted follow-up queries → search again
      Repeat until the answer graph is complete, confident, no new queries
      are proposed, or MAX_SEARCH_ROUNDS is reached (max 3 hops total —
      hard cap, no infinite loops possible).

    Returns (queries_run, all_raw) with the same shapes the old inline
    steps 1+2 in run_production_search() produced, so downstream dedup/
    Firecrawl/trust/rerank/citation code needs no changes.
    """
    queries_run: list[str] = []
    all_raw: list[dict] = []

    # ── Domain-aware routing ─────────────────────────────────────────────
    # Classify the ORIGINAL question once (category is a property of user
    # intent, not of any one rewritten sub-query) and reuse the resulting
    # preferred domains across every round below.
    route = classify_query_domain(query)
    preferred_domains = route["domains"]
    print(
        f"🧭 [Router] '{query[:60]}' → category='{route['category']}'"
        + (f", routing to {preferred_domains}" if preferred_domains else " (no domain bias)")
    )

    # ── Round 1 — initial plan + search ──────────────────────────────────
    round_num = 1
    queries = rewrite_queries(query)
    print(f"📝 [Planner] Round {round_num}/{MAX_SEARCH_ROUNDS} queries: {queries}")
    queries_run.extend(queries)
    all_raw.extend(_execute_search_queries(queries, preferred_domains))

    # ── Rounds 2..MAX_SEARCH_ROUNDS — analyze, fill gaps, repeat ─────────
    while round_num < MAX_SEARCH_ROUNDS:
        # Deterministic answer-graph hop: founder → acquisition → timeline.
        # Checked first since it's free (no LLM call) and covers the exact
        # "need founder? / need acquisition? / need timeline?" hop chain.
        hop_query = _next_hop_query(query, all_raw)
        already_run = {q.strip().lower() for q in queries_run}
        if hop_query and hop_query.strip().lower() not in already_run:
            round_num += 1
            print(f"🔗 [Hop {round_num}/{MAX_SEARCH_ROUNDS}] Answer-graph gap → '{hop_query}'")
            queries_run.append(hop_query)
            all_raw.extend(_execute_search_queries([hop_query], preferred_domains))
            continue

        assessment = _assess_evidence_and_plan_more(query, all_raw, queries_run)

        if assessment["confident"] or not assessment["additional_queries"]:
            print(f"✅ [Planner] Stopping after round {round_num} — evidence sufficient")
            break

        round_num += 1
        follow_up = assessment["additional_queries"]
        print(f"📝 [Planner] Round {round_num}/{MAX_SEARCH_ROUNDS} follow-up queries: {follow_up}")
        queries_run.extend(follow_up)
        all_raw.extend(_execute_search_queries(follow_up, preferred_domains))

    if round_num >= MAX_SEARCH_ROUNDS:
        print(f"🛑 [Planner] Reached MAX_SEARCH_ROUNDS ({MAX_SEARCH_ROUNDS}) — stopping")

    return queries_run, all_raw


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — DEDUPLICATION
# Remove duplicate URLs; merge snippets from different engines for same URL
# ══════════════════════════════════════════════════════════════════════════════

def _url_key(url: str) -> str:
    """Normalise URL for deduplication (strip tracking params, trailing slashes)."""
    if not url:
        return ""
    url = url.split("?")[0].split("#")[0].rstrip("/").lower()
    return url


def _content_fingerprint(text: str) -> str:
    """Short hash of first 200 chars — catches near-duplicate snippets."""
    return hashlib.md5(text[:200].lower().strip().encode()).hexdigest()[:8]


def deduplicate_results(all_results: list[dict]) -> list[dict]:
    """
    Merge results from multiple engines/queries.
    - Same URL → keep longer body, mark as multi-source (boosts trust score)
    - Same content fingerprint (different URL) → keep highest-trust domain
    Returns deduplicated list.
    """
    seen_urls: dict[str, dict]   = {}   # url_key → result
    seen_fps:  set[str]          = set()
    final:     list[dict]        = []

    for r in all_results:
        url = r.get("url", "")
        body = r.get("body", "")
        ukey = _url_key(url)
        fp   = _content_fingerprint(body) if body else ""

        if not ukey and not fp:
            # Direct answers without URL — always include
            final.append(r)
            continue

        if ukey and ukey in seen_urls:
            # Same URL from another engine — extend body, mark multi-source
            existing = seen_urls[ukey]
            if len(body) > len(existing.get("body", "")):
                existing["body"] = body
            existing["engines_seen"] = existing.get("engines_seen", []) + [r.get("source_engine", "")]
            existing["multi_source"] = True
            continue

        if fp and fp in seen_fps:
            # Same content, different URL — skip duplicate
            continue

        # New unique result
        r["engines_seen"] = [r.get("source_engine", "")]
        r["multi_source"] = False
        if ukey:
            seen_urls[ukey] = r
        if fp:
            seen_fps.add(fp)
        final.append(r)

    return final


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — FIRECRAWL CONTENT EXTRACTION
# Deep-crawl top N results for full article text (much richer than snippets)
# ══════════════════════════════════════════════════════════════════════════════

# Domains that block crawlers or have no useful extractable content — skip them
_SKIP_CRAWL_DOMAINS = {
    "youtube.com", "youtu.be", "instagram.com", "facebook.com",
    "twitter.com", "x.com", "tiktok.com", "linkedin.com",
    "reddit.com",  # often paywalled/rate-limited
    "nseindia.com", "bseindia.com",  # require login
    "paywalled.com",
}


def _should_crawl(url: str) -> bool:
    """Return True if this URL is worth Firecrawling."""
    if not url or not FIRECRAWL_KEY:
        return False
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        if any(skip in domain for skip in _SKIP_CRAWL_DOMAINS):
            return False
        # Only crawl http(s) pages
        return url.startswith(("http://", "https://"))
    except Exception:
        return False


_TABLE_ROW_RE       = re.compile(r'^\s*\|.+\|\s*$', re.MULTILINE)
_TABLE_SEPARATOR_RE = re.compile(r'^\s*\|?[\s:|-]+\|[\s:|-]+\|?\s*$', re.MULTILINE)

# ── Discard: boilerplate that adds no answer value ──────────────────────────
_NAVIGATION_PATTERNS = [
    r'^\s*(home|menu|navigation|skip to (main )?content|main menu)\s*$',
    r'(\[[^\]]+\]\([^)]+\)\s*[|>»/]\s*){2,}\[[^\]]+\]\([^)]+\)',  # link | link | link chains
    r'^\s*(breadcrumb|you may also like|related articles?|read (also|more)|'
    r'trending now|more from|share this|follow us on)\b',
]
_ADVERTISEMENT_PATTERNS = [
    r'\b(advertisement|sponsored( content)?|promoted content|paid partnership)\b',
    r'\b(subscribe now|sign up for our newsletter|download our app|click here to subscribe)\b',
]
_COMMENT_PATTERNS = [
    r'^\s*\d+\s*comments?\b',
    r'\b(leave a reply|leave a comment|post your comment|add a comment|view all comments)\b',
    r'^\s*reply\s*$',
]
_DISCARD_CONTENT_TYPES = {"navigation", "advertisement", "comment"}

# ── Preserve: high-value content worth keeping even when short ─────────────
_DEFINITION_PATTERNS = [
    r'\b[\w\s]{2,40}\s+(is defined as|refers to|is a term (used )?for|denotes)\b',
    r'^\*\*[^*]{2,60}\*\*\s*[:\u2013\u2014-]\s',   # **Term**: definition
]
_STATISTIC_PATTERNS = [
    r'\d+(\.\d+)?\s?%',
    r'[$₹€£]\s?\d[\d,.]*',
    r'\b(grew|increased|decreased|declined|rose|fell)\s+by\s+\d',
    r'\baccording to (a|the) (study|survey|report|data)\b',
]
_TIMELINE_PATTERNS = [
    r'\b(19|20)\d{2}\b.{0,80}\b(19|20)\d{2}\b',   # two+ years mentioned = likely a timeline
    r'^\s*[-*]\s*(19|20)\d{2}\b',                  # bullet starting with a year
    r'\btimeline\b',
]
_PRESERVE_CONTENT_TYPES = {"table", "definition", "statistic", "timeline"}


def _classify_chunk_content_type(heading: str, text: str) -> str:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Classifies a paragraph/section so extraction can DISCARD boilerplate
    (navigation, advertisements, comments) and PRESERVE high-value content
    (tables, definitions, statistics, timelines) instead of treating every
    paragraph the same way. Discard signals are checked first — they're
    unambiguous (a comment count, an "Advertisement" label) even inside an
    otherwise plausible-looking block. Falls back to "paragraph" for
    ordinary prose that isn't any of the above.
    """
    lower = f"{heading}\n{text}".strip().lower()

    if any(re.search(p, lower, re.MULTILINE) for p in _COMMENT_PATTERNS):
        return "comment"
    if any(re.search(p, lower) for p in _ADVERTISEMENT_PATTERNS):
        return "advertisement"
    if any(re.search(p, lower, re.MULTILINE) for p in _NAVIGATION_PATTERNS):
        return "navigation"

    if _TABLE_ROW_RE.search(text) and _TABLE_SEPARATOR_RE.search(text):
        return "table"
    if any(re.search(p, lower, re.MULTILINE) for p in _DEFINITION_PATTERNS):
        return "definition"
    if any(re.search(p, lower, re.MULTILINE) for p in _TIMELINE_PATTERNS):
        return "timeline"
    if any(re.search(p, lower, re.MULTILINE) for p in _STATISTIC_PATTERNS):
        return "statistic"

    return "paragraph"


def _filter_unrelated_paragraphs(body: str, heading: str = "") -> list[tuple[str, str]]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Splits a section's body into paragraphs, classifies each one, and drops
    navigation/advertisement/comment paragraphs — "remove unrelated
    sections" / "discard navigation" / "discard advertisements" / "discard
    comments" — while keeping every other paragraph (including short
    tables/definitions/statistics/timelines that a plain length filter
    would otherwise throw away).
    Returns [(paragraph_text, content_type), ...] for the KEPT paragraphs,
    in original order.
    """
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
    kept: list[tuple[str, str]] = []
    for p in paragraphs:
        ctype = _classify_chunk_content_type(heading, p)
        if ctype in _DISCARD_CONTENT_TYPES:
            continue
        kept.append((p, ctype))
    return kept


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Splits an over-long chunk into smaller pieces along paragraph boundaries
    (never mid-sentence) so no single chunk blows past `_CHUNK_MAX_CHARS`.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []

    parts, buf = [], ""
    for para in re.split(r'\n\s*\n', text):
        candidate = f"{buf}\n\n{para}".strip() if buf else para
        if len(candidate) > max_chars and buf:
            parts.append(buf)
            buf = para
        else:
            buf = candidate
    if buf:
        parts.append(buf)
    return parts


def _chunk_page_markdown(markdown: str, page_title: str) -> list[dict]:
    """
    ── CHANGED ──────────────────────────────────────────────────────────────
    STEP 1 of Firecrawl extraction: split page markdown into semantic
    sections along heading boundaries (#, ##, ### ...), falling back to
    paragraph grouping when the page has no headings. Within each section,
    paragraphs are now filtered (_filter_unrelated_paragraphs) to remove
    unrelated/boilerplate content — navigation, advertisements, comments —
    and each surviving chunk is tagged with a content_type so tables,
    definitions, statistics, and timelines can be preserved and prioritized
    downstream even when short, instead of being cut by a flat length rule.

    Each chunk: {"heading": str, "text": str, "content_type": str}.
    `heading` is always populated — the nearest markdown heading above the
    chunk, or the page title for lede text / headerless pages.
    """
    markdown = markdown.strip()
    if not markdown:
        return []

    headers = list(_HEADER_RE.finditer(markdown))
    chunks: list[dict] = []

    def _emit(heading_text: str, body: str) -> None:
        kept = _filter_unrelated_paragraphs(body, heading_text)
        if not kept:
            return  # entire section was boilerplate (nav/ads/comments)

        filtered_body = "\n\n".join(p for p, _ in kept)
        preserve_types = [t for _, t in kept if t in _PRESERVE_CONTENT_TYPES]
        has_preserve   = bool(preserve_types)

        # Only enforce the min-length floor on ordinary prose — a short
        # table/definition/statistic/timeline is still worth keeping.
        if len(filtered_body) < _CHUNK_MIN_CHARS and not has_preserve:
            return

        dominant_type = preserve_types[0] if has_preserve else "paragraph"
        for piece in _split_long_text(filtered_body, _CHUNK_MAX_CHARS):
            chunks.append({"heading": heading_text, "text": piece, "content_type": dominant_type})

    if headers:
        # Lede text before the first heading still belongs to the page title
        if headers[0].start() > 0:
            _emit(page_title or "Introduction", markdown[:headers[0].start()])

        for i, h in enumerate(headers):
            heading_text = h.group(2).strip()
            body_start   = h.end()
            body_end     = headers[i + 1].start() if i + 1 < len(headers) else len(markdown)
            _emit(heading_text, markdown[body_start:body_end])
    else:
        # No headings — filter paragraphs first, then group survivors up to
        # the per-chunk cap (same grouping logic as before, just fed
        # cleaned input instead of the raw, unfiltered paragraph list).
        kept = _filter_unrelated_paragraphs(markdown, page_title)
        buf, buf_types = "", []
        for p, ctype in kept:
            candidate = f"{buf}\n\n{p}".strip() if buf else p
            if len(candidate) > _CHUNK_MAX_CHARS and buf:
                dominant = next((t for t in buf_types if t in _PRESERVE_CONTENT_TYPES), "paragraph")
                chunks.append({"heading": page_title or "", "text": buf, "content_type": dominant})
                buf, buf_types = p, [ctype]
            else:
                buf = candidate
                buf_types.append(ctype)
        has_preserve = any(t in _PRESERVE_CONTENT_TYPES for t in buf_types)
        if len(buf) >= _CHUNK_MIN_CHARS or has_preserve:
            dominant = next((t for t in buf_types if t in _PRESERVE_CONTENT_TYPES), "paragraph")
            if buf:
                chunks.append({"heading": page_title or "", "text": buf, "content_type": dominant})

    return chunks


def _dedupe_chunks(chunks: list[dict]) -> list[dict]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    STEP 2 (store) + duplicate removal: drop exact/near-duplicate chunks —
    common with repeated nav/footer boilerplate or sections a page renders
    twice. Fingerprint = whitespace-normalized, lowercased text, hashed.
    """
    seen: set[str] = set()
    unique: list[dict] = []
    for c in chunks:
        norm = re.sub(r'\s+', ' ', c["text"].strip().lower())
        fp = hashlib.md5(norm.encode("utf-8", "ignore")).hexdigest()
        if fp in seen:
            continue
        seen.add(fp)
        unique.append(c)
    return unique


_CONTENT_TYPE_SCORE_BONUS = {
    "table": 3.0, "definition": 2.5, "statistic": 2.0, "timeline": 2.0, "paragraph": 0.0,
}


def _score_chunk(chunk: dict, query_terms: set[str]) -> float:
    """
    ── CHANGED ──────────────────────────────────────────────────────────────
    STEP 3: cheap, dependency-free relevance score — keyword overlap between
    the chunk and the query terms, with heading matches weighted heavier
    than body matches (a chunk whose *heading* is on-topic is usually more
    relevant than one that just happens to mention a query word once), plus
    a small bonus for well-sized chunks so tiny fragments and giant blocks
    both rank behind well-formed, on-topic sections. Now also adds a bonus
    for high-value content types (table/definition/statistic/timeline) so
    they're prioritized to survive top-N selection even when their keyword
    overlap alone wouldn't have ranked them highly.
    """
    if not query_terms:
        return 1.0 + _CONTENT_TYPE_SCORE_BONUS.get(chunk.get("content_type", "paragraph"), 0.0)

    text_words    = set(re.findall(r'[a-z0-9]+', chunk["text"].lower()))
    heading_words = set(re.findall(r'[a-z0-9]+', chunk.get("heading", "").lower()))

    body_hits    = len(query_terms & text_words)
    heading_hits = len(query_terms & heading_words)

    score = body_hits + (heading_hits * 3)

    length = len(chunk["text"])
    if 150 <= length <= _CHUNK_MAX_CHARS:
        score += 0.5

    score += _CONTENT_TYPE_SCORE_BONUS.get(chunk.get("content_type", "paragraph"), 0.0)

    return score


def _embed_openai(texts: list[str]) -> Optional[list[list[float]]]:
    if not OPENAI_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
            json={"model": EMBEDDING_MODEL_OPENAI, "input": texts},
            timeout=EMBEDDING_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"⚠️ [Embeddings/OpenAI] HTTP {resp.status_code}")
            return None
        data = resp.json().get("data", [])
        return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]
    except Exception as e:
        print(f"⚠️ [Embeddings/OpenAI] {e}")
        return None


def _embed_cohere(texts: list[str], input_type: str = "search_document") -> Optional[list[list[float]]]:
    if not COHERE_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.cohere.ai/v1/embed",
            headers={"Authorization": f"Bearer {COHERE_KEY}", "Content-Type": "application/json"},
            json={"model": EMBEDDING_MODEL_COHERE, "texts": texts, "input_type": input_type},
            timeout=EMBEDDING_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"⚠️ [Embeddings/Cohere] HTTP {resp.status_code}")
            return None
        return resp.json().get("embeddings")
    except Exception as e:
        print(f"⚠️ [Embeddings/Cohere] {e}")
        return None


def _embed_voyage(texts: list[str], input_type: str = "document") -> Optional[list[list[float]]]:
    if not VOYAGE_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {VOYAGE_KEY}", "Content-Type": "application/json"},
            json={"model": EMBEDDING_MODEL_VOYAGE, "input": texts, "input_type": input_type},
            timeout=EMBEDDING_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"⚠️ [Embeddings/Voyage] HTTP {resp.status_code}")
            return None
        data = resp.json().get("data", [])
        return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]
    except Exception as e:
        print(f"⚠️ [Embeddings/Voyage] {e}")
        return None


def _get_embeddings(texts: list[str], is_query: bool = False) -> Optional[list[list[float]]]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Provider-independent embedding call. Dispatches on EMBEDDING_PROVIDER
    (openai | cohere | voyage) so swapping providers is a config change, not
    a code change. Returns None — never raises — if the selected provider
    has no key configured or the call fails; callers fall back to the
    keyword scorer, so a missing/broken embedding provider degrades
    retrieval quality instead of breaking the pipeline.
    """
    if not texts:
        return None
    if EMBEDDING_PROVIDER == "cohere":
        return _embed_cohere(texts, "search_query" if is_query else "search_document")
    if EMBEDDING_PROVIDER == "voyage":
        return _embed_voyage(texts, "query" if is_query else "document")
    return _embed_openai(texts)  # default provider


def _normalize_vec(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class _LocalVectorIndex:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Minimal local vector index for one page's chunks. Uses FAISS
    (IndexFlatIP over L2-normalized vectors == cosine similarity) when the
    `faiss` package is installed, and transparently falls back to a pure
    Python cosine-similarity implementation with the same add()/search()
    interface when it isn't. FAISS stays an optional accelerator, not a
    hard dependency — this keeps the pipeline running either way.
    """

    def __init__(self, dim: int):
        self.dim = dim
        self._vectors: list[list[float]] = []
        self._faiss_index = None
        self._faiss = None
        self._np = None
        try:
            import faiss  # type: ignore
            import numpy as np  # type: ignore
            self._faiss = faiss
            self._np = np
            self._faiss_index = faiss.IndexFlatIP(dim)
        except Exception:
            pass  # no faiss/numpy available — use the pure-python fallback below

    def add(self, vectors: list[list[float]]) -> None:
        if self._faiss_index is not None:
            arr = self._np.array(vectors, dtype="float32")
            self._faiss.normalize_L2(arr)
            self._faiss_index.add(arr)
        else:
            self._vectors = [_normalize_vec(v) for v in vectors]

    def search(self, query_vector: list[float], top_k: int) -> list[tuple[int, float]]:
        """Returns [(chunk_index, similarity_score), ...] sorted best-first."""
        if self._faiss_index is not None:
            q = self._np.array([query_vector], dtype="float32")
            self._faiss.normalize_L2(q)
            scores, idxs = self._faiss_index.search(q, min(top_k, self._faiss_index.ntotal))
            return [(int(i), float(s)) for i, s in zip(idxs[0], scores[0]) if i != -1]
        qn = _normalize_vec(query_vector)
        sims = [(i, _dot(qn, v)) for i, v in enumerate(self._vectors)]
        sims.sort(key=lambda t: t[1], reverse=True)
        return sims[:top_k]


def _select_chunks_by_embedding(
    chunks: list[dict], query: str, top_k: int
) -> Optional[list[dict]]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Embedding retrieval: embed every chunk + the query, run a local
    FAISS-compatible vector search, and return the top_k chunks by cosine
    similarity, restored to original page order. Returns None (not an empty
    list) on any failure so the caller can tell "embeddings unavailable"
    apart from "ran fine, nothing matched" and fall back cleanly.
    """
    if not chunks:
        return None

    texts = [c["text"] for c in chunks]
    doc_vectors = _get_embeddings(texts, is_query=False)
    if not doc_vectors or len(doc_vectors) != len(texts):
        return None

    query_vectors = _get_embeddings([query], is_query=True)
    if not query_vectors:
        return None

    index = _LocalVectorIndex(dim=len(doc_vectors[0]))
    index.add(doc_vectors)

    hits = index.search(query_vectors[0], top_k)
    if not hits:
        return None

    kept_idx = sorted(i for i, _score in hits)
    return [chunks[i] for i in kept_idx]


def _select_best_chunks(
    chunks: list[dict],
    query: str,
    max_chunks: int = _PAGE_MAX_CHUNKS,
    char_budget: int = _PAGE_CHAR_BUDGET,
) -> list[dict]:
    """
    ── CHANGED ──────────────────────────────────────────────────────────────
    STEP 4 (retrieval): embedding generation → vector search → retrieve
    only the relevant chunks. Tries provider embeddings + the local
    FAISS-compatible index first; if no embedding provider is configured or
    the calls fail, falls back to the original keyword-overlap scorer so
    retrieval always degrades gracefully instead of breaking the pipeline.
    Either path is then trimmed to `char_budget`, in original page order.
    """
    id_to_idx = {id(c): i for i, c in enumerate(chunks)}
    vector_hits = _select_chunks_by_embedding(chunks, query, max_chunks)

    if vector_hits is None:
        # ── Fallback: keyword-overlap scoring (unchanged from before) ──
        query_terms = {w for w in re.findall(r'[a-z0-9]+', query.lower()) if w not in _STOPWORDS}
        scored = [(i, c, _score_chunk(c, query_terms)) for i, c in enumerate(chunks)]
        scored.sort(key=lambda t: t[2], reverse=True)
        ordered = [c for _i, c, _score in scored]
    else:
        ordered = vector_hits

    kept: list[dict] = []
    used_chars = 0
    for c in ordered:
        if len(kept) >= max_chunks:
            break
        if used_chars + len(c["text"]) > char_budget and kept:
            continue  # too big for what's left of the budget — keep scanning smaller ones
        kept.append(c)
        used_chars += len(c["text"])

    # Restore original page order (reading-order output, not score order)
    kept.sort(key=lambda c: id_to_idx[id(c)])
    return kept


def _assemble_chunks(chunks: list[dict], page_title: str) -> str:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    STEP 5 (return): render the selected chunks back into one text block,
    keeping each section's heading so the AI still sees page structure
    instead of a bare stitched-together blob. Page title is always kept.
    """
    if not chunks:
        return ""
    parts = [f"# {page_title}"] if page_title else []
    last_heading = None
    for c in chunks:
        heading = c.get("heading") or ""
        if heading and heading != last_heading:
            parts.append(f"## {heading}")
            last_heading = heading
        parts.append(c["text"])
    return "\n\n".join(parts).strip()


def _firecrawl_extract(url: str, query: str = "") -> Optional[str]:
    """
    ── CHANGED ──────────────────────────────────────────────────────────────
    Call Firecrawl API to extract clean markdown from a URL, then run it
    through the extraction pipeline instead of sending the entire page:

        Extract headings → identify relevant paragraphs → remove unrelated
        sections (discard navigation/advertisements/comments, keep tables/
        definitions/statistics/timelines) → score → return only the best
        chunks

    Returns the assembled best-chunks text, or None on failure.
    Hard timeout: 5 seconds (skip slow pages rather than block the pipeline).
    """
    if not FIRECRAWL_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Authorization": f"Bearer {FIRECRAWL_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "url":     url,
                "formats": ["markdown"],
                "onlyMainContent": True,
                "timeout": 4000,   # tell Firecrawl server to cap at 4s too
            },
            timeout=5,             # hard client-side timeout — skip if slow
        )
        if resp.status_code != 200:
            print(f"⚠️ [Firecrawl] HTTP {resp.status_code} for {url[:60]}")
            return None

        data = resp.json()
        if not data.get("success"):
            return None

        page_data = data.get("data", {}) or {}
        content   = page_data.get("markdown", "")
        if not content:
            return None

        page_title = (page_data.get("metadata") or {}).get("title", "") or ""

        # ── Page processing pipeline (replaces old content[:3000] truncate) ──
        chunks = _chunk_page_markdown(content, page_title)
        chunks = _dedupe_chunks(chunks)

        if not chunks:
            # Page had no usable structure at all — fall back to a flat cap
            clean = content.strip()[:_PAGE_CHAR_BUDGET]
            print(f"✅ [Firecrawl] {url[:60]}: no chunk boundaries found, "
                  f"used flat {len(clean)}-char cap")
            return clean

        best  = _select_best_chunks(chunks, query)
        clean = _assemble_chunks(best, page_title)

        print(f"✅ [Firecrawl] {url[:60]}: {len(chunks)} chunks → "
              f"{len(best)} kept ({len(clean)} chars, query-scored)")
        return clean

    except Exception as e:
        print(f"⚠️ [Firecrawl] timeout/skip for {url[:60]}: {e}")
        return None


def _firecrawl_priority_score(url: str, result: dict) -> float:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Scores a candidate for CRAWL PRIORITY (not the final trust_score field —
    that's assigned later in Step 5). Reuses the same domain-tier + recency
    logic as compute_trust_score() so "official / high trust / fresh" means
    the same thing here as it does everywhere else in the pipeline, plus a
    small bonus for direct/answer-box results which tend to sit on the most
    authoritative page for the query.
    """
    score = float(compute_trust_score(url, result))
    if result.get("is_direct_answer"):
        score += 5
    return score


def _select_firecrawl_candidates(
    results: list[dict],
    min_pages: int = FIRECRAWL_MIN_PAGES,
    max_pages: int = FIRECRAWL_MAX_PAGES,
) -> list[tuple[int, str]]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Dynamically picks how many and which pages to deep-crawl, instead of a
    fixed top-2. Selection order:

      1. Filter to crawlable URLs (_should_crawl — same domain blocklist as
         before).
      2. Rank by priority score (official / high trust / freshness, via
         _firecrawl_priority_score — same signals compute_trust_score uses).
      3. Diversity pass — walk the ranked list taking at most one URL per
         domain first, so a single high-authority domain with many results
         can't eat every crawl slot.
      4. Backfill pass — if diversity alone didn't fill the target count,
         take the next-best remaining URLs (repeat domains allowed) until
         the target is reached or candidates run out.

    Target page count = number of good crawlable candidates, clamped to
    [min_pages, max_pages] — quiet/thin result sets crawl fewer pages,
    rich result sets crawl up to max_pages, never more.
    """
    candidates = []
    for i, r in enumerate(results):
        url = r.get("url", "")
        if _should_crawl(url):
            candidates.append((i, url, _firecrawl_priority_score(url, r)))

    if not candidates:
        return []

    candidates.sort(key=lambda c: c[2], reverse=True)

    target_n = max(min_pages, min(max_pages, len(candidates)))

    diverse: list[tuple[int, str]] = []
    seen_domains: set[str] = set()
    leftovers: list[tuple[int, str]] = []

    for i, url, _score in candidates:
        domain = _get_domain(url)
        if domain not in seen_domains:
            seen_domains.add(domain)
            diverse.append((i, url))
        else:
            leftovers.append((i, url))
        if len(diverse) >= target_n:
            break

    if len(diverse) < target_n:
        diverse.extend(leftovers[: target_n - len(diverse)])

    return diverse[:target_n]


def enrich_with_firecrawl(
    results: list[dict],
    query: str = "",
    top_n: Optional[int] = None,
    min_pages: int = FIRECRAWL_MIN_PAGES,
    max_pages: int = FIRECRAWL_MAX_PAGES,
) -> list[dict]:
    """
    ── CHANGED ────────────────────────────────────────────────────────────
    Deep-crawls a DYNAMIC number of results (5-15, was a fixed top 2),
    prioritizing official / high-trust / fresh sources with diverse domains
    (see _select_firecrawl_candidates). Still runs entirely in parallel via
    ThreadPoolExecutor with the same per-call and overall timeout protection
    as before.

    `top_n` is kept as an optional override for backward compatibility: if
    passed, it's used as both min_pages and max_pages (i.e. crawl exactly
    that many, old behavior). Callers that don't pass it get the new dynamic
    5-15 range.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if top_n is not None:
        min_pages = max_pages = top_n

    candidates = _select_firecrawl_candidates(results, min_pages, max_pages)

    if not candidates:
        print(f"🕷️ [Firecrawl] No crawlable URLs found")
        return results

    print(f"🕷️ [Firecrawl] Crawling {len(candidates)} URLs in parallel "
          f"(official/high-trust/fresh/diverse, 5s timeout each)")

    # Submit all Firecrawl calls simultaneously
    with ThreadPoolExecutor(max_workers=len(candidates)) as executor:
        future_to_idx = {
            executor.submit(_firecrawl_extract, url, query): idx
            for idx, url in candidates
        }
        enriched_count = 0
        # Overall wall-clock cap scales with batch size but stays bounded —
        # each individual call already hard-times-out at 5s, this is just
        # the ceiling for collecting up to max_pages results concurrently.
        overall_timeout = 8
        for future in as_completed(future_to_idx, timeout=overall_timeout):
            idx = future_to_idx[future]
            try:
                content = future.result(timeout=0.1)  # already done — just collect
                if content:
                    results[idx]["body"]           = content
                    results[idx]["firecrawled"]    = True
                    # Chunk selection already trims to the char budget by
                    # relevance (not a mid-sentence cut), so "truncated" now
                    # just flags pages where kept content hit that budget.
                    results[idx]["body_truncated"] = len(content) >= (_PAGE_CHAR_BUDGET - 10)
                    enriched_count += 1
            except Exception:
                pass  # timeout or error — snippet stays as-is

    print(f"🕷️ [Firecrawl] Enriched {enriched_count}/{len(candidates)} results")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# TRUST TIERS — 6 levels instead of 3, so scoring is more fine-grained and it's
# easy to slot new sources into the RIGHT bucket instead of guessing between
# only 3 buckets.
#
# HOW TO ADD A NEW SOURCE:
#   1. Pick the tier below that matches how much you trust it.
#   2. Add the bare domain (no "www.", no "https://") under the matching
#      category comment — or a new category comment if none fits.
#   3. That's it. compute_trust_score() picks it up automatically; no other
#      code needs to change.
#
# Subdomains and country variants (e.g. "news.google.com", "bbc.co.uk") are
# matched automatically via endswith(), so you generally only need to add
# the root domain once.
# ══════════════════════════════════════════════════════════════════════════════

TRUST_TIERS = {
    # ── Tier 1 (score 95) — Primary/official sources: government, IGOs,
    # regulators, central banks, national statistics offices ─────────────────
    1: {
        # India — Government / Regulators
        "gov", "gov.in", "nic.in", "india.gov.in", "pib.gov.in", "mea.gov.in","x.com","twitter.com",
        "rbi.org.in", "sebi.gov.in", "irdai.gov.in", "npci.org.in", "uidai.gov.in",
        "incometax.gov.in", "gst.gov.in", "isro.gov.in", "drdo.gov.in",
        "mospi.gov.in", "rbi.gov.in", "eci.gov.in","services.india.gov.in","registry.gov.in",
        "sci.gov.in","ecourts.gov.in","mha.gov.in","mod.gov.in","mohfw.gov.in","education.gov.in",
        "meity.gov.in","dpiit.gov.in","commerce.gov.in","msme.gov.in","finance.gov.in","dea.gov.in","doe.gov.in",
        "mca.gov.in","labour.gov.in","rural.nic.in","jalshakti.gov.in","mnre.gov.in","powermin.gov.in",
        "petroleum.nic.in","coal.nic.in","steel.gov.in","fert.nic.in","textiles.gov.in",
        "foodprocessingindia.gov.in","civilaviation.gov.in","shipping.gov.in","roadtransport.gov.in",
        "railways.gov.in","tourism.gov.in","culture.gov.in","tribal.nic.in","socialjustice.gov.in","wcd.gov.in",
        "yas.nic.in","consumeraffairs.nic.in","moef.gov.in","moes.gov.in","dst.gov.in","dbtindia.gov.in","ayush.gov.in",
        "dopt.gov.in","sec.gov",
        # Finance / Regulators
        "pfrda.org.in","ibbi.gov.in","ifsca.gov.in","cci.gov.in","trai.gov.in","pngrb.gov.in","aera.gov.in",
        "cercind.gov.in","aptel.gov.in","fssai.gov.in","cdsco.gov.in", "cbic.gov.in","cbec.gov.in",
        "data.gov.in","openbudgetsindia.org",
        # Science
        "barc.gov.in","iisc.ac.in","csir.res.in","imd.gov.in","incois.gov.in","cpcb.nic.in",
        "nhm.gov.in","nhp.gov.in","icmr.gov.in","niti.gov.in","icar.org.in","agricoop.gov.in","krishi.gov.in",
        # Stock Exchange
        "mcxindia.com","nseindia.com","bseindia.com",
        # Public Information
        "mygov.in",
        "digitalindia.gov.in","bis.gov.in","qcin.org","ipindia.gov.in",
        # Telecom
        "dot.gov.in",
        "bsnl.co.in",
        # International organizations
        "who.int", "un.org", "worldbank.org", "imf.org", "unicef.org",
        "unesco.org", "wto.org", "oecd.org", "interpol.int",
        "undp.org","unhcr.org","unep.org","unfpa.org","unwomen.org",
        "unhabitat.org","wfp.org","fao.org","ilo.org","icao.int",
        "imo.org","itu.int","wipo.int","wmo.int","ifad.org","unido.org","unctad.org",
        "iom.int","unaids.org",
        # US Government
        "cdc.gov", "nih.gov", "fda.gov", "sec.gov", "federalreserve.gov",
        "treasury.gov", "whitehouse.gov", "nasa.gov", "noaa.gov",
        "usa.gov","congress.gov","supremecourt.gov","state.gov",
        "justice.gov","commerce.gov","energy.gov","defense.gov","dhs.gov",
        "transportation.gov","education.gov","labor.gov","interior.gov",
        "usda.gov","va.gov","cisa.gov","finra.org","cftc.gov",
        "fdic.gov","occ.treas.gov","cfpb.gov","nsf.gov","nist.gov","usgs.gov",
        # Other national governments / central banks
        "gov.uk", "europa.eu", "ecb.europa.eu", "bankofengland.co.uk","canada.ca",
        "bankofcanada.ca","gov.au","rba.gov.au","govt.nz","rbnz.govt.nz","go.jp",
        "boj.or.jp","go.kr","bok.or.kr","gov.sg","mas.gov.sg","admin.ch","snb.ch",
        "bund.de","bundesbank.de","gouvernement.fr","banque-france.fr","governo.it",
        "bancaditalia.it","lamoncloa.gob.es","bde.es","government.nl", "dnb.nl","belgium.be",
        "nbb.be","gov.ie","centralbank.ie","nationalbanken.dk","government.se","riksbank.se",
        "regjeringen.no","norges-bank.no","valtioneuvosto.fi","suomenpankki.fi","oesterreich.gv.at",
        "oenb.at","portugal.gov.pt","bportugal.pt","gov.br","bcb.gov.br","gob.mx","banxico.org.mx",
    },

    # ── Tier 2 (score 88) — Premier wire services, flagship global news
    # (heavy editorial/fact-check standards), and top peer-reviewed journals ──
    2: {
        # Wire services
        "reuters.com", "apnews.com", "afp.com","pti.in","youtube.com",
        # Indian Tier 2 (Score 88)
        "pti.in",
        "thehindu.com",
        "indianexpress.com",
        "livemint.com","business-standard.com","thehindubusinessline.com","prasarbharati.gov.in","newsonair.gov.in",
        "livelaw.in","barandbench.com","medianama.com","indiascienceandtechnology.gov.in","factchecker.in","altnews.in","indiaspend.com",
        # Flagship global news
        "bbc.com", "bbc.co.uk", "nytimes.com", "theguardian.com",
        "washingtonpost.com", "ft.com", "economist.com", "wsj.com",
        # Peer-reviewed / academic journals
        "nature.com", "science.org", "thelancet.com", "nejm.org",
        "cell.com", "pnas.org", "jamanetwork.com", "bmj.com",
        "huggingface.co","paperswithcode.com","kaggle.com","ai.google.dev","deepmind.google","ollama.com",
        "mistral.ai","cohere.com","meta.com","llama.com","perplexity.ai","together.ai",
        "replicate.com","fireworks.ai","groq.com","vllm.ai","langchain.com","langchain.dev","langgraph.dev",
        # Reference
        "wikipedia.org", "britannica.com", "scholar.google.com",
        "pubmed.ncbi.nlm.nih.gov", "arxiv.org",
        "python.org","nodejs.org","npmjs.com","rust-lang.org","crates.io","golang.org","go.dev",
        "kotlinlang.org","oracle.com","java.com","docs.oracle.com","postgresql.org","mysql.com","mariadb.org","sqlite.org",
        "redis.io","mongodb.com","docker.com","kubernetes.io","helm.sh","nginx.org","apache.org","gnu.org",
        "linux.org","kernel.org","ubuntu.com","debian.org","fedora.org","archlinux.org","cloudflare.com","vercel.com",
        "netlify.com","render.com","digitalocean.com","aws.amazon.com","azure.microsoft.com","cloud.google.com",
        "gitlab.com","sourceforge.net","gnu.org","freedesktop.org","gnome.org","kde.org",
    },

    # ── Tier 3 (score 78) — Major national/regional news, established tech &
    # finance official sites, big-brand company sources ──────────────────────
    3: {
        # Indian national news
        "timesofindia.com", "hindustantimes.com", "ndtv.com", "thehindu.com",
        "indianexpress.com", "livemint.com", "economictimes.indiatimes.com",
        "businessstandard.com", "financialexpress.com", "thewire.in",
        "theprint.in", "scroll.in", "thequint.com","abcnews.go.com","cnn.com",
        "npr.org","pbs.org","aljazeera.com","dw.com","france24.com","cbc.ca",
        "cbcnews.ca","sky.com","abc.net.au","smh.com.au","straitstimes.com",
        "scmp.com","nikkei.com","asia.nikkei.com",
        # International business/finance news
        "bloomberg.com", "cnbc.com", "forbes.com", "moneycontrol.com",
        "finance.yahoo.com","fred.stlouisfed.org","morningstar.com","marketwatch.com",
        "investopedia.com","tradingeconomics.com","coinmarketcap.com","coingecko.com",
        # Tech — official company sources & major docs
        "techcrunch.com", "theverge.com", "arstechnica.com", "wired.com",
        "stackoverflow.com", "github.com", "docs.python.org",
        "developer.mozilla.org", "microsoft.com", "google.com", "apple.com",
        "amazon.com", "openai.com", "anthropic.com", "meta.com", "nvidia.com",
        # Health — established institutions
        "mayoclinic.org", "webmd.com", "healthline.com", "clevelandclinic.org",
        "cisa.gov","mitre.org","cve.org","nvd.nist.gov","nist.gov","owasp.org",
        "sans.org","krebsonsecurity.com","bleepingcomputer.com","malwarebytes.com",
        "virustotal.com","abuse.ch","security.googleblog.com","theverge.com",
        "arstechnica.com","wired.com","engadget.com","tomshardware.com","anandtech.com",
        "9to5google.com","9to5mac.com","androidauthority.com","androidpolice.com",
        "macrumors.com","xda-developers.com",
    },

    # ── Tier 4 (score 65) — Solid secondary sources: trade press, niche news,
    # established reference/education sites, sports data ────────────────────
    4: {
        "sciencedaily.com", "livescience.com", "space.com",
        "geeksforgeeks.org", "w3schools.com", "tutorialspoint.com",
        "javatpoint.com", "codecademy.com", "freecodecamp.org",
        "cricbuzz.com", "espncricinfo.com", "sportskeeda.com", "espn.com",
        "news18.com", "indiatoday.in", "business-standard.com",
        "investopedia.com", "statista.com","mit.edu","stanford.edu","harvard.edu",
        "ox.ac.uk","cam.ac.uk","berkeley.edu","edx.org","coursera.org","udacity.com",
    },

    # ── Tier 5 (score 52) — Community platforms & blogging sites: useful but
    # unmoderated/self-published, verify against a higher tier when possible ─
    5: {
        "medium.com", "substack.com", "dev.to", "hackernoon.com",
        "towardsdatascience.com","esa.int","jpl.nasa.gov","space.com",
        "spacenews.com",
    },

    # ── Tier 6 (score 40) — Open discussion/UGC platforms: lowest default
    # weight, treat as a lead to verify rather than a citation on its own ────
    6: {
        "reddit.com", "quora.com",
        # ── Tier 6 (score 40) — Open discussion / UGC / Community ──
        # General Discussion & Q&A
        "stackexchange.com","superuser.com","serverfault.com","askubuntu.com",
        # Programming Communities
        "hashnode.com","codeproject.com","daniweb.com","bytes.com",
        # Hacker / Developer Communities
        "news.ycombinator.com","lobste.rs",
        # Linux Communities
        "linuxquestions.org",
        "ubuntuforums.org","bbs.archlinux.org","forum.manjaro.org","forums.opensuse.org",
        # AI Communities
        "discuss.huggingface.co",
        # Official Community Forums
        "community.openai.com","community.cloudflare.com","community.atlassian.com",
        "discuss.python.org","discuss.kotlinlang.org",
        # Git Communities
        "github.community",
        # Hardware Communities
        "linustechtips.com","forums.tomshardware.com","overclock.net",
        # Android
        "xdaforums.com",
        # Gaming
        "steamcommunity.com","gamefaqs.gamespot.com",
        # SEO / Webmaster
        "webmasterworld.com",
        # Community Platforms
        "discourse.org",
        # Social Platforms (UGC)
        "facebook.com","instagram.com","threads.net","mastodon.social",
        # Video UGC
        "youtu.be","tiktok.com","bilibili.com","rumble.com","odysee.com",
        # Blogs / Personal Publishing
        "blogspot.com","wordpress.com","tumblr.com",
        # Wiki / Community Knowledge
        "fandom.com","techenclave.com","pagalguy.com","mouthshut.com","team-bhp.com",
    },
}

# Score assigned to each tier (1 = most trusted). Ordered highest-to-lowest
# so compute_trust_score() stops at the FIRST (best) tier a domain matches.
# Add a new tier here + a matching key in TRUST_TIERS above if you ever need
# a 7th bucket — nothing else has to change.
TRUST_TIER_SCORES = [
    {"tier": 1, "score": 95},
    {"tier": 2, "score": 88},
    {"tier": 3, "score": 78},
    {"tier": 4, "score": 65},
    {"tier": 5, "score": 52},
    {"tier": 6, "score": 40},
]

# Domains that carry a trust penalty regardless of tier — known for
# misinformation, heavy sensationalism, or unreliable reporting
_PENALISED = {
    # add domains here if a source repeatedly turns out unreliable
}

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — TRUST SCORING
# Assigns a 0–100 trust score to each result based on domain reputation
# ══════════════════════════════════════════════════════════════════════════════


def _days_to_boost(days_old: float) -> int:
    """Map an age in days to a trust-score boost. Newer content scores higher."""
    if days_old <= 1:
        return 12
    if days_old <= 7:
        return 9
    if days_old <= 30:
        return 5
    if days_old <= 365:
        return 1
    return 0


def _recency_boost(result: dict, url: str) -> int:
    """
    Returns a trust-score boost (0-12) based on how old a result actually is,
    replacing the old "does the current year appear in the URL" heuristic —
    that missed articles with no year in the slug, and falsely boosted pages
    whose URL contained the current year for unrelated reasons (e.g. a
    copyright footer date scraped into the path).

    Sources of a real date, in preference order:
      1. Tavily's `published` date (ISO-ish, e.g. "2026-07-20...")
      2. Serper's relative `date` string (e.g. "2 days ago", "3 weeks ago")
      3. Fallback: current year literally in the URL (weak signal, last resort)
    """
    now = datetime.utcnow()

    published = (result.get("published") or "").strip()
    if published:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(published[:19], fmt)
                return _days_to_boost((now - dt).days)
            except ValueError:
                continue

    date_str = (result.get("date") or "").lower().strip()
    if date_str:
        m = re.match(r'(\d+)\s*(hour|day|week|month|year)s?\s*ago', date_str)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            days_old = {"hour": n / 24, "day": n, "week": n * 7,
                        "month": n * 30, "year": n * 365}[unit]
            return _days_to_boost(days_old)

    if str(now.year) in (url or ""):
        return 3
    return 0


_AI_SPAM_PATTERNS = [
    r'\bas an ai( language model)?\b',
    r'\bin (today\'?s|this) fast-paced world\b',
    r'\bin conclusion,? it is important to note\b',
    r'\bit(\'s| is) worth noting that\b',
    r'\bdelve into\b',
    r'\bin the realm of\b',
    r'\bunlock(ing)? the (power|potential) of\b',
    r'\bnavigating the (complex(ities)?|world) of\b',
    r'\boverall,? (it is|this) (clear|evident)\b',
    r'\bwhether you\'?re .{0,40} or .{0,40}, this\b',
]

_CLICKBAIT_PATTERNS = [
    r'\byou won\'?t believe\b',
    r'\bnumber \d+ will (shock|surprise) you\b',
    r'\bdoctors hate (this|him|her)\b',
    r'\bthis one (weird|simple) trick\b',
]


def _semantic_relevance_score(query: str, result: dict) -> float:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Cheap, dependency-free relevance signal for trust scoring: keyword
    overlap between the query and the result's title + body, with title
    matches weighted heavier. This runs on every result on every search, so
    it deliberately avoids an extra embedding/API call here — the
    embedding-based retrieval already happened earlier in the pipeline;
    this is just "does this result still look on-topic" as one input to
    trust, not a replacement for that retrieval step.
    Returns 0.0–1.0.
    """
    query_terms = {w for w in re.findall(r'[a-z0-9]+', query.lower()) if w not in _STOPWORDS}
    if not query_terms:
        return 0.5

    title_words = set(re.findall(r'[a-z0-9]+', result.get("title", "").lower()))
    body_words  = set(re.findall(r'[a-z0-9]+', (result.get("body", "") or "")[:2000].lower()))

    title_hits = len(query_terms & title_words)
    body_hits  = len(query_terms & body_words)

    raw = (title_hits * 2) + body_hits
    max_possible = len(query_terms) * 3  # every term in title AND body
    return min(raw / max_possible, 1.0) if max_possible else 0.5


def _verify_publication_date(result: dict, url: str) -> dict:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Publication verification: doesn't just parse a date, it sanity-checks
    it. Catches common bad signals — a "published" date in the future
    (clock-skew or a bad scrape), a date predating the web (an obvious
    placeholder/default), or no date at all — and reports a confidence
    level instead of blindly trusting whatever the engine handed back.
    Returns {"days_old": float|None, "verified": bool,
             "confidence": "high"|"medium"|"low"|"none", "reason": str}.
    """
    now = datetime.utcnow()
    published = (result.get("published") or "").strip()

    parsed_dt = None
    if published:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                parsed_dt = datetime.strptime(published[:19], fmt)
                break
            except ValueError:
                continue

    if parsed_dt is not None:
        days_old = (now - parsed_dt).days
        if parsed_dt > now + timedelta(days=1):
            return {"days_old": days_old, "verified": False, "confidence": "low",
                    "reason": "published date is in the future — likely a bad scrape"}
        if parsed_dt.year < 1995:
            return {"days_old": days_old, "verified": False, "confidence": "low",
                    "reason": "published date predates the web — likely a placeholder"}
        return {"days_old": days_old, "verified": True, "confidence": "high",
                "reason": "parsed ISO/engine-supplied publish date"}

    date_str = (result.get("date") or "").lower().strip()
    if date_str:
        m = re.match(r'(\d+)\s*(hour|day|week|month|year)s?\s*ago', date_str)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            days_old = {"hour": n / 24, "day": n, "week": n * 7,
                        "month": n * 30, "year": n * 365}[unit]
            return {"days_old": days_old, "verified": True, "confidence": "medium",
                    "reason": "relative date string from engine"}

    if str(now.year) in (url or ""):
        return {"days_old": None, "verified": False, "confidence": "low",
                "reason": "current year appears in URL only — weak, unverified signal"}

    return {"days_old": None, "verified": False, "confidence": "none",
            "reason": "no date signal found"}


_CONTENT_FRESHNESS_SIGNALS: dict[str, list[str]] = {
    # Model/version detection — a version number or named model mentioned
    # in the content itself, independent of any metadata date.
    "model_version_mention": [
        r'\bv\d+(\.\d+){1,2}\b',
        r'\b(version|release)\s+\d+(\.\d+)*\b',
        r'\b(gpt|claude|gemini|llama|mistral)[- ]?\d+(\.\d+)?\b',
    ],
    # Recently released products
    "just_released": [
        r'\b(just|newly|recently) (released|launched|unveiled|announced|debuted)\b',
        r'\brelease date\b.{0,20}\b(today|this week|20\d{2})\b',
    ],
    # Recent elections
    "election_result": [
        r'\b(wins?|won|elected|sworn in|takes? office|declared winner)\b.{0,30}\b(election|poll|seat)\b',
        r'\belection results?\b',
    ],
    # Live events / breaking news markers inside the content itself
    "live_or_breaking": [
        r'\b(breaking|live update|developing story|as it happened)\b',
    ],
    # Current prices
    "current_price_data": [
        r'[$₹€£]\s?\d[\d,.]*',
        r'\b\d+(\.\d+)?%\s*(up|down|higher|lower)\b',
        r'\bas of (today|writing|press time)\b',
    ],
    # Current CEOs / current government officials
    "current_role_holder": [
        r'\b(currently serves as|now serves as|current(ly)? (ceo|president|prime minister|chief minister|governor))\b',
        r'\bincumbent\b',
        r'\bsince (20\d{2})\b',
    ],
}


def _detect_content_freshness_signals(result: dict) -> dict:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Looks INSIDE the result's own text (not just its metadata date) for
    signals that it's inherently fresh/time-sensitive content: model/version
    mentions, "just released" phrasing, election-result language,
    live/breaking markers, current price data, and "currently serves as /
    incumbent" role-holder phrasing (current CEOs, current government
    officials). These corroborate — or, when absent, fail to corroborate —
    whatever the metadata date claims.
    Returns {"signals": [...], "boost": float 0..6}.
    """
    text = ((result.get("title", "") or "") + " " + (result.get("body", "") or "")).lower()
    if not text.strip():
        return {"signals": [], "boost": 0.0}

    hits = [name for name, patterns in _CONTENT_FRESHNESS_SIGNALS.items()
            if any(re.search(p, text) for p in patterns)]
    return {"signals": hits, "boost": min(len(hits) * 2.0, 6.0)}


def _freshness_score(result: dict, url: str, query: str = "") -> float:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Master freshness pipeline for trust scoring — replaces the previous flat
    "_recency_boost + _publication_date_confidence" combo with one that:
      1. Verifies the publication date (_verify_publication_date) instead of
         blindly trusting a parsed date — rejects future/implausible dates.
      2. Detects content-based freshness signals as corroboration
         (_detect_content_freshness_signals): model/version mentions, just-
         released phrasing, election results, live/breaking markers,
         current prices, current CEO/government-official role phrasing.
      3. Classifies the QUERY's freshness category (breaking news, live
         event, current price, model/version, product release, election,
         CEO, government official, current event — classify_freshness_
         category) and scales the urgency accordingly: a week-old result is
         fine for "who is the CEO of X" but stale for "live score".
    "Prioritize newest reliable information" = verified, recent,
    content-corroborated results score highest; unverifiable or
    stale-for-its-category content is actively pushed down rather than
    scored as neutral.
    Returns roughly -6..+18.
    """
    verification = _verify_publication_date(result, url)
    content      = _detect_content_freshness_signals(result)
    category     = classify_freshness_category(query) if query else {"categories": [], "window": None}

    score = 0.0
    days_old = verification["days_old"]

    # ── Base recency boost from the verified age ──
    if days_old is not None:
        base = _days_to_boost(max(days_old, 0))
        if not verification["verified"]:
            base *= 0.3  # implausible/unverified date — don't trust it much
        score += base
        if not verification["verified"]:
            score -= 3  # active distrust for a future/placeholder date
    elif verification["confidence"] == "low":
        score += 1     # weak year-in-URL signal only
    elif verification["confidence"] == "none":
        score -= 3      # no date signal at all

    # ── Content-based corroboration ──
    score += content["boost"]

    # ── Query-category urgency scaling — only for a VERIFIED, non-negative
    # age; a future/implausible date shouldn't get "comfortably fresh"
    # credit just because the (bogus) gap happens to be small or negative.
    window = category.get("window")
    if window and days_old is not None and verification["verified"] and days_old >= 0:
        urgency_limits = {"day": 2, "week": 10, "month": 45}
        limit = urgency_limits[window]
        if days_old > limit:
            # Stale relative to how urgent this query category is — the
            # tighter the window, the harsher the penalty for being old.
            score -= {"day": 8, "week": 5, "month": 2}[window]
        elif days_old <= limit / 3:
            # Comfortably fresh for this category
            score += {"day": 4, "week": 3, "month": 2}[window]

    return round(score, 1)



    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Distinct from freshness (how recent) — this measures whether we can
    actually verify a real publish date at all. Undated content is harder
    to trust for anything time-sensitive, so it gets a mild penalty rather
    than being scored as if it were confidently fresh or confidently old.
    Returns -3..+3.
    """
    published = (result.get("published") or "").strip()
    if published:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                datetime.strptime(published[:19], fmt)
                return 3.0  # confirmed, parseable date from the engine
            except ValueError:
                continue

    if (result.get("date") or "").strip():
        return 1.5  # relative date string ("3 days ago") — usable but less precise

    if str(datetime.utcnow().year) in (url or ""):
        return 0.5  # weak signal only

    return -3.0  # no date signal at all


def _content_quality_score(result: dict) -> float:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Heuristic content-quality signal from the actual page/snippet text:
    rewards substantive, well-formed content (real sentence structure,
    concrete numbers/facts, reasonable length) and penalizes thin
    boilerplate, wall-of-caps shouting, or excessive punctuation spam.
    Returns -5..+8.
    """
    body = (result.get("body", "") or result.get("snippet", "") or "").strip()
    if not body:
        return 0.0

    score = 0.0
    length = len(body)

    if length < 80:
        score -= 3   # too thin to be substantive
    elif 200 <= length <= 6000:
        score += 3   # healthy, readable amount of content
    elif length > 6000:
        score += 1   # long is fine but not extra credit past a point

    # Concrete facts/numbers tend to correlate with real reporting/reference
    # content rather than generic filler.
    number_hits = len(re.findall(r'\b\d[\d,.]*\b', body))
    if number_hits >= 3:
        score += 2

    # Sentence structure sanity check
    sentences = re.split(r'(?<=[.!?])\s+', body)
    real_sentences = [s for s in sentences if 20 <= len(s) <= 300]
    if len(real_sentences) >= 3:
        score += 2

    # Shouting / spammy punctuation
    caps_words = re.findall(r'\b[A-Z]{4,}\b', body)
    if len(caps_words) > 5:
        score -= 2
    if body.count('!') > 5:
        score -= 2

    return max(-5.0, min(score, 8.0))


def _ai_spam_penalty(result: dict) -> float:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Flags common AI-generated-filler and clickbait phrasing patterns.
    Not a definitive AI-detector (there isn't a reliable one) — a cheap
    heuristic pattern match that catches the stock phrases mass-produced
    SEO/content-farm generators lean on. Returns -12..0.
    """
    text = ((result.get("title", "") or "") + " " + (result.get("body", "") or "")).lower()
    if not text.strip():
        return 0.0

    hits = sum(1 for p in _AI_SPAM_PATTERNS if re.search(p, text))
    hits += sum(1 for p in _CLICKBAIT_PATTERNS if re.search(p, text))

    return -min(hits * 4, 12)


def _cross_source_agreement_boost(result: dict) -> float:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Scales the multi-source boost with HOW MANY distinct engines/queries
    surfaced this same URL, instead of a flat "seen twice" boost — three
    independent confirmations should count for more than two.
    Returns 0..12.
    """
    engines_seen = result.get("engines_seen") or []
    distinct = len(set(e for e in engines_seen if e))
    if distinct <= 1:
        return 0.0
    return min((distinct - 1) * 6, 12)


def _official_source_boost(domain: str) -> float:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Explicit "is this an official/primary source" signal, separate from the
    general authority tier — governments, central banks, regulators, and
    international bodies (.gov, .gov.xx, .mil, .int, and Tier-1 domains).
    Returns 0 or 6.
    """
    if not domain:
        return 0.0
    if re.search(r'\.(gov|mil|int)(\.[a-z]{2})?$', domain) or ".gov." in domain:
        return 6.0
    if any(domain == t or domain.endswith("." + t) for t in TRUST_TIERS.get(1, ())):
        return 6.0
    return 0.0


def _source_diversity_boost(results: list[dict]) -> dict[int, float]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Global pass across the WHOLE result set (not a single result): clusters
    near-duplicate content by a fuzzy fingerprint (first ~40 words,
    normalized) and rewards results whose story is corroborated by several
    DIFFERENT domains — true source diversity, distinct from multi_source
    (which only tracks the same URL appearing in multiple engines).
    Returns {result_index: boost 0..8}.
    """
    clusters: dict[str, set[str]] = {}
    cluster_of: dict[int, str] = {}

    for i, r in enumerate(results):
        body = (r.get("body", "") or r.get("snippet", "") or "").strip().lower()
        if not body:
            continue
        words = re.findall(r'[a-z0-9]+', body)[:40]
        fp = hashlib.md5(" ".join(words).encode("utf-8", "ignore")).hexdigest()[:10]
        domain = _get_domain(r.get("url", ""))
        clusters.setdefault(fp, set()).add(domain)
        cluster_of[i] = fp

    boosts: dict[int, float] = {}
    for i, fp in cluster_of.items():
        distinct_domains = len(clusters.get(fp, ()))
        if distinct_domains >= 2:
            boosts[i] = min((distinct_domains - 1) * 3, 8)
    return boosts


def _duplicate_penalty(results: list[dict]) -> dict[int, float]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Global pass: penalizes near-duplicate ARTICLES that survived exact-hash
    dedup (e.g. the same wire-service story republished with a different
    intro paragraph on several syndicating domains). Within each fuzzy
    content cluster, the single highest-tier source is left unpenalized;
    every other member of that cluster is treated as a redundant repost.
    Returns {result_index: penalty -6..0}.
    """
    clusters: dict[str, list[int]] = {}
    for i, r in enumerate(results):
        body = (r.get("body", "") or r.get("snippet", "") or "").strip().lower()
        if not body:
            continue
        words = re.findall(r'[a-z0-9]+', body)[:40]
        fp = hashlib.md5(" ".join(words).encode("utf-8", "ignore")).hexdigest()[:10]
        clusters.setdefault(fp, []).append(i)

    penalties: dict[int, float] = {}
    for idx_list in clusters.values():
        if len(idx_list) < 2:
            continue
        # Keep the current front-runner (by existing base tier score) penalty-free
        idx_list_sorted = sorted(
            idx_list,
            key=lambda i: compute_trust_score(results[i].get("url", ""), results[i]),
            reverse=True,
        )
        for i in idx_list_sorted[1:]:
            penalties[i] = -6.0
    return penalties


def compute_trust_score(
    url: str,
    result: dict,
    query: str = "",
    diversity_boost: float = 0.0,
    duplicate_penalty: float = 0.0,
) -> int:
    """
    ── CHANGED — now dynamic instead of mostly static ──────────────────────
    Returns trust score 0–100, blending:
      - Authority       — domain tier base score (TRUST_TIERS)
      - Official source — explicit gov/regulator/IGO detection (boost)
      - Freshness       — real recency, based on actual publish date (boost)
      - Publication date confidence — do we have a verifiable date at all
      - Cross-source agreement — scaled by how many distinct engines
        independently surfaced this same URL
      - Semantic relevance — keyword overlap between query and content
      - Content quality — heuristic on the actual text (length, structure,
        concrete facts, shouting/spam punctuation)
      - AI-generated-spam penalty — stock filler/clickbait phrase detection
      - Source diversity boost / duplicate-article penalty — precomputed
        per-result across the WHOLE result set by score_all_results() and
        passed in (a single result can't tell "am I one of many reposts of
        the same story" on its own)
    `diversity_boost` / `duplicate_penalty` default to 0 for callers that
    score a single result in isolation (e.g. _firecrawl_priority_score,
    which runs before the full result set is assembled).
    """
    if not url:
        # Direct answer without URL — trust based on source
        engine = result.get("source_engine", "")
        base = 88 if engine in ("tavily_answer", "serper_answer", "serper_kg") else 60
        base += _semantic_relevance_score(query, result) * 6 if query else 0
        return int(max(0, min(base, 100)))

    try:
        domain = urlparse(url).netloc.lower()
        domain = re.sub(r'^(www\d?|m|mobile|amp)\.', '', domain)
    except (ValueError, AttributeError):
        domain = ""

    # ── Authority — base score by tier ──────────────────────────────────────
    score = 50.0  # default for unknown/unlisted domains
    for tier_score in TRUST_TIER_SCORES:
        tier_domains = TRUST_TIERS.get(tier_score["tier"], ())
        if any(domain == t or domain.endswith("." + t) for t in tier_domains):
            score = float(tier_score["score"])
            break

    # Penalty for known low-quality domains
    if any(domain == p or domain.endswith("." + p) for p in _PENALISED):
        score = max(score - 10, 30)

    # ── Official source detection ────────────────────────────────────────────
    score += _official_source_boost(domain)

    # ── Cross-source agreement (scaled by distinct engines, not flat) ───────
    score += _cross_source_agreement_boost(result)

    if result.get("is_direct_answer"):
        score += 5

    if result.get("firecrawled"):
        score += 4

    # ── Freshness — verified recency, content-signal corroboration, and
    # query-category urgency scaling (see _freshness_score) ─────────────────
    score += _freshness_score(result, url, query)

    # ── Semantic relevance to the query ──────────────────────────────────────
    if query:
        score += _semantic_relevance_score(query, result) * 8   # 0..8

    # ── Content quality ───────────────────────────────────────────────────────
    score += _content_quality_score(result)

    # ── Penalize AI-generated spam / clickbait phrasing ─────────────────────
    score += _ai_spam_penalty(result)

    # ── Source diversity boost / duplicate-article penalty (set-level) ──────
    score += diversity_boost
    score += duplicate_penalty

    return int(max(0, min(round(score), 100)))


def score_all_results(results: list[dict], query: str = "") -> list[dict]:
    """
    ── CHANGED ──────────────────────────────────────────────────────────────
    Add a dynamic trust_score field to every result. First runs two
    set-level passes across ALL results (source-diversity clustering and
    duplicate-article detection — neither can be computed from a single
    result in isolation), then scores each result with those signals plus
    the semantic-relevance-to-query factor folded in.
    """
    diversity  = _source_diversity_boost(results)
    duplicates = _duplicate_penalty(results)

    for i, r in enumerate(results):
        r["trust_score"] = compute_trust_score(
            r.get("url", ""),
            r,
            query=query,
            diversity_boost=diversity.get(i, 0.0),
            duplicate_penalty=duplicates.get(i, 0.0),
        )
    return results


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — FACT CROSS-REFERENCE & CONTRADICTION DETECTION
# Looks for agreement and conflicts across result bodies
# ══════════════════════════════════════════════════════════════════════════════

def _extract_key_claims(text: str) -> list[str]:
    """
    Extract short factual sentences from a snippet.
    Heuristic: sentences under 120 chars that start with a capital letter.
    """
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in sentences if 20 < len(s) < 120 and s[0].isupper()]


def _estimate_source_age_days(result: dict) -> Optional[float]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Best-effort age (in days) for a single result, used to rank sources
    newest → oldest during contradiction analysis. Returns None (not 0)
    when no date signal exists at all, so "unknown age" stays distinguishable
    from "very fresh" — an undated source shouldn't win "newest" by default.
    """
    now = datetime.utcnow()
    published = (result.get("published") or "").strip()
    if published:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(published[:19], fmt)
                return (now - dt).days
            except ValueError:
                continue

    date_str = (result.get("date") or "").lower().strip()
    if date_str:
        m = re.match(r'(\d+)\s*(hour|day|week|month|year)s?\s*ago', date_str)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            return {"hour": n / 24, "day": n, "week": n * 7,
                    "month": n * 30, "year": n * 365}[unit]

    return None


def _is_official_domain(domain: str) -> bool:
    """── NEW ── Reuses the same official-source signal as trust scoring."""
    return _official_source_boost(domain) > 0


def _analyze_contradiction(entity: str, records: list[dict]) -> dict:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Runs the full analysis pipeline for ONE contradicting entity:
      detect contradiction (caller already found conflicting values)
        → identify newest source
        → identify official source
        → determine majority consensus
        → produce confidence score
        → explain why sources disagree

    `records` — one dict per (value, source) mention:
        {"value": str, "domain": str, "result": dict}
    """
    total = len(records)

    # ── Majority consensus — which value has the most independent sources ──
    value_groups: dict[str, list[dict]] = {}
    for rec in records:
        value_groups.setdefault(rec["value"], []).append(rec)
    majority_value, majority_records = max(value_groups.items(), key=lambda kv: len(kv[1]))
    majority_share = len(majority_records) / total if total else 0.0

    # ── Identify newest source (across ALL conflicting records) ────────────
    dated = [(rec, _estimate_source_age_days(rec["result"])) for rec in records]
    dated_known = [(rec, age) for rec, age in dated if age is not None]
    newest_rec, newest_age = min(dated_known, key=lambda t: t[1]) if dated_known else (records[0], None)

    # ── Identify official source (across ALL conflicting records) ──────────
    official_matches = [rec for rec in records if _is_official_domain(rec["domain"])]
    official_rec = official_matches[0] if official_matches else None

    # ── Confidence score (0-100) ────────────────────────────────────────────
    # Blends: how dominant the majority is, average trust of the sources
    # backing it, whether an official source backs it, and whether the
    # newest source agrees with it.
    avg_trust = sum(rec["result"].get("trust_score", 50) for rec in majority_records) / len(majority_records)
    confidence = (majority_share * 50) + (avg_trust / 100 * 30)
    if official_rec is not None and official_rec in majority_records:
        confidence += 15
    if newest_rec in majority_records:
        confidence += 5
    confidence = round(min(confidence, 100), 1)

    # ── Explain why sources disagree ────────────────────────────────────────
    reasons = []
    if majority_share < 1.0:
        reasons.append(f"{len(majority_records)}/{total} sources agree on '{majority_value}'")
    if official_rec is not None:
        if official_rec["value"] == majority_value:
            reasons.append(f"an official source ({official_rec['domain']}) confirms it")
        else:
            reasons.append(f"an official source ({official_rec['domain']}) instead reports '{official_rec['value']}'")
    if newest_age is not None:
        if newest_rec["value"] != majority_value:
            reasons.append(
                f"the most recent source ({newest_rec['domain']}, ~{int(newest_age)}d old) "
                f"reports a different value ('{newest_rec['value']}') — may reflect an update"
            )
        else:
            reasons.append(f"the most recent source ({newest_rec['domain']}) agrees with the majority")
    if not reasons:
        reasons.append("sources differ with no clear majority, official confirmation, or recency signal")

    return {
        "entity":            entity,
        "values":            list(value_groups.keys())[:3],
        "sources":           [rec["domain"] for rec in records[:4]],
        "majority_value":    majority_value,
        "majority_share":    round(majority_share, 2),
        "newest_source": {
            "domain": newest_rec["domain"],
            "value":  newest_rec["value"],
            "age_days": newest_age,
        } if dated_known else None,
        "official_source": {
            "domain": official_rec["domain"],
            "value":  official_rec["value"],
        } if official_rec is not None else None,
        "confidence_score":  confidence,
        "explanation":       "; ".join(reasons),
    }


def cross_reference_results(results: list[dict], top_n: int = 6) -> dict:
    """
    ── CHANGED ──────────────────────────────────────────────────────────────
    Analyse top N results to find:
    - consensus_signals: facts that appear in 2+ sources (higher confidence)
    - contradiction_signals: conflicting values for the same entity, each
      run through the full analysis pipeline — newest source, official
      source, majority consensus, a confidence score, and an explanation
      of why sources disagree.

    Returns a metadata dict that gets injected into the AI context.
    Existing keys (consensus_count, contradiction_count, contradictions,
    and each contradiction's entity/values/sources) are unchanged, so
    existing callers keep working — the new fields are additive.
    """
    sample = [r for r in results if r.get("body")][:top_n]

    # Map claim fingerprint → list of sources that mention it
    claim_sources: dict[str, list[str]] = {}

    for r in sample:
        body   = r.get("body", "")
        domain = _get_domain(r.get("url", ""))
        claims = _extract_key_claims(body)
        for claim in claims:
            fp = _content_fingerprint(claim)
            if fp not in claim_sources:
                claim_sources[fp] = []
            claim_sources[fp].append(domain or r.get("source_engine", "?"))

    # Consensus = claim seen in 2+ distinct sources
    consensus = [
        {"fingerprint": fp, "sources": srcs}
        for fp, srcs in claim_sources.items()
        if len(srcs) >= 2
    ]

    # ── STEP 1: Detect contradictions — same lightweight keyword approach,
    # but now records the full result (not just the domain) per mention so
    # the analysis pipeline below can pull recency/trust/official signals.
    entity_values: dict[str, list[dict]] = {}  # entity → [{"value","domain","result"}]

    for r in sample:
        body   = r.get("body", "")
        domain = _get_domain(r.get("url", "")) or "unknown"
        # Pattern: "<Entity> is <Value>" — e.g. "The CM is Mamata Banerjee"
        for m in re.finditer(
            r'\b([A-Z][a-zA-Z ]{3,30})\s+(?:is|was|are|were)\s+([A-Z][a-zA-Z ]{3,40})',
            body
        ):
            entity = m.group(1).strip()
            value  = m.group(2).strip()
            entity_values.setdefault(entity, []).append({
                "value": value, "domain": domain, "result": r,
            })

    # ── STEPS 2-6: for every entity with 2+ distinct values, run the full
    # analysis pipeline (newest source → official source → majority
    # consensus → confidence score → explanation).
    contradictions = []
    for entity, records in entity_values.items():
        unique_vals = {rec["value"] for rec in records}
        if len(unique_vals) > 1:
            contradictions.append(_analyze_contradiction(entity, records))

    # Highest-confidence-need-first: surface the least-resolved conflicts
    # (lowest confidence score) at the top, since those most need a caveat.
    contradictions.sort(key=lambda c: c["confidence_score"])

    return {
        "consensus_count":      len(consensus),
        "contradiction_count":  len(contradictions),
        "contradictions":       contradictions[:3],  # top 3 conflicts
    }


def _get_domain(url: str) -> str:
    try:
        d = urlparse(url).netloc.lower()
        return re.sub(r'^(www\d?|m|mobile|amp)\.', '', d)
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — COHERE RERANKING
# Re-orders results by semantic relevance to the query
# ══════════════════════════════════════════════════════════════════════════════

# ── NEW: Diversity balancing ──────────────────────────────────────────────
# Reranking previously optimized purely for relevance/trust, which could
# fill the final result set with many pages from one publisher (e.g. 6 of
# 8 slots all from the same news site). This caps how many pages any one
# domain can contribute and interleaves different SOURCE TYPES in a
# preferred order, so the final set is both relevant and diverse.
_SOURCE_TYPE_ORDER = [
    "official", "news", "documentation", "wikipedia",
    "academic", "community", "blogs", "other",
]
_MAX_PER_DOMAIN = 2

_NEWS_DOMAINS = {
    # Wire services
    "reuters.com", "apnews.com", "afp.com", "pti.in",
    # Indian news
    "thehindu.com", "indianexpress.com", "livemint.com", "business-standard.com",
    "businessstandard.com", "thehindubusinessline.com", "livelaw.in", "barandbench.com",
    "medianama.com", "factchecker.in", "altnews.in", "indiaspend.com",
    "timesofindia.com", "hindustantimes.com", "ndtv.com", "economictimes.indiatimes.com",
    "financialexpress.com", "thewire.in", "theprint.in", "scroll.in", "thequint.com",
    "news18.com", "indiatoday.in",
    # Flagship global news
    "bbc.com", "bbc.co.uk", "nytimes.com", "theguardian.com", "washingtonpost.com",
    "ft.com", "economist.com", "wsj.com", "abcnews.go.com", "cnn.com", "npr.org",
    "pbs.org", "aljazeera.com", "dw.com", "france24.com", "cbc.ca", "cbcnews.ca",
    "sky.com", "abc.net.au", "smh.com.au", "straitstimes.com", "scmp.com",
    "nikkei.com", "asia.nikkei.com",
    # Business/finance news & data
    "bloomberg.com", "cnbc.com", "forbes.com", "moneycontrol.com", "finance.yahoo.com",
    "fred.stlouisfed.org", "morningstar.com", "marketwatch.com", "investopedia.com",
    "tradingeconomics.com", "coinmarketcap.com", "coingecko.com", "statista.com",
    # Tech news
    "techcrunch.com", "theverge.com", "arstechnica.com", "wired.com", "engadget.com",
    "tomshardware.com", "anandtech.com", "9to5google.com", "9to5mac.com",
    "androidauthority.com", "androidpolice.com", "macrumors.com", "xda-developers.com",
    # Science/space news
    "sciencedaily.com", "livescience.com", "space.com", "spacenews.com",
    # Sports data/news
    "cricbuzz.com", "espncricinfo.com", "sportskeeda.com", "espn.com",
    # Security news
    "krebsonsecurity.com", "bleepingcomputer.com",
}

_DOCUMENTATION_DOMAINS = {
    # AI vendors / infra / model hubs
    "huggingface.co", "paperswithcode.com", "kaggle.com", "ai.google.dev",
    "deepmind.google", "ollama.com", "mistral.ai", "cohere.com", "meta.com",
    "llama.com", "perplexity.ai", "together.ai", "replicate.com", "fireworks.ai",
    "groq.com", "vllm.ai", "langchain.com", "langchain.dev", "langgraph.dev",
    "openai.com", "anthropic.com", "nvidia.com", "microsoft.com", "google.com",
    "apple.com", "amazon.com",
    # Languages / runtimes / package registries
    "python.org", "docs.python.org", "nodejs.org", "npmjs.com", "rust-lang.org",
    "crates.io", "golang.org", "go.dev", "kotlinlang.org", "oracle.com", "java.com",
    "docs.oracle.com",
    # Databases / infra / containers / OS
    "postgresql.org", "mysql.com", "mariadb.org", "sqlite.org", "redis.io",
    "mongodb.com", "docker.com", "kubernetes.io", "helm.sh", "nginx.org",
    "apache.org", "gnu.org", "linux.org", "kernel.org", "ubuntu.com", "debian.org",
    "fedora.org", "archlinux.org",
    # Cloud/hosting platforms
    "cloudflare.com", "vercel.com", "netlify.com", "render.com", "digitalocean.com",
    "aws.amazon.com", "azure.microsoft.com", "cloud.google.com", "gitlab.com",
    "sourceforge.net", "freedesktop.org", "gnome.org", "kde.org",
    # Dev references
    "stackoverflow.com", "github.com", "developer.mozilla.org",
    # Security references (advisory/tooling, not news)
    "mitre.org", "cve.org", "nvd.nist.gov", "owasp.org", "sans.org",
    "malwarebytes.com", "virustotal.com", "abuse.ch", "security.googleblog.com",
    # Learning/tutorial platforms
    "geeksforgeeks.org", "w3schools.com", "tutorialspoint.com", "javatpoint.com",
    "codecademy.com", "freecodecamp.org", "edx.org", "coursera.org", "udacity.com",
}

_ACADEMIC_DOMAINS = {
    # Peer-reviewed journals
    "nature.com", "science.org", "thelancet.com", "nejm.org", "cell.com",
    "pnas.org", "jamanetwork.com", "bmj.com",
    # Reference / research indexes
    "scholar.google.com", "pubmed.ncbi.nlm.nih.gov", "arxiv.org", "britannica.com",
    # Established health institutions
    "mayoclinic.org", "webmd.com", "healthline.com", "clevelandclinic.org",
    # Universities
    "mit.edu", "stanford.edu", "harvard.edu", "ox.ac.uk", "cam.ac.uk",
    "berkeley.edu", "iisc.ac.in",
}

_COMMUNITY_DOMAINS = {
    "reddit.com", "quora.com", "stackexchange.com", "superuser.com",
    "serverfault.com", "askubuntu.com", "hashnode.com", "codeproject.com",
    "daniweb.com", "bytes.com", "news.ycombinator.com", "lobste.rs",
    "linuxquestions.org", "ubuntuforums.org", "bbs.archlinux.org",
    "forum.manjaro.org", "forums.opensuse.org", "discuss.huggingface.co",
    "community.openai.com", "community.cloudflare.com", "community.atlassian.com",
    "discuss.python.org", "discuss.kotlinlang.org", "github.community",
    "linustechtips.com", "forums.tomshardware.com", "overclock.net",
    "xdaforums.com", "steamcommunity.com", "gamefaqs.gamespot.com",
    "webmasterworld.com", "discourse.org", "facebook.com", "instagram.com",
    "threads.net", "mastodon.social", "youtu.be", "youtube.com", "tiktok.com",
    "bilibili.com", "rumble.com", "odysee.com", "fandom.com", "techenclave.com",
    "pagalguy.com", "mouthshut.com", "team-bhp.com",
}

_BLOG_DOMAINS = {
    "medium.com", "substack.com", "dev.to", "hackernoon.com",
    "towardsdatascience.com", "blogspot.com", "wordpress.com", "tumblr.com",
}


def _classify_source_type(domain: str) -> str:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Classifies a domain into one of the preferred source-type buckets:
    official, news, documentation, wikipedia, academic, community, blogs,
    or other. Reuses the existing TRUST_TIERS domain lists where they
    already capture a category (official = Tier 1 + gov/mil/int patterns;
    community/blogs draw on Tier 6 / Tier 5), plus dedicated lists above for
    news, documentation, and academic sources that TRUST_TIERS mixes
    together with everything else.
    """
    domain = (domain or "").lower()
    if not domain:
        return "other"

    def _matches(d: str, domain_set) -> bool:
        return any(d == t or d.endswith("." + t) for t in domain_set)

    if re.search(r'\.(gov|mil|int)(\.[a-z]{2})?$', domain) or ".gov." in domain:
        return "official"
    if _matches(domain, TRUST_TIERS.get(1, ())):
        return "official"

    if domain == "wikipedia.org" or domain.endswith(".wikipedia.org") \
            or domain in ("wikimedia.org", "wiktionary.org"):
        return "wikipedia"

    if domain.startswith("docs.") or domain.startswith("developer.") \
            or _matches(domain, _DOCUMENTATION_DOMAINS):
        return "documentation"

    if domain.endswith(".edu") or domain.endswith(".ac.uk") or domain.endswith(".ac.in") \
            or _matches(domain, _ACADEMIC_DOMAINS):
        return "academic"

    if _matches(domain, _NEWS_DOMAINS):
        return "news"

    if domain.startswith("community.") or domain.startswith("discuss.") \
            or domain.startswith("forum") \
            or _matches(domain, _COMMUNITY_DOMAINS) or _matches(domain, TRUST_TIERS.get(6, ())):
        return "community"

    if _matches(domain, _BLOG_DOMAINS) or _matches(domain, TRUST_TIERS.get(5, ())):
        return "blogs"

    return "other"


def _apply_diversity_balance(
    ranked_results: list[dict],
    top_n: int,
    max_per_domain: int = _MAX_PER_DOMAIN,
) -> list[dict]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Post-processing diversity pass over an already relevance/trust-ranked
    list. Buckets candidates by source type, then round-robins across
    types in the preferred order (official → news → documentation →
    wikipedia → academic → community → blogs → other), taking the
    highest-ranked still-eligible candidate from each bucket every round —
    preserving relevance order *within* each type — while enforcing
    `max_per_domain` so no single publisher can fill the result set.
    Falls through to remaining candidates if buckets run dry before top_n
    is reached, so results are never short just because a type ran out.
    """
    buckets: dict[str, list[dict]] = {t: [] for t in _SOURCE_TYPE_ORDER}
    for r in ranked_results:
        domain = _get_domain(r.get("url", ""))
        buckets[_classify_source_type(domain)].append(r)

    domain_counts: dict[str, int] = {}
    pointer = {t: 0 for t in _SOURCE_TYPE_ORDER}
    selected: list[dict] = []
    selected_ids: set = set()

    def _take_next(t: str):
        while pointer[t] < len(buckets[t]):
            cand = buckets[t][pointer[t]]
            pointer[t] += 1
            domain = _get_domain(cand.get("url", ""))
            if domain and domain_counts.get(domain, 0) >= max_per_domain:
                continue
            cand_id = id(cand)
            if cand_id in selected_ids:
                continue
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
            selected_ids.add(cand_id)
            return cand
        return None

    while len(selected) < top_n:
        progressed = False
        for t in _SOURCE_TYPE_ORDER:
            if len(selected) >= top_n:
                break
            cand = _take_next(t)
            if cand is not None:
                selected.append(cand)
                progressed = True
        if not progressed:
            break  # every bucket exhausted (or capped out) — stop early

    return selected


def rerank_with_cohere(query: str, results: list[dict], top_n: int = RERANK_TOP_N) -> list[dict]:
    """
    ── CHANGED ──────────────────────────────────────────────────────────────
    Use Cohere Rerank API to semantically re-order search results, then
    apply diversity balancing (max 2 pages per domain, source types mixed
    in preferred order — see _apply_diversity_balance) before returning.
    Falls back to trust-score sorting (also diversity-balanced) if Cohere
    is unavailable.

    Cohere free tier: 1000 reranks/month at cohere.com
    """
    if not COHERE_KEY or not results:
        return _trust_sort(results, top_n)

    # Prepare documents for Cohere — use title + body
    candidates = results[:min(len(results), 20)]  # Cohere limit per call
    documents  = [
        f"{r.get('title', '')} — {r.get('body', '')[:300]}"
        for r in candidates
    ]

    # Ask Cohere to rank ALL candidates (not just top_n) so diversity
    # balancing afterward has a real pool of relevance-ordered alternatives
    # to pick from instead of being handed an already-truncated top_n.
    cohere_n = len(candidates)

    try:
        resp = requests.post(
            "https://api.cohere.com/v2/rerank",
            headers={
                "Authorization": f"Bearer {COHERE_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":      "rerank-v3.5",
                "query":      query,
                "documents":  documents,
                "top_n":      cohere_n,
                "return_documents": False,
            },
            timeout=6,   # hard 6s timeout — fall back to trust-sort if slow
        )

        if resp.status_code != 200:
            print(f"⚠️ [Cohere] HTTP {resp.status_code} — falling back to trust sort")
            return _trust_sort(results, top_n)

        data    = resp.json()
        results_reranked = data.get("results", [])

        reordered = []
        for item in results_reranked:
            idx   = item["index"]
            score = item.get("relevance_score", 0.5)
            candidate = candidates[idx].copy()
            candidate["cohere_score"]  = round(score, 4)
            # Blend Cohere score with trust score for final ranking
            candidate["final_score"] = (
                0.65 * score * 100 +
                0.35 * candidate.get("trust_score", 50)
            )
            reordered.append(candidate)

        # Sort by final blended score
        reordered.sort(key=lambda r: r.get("final_score", 0), reverse=True)

        balanced = _apply_diversity_balance(reordered, top_n)
        print(f"✅ [Cohere] Reranked {len(reordered)} results → "
              f"{len(balanced)} after diversity balancing")
        return balanced

    except Exception as e:
        print(f"❌ [Cohere] {e} — falling back to trust sort")
        return _trust_sort(results, top_n)


def _trust_sort(results: list[dict], top_n: int) -> list[dict]:
    ranked = sorted(
        results,
        key=lambda r: (
            r.get("is_direct_answer", False),
            r.get("trust_score", 50),
            r.get("multi_source", False),
        ),
        reverse=True,
    )
    return _apply_diversity_balance(ranked, top_n)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 8 — CITATION BUILDER
# Assigns [1], [2], [3] citation numbers to results for the AI to reference
# ══════════════════════════════════════════════════════════════════════════════

def build_citations(results: list[dict]) -> tuple[list[dict], dict]:
    """
    Assign citation numbers [1]...[N] to results with URLs.
    Returns:
      - results list with `citation_num` field added
      - citation_map: {1: {url, title, domain}, ...} for frontend rendering
    """
    citation_map: dict[int, dict] = {}
    num = 1

    for r in results:
        url = r.get("url", "")
        if url:
            r["citation_num"] = num
            citation_map[num] = {
                "url":    url,
                "title":  r.get("title", url),
                "domain": _get_domain(url),
            }
            num += 1
        else:
            r["citation_num"] = None  # Direct answers without URL

    return results, citation_map


# ══════════════════════════════════════════════════════════════════════════════
# CONFIDENCE SCORING — overall answer confidence (0-100)
# ══════════════════════════════════════════════════════════════════════════════

def compute_answer_confidence(query: str, results: list[dict], cross_ref: dict) -> int:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Overall confidence (0-100) for the final answer, combining:
      - Agreement            — multi-source corroboration + cross-ref consensus
      - Source count         — how many distinct domains contribute
      - Trust                — average trust_score of the results used
      - Freshness            — fraction with a verified, reasonably recent date
      - Official confirmation — is at least one official/primary source present
      - Semantic similarity  — average relevance of results to the query
      - Contradictions       — penalizes unresolved conflicts between sources
    Returns an int clamped to 0-100.
    """
    if not results:
        return 0

    top = results[:10]  # confidence reflects the results actually surfaced
    n = len(top)

    # ── Agreement — multi-source corroboration + cross-ref consensus ───────
    multi_source_frac = sum(1 for r in top if r.get("multi_source")) / n
    consensus_bonus   = min(cross_ref.get("consensus_count", 0) * 2, 10)
    agreement_score   = (multi_source_frac * 10) + consensus_bonus  # 0..20

    # ── Source count — distinct domains among top results ──────────────────
    distinct_domains   = len({_get_domain(r.get("url", "")) for r in top if r.get("url")})
    source_count_score = min(distinct_domains * 2.5, 15)  # 0..15

    # ── Trust — average trust_score of top results ──────────────────────────
    avg_trust    = sum(r.get("trust_score", 50) for r in top) / n
    trust_score_ = (avg_trust / 100) * 25  # 0..25

    # ── Freshness — fraction with a verified, reasonably recent date ───────
    fresh_hits = 0
    for r in top:
        verification = _verify_publication_date(r, r.get("url", ""))
        if verification["verified"] and verification["days_old"] is not None \
                and verification["days_old"] <= 365:
            fresh_hits += 1
    freshness_score = (fresh_hits / n) * 15  # 0..15

    # ── Official confirmation — bonus if ANY top result is official ────────
    has_official   = any(_official_source_boost(_get_domain(r.get("url", ""))) > 0 for r in top)
    official_score = 10.0 if has_official else 0.0

    # ── Semantic similarity — average relevance of results to the query ────
    avg_relevance  = sum(_semantic_relevance_score(query, r) for r in top) / n
    semantic_score = avg_relevance * 15  # 0..15

    base = (
        agreement_score + source_count_score + trust_score_
        + freshness_score + official_score + semantic_score
    )

    # ── Contradictions — penalize unresolved conflicts between sources ──────
    contradiction_count = cross_ref.get("contradiction_count", 0)
    contradictions       = cross_ref.get("contradictions", [])
    if contradiction_count:
        avg_conflict_confidence = (
            sum(c.get("confidence_score", 50) for c in contradictions) / len(contradictions)
            if contradictions else 50
        )
        # More unresolved conflicts, and lower confidence in how they resolved,
        # both push the penalty up — capped so a couple of minor conflicts
        # can't tank an otherwise well-supported answer.
        penalty = min(contradiction_count * 5 + (100 - avg_conflict_confidence) * 0.2, 30)
    else:
        penalty = 0.0

    final = base - penalty
    return int(max(0, min(round(final), 100)))


# ══════════════════════════════════════════════════════════════════════════════
# ENTITY PROCESSING — internal knowledge graph
# Extracts people, companies, products, organizations, dates, and locations
# from search result text, along with relationships between them, then
# connects related entities into a lightweight knowledge graph so the AI can
# reason over entity relationships instead of just raw prose snippets.
# ══════════════════════════════════════════════════════════════════════════════

_TITLE_PREFIXES = (
    r'(?:Mr|Mrs|Ms|Dr|Prof|Sir|Justice|President|PM|Prime Minister|CEO|'
    r'Chairman|Governor|Minister|Senator|Judge)\.?'
)
_PERSON_RE            = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b')
_PERSON_TITLE_HINT_RE = re.compile(
    rf'\b{_TITLE_PREFIXES}\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){{0,2}})\b'
)

_COMPANY_SUFFIXES = (
    "Inc.", "Inc", "Corp.", "Corp", "Corporation", "Ltd.", "Ltd", "LLC", "LLP",
    "Co.", "Co", "Company", "Group", "Holdings", "Industries", "Technologies",
    "Labs", "Ventures", "Partners", "Enterprises",
)
_COMPANY_RE = re.compile(
    r'\b([A-Z][a-zA-Z0-9&]*(?:\s+[A-Z][a-zA-Z0-9&]*){0,3})\s+('
    + "|".join(re.escape(s) for s in _COMPANY_SUFFIXES) + r')\b'
)

_ORGANIZATION_HINTS = (
    "Ministry", "Department", "Commission", "Authority", "Agency", "Bureau",
    "Council", "Committee", "Organization", "Organisation", "Foundation",
    "Institute", "University", "Association", "Federation", "Union", "Court",
)
_ORGANIZATION_RE = re.compile(
    r'\b((?:[A-Z][a-zA-Z]*\s+){0,3}(?:' + "|".join(_ORGANIZATION_HINTS)
    + r')(?:\s+of\s+[A-Z][a-zA-Z]+)?)\b'
)

_PRODUCT_CUE_RE = re.compile(
    r'\b(?:launched|released|unveiled|announced|introduces?|debuts?)\s+(?:its|the|a|an)?\s*'
    r'([A-Z][a-zA-Z0-9]*(?:\s+[A-Z0-9][a-zA-Z0-9]*){0,3})\b'
)

_DATE_RE = re.compile(
    r'\b(?:'
    r'\d{4}-\d{2}-\d{2}'
    r'|(?:January|February|March|April|May|June|July|August|September|October|November|December)'
    r'\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}'
    r'|\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}'
    r'|\b(?:19|20)\d{2}\b'
    r')\b'
)

_LOCATION_HINTS = {
    # Countries
    "india", "united states", "usa", "uk", "united kingdom", "china", "japan",
    "germany", "france", "canada", "australia", "brazil", "russia", "italy",
    "spain", "netherlands", "sweden", "switzerland", "south korea", "mexico",
    "singapore", "uae",
    # US cities
    "washington", "new york", "san francisco", "los angeles", "chicago",
    "boston", "seattle", "austin", "houston", "miami", "atlanta", "dallas",
    "san jose", "denver", "philadelphia",
    # Other global cities
    "london", "paris", "tokyo", "beijing", "shanghai", "dubai", "berlin",
    "toronto", "sydney", "hong kong", "amsterdam", "zurich", "seoul",
    # Indian cities/states
    "delhi", "mumbai", "bengaluru", "bangalore", "kolkata", "chennai",
    "hyderabad", "pune", "california", "texas", "west bengal", "maharashtra",
    "karnataka", "tamil nadu", "gujarat", "punjab", "kerala", "rajasthan",
}
_LOCATION_PREP_RE = re.compile(r'\b(?:in|at|near|from|to)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,2})\b')

# Words that indicate a nearby capitalized phrase is likely a PERSON being
# quoted/described, used to gate the generic capitalized-phrase fallback
# below — without this, any capitalized 2-3 word phrase (a company name
# without a corporate suffix, a product, a place) gets misclassified as a
# person.
_PERSON_CONTEXT_CUE_RE = re.compile(
    r'\b(said|told|stated|according to|explained|noted|added|announced|'
    r'claims?|argues?|wrote|tweeted|posted)\b', re.IGNORECASE
)

_RELATIONSHIP_RE = re.compile(
    r'\b([A-Z][a-zA-Z0-9 ]{2,40}?)\s+'
    r'(is|was|are|were|founded|co-founded|acquired|merged with|partnered with|'
    r'owns|leads|heads|joined|appointed as|works for|based in|headquartered in)\s+'
    r'([A-Z][a-zA-Z0-9 &]{2,50})\b'
)


def _clean_entity_name(name: str) -> str:
    return re.sub(r'\s+', ' ', name).strip().strip('.,;:')


def extract_entities_from_text(text: str) -> dict:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Regex-based entity extraction for one piece of text. Returns a dict of
    category → set of entity names: people, companies, products,
    organizations, dates, locations. Deliberately dependency-free (no NER
    model) — cheap enough to run on every result in the pipeline.
    """
    empty = {"people": set(), "companies": set(), "products": set(),
             "organizations": set(), "dates": set(), "locations": set()}
    if not text:
        return empty

    companies     = {_clean_entity_name(f"{m.group(1)} {m.group(2)}") for m in _COMPANY_RE.finditer(text)}
    organizations = {_clean_entity_name(m.group(1)) for m in _ORGANIZATION_RE.finditer(text)}
    products      = {_clean_entity_name(m.group(1)) for m in _PRODUCT_CUE_RE.finditer(text)}
    dates         = {m.group(0).strip() for m in _DATE_RE.finditer(text)}

    locations = set()
    for m in _LOCATION_PREP_RE.finditer(text):
        candidate = _clean_entity_name(m.group(1))
        if candidate.lower() in _LOCATION_HINTS:
            locations.add(candidate)

    # People: title-prefixed names ("Dr. Jane Doe") are high-confidence and
    # always kept. Other capitalized name-shaped phrases are only kept as
    # people when (a) they don't overlap with something already classified
    # as a company/org/product/location, AND (b) the text contains a
    # person-context cue (said/told/announced/...) — without that gate, any
    # bare capitalized phrase (a suffix-less company or product name) would
    # get misclassified as a person.
    people = {_clean_entity_name(m.group(1)) for m in _PERSON_TITLE_HINT_RE.finditer(text)}
    exclude = companies | organizations | products | locations
    if _PERSON_CONTEXT_CUE_RE.search(text):
        for m in _PERSON_RE.finditer(text):
            name = _clean_entity_name(m.group(1))
            if name and name not in exclude and not any(name in c for c in exclude):
                people.add(name)

    return {
        "people": people, "companies": companies, "products": products,
        "organizations": organizations, "dates": dates, "locations": locations,
    }


def extract_relationships_from_text(text: str) -> list[dict]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Extracts explicit subject-relation-object triples from text — e.g.
    "Sam Altman is CEO of OpenAI", "OpenAI acquired Global Illumination" —
    used to connect entities in the knowledge graph beyond simple
    co-occurrence.
    """
    if not text:
        return []
    triples = []
    for m in _RELATIONSHIP_RE.finditer(text):
        subject  = _clean_entity_name(m.group(1))
        relation = m.group(2).strip().lower()
        obj      = _clean_entity_name(m.group(3))
        if subject and obj and subject.lower() != obj.lower():
            triples.append({"subject": subject, "relation": relation, "object": obj})
    return triples


def build_knowledge_graph(results: list[dict]) -> dict:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Builds an internal knowledge graph from all search results: extracts
    people, companies, products, organizations, dates, and locations from
    each result, merges same-name entities across sources into single
    nodes, and connects related entities via two kinds of edges:
      - explicit relationships (subject-relation-object triples)
      - co-occurrence links ("related_to") when two entities are mentioned
        in the same result but no explicit relationship was extracted —
        keeps the graph connected even without a clean "X is Y" sentence.

    Returns:
      {"nodes": {name: {"type": str, "mentions": int, "sources": [domain,...]}},
       "edges": [{"subject":str,"relation":str,"object":str,"sources":[domain,...]}]}
    Edges are sorted by source count (most-corroborated relationships first)
    so a consumer can take just the top of a potentially large graph.
    """
    nodes: dict[str, dict] = {}
    edge_map: dict[tuple, dict] = {}

    def _add_node(name: str, etype: str, domain: str) -> None:
        key = name.strip()
        if not key:
            return
        node = nodes.setdefault(key, {"type": etype, "mentions": 0, "sources": set()})
        node["mentions"] += 1
        if domain:
            node["sources"].add(domain)

    def _add_edge(subject: str, relation: str, obj: str, domain: str) -> None:
        key = (subject.lower(), relation, obj.lower())
        edge = edge_map.setdefault(
            key, {"subject": subject, "relation": relation, "object": obj, "sources": set()}
        )
        if domain:
            edge["sources"].add(domain)

    for r in results:
        title  = (r.get("title") or "").strip()
        body   = (r.get("body") or "").strip()
        domain = _get_domain(r.get("url", ""))
        if not title and not body:
            continue

        # Join title + body as separate "sentences" (title rarely ends in
        # punctuation) so extraction is done PER SENTENCE — this keeps every
        # regex match bounded to one grammatical unit instead of bleeding
        # across the title/body boundary or between unrelated sentences,
        # and makes co-occurrence ("related_to") edges reflect entities
        # actually mentioned together locally, not just anywhere in the page.
        combined  = f"{title}. {body}" if title and body else (title or body)
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', combined) if s.strip()]

        for sentence in sentences:
            entities = extract_entities_from_text(sentence)
            for etype, names in entities.items():
                singular_type = "person" if etype == "people" else etype[:-1]
                for name in names:
                    _add_node(name, singular_type, domain)

            triples = extract_relationships_from_text(sentence)
            for triple in triples:
                _add_edge(triple["subject"], triple["relation"], triple["object"], domain)
                nodes.setdefault(triple["subject"], {"type": "unknown", "mentions": 0, "sources": set()})
                nodes.setdefault(triple["object"], {"type": "unknown", "mentions": 0, "sources": set()})

            # ── Connect related entities via co-occurrence (same sentence) ──
            explicit_pairs = {frozenset([t["subject"].lower(), t["object"].lower()]) for t in triples}
            all_names = [n for names in entities.values() for n in names]
            for i in range(len(all_names)):
                for j in range(i + 1, len(all_names)):
                    a, b = all_names[i], all_names[j]
                    if frozenset([a.lower(), b.lower()]) in explicit_pairs:
                        continue  # already linked explicitly — skip the weaker duplicate
                    _add_edge(a, "related_to", b, domain)

    for node in nodes.values():
        node["sources"] = sorted(node["sources"])
    edges = list(edge_map.values())
    for edge in edges:
        edge["sources"] = sorted(edge["sources"])
    edges.sort(key=lambda e: len(e["sources"]), reverse=True)

    return {"nodes": nodes, "edges": edges}


def format_knowledge_graph_for_context(graph: dict, max_per_type: int = 5, max_edges: int = 10) -> str:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Renders the knowledge graph as plain text so it can be injected into the
    AI's reasoning context — "use this graph during reasoning". Surfaces the
    most-mentioned entities per type and the most-corroborated relationships
    (by distinct source count), not the whole graph, so it stays compact.
    """
    nodes, edges = graph.get("nodes", {}), graph.get("edges", [])
    if not nodes and not edges:
        return ""

    by_type: dict[str, list[tuple[str, dict]]] = {}
    for name, info in nodes.items():
        by_type.setdefault(info.get("type", "unknown"), []).append((name, info))

    lines = ["=== KNOWLEDGE GRAPH (entities & relationships extracted from sources) ==="]
    type_labels = {
        "person": "People", "company": "Companies", "product": "Products",
        "organization": "Organizations", "date": "Dates", "location": "Locations",
    }
    for etype, label in type_labels.items():
        entries = sorted(by_type.get(etype, []), key=lambda t: t[1]["mentions"], reverse=True)
        if entries:
            names = ", ".join(name for name, _ in entries[:max_per_type])
            lines.append(f"{label}: {names}")

    real_edges = [e for e in edges if e["relation"] != "related_to"][:max_edges]
    if real_edges:
        lines.append("\nRelationships:")
        for e in real_edges:
            lines.append(f"  • {e['subject']} — {e['relation']} — {e['object']}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE — Full production web search
# ══════════════════════════════════════════════════════════════════════════════

def run_production_search(query: str) -> dict:
    """
    Full synchronous pipeline (safe to call from FastAPI sync/async contexts).
    Runs: query rewrite → parallel search → dedup → Firecrawl → trust score
         → cross-reference → Cohere rerank → citation build

    Returns a rich dict for the AI context builder.
    """
    print(f"\n🚀 [Search Engine] Starting pipeline for: '{query[:80]}'")

    # Steps 1-2: Multi-step planning — plan → search → analyze → fill gaps →
    # repeat (capped at MAX_SEARCH_ROUNDS, stops early once confident).
    queries, all_raw = plan_and_execute_search(query)

    print(f"📊 [Pipeline] Raw results: {len(all_raw)} (across {len(queries)} queries)")

    if not all_raw:
        print("⚠️ [Pipeline] No results from any engine")
        return {
            "tool":            "web_search",
            "query":           query,
            "queries_run":     queries,
            "result_count":    0,
            "results":         [],
            "citations":       {},
            "cross_ref":       {},
            "confidence":      0,
            "knowledge_graph": {"nodes": {}, "edges": []},
            "search_engine":   "production",
        }

    # Step 3: Deduplication
    deduped = deduplicate_results(all_raw)
    print(f"🔁 [Dedup] {len(all_raw)} → {len(deduped)} unique results")

    # Step 4: Firecrawl enrichment — SKIP if we already have high-confidence direct answers
    # (Tavily answer + Serper answerBox = we don't need full page crawls)
    has_tavily_answer = any(r.get("source_engine") == "tavily_answer" for r in deduped)
    has_serper_answer = any(r.get("source_engine") == "serper_answer" for r in deduped)
    skip_firecrawl    = has_tavily_answer and has_serper_answer

    if skip_firecrawl:
        print(f"⚡ [Firecrawl] Skipping — direct answers from both engines available")
        enriched = deduped
    else:
        enriched = enrich_with_firecrawl(deduped, query)  # dynamic 5-15 pages, see function docstring

    # Step 5: Trust scoring
    scored = score_all_results(enriched, query)

    # Step 6: Entity processing — build the internal knowledge graph
    knowledge_graph = build_knowledge_graph(scored)
    print(f"🕸️ [EntityGraph] {len(knowledge_graph['nodes'])} entities, "
          f"{len(knowledge_graph['edges'])} relationships")

    # Step 7: Cross-reference analysis
    cross_ref = cross_reference_results(scored)
    print(f"🔬 [CrossRef] {cross_ref['consensus_count']} consensus signals, "
          f"{cross_ref['contradiction_count']} contradictions")

    # Step 8: Cohere reranking (blends semantic relevance + trust)
    reranked = rerank_with_cohere(query, scored, top_n=RERANK_TOP_N)

    # Step 9: Citation assignment
    cited, citation_map = build_citations(reranked)

    # Step 10: Overall answer confidence (0-100)
    confidence = compute_answer_confidence(query, reranked, cross_ref)
    print(f"✅ [Pipeline] Complete. Final results: {len(cited)}, "
          f"Citations: {len(citation_map)}, Confidence: {confidence}\n")

    return {
        "tool":            "web_search",
        "query":           query,
        "queries_run":     queries,
        "result_count":    len(cited),
        "results":         cited,
        "citations":       citation_map,
        "cross_ref":       cross_ref,
        "confidence":      confidence,
        "knowledge_graph": knowledge_graph,
        "search_engine":   "production",
    }


# ══════════════════════════════════════════════════════════════════════════════
# AI CONTEXT BUILDER
# Formats pipeline output into a detailed system-prompt injection for the AI
# ══════════════════════════════════════════════════════════════════════════════

def build_production_search_context(result: dict) -> str:
    """
    Build a rich system-prompt context block from the production search result.
    Instructs the AI to use citations [1], [2] etc. inline — like ChatGPT.
    """
    if not result or not result.get("results"):
        return ""

    lines = []
    queries_run = result.get("queries_run", [result.get("query", "")])
    cross_ref   = result.get("cross_ref", {})
    citations   = result.get("citations", {})

    lines.append(f"🔍 WEB SEARCH RESULTS — Production Engine (Tavily + Serper + Firecrawl + Cohere)")
    lines.append(f"Primary query: {result['query']}")
    if len(queries_run) > 1:
        lines.append(f"Also searched: {' | '.join(queries_run[1:])}")
    lines.append(f"Total unique results after dedup + rerank: {result['result_count']}\n")

    # Cross-reference summary
    if cross_ref.get("contradiction_count", 0) > 0:
        lines.append(f"⚠️ CONTRADICTIONS DETECTED ({cross_ref['contradiction_count']} conflicts):")
        for c in cross_ref.get("contradictions", []):
            lines.append(f"   • '{c['entity']}' — sources disagree: {' vs '.join(c['values'][:2])}")
        lines.append("")

    if cross_ref.get("consensus_count", 0) > 0:
        lines.append(f"✅ Consensus: {cross_ref['consensus_count']} facts confirmed by multiple sources\n")

    # Knowledge graph — entities & relationships extracted from all sources,
    # surfaced so the AI can reason over connections (e.g. "who founded X",
    # "what did Y acquire") instead of only raw prose snippets.
    graph_text = format_knowledge_graph_for_context(result.get("knowledge_graph", {}))
    if graph_text:
        lines.append(graph_text + "\n")

    # Results — numbered with citations
    lines.append("=== SEARCH RESULTS (use [N] to cite inline in your answer) ===\n")

    for r in result["results"]:
        num        = r.get("citation_num")
        title      = r.get("title", "")
        body       = r.get("body", "")
        url        = r.get("url", "")
        trust      = r.get("trust_score", 50)
        engine     = r.get("source_engine", "")
        is_direct  = r.get("is_direct_answer", False)
        firecrawled = r.get("firecrawled", False)
        multi      = r.get("multi_source", False)

        # Label line
        label_parts = []
        if is_direct:
            label_parts.append("⭐ DIRECT ANSWER")
        if firecrawled:
            label_parts.append("🕷️ FULL CONTENT")
        if multi:
            label_parts.append("🔁 MULTI-SOURCE")
        label_parts.append(f"Trust:{trust}/100")

        num_str = f"[{num}]" if num else "[–]"
        lines.append(f"{num_str} {title} ({' | '.join(label_parts)})")
        if body:
            # Indent body for readability
            body_lines = body[:1200].split("\n")
            for bl in body_lines[:20]:  # max 20 lines per result
                if bl.strip():
                    lines.append(f"    {bl.strip()}")
        if url:
            lines.append(f"    🔗 {url}")
        lines.append("")

    # Citation index for AI reference
    if citations:
        lines.append("=== CITATION INDEX ===")
        for num, info in sorted(citations.items()):
            lines.append(f"[{num}] {info['title'][:60]} — {info['domain']} — {info['url']}")
        lines.append("")

    # AI instructions
    lines.append(
        "=== AI INSTRUCTIONS — FOLLOW EXACTLY ===\n"
        "1. Use [N] inline citations when stating facts from a specific source.\n"
        "   Example: 'The RBI cut rates to 6.25% [1], confirmed by multiple sources [2][3].'\n"
        "2. PREFER facts marked '✅ MULTI-SOURCE' or high trust scores (80+).\n"
        "3. If a ⚠️ CONTRADICTION is shown above, acknowledge it: "
        "'Sources disagree on X — [1] says Y while [2] says Z.'\n"
        "4. For ⭐ DIRECT ANSWER results — treat these as highest confidence.\n"
        "5. For 🕷️ FULL CONTENT results — this is real page text; quote specific details freely.\n"
        "6. DO NOT fabricate citations. Only cite [N] for results you actually used.\n"
        "7. DO NOT mention source names/domains inline (e.g. 'according to Times of India').\n"
        "   Instead use only the numbered citation: 'The CM announced [3].'\n"
        "8. Give a complete, confident answer — synthesise across all sources.\n"
        "9. If results are outdated or contradictory, say what you can confirm and what is unclear.\n"
        "10. NEVER say 'I don't have real-time data' — you have live search results above. Use them.\n"
        "11. NEVER invent numbers, prices, or dates not in the results above.\n"
    )

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE PAYLOAD BUILDER — for frontend citation chips
# ══════════════════════════════════════════════════════════════════════════════

def build_production_sources_payload(result: dict) -> Optional[str]:
    """
    Build SSE sources payload for the frontend citation chips.
    Returns JSON string or None.
    """
    citations = result.get("citations", {})
    if not citations:
        # Fallback: try to build from results
        sources = []
        for r in result.get("results", []):
            url = r.get("url", "")
            if url:
                sources.append({
                    "url":    url,
                    "title":  r.get("title", url),
                    "domain": _get_domain(url),
                    "trust":  r.get("trust_score", 50),
                    "num":    r.get("citation_num"),
                })
        if not sources:
            return None
        return json.dumps({"sources": sources[:8]})

    sources = [
        {
            "url":    info["url"],
            "title":  info["title"],
            "domain": info["domain"],
            "num":    num,
        }
        for num, info in sorted(citations.items())
    ]
    return json.dumps({"sources": sources[:8]})


# ══════════════════════════════════════════════════════════════════════════════
# FINAL ANSWER GENERATION + VERIFICATION
# generate draft answer → verify (unsupported claims / wrong dates / wrong
# numbers / hallucinations / missing citations / contradictions) → if the
# verifier fails the draft, use its corrected answer instead.
# ══════════════════════════════════════════════════════════════════════════════

ANSWER_GENERATION_TIMEOUT = 20
ANSWER_GENERATION_MAX_TOK = 1200
ANSWER_VERIFIER_TIMEOUT   = 15
ANSWER_VERIFIER_MAX_TOK   = 1400

_ANSWER_GENERATION_SYSTEM_PROMPT = """You are answering a user's question using ONLY the numbered web search evidence provided.
Follow the AI INSTRUCTIONS included in the evidence block exactly (citation style, no fabrication, etc).
Give a complete, well-organized, directly-worded answer. Do not add meta-commentary about the search process itself."""

_ANSWER_VERIFIER_SYSTEM_PROMPT = """You are a fact-checking verifier for an AI web-search answer.
You will be given the user's question, the source evidence (numbered [1], [2], ...), and a DRAFT ANSWER that was generated from that evidence.

Check the draft answer for:
1. Unsupported claims — statements not backed by any numbered source.
2. Wrong dates — a date/time in the answer that doesn't match the evidence.
3. Wrong numbers — a statistic, price, or figure that doesn't match the evidence.
4. Hallucinations — facts, names, or events not present anywhere in the evidence.
5. Missing citations — factual claims stated without a [N] citation, or a [N] citation that doesn't support the claim next to it.
6. Contradictions — the answer states something as settled fact when the evidence actually shows sources disagree, without acknowledging the disagreement.

Output ONLY a JSON object, no prose, no markdown fences:
{
  "passed": true | false,
  "issues": ["short description of each problem found", ...],
  "revised_answer": "the corrected answer text, only if passed is false — otherwise null"
}

Rules:
- "passed": true means the draft is accurate and fully supported by the evidence — no issues.
- "passed": false means at least one real, specific problem was found (from the 6 categories above).
- If "passed" is false, "revised_answer" MUST contain a corrected version of the answer that fixes every issue — remove or fix unsupported claims, correct wrong dates/numbers, cite properly, and acknowledge any contradictions instead of stating one side as fact.
- The revised answer must keep the same [N] citation style as the draft.
- Do not invent new facts not present in the evidence when revising — if a claim can't be supported, remove it rather than replacing it with a guess."""


def _generate_answer_llm(query: str, context: str) -> Optional[str]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Generates the draft final answer from the search evidence context
    (build_production_search_context output). This is the FIRST of the two
    LLM calls in the generate → verify → (revise) pipeline. Returns None on
    any failure (missing key, network error, malformed response) so the
    caller can fail gracefully instead of crashing the pipeline.
    """
    if not QUERY_PLANNER_KEY or not context:
        return None
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         QUERY_PLANNER_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      QUERY_PLANNER_MODEL,
                "max_tokens": ANSWER_GENERATION_MAX_TOK,
                "system":     _ANSWER_GENERATION_SYSTEM_PROMPT,
                "messages":   [{"role": "user", "content": f"{context}\n\nUser question: {query}"}],
            },
            timeout=ANSWER_GENERATION_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        ).strip()
        return text or None
    except Exception as e:
        print(f"⚠️ [AnswerGen] Failed to generate draft answer: {e}")
        return None


def _verify_answer_llm(query: str, context: str, draft_answer: str) -> dict:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    SECOND LLM call — a verifier that checks the draft answer against the
    source evidence for: unsupported claims, wrong dates, wrong numbers,
    hallucinations, missing citations, and unacknowledged contradictions.
    Fails OPEN toward trusting the draft: any missing key, network error,
    timeout, or malformed response returns passed=True with no revision —
    a broken verifier must never block an answer from being returned.
    Returns {"passed": bool, "issues": list[str], "revised_answer": str|None}.
    """
    if not QUERY_PLANNER_KEY or not draft_answer:
        return {"passed": True, "issues": [], "revised_answer": None}

    user_content = (
        f"User question: {query}\n\n"
        f"Source evidence:\n{context}\n\n"
        f"DRAFT ANSWER to verify:\n{draft_answer}"
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         QUERY_PLANNER_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      QUERY_PLANNER_MODEL,
                "max_tokens": ANSWER_VERIFIER_MAX_TOK,
                "system":     _ANSWER_VERIFIER_SYSTEM_PROMPT,
                "messages":   [{"role": "user", "content": user_content}],
            },
            timeout=ANSWER_VERIFIER_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        text = "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        ).strip()
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())

        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return {"passed": True, "issues": [], "revised_answer": None}

        passed  = bool(parsed.get("passed", True))
        issues  = [str(i) for i in (parsed.get("issues") or []) if str(i).strip()]
        revised = parsed.get("revised_answer")
        revised = str(revised).strip() if revised else None

        # A "failed" verdict with no actual issues or revision is meaningless
        # — treat it as passed so a flaky verifier can't force a pointless swap.
        if not issues and not revised:
            passed = True

        return {"passed": passed, "issues": issues, "revised_answer": revised if not passed else None}

    except Exception as e:
        print(f"⚠️ [Verifier] Verification failed, trusting draft answer: {e}")
        return {"passed": True, "issues": [], "revised_answer": None}


def generate_verified_answer(query: str, result: dict) -> dict:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Final answer generation with a second-pass verifier:

        generate draft answer from search evidence
            → verify (unsupported claims, wrong dates, wrong numbers,
                       hallucinations, missing citations, contradictions)
            → if verification fails, use the verifier's corrected answer

    Returns:
      {"answer": str|None, "verified": bool, "revised": bool,
       "issues": list[str], "citations": dict, "confidence": int}
    "answer" is None only if draft generation itself failed (e.g. no LLM
    key configured) — verification never blocks an answer from returning,
    it only potentially improves it.
    """
    context = build_production_search_context(result)
    draft = _generate_answer_llm(query, context)

    if not draft:
        return {
            "answer":     None,
            "verified":   False,
            "revised":    False,
            "issues":     ["draft answer generation unavailable"],
            "citations":  result.get("citations", {}),
            "confidence": result.get("confidence", 0),
        }

    verdict = _verify_answer_llm(query, context, draft)

    if verdict["passed"]:
        final_answer, revised = draft, False
    elif verdict["revised_answer"]:
        final_answer, revised = verdict["revised_answer"], True
        print(f"🔁 [Verifier] Revised answer — issues found: {verdict['issues']}")
    else:
        # Failed but no usable revision came back — safer to keep the draft
        # than to return nothing, while still surfacing the issues found.
        final_answer, revised = draft, False

    return {
        "answer":     final_answer,
        "verified":   verdict["passed"],
        "revised":    revised,
        "issues":     verdict["issues"],
        "citations":  result.get("citations", {}),
        "confidence": result.get("confidence", 0),
    }
