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
import asyncio
import hashlib
import requests
from datetime import datetime
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

# Needs the tightest window — today/this-instant data (prices, scores, weather,
# breaking news). Maps to Tavily time_range="day" / Serper tbs="qdr:d".
_RECENCY_DAY_PATTERNS = [
    r'\b(today|right now|currently|live|breaking|just (now|happened)|this (morning|moment))\b',
    r'\b(price|stock|share price|nifty|sensex|crypto|bitcoin|exchange rate)\b',
    r'\b(score|live score|match score|who\'?s? (winning|leading))\b',
    r'\bweather\b',
]

# Needs a wider window — recent-but-not-instant (this week's news, a recent
# release, an ongoing situation). Maps to time_range="week" / tbs="qdr:w".
_RECENCY_WEEK_PATTERNS = [
    r'\b(latest|newest|recent(ly)?|this week|new (version|release|update|model))\b',
    r'\b(news|headlines|happened|announcement|announced)\b',
]

# Needs a moderate window — "who currently holds this role" style questions.
# These aren't as urgent as breaking news but are still wrong if answered from
# stale training data. Maps to time_range="month" / tbs="qdr:m".
_RECENCY_MONTH_PATTERNS = [
    r'\b(cm|chief minister|prime minister|president|governor|minister|mayor|'
    r'ceo|chancellor|elected|appointed|resigned|won (the )?election)\b',
    r'\bwho is the (current|new)\b',
    r'\bstill (the|in|alive|running|active|ceo|president)\b',
]


def detect_recency_window(query: str) -> Optional[str]:
    """
    Returns "day" | "week" | "month" | None.
    None means: generic search, no time constraint needed (e.g. "what is
    photosynthesis" — Wikipedia-style facts don't benefit from date-boxing
    and over-constraining them can actually *reduce* result quality).
    """
    lower = query.lower()
    if any(re.search(p, lower) for p in _RECENCY_DAY_PATTERNS):
        return "day"
    if any(re.search(p, lower) for p in _RECENCY_WEEK_PATTERNS):
        return "week"
    if any(re.search(p, lower) for p in _RECENCY_MONTH_PATTERNS):
        return "month"
    return None


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

def _tavily_search_sync(query: str, max_results: int = RESULTS_PER_ENGINE) -> list[dict]:
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


def _serper_search_sync(query: str, max_results: int = RESULTS_PER_ENGINE) -> list[dict]:
    """Synchronous Serper (Google Search API) — called from thread pool."""
    if not SERPER_KEY:
        return []
    try:
        payload = {"q": query, "num": max_results, "gl": "in", "hl": "en"}

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


async def _search_parallel(query: str) -> tuple[list, list]:
    """Run Tavily + Serper simultaneously, return both result lists."""
    loop = asyncio.get_event_loop()
    tavily_task = loop.run_in_executor(None, _tavily_search_sync, query)
    serper_task = loop.run_in_executor(None, _serper_search_sync, query)
    tavily_res, serper_res = await asyncio.gather(tavily_task, serper_task)
    return tavily_res, serper_res


def _execute_search_queries(queries: list[str]) -> list[dict]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Runs Tavily + Serper for every query in `queries` and returns the
    combined raw results. This is just the old inline loop from
    run_production_search(), extracted so the iterative planner (below) can
    call it once per round without duplicating the event-loop plumbing.
    """
    all_raw: list[dict] = []
    for q in queries:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            tavily_res, serper_res = loop.run_until_complete(_search_parallel(q))
            loop.close()
        except Exception:
            # Fallback to sequential if event loop issues
            tavily_res = _tavily_search_sync(q)
            serper_res = _serper_search_sync(q)

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


def plan_and_execute_search(query: str) -> tuple[list[str], list[dict]]:
    """
    ── NEW ──────────────────────────────────────────────────────────────────
    Iterative multi-step search planner:

      Round 1: rewrite_queries() → search
      Analyze: is the evidence sufficient?
        - yes, or LLM unavailable/fails  → stop
        - no  → LLM proposes targeted follow-up queries → search again
      Repeat until confident, no new queries are proposed, or
      MAX_SEARCH_ROUNDS is reached (hard cap — no infinite loops possible).

    Returns (queries_run, all_raw) with the same shapes the old inline
    steps 1+2 in run_production_search() produced, so downstream dedup/
    Firecrawl/trust/rerank/citation code needs no changes.
    """
    queries_run: list[str] = []
    all_raw: list[dict] = []

    # ── Round 1 — initial plan + search ──────────────────────────────────
    round_num = 1
    queries = rewrite_queries(query)
    print(f"📝 [Planner] Round {round_num}/{MAX_SEARCH_ROUNDS} queries: {queries}")
    queries_run.extend(queries)
    all_raw.extend(_execute_search_queries(queries))

    # ── Rounds 2..MAX_SEARCH_ROUNDS — analyze, fill gaps, repeat ─────────
    while round_num < MAX_SEARCH_ROUNDS:
        assessment = _assess_evidence_and_plan_more(query, all_raw, queries_run)

        if assessment["confident"] or not assessment["additional_queries"]:
            print(f"✅ [Planner] Stopping after round {round_num} — evidence sufficient")
            break

        round_num += 1
        follow_up = assessment["additional_queries"]
        print(f"📝 [Planner] Round {round_num}/{MAX_SEARCH_ROUNDS} follow-up queries: {follow_up}")
        queries_run.extend(follow_up)
        all_raw.extend(_execute_search_queries(follow_up))

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


def _firecrawl_extract(url: str) -> Optional[str]:
    """
    Call Firecrawl API to extract clean markdown from a URL.
    Returns clean text or None on failure.
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

        content = data.get("data", {}).get("markdown", "")
        if not content:
            return None

        # Truncate to 3000 chars — enough context without blowing the prompt
        clean = content.strip()[:3000]
        print(f"✅ [Firecrawl] Extracted {len(clean)} chars from {url[:60]}")
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
            executor.submit(_firecrawl_extract, url): idx
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
                    results[idx]["body_truncated"] = len(content) >= 2990
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
        "gov", "gov.in", "nic.in", "india.gov.in", "pib.gov.in", "mea.gov.in",
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
        "dopt.gov.in",
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
        "reuters.com", "apnews.com", "afp.com","pti.in",
        # Flagship global news
        "bbc.com", "bbc.co.uk", "nytimes.com", "theguardian.com",
        "washingtonpost.com", "ft.com", "economist.com", "wsj.com",
        # Peer-reviewed / academic journals
        "nature.com", "science.org", "thelancet.com", "nejm.org",
        "cell.com", "pnas.org", "jamanetwork.com", "bmj.com",
        # Reference
        "wikipedia.org", "britannica.com", "scholar.google.com",
        "pubmed.ncbi.nlm.nih.gov", "arxiv.org",
    },

    # ── Tier 3 (score 78) — Major national/regional news, established tech &
    # finance official sites, big-brand company sources ──────────────────────
    3: {
        # Indian national news
        "timesofindia.com", "hindustantimes.com", "ndtv.com", "thehindu.com",
        "indianexpress.com", "livemint.com", "economictimes.indiatimes.com",
        "businessstandard.com", "financialexpress.com", "thewire.in",
        "theprint.in", "scroll.in", "thequint.com",
        # International business/finance news
        "bloomberg.com", "cnbc.com", "forbes.com", "moneycontrol.com",
        "finance.yahoo.com",
        # Tech — official company sources & major docs
        "techcrunch.com", "theverge.com", "arstechnica.com", "wired.com",
        "stackoverflow.com", "github.com", "docs.python.org",
        "developer.mozilla.org", "microsoft.com", "google.com", "apple.com",
        "amazon.com", "openai.com", "anthropic.com", "meta.com", "nvidia.com",
        # Health — established institutions
        "mayoclinic.org", "webmd.com", "healthline.com", "clevelandclinic.org",
    },

    # ── Tier 4 (score 65) — Solid secondary sources: trade press, niche news,
    # established reference/education sites, sports data ────────────────────
    4: {
        "sciencedaily.com", "livescience.com", "space.com",
        "geeksforgeeks.org", "w3schools.com", "tutorialspoint.com",
        "javatpoint.com", "codecademy.com", "freecodecamp.org",
        "cricbuzz.com", "espncricinfo.com", "sportskeeda.com", "espn.com",
        "news18.com", "indiatoday.in", "business-standard.com",
        "investopedia.com", "statista.com",
    },

    # ── Tier 5 (score 52) — Community platforms & blogging sites: useful but
    # unmoderated/self-published, verify against a higher tier when possible ─
    5: {
        "medium.com", "substack.com", "dev.to", "hackernoon.com",
        "towardsdatascience.com",
    },

    # ── Tier 6 (score 40) — Open discussion/UGC platforms: lowest default
    # weight, treat as a lead to verify rather than a citation on its own ────
    6: {
        "reddit.com", "quora.com",
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


def compute_trust_score(url: str, result: dict) -> int:
    """
    Returns trust score 0–100 for a search result.
    Factors:
      - Domain tier (base score) — now 6 tiers, see TRUST_TIERS above
      - Multi-source confirmation (boost)
      - Direct answer from engine (boost)
      - Real recency, based on actual publish date (boost)
      - Firecrawled full content (slight boost — real page)
    """
    if not url:
        # Direct answer without URL — trust based on source
        engine = result.get("source_engine", "")
        if engine in ("tavily_answer", "serper_answer", "serper_kg"):
            return 88
        return 60

    try:
        domain = urlparse(url).netloc.lower()
        # Strip www, m., etc.
        domain = re.sub(r'^(www\d?|m|mobile|amp)\.', '', domain)
    except (ValueError, AttributeError):
        domain = ""

    # Base score by tier — walk TRUST_TIERS from most to least trusted and
    # take the first (i.e. highest) tier the domain matches. A domain never
    # scores lower than its worst matching tier would suggest, since we stop
    # at the first hit.
    score = 50  # default for unknown/unlisted domains

    for tier_score in TRUST_TIER_SCORES:
        tier_domains = TRUST_TIERS.get(tier_score["tier"], ())
        if any(domain == t or domain.endswith("." + t) for t in tier_domains):
            score = tier_score["score"]
            break

    # Penalty for known low-quality domains
    if any(domain == p or domain.endswith("." + p) for p in _PENALISED):
        score = max(score - 10, 30)

    # Boosts
    if result.get("multi_source"):
        score = min(score + 8, 100)   # confirmed by 2+ engines

    if result.get("is_direct_answer"):
        score = min(score + 5, 100)   # featured snippet / direct answer

    if result.get("firecrawled"):
        score = min(score + 4, 100)   # real page content, not just snippet

    score = min(score + _recency_boost(result, url), 100)

    return score


def score_all_results(results: list[dict]) -> list[dict]:
    """Add trust_score field to every result."""
    for r in results:
        r["trust_score"] = compute_trust_score(r.get("url", ""), r)
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


def cross_reference_results(results: list[dict], top_n: int = 6) -> dict:
    """
    Analyse top N results to find:
    - consensus_signals: facts that appear in 2+ sources (higher confidence)
    - contradiction_signals: conflicting values for the same entity

    Returns a metadata dict that gets injected into the AI context.
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

    # Simple contradiction detection: look for "X is Y" vs "X is Z" patterns
    # We use a lightweight keyword approach — no LLM needed
    contradictions = []
    entity_values: dict[str, list[tuple[str, str]]] = {}  # entity → [(value, source)]

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
            if entity not in entity_values:
                entity_values[entity] = []
            entity_values[entity].append((value, domain))

    for entity, val_srcs in entity_values.items():
        unique_vals = list({v for v, _ in val_srcs})
        if len(unique_vals) > 1:
            contradictions.append({
                "entity":  entity,
                "values":  unique_vals[:3],
                "sources": [s for _, s in val_srcs[:4]],
            })

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

def rerank_with_cohere(query: str, results: list[dict], top_n: int = RERANK_TOP_N) -> list[dict]:
    """
    Use Cohere Rerank API to semantically re-order search results.
    Falls back to trust-score sorting if Cohere is unavailable.

    Cohere free tier: 1000 reranks/month at cohere.com
    """
    if not COHERE_KEY or not results:
        # Fallback: sort by trust_score descending, direct answers first
        return sorted(
            results,
            key=lambda r: (
                r.get("is_direct_answer", False),
                r.get("trust_score", 50),
                r.get("multi_source", False),
            ),
            reverse=True,
        )[:top_n]

    # Prepare documents for Cohere — use title + body
    candidates = results[:min(len(results), 20)]  # Cohere limit per call
    documents  = [
        f"{r.get('title', '')} — {r.get('body', '')[:300]}"
        for r in candidates
    ]

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
                "top_n":      min(top_n, len(candidates)),
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
        print(f"✅ [Cohere] Reranked {len(reordered)} results")
        return reordered

    except Exception as e:
        print(f"❌ [Cohere] {e} — falling back to trust sort")
        return _trust_sort(results, top_n)


def _trust_sort(results: list[dict], top_n: int) -> list[dict]:
    return sorted(
        results,
        key=lambda r: (
            r.get("is_direct_answer", False),
            r.get("trust_score", 50),
            r.get("multi_source", False),
        ),
        reverse=True,
    )[:top_n]


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
            "tool":          "web_search",
            "query":         query,
            "queries_run":   queries,
            "result_count":  0,
            "results":       [],
            "citations":     {},
            "cross_ref":     {},
            "search_engine": "production",
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
        enriched = enrich_with_firecrawl(deduped)  # dynamic 5-15 pages, see function docstring

    # Step 5: Trust scoring
    scored = score_all_results(enriched)

    # Step 6: Cross-reference analysis
    cross_ref = cross_reference_results(scored)
    print(f"🔬 [CrossRef] {cross_ref['consensus_count']} consensus signals, "
          f"{cross_ref['contradiction_count']} contradictions")

    # Step 7: Cohere reranking (blends semantic relevance + trust)
    reranked = rerank_with_cohere(query, scored, top_n=RERANK_TOP_N)

    # Step 8: Citation assignment
    cited, citation_map = build_citations(reranked)

    print(f"✅ [Pipeline] Complete. Final results: {len(cited)}, "
          f"Citations: {len(citation_map)}\n")

    return {
        "tool":          "web_search",
        "query":         query,
        "queries_run":   queries,
        "result_count":  len(cited),
        "results":       cited,
        "citations":     citation_map,
        "cross_ref":     cross_ref,
        "search_engine": "production",
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
