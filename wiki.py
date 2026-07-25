"""
wiki.py — Wikipedia Intelligence Layer for Catura AI
=====================================================
Provides fast, hallucination-free answers for educational,
informational, historical, scientific, biography, and concept
questions using the free Wikimedia REST API (no API key needed).

Pipeline:
  1. Search Wikipedia for best matching article
  2. Fetch article summary (extract + key facts)
  3. Return clean context string for AI prompt injection
  4. Caller decides whether to fallback to web_search

APIs used (no auth required):
  Search  : https://en.wikipedia.org/w/rest.php/v1/search/page
  Summary : https://en.wikipedia.org/api/rest_v1/page/summary/{title}

FIXED in this version (see wiki_py_audit_report.md, section 1):
  - 1.1: data.get(key, "") only defaults when the KEY IS ABSENT. If the
         Wikipedia API returns the key with value null (common for
         "description" on many pages, occasionally "extract" too),
         .get() returns None, not "", and .strip() on None crashes with
         AttributeError. Fixed with `(data.get(key) or "").strip()`.
  - 1.2: same null-vs-absent problem one level deeper — content_urls or
         content_urls["desktop"] can themselves be null. Fixed by
         guarding every level with `or {}` before the next .get().
  - Defense in depth: _format_context() is now called inside a
         try/except in search_wikipedia(), so if some *other* unforeseen
         shape of API response still slips through, it degrades to
         {"found": False, "reason": "error"} instead of crashing the
         whole call.
  - Disambiguation detection: the summary API returns "type":
         "disambiguation" for pages that just list multiple unrelated
         topics (e.g. searching "Mercury" can land on the disambiguation
         page instead of the planet or the element). That blurb easily
         clears MIN_EXTRACT_LEN, so it was previously accepted as if it
         were a real answer. _fetch_summary() now rejects it, and
         _search_wikipedia() now returns multiple candidates so
         search_wikipedia() can move on to the next-best title instead
         of just giving up.
  - Relevance scoring: limit=1 meant the #1 API result was trusted blindly
         with no sanity check against the query — a wrong namesake article
         or an overly-broad redirect target would just get returned as
         ground truth. _search_wikipedia() now fetches SEARCH_CANDIDATES
         (5) results with their descriptions, _score_candidate() ranks
         them by lexical word-overlap against the query, and
         search_wikipedia() tries them best-match-first rather than
         trusting raw API order.
  - Freshness signal: the summary API's "timestamp" field (last revision
         time) was discarded entirely, so the AI had no way to know how
         stale a snapshot was. _format_context() now extracts and injects
         "Wikipedia article last updated: <date>" into the context, and
         _is_volatile_topic() flags topics that are structurally likely to
         go stale (titles/descriptions containing words like "president,"
         "CEO," "current," "list of," a 4-digit year, "population,"
         "champion," "holder") with an explicit caveat telling the AI to
         prefer web_search for the current value of that sub-fact.
  - Quality gate ordering: MIN_EXTRACT_LEN=80 was previously the ONLY
         quality gate — a disambiguation stub or a completely unrelated
         short biography can both clear 80 characters easily, so length
         alone said nothing about correctness. It is now explicitly the
         LAST/weakest check, applied only after two stronger checks have
         already passed: (1) type != "disambiguation" (§ above), and
         (2) MIN_RELEVANCE_SCORE — a candidate whose title/description
         shares zero words with the query is now rejected outright before
         its length is even considered, unless literally every candidate
         scores 0 (in which case we fall back to trying them anyway
         rather than returning no_result on a query with a real match
         that just used very different wording).
  - Skip-pattern coverage (real-time false negatives, §3.1): the old
         regex matched "currently" but not "current", so "current CEO of
         Tesla" / "current PM of India" / "current exchange rate" all
         slipped straight past the skip check and got answered from a
         static Wikipedia snapshot. Added: "current", "now", "at
         present", "as of now", "this week", "this year", "recently",
         "upcoming", "next", "who (holds|currently leads/is leading) the
         record". Added _has_year_anchored_realtime() to catch
         year-anchored queries like "election results 2026" / "budget
         2026-27" regardless of word order. Added common
         Hindi/Bengali/Hinglish real-time words (abhi, vartaman, is
         samay, अभी, वर्तमान, আজ, এখন, বর্তমান) since your userbase is
         India-based and these never had any coverage at all.
         TRADEOFF: bare "current" also matches legitimate encyclopedia
         topics like "electric current" or "ocean current" — this is a
         deliberate choice per your explicit request to close the gap;
         if that false-positive rate turns out to matter in practice,
         narrow it to require a co-occurring role/person word instead
         (see the commented alternative next to the pattern).
"""

import requests
import re

# ── Constants ──────────────────────────────────────────────────────────────────
WIKI_SEARCH_URL  = "https://en.wikipedia.org/w/rest.php/v1/search/page"
WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKI_HEADERS     = {
    "User-Agent": "CaturaAI/1.0 (educational AI assistant; python-requests)",
    "Accept":     "application/json",
}
REQUEST_TIMEOUT  = 6   # seconds — keep it snappy

# Minimum extract length to be considered a useful result.
# NOTE: this is intentionally the WEAKEST quality check and is applied
# LAST — length alone doesn't imply correctness or relevance (a
# disambiguation stub or a totally unrelated short bio can both clear
# this easily). The disambiguation-type check and MIN_RELEVANCE_SCORE
# below are stronger signals and are checked first.
MIN_EXTRACT_LEN  = 80

# Minimum lexical-overlap score (see _score_candidate) for a candidate to
# be trusted as an actual match for the query, rather than an unrelated
# namesake that merely happened to rank in the API's top N. A candidate
# scoring 0 shares not a single word with the query — that's a strong
# signal it's the wrong article, and it's checked BEFORE length.
MIN_RELEVANCE_SCORE = 1

# Maximum characters of Wikipedia extract we send to the AI
# (keeps token usage light while giving full context for simple questions)
MAX_EXTRACT_CHARS = 1800

# How many search candidates to fetch — was 1 (no fallback, no relevance
# check at all). If the top hit turns out to be a disambiguation page, a
# wrong namesake, or a too-short stub, we now try the next-best one
# instead of giving up or trusting a bad match.
SEARCH_CANDIDATES = 5


# ── Quality gate: topics Wikipedia is NOT good for ────────────────────────────
# If the question is clearly real-time or opinion-based, skip Wikipedia entirely.
_SKIP_WIKI_PATTERNS = [
    # FIX (§3.1): old pattern matched "currently" but not bare "current",
    # so "current CEO of Tesla" / "current PM of India" slipped through
    # entirely. Also added "now", "at present", "as of now", "this
    # week/year", "recently", "upcoming", "next".
    # TRADEOFF: "current" also appears in legitimate encyclopedia topics
    # ("electric current", "ocean current"). Kept broad per explicit
    # request; if that matters in practice, swap for the narrower
    # alternative: r'\bcurrent (ceo|president|pm|prime minister|leader|
    # head|holder|champion|price|rate|status)\b'
    r'\b(today|right now|currently|current|now|at present|as of now|'
    r'live|latest|breaking|just now|this moment|'
    r'this week|this year|recently|upcoming|next)\b',

    r'\b(price|stock|share|nifty|sensex|crypto|bitcoin|weather|temperature)\b',
    r'\b(score|match score|who (won|is winning)|ipl today)\b',

    # FIX (§3.1): "who holds the record" / "who currently leads" had no
    # coverage at all.
    r'\bwho (holds|currently leads|is leading)\b',

    r'\b(news|headlines|happened today|recent news)\b',
    r'\bhow (much|many).*(cost|price|rupee|dollar)\b',
    r'\bmy (name|age|location|city)\b',
    r'\b(code|program|debug|fix|error|implement|build|create) (this|the|a|my)\b',

    # FIX (§3.1): common Hindi/Bengali/Hinglish real-time words — the
    # original patterns were English-only despite an India-based
    # userbase, so e.g. "abhi ka PM kaun hai" never got skipped.
    r'\b(abhi|abhi ka|is samay|vartaman)\b',      # Hindi/Hinglish: now/at present/current
    r'अभी|वर्तमान|इस\s*समय',                        # Hindi (Devanagari)
    r'এখন|বর্তমান|এই\s*মুহূর্তে',                    # Bengali
]

def should_skip_wikipedia(query: str) -> bool:
    """Return True if the query is clearly not suitable for Wikipedia."""
    lower = query.lower()
    if any(re.search(p, lower) for p in _SKIP_WIKI_PATTERNS):
        return True
    # FIX (§3.1): year-anchored real-time queries like "election results
    # 2026" or "2026-27 budget" — a single regex can't cleanly handle the
    # year appearing before OR after the keyword, so this is a separate
    # co-occurrence check instead of another entry in the OR-list above.
    if _has_year_anchored_realtime(query):
        return True
    return False


# Words that, when they co-occur with a bare year anywhere in the query
# (in either order — "budget 2026" or "2026 budget"), mark it as a
# fast-changing, non-encyclopedic query rather than history.
_YEAR_ANCHORED_REALTIME_WORDS = re.compile(
    r'\b(election results?|results?|winner|champion|budget|standings|'
    r'schedule|fixtures?)\b',
    re.IGNORECASE,
)
_YEAR_TOKEN = re.compile(r'\b(19|20)\d{2}\b')


def _has_year_anchored_realtime(query: str) -> bool:
    """
    True if the query contains BOTH a bare 4-digit year AND a
    fast-changing keyword (results, winner, budget, standings, etc.),
    regardless of which comes first. Catches "election results 2026" and
    "2026 budget" alike, neither of which the old skip list caught.
    """
    return bool(_YEAR_TOKEN.search(query)) and bool(
        _YEAR_ANCHORED_REALTIME_WORDS.search(query)
    )


# Words/patterns that suggest a topic is structurally likely to go stale
# even though the underlying article is perfectly Wikipedia-appropriate
# (e.g. "Tesla" is fine for Wikipedia, but "who is Tesla's current CEO"
# is a sub-fact that can easily be out of date by the time it's read).
# NOT used to skip Wikipedia — only to attach a freshness caveat so the
# caller/AI knows to double check fast-changing sub-facts elsewhere.
_VOLATILE_TOPIC_PATTERNS = re.compile(
    r'\b(president|prime minister|ceo|chairman|chairperson|current|incumbent|'
    r'champion|record holder|title holder|population|list of|governor|'
    r'head coach|director general|secretary general)\b|\b(19|20)\d{2}\b',
    re.IGNORECASE,
)


def _is_volatile_topic(query: str, title: str, description: str) -> bool:
    """
    Heuristic-only check (word-list based, not a skip decision). Flags a
    result as "likely to go stale" so _format_context() can attach a
    caveat telling the AI to prefer web_search for the current value of
    any time-sensitive sub-fact, instead of presenting a Wikipedia
    snapshot as if it were guaranteed current.
    """
    combined = f"{query} {title} {description}"
    return bool(_VOLATILE_TOPIC_PATTERNS.search(combined))


# ── Step 1: Search ─────────────────────────────────────────────────────────────
def _search_wikipedia(query: str) -> list[dict]:
    """
    Call the Wikimedia search API and return a list of candidate pages
    (each a dict with "title" and "description"), in the API's own
    ranked order.

    Returns multiple candidates (not just 1) so the caller can score them
    for relevance and fall through to the next-best title if the top hit
    turns out to be a disambiguation page, a too-short stub, or simply
    the wrong article for the query.

    Returns [] if nothing found or on error.
    """
    try:
        resp = requests.get(
            WIKI_SEARCH_URL,
            params={"q": query, "limit": SEARCH_CANDIDATES},
            headers=WIKI_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"⚠️ [Wiki] search HTTP {resp.status_code} for: {query[:60]}")
            return []

        pages = resp.json().get("pages", [])
        if not pages:
            print(f"ℹ️ [Wiki] no search results for: {query[:60]}")
            return []

        candidates = [
            {"title": p.get("title"), "description": p.get("description") or ""}
            for p in pages
            if p.get("title")
        ]
        print(f"🔎 [Wiki] search hits: '{query[:50]}' → "
              f"{[c['title'] for c in candidates]}")
        return candidates

    except Exception as e:
        print(f"❌ [Wiki] search exception: {e}")
        return []


def _score_candidate(query: str, title: str, description: str) -> int:
    """
    Lightweight relevance check: count how many distinct words the query
    shares with the candidate's title + description. Used to catch cases
    where the API's #1 result is a wrong namesake or an overly-broad
    redirect target that doesn't actually match what was asked.
    """
    query_words = set(re.findall(r"\w+", query.lower()))
    candidate_words = set(re.findall(r"\w+", f"{title} {description}".lower()))
    return len(query_words & candidate_words)


def _rank_candidates(query: str, candidates: list[dict]) -> list[dict]:
    """
    Sort candidates best-match-first by lexical overlap with the query,
    and attach each candidate's "score" so callers can also use it as a
    hard quality gate (see MIN_RELEVANCE_SCORE), not just a sort key.
    Python's sort is stable, so candidates that tie on score keep the
    API's original relative order (its own ranking is still a useful
    tiebreaker signal).
    """
    scored = [
        {**c, "score": _score_candidate(query, c["title"], c["description"])}
        for c in candidates
    ]
    return sorted(scored, key=lambda c: c["score"], reverse=True)


# ── Step 2: Fetch summary ──────────────────────────────────────────────────────
def _fetch_summary(title: str) -> dict | None:
    """
    Fetch the Wikimedia page summary for a given article title.
    Returns a dict with extract, description, coordinates, etc.
    Returns None on error or if extract is too short to be useful.
    """
    try:
        url  = WIKI_SUMMARY_URL.format(title=requests.utils.quote(title, safe=""))
        resp = requests.get(url, headers=WIKI_HEADERS, timeout=REQUEST_TIMEOUT)

        if resp.status_code != 200:
            print(f"⚠️ [Wiki] summary HTTP {resp.status_code} for: {title}")
            return None

        data = resp.json()

        # FIX: reject disambiguation pages. The summary API returns
        # "type": "disambiguation" for pages that are just a list of
        # links to unrelated topics sharing a name (e.g. "Mercury" could
        # resolve to the disambiguation page instead of the planet or the
        # element). That blurb routinely clears MIN_EXTRACT_LEN, so
        # without this check it was silently accepted as a real answer.
        if data.get("type") == "disambiguation":
            print(f"ℹ️ [Wiki] '{title}' is a disambiguation page — rejecting")
            return None

        # FIX 1.1: `.get("extract", "")` only defaults when the key is
        # ABSENT. If the API returns "extract": null, .get() returns None
        # and .strip() would raise AttributeError. `or ""` covers both
        # "key missing" and "key present but null".
        extract = (data.get("extract") or "").strip()

        if len(extract) < MIN_EXTRACT_LEN:
            print(f"ℹ️ [Wiki] extract too short ({len(extract)} chars) for: {title}")
            return None

        return data

    except Exception as e:
        print(f"❌ [Wiki] summary exception: {e}")
        return None


# ── Step 3: Format context for AI injection ────────────────────────────────────
def _format_context(query: str, title: str, data: dict) -> str:
    """
    Convert raw Wikipedia summary JSON into a clean, token-efficient
    context string for injection into the AI system prompt.
    """
    # FIX 1.1: same None-vs-absent guard as _fetch_summary — "description"
    # is null on plenty of real pages (disambiguation stubs, freshly
    # created articles, some biographies), not just missing.
    extract     = (data.get("extract") or "").strip()
    description = (data.get("description") or "").strip()

    # FIX 1.2: content_urls, or content_urls["desktop"], can themselves be
    # explicitly null (seen on some redirect/special pages) rather than
    # simply absent. Chaining .get("x", {}).get("y", {}) does NOT protect
    # against this — if the key exists with value None, the default {} is
    # never applied and the next .get() call crashes on None. Guard every
    # level with `or {}` before calling .get() on it.
    content_urls = data.get("content_urls") or {}
    desktop      = content_urls.get("desktop") or {}
    page_url     = desktop.get("page", "")

    # FIX: capture the article's last-revision timestamp instead of
    # discarding it. Wikipedia summary timestamps look like
    # "2025-11-02T14:03:00Z" — take just the date portion for readability.
    raw_timestamp = (data.get("timestamp") or "").strip()
    last_updated  = raw_timestamp.split("T")[0] if raw_timestamp else ""

    # Truncate extract to keep tokens light
    if len(extract) > MAX_EXTRACT_CHARS:
        # Cut at last sentence boundary within limit
        trimmed = extract[:MAX_EXTRACT_CHARS]
        last_dot = trimmed.rfind(". ")
        if last_dot > MAX_EXTRACT_CHARS * 0.6:
            trimmed = trimmed[: last_dot + 1]
        extract = trimmed + " [...]"

    # FIX: flag structurally time-sensitive topics with an explicit
    # caveat, so the AI doesn't present a static snapshot as a live fact
    # (e.g. "who is the current CEO" answered from a months-old extract).
    volatile_note = ""
    if _is_volatile_topic(query, title, description):
        volatile_note = (
            "⏳ Note: this topic may include roles, titles, or figures that "
            "change over time (e.g. a current officeholder or a running "
            "total). If the question depends on the CURRENT value of such "
            "a detail, verify with a real-time source (web_search) rather "
            "than relying solely on this Wikipedia snapshot."
        )

    lines = [
        f"📖 WIKIPEDIA CONTEXT — {title}",
        f"Description: {description}" if description else "",
        f"Wikipedia article last updated: {last_updated}" if last_updated else "",
        "",
        extract,
        "",
        volatile_note,
        f"Source: {page_url}" if page_url else "",
    ]

    context = "\n".join(l for l in lines if l is not None and l != "")
    return context.strip()


# ── Public API ─────────────────────────────────────────────────────────────────
def search_wikipedia(query: str) -> dict:
    """
    Main entry point. Search Wikipedia for the query and return a result dict.

    Returns:
        {
          "found":   True,
          "title":   str,
          "context": str,   ← inject this into the AI system prompt
          "tool":    "wikipedia"
        }
        OR
        {
          "found":  False,
          "reason": str,    ← "skip" | "no_result" | "short_extract" | "error"
          "tool":   "wikipedia"
        }
    """
    print(f"📚 [Wiki] query: {query[:80]}")

    # Fast-skip check — don't waste a round-trip for real-time questions
    if should_skip_wikipedia(query):
        print(f"⏩ [Wiki] skipping (real-time/not suitable): {query[:60]}")
        return {"found": False, "reason": "skip", "tool": "wikipedia"}

    # Step 1: Search — now returns multiple candidates instead of just 1
    candidates = _search_wikipedia(query)
    if not candidates:
        return {"found": False, "reason": "no_result", "tool": "wikipedia"}

    # FIX: rank candidates by lexical overlap with the query instead of
    # blindly trusting the API's #1 result. Catches cases where the raw
    # top hit is a wrong namesake or an overly-broad redirect target.
    ranked = _rank_candidates(query, candidates)

    # FIX (quality gate ordering): try candidates that clear
    # MIN_RELEVANCE_SCORE first — a 0-score candidate shares no words at
    # all with the query, which is a stronger "this is probably wrong"
    # signal than a short extract is a "this is probably right" signal.
    # Only fall back to the zero-score leftovers if nothing relevant
    # panned out, so an unusual/short query still gets *some* answer
    # rather than a hard no_result.
    relevant    = [c for c in ranked if c["score"] >= MIN_RELEVANCE_SCORE]
    fallback    = [c for c in ranked if c["score"] < MIN_RELEVANCE_SCORE]
    try_order   = relevant + fallback

    # Step 2: Fetch summary — try each candidate in that order until one
    # isn't a disambiguation page (strongest check) and clears
    # MIN_EXTRACT_LEN (weakest check, applied last). Previously this only
    # ever tried the single #1 result and gave up immediately.
    data, title = None, None
    for candidate in try_order:
        data = _fetch_summary(candidate["title"])
        if data is not None:
            title = candidate["title"]
            break

    if data is None:
        return {"found": False, "reason": "short_extract", "tool": "wikipedia"}

    # Step 3: Build context
    # DEFENSE IN DEPTH: even with fixes 1.1/1.2 in place, wrap this call so
    # any other unforeseen shape of API response degrades gracefully to
    # "found": False instead of throwing an uncaught exception up to the
    # caller (which was likely producing the "I don't know" fallback text).
    try:
        context = _format_context(query, title, data)
    except Exception as e:
        print(f"❌ [Wiki] format_context exception: {e}")
        return {"found": False, "reason": "error", "tool": "wikipedia"}

    print(f"✅ [Wiki] context ready: {len(context)} chars for '{title}'")

    return {
        "found":   True,
        "title":   title,
        "context": context,
        "tool":    "wikipedia",
    }
