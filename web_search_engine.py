"""
╔══════════════════════════════════════════════════════════════════════════════╗
║            CATURA AI — PRODUCTION WEB SEARCH ENGINE  v2.0                  ║
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

# ── How many raw results to fetch per engine ─────────────────────────────────
RESULTS_PER_ENGINE = 6
# ── How many results to deep-crawl with Firecrawl (expensive — keep low) ─────
FIRECRAWL_TOP_N    = 3
# ── Max Cohere rerank candidates ─────────────────────────────────────────────
RERANK_TOP_N       = 8


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — QUERY REWRITER
# Expands a user question into 2-3 targeted search queries for better coverage
# ══════════════════════════════════════════════════════════════════════════════

def rewrite_queries(original: str) -> list[str]:
    """
    Generate 2-3 smart search queries from a user question.
    No LLM call needed — pattern-based and fast.
    Returns list of unique queries, original always first.
    """
    lower    = original.lower().strip().rstrip("?.,!")
    queries  = [original]
    year_now = datetime.utcnow().year

    # ── Political / government queries — add recency + official ──────────────
    if re.search(r'\b(cm|chief minister|prime minister|president|governor|minister|mayor|ceo|chancellor|elected|appointed|won)\b', lower):
        queries.append(f"{lower} {year_now}")
        queries.append(f"latest {lower}")

    # ── Real-time / current events ────────────────────────────────────────────
    elif re.search(r'\b(latest|current|now|today|recently|new|just)\b', lower):
        queries.append(f"{lower} {year_now}")
        if not lower.startswith("news"):
            queries.append(f"news {lower}")

    # ── Factual / Wikipedia-style ─────────────────────────────────────────────
    elif re.search(r'\b(what is|who is|when was|where is|how does|explain|define)\b', lower):
        queries.append(f"{lower} explained")
        queries.append(f"{lower} official definition")

    # ── Price / finance ───────────────────────────────────────────────────────
    elif re.search(r'\b(price|stock|share|crypto|bitcoin|nifty|sensex|rate)\b', lower):
        queries.append(f"{lower} today {year_now}")
        queries.append(f"{lower} live price")

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


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — PARALLEL SEARCH (Tavily + Serper)
# Both engines run simultaneously via asyncio.gather
# ══════════════════════════════════════════════════════════════════════════════

def _tavily_search_sync(query: str, max_results: int = RESULTS_PER_ENGINE) -> list[dict]:
    """Synchronous Tavily search — called from thread pool."""
    if not TAVILY_KEY:
        return []
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key":             TAVILY_KEY,
                "query":               query,
                "max_results":         max_results,
                "include_answer":      True,
                "include_raw_content": False,
                "search_depth":        "basic",
            },
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

    except Exception as e:
        print(f"❌ [Tavily] {e}")
        return []


def _serper_search_sync(query: str, max_results: int = RESULTS_PER_ENGINE) -> list[dict]:
    """Synchronous Serper (Google Search API) — called from thread pool."""
    if not SERPER_KEY:
        return []
    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY":   SERPER_KEY,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": max_results, "gl": "in", "hl": "en"},
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
            })

        print(f"✅ [Serper] '{query[:60]}' → {len(results)} results")
        return results

    except Exception as e:
        print(f"❌ [Serper] {e}")
        return []


async def _search_parallel(query: str) -> tuple[list, list]:
    """Run Tavily + Serper simultaneously, return both result lists."""
    loop = asyncio.get_event_loop()
    tavily_task = loop.run_in_executor(None, _tavily_search_sync, query)
    serper_task = loop.run_in_executor(None, _serper_search_sync, query)
    tavily_res, serper_res = await asyncio.gather(tavily_task, serper_task)
    return tavily_res, serper_res


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
                "timeout": 15000,
            },
            timeout=20,
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
        print(f"❌ [Firecrawl] {url[:60]}: {e}")
        return None


def enrich_with_firecrawl(results: list[dict], top_n: int = FIRECRAWL_TOP_N) -> list[dict]:
    """
    For the top N crawlable results, replace their snippet with full page content.
    Runs synchronously (acceptable — called before streaming starts).
    """
    crawled = 0
    for r in results:
        if crawled >= top_n:
            break
        url = r.get("url", "")
        if not _should_crawl(url):
            continue
        full_content = _firecrawl_extract(url)
        if full_content:
            r["body"]           = full_content
            r["firecrawled"]    = True
            r["body_truncated"] = len(full_content) >= 2990
            crawled += 1

    print(f"🕷️ [Firecrawl] Enriched {crawled} results")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — TRUST SCORING
# Assigns a 0–100 trust score to each result based on domain reputation
# ══════════════════════════════════════════════════════════════════════════════

# Tier 1 — Highest trust (90–100): Government, major international news, academic
_TIER1 = {
    # Government / Official
    "gov", "gov.in", "nic.in", "india.gov.in", "pib.gov.in", "mea.gov.in",
    "rbi.org.in", "sebi.gov.in", "irdai.gov.in", "npci.org.in", "uidai.gov.in",
    "incometax.gov.in", "gst.gov.in", "isro.gov.in", "drdo.gov.in",
    "who.int", "un.org", "worldbank.org", "imf.org", "unicef.org",
    "cdc.gov", "nih.gov", "fda.gov",
    # Top-tier news
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk",
    # Academic / Reference
    "wikipedia.org", "britannica.com", "scholar.google.com", "pubmed.ncbi.nlm.nih.gov",
    "nature.com", "science.org", "thelancet.com",
    # Finance official
    "nseindia.com", "bseindia.com", "moneycontrol.com", "finance.yahoo.com",
}

# Tier 2 — High trust (75–89): Major Indian/international news, established tech
_TIER2 = {
    "timesofindia.com", "hindustantimes.com", "ndtv.com", "thehindu.com",
    "indianexpress.com", "livemint.com", "economictimes.indiatimes.com",
    "businessstandard.com", "financialexpress.com", "thewire.in",
    "theprint.in", "scroll.in", "thequint.com",
    "nytimes.com", "theguardian.com", "washingtonpost.com", "ft.com",
    "bloomberg.com", "cnbc.com", "forbes.com", "techcrunch.com",
    "stackoverflow.com", "github.com", "docs.python.org", "developer.mozilla.org",
    "microsoft.com", "google.com", "apple.com", "amazon.com",
    "openai.com", "anthropic.com",
    "healthline.com", "webmd.com", "mayoclinic.org",
}

# Tier 3 — Medium trust (55–74): Blogs, niche publications, wiki-style
_TIER3 = {
    "medium.com", "substack.com", "quora.com", "reddit.com",
    "towardsdatascience.com", "hackernoon.com", "dev.to",
    "geeksforgeeks.org", "w3schools.com", "tutorialspoint.com",
    "javatpoint.com", "codecademy.com",
    "cricbuzz.com", "espncricinfo.com", "sportskeeda.com",
    "livescience.com", "sciencedaily.com",
}

# Domains that carry a trust penalty — known for misinformation/low quality
_PENALISED = {
    "news18.com",  # sometimes sensationalist — not penalised hard, just lower
}


def compute_trust_score(url: str, result: dict) -> int:
    """
    Returns trust score 0–100 for a search result.
    Factors:
      - Domain tier (base score)
      - Multi-source confirmation (boost)
      - Direct answer from engine (boost)
      - Recency signals in URL (small boost)
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
    except Exception:
        domain = ""

    # Base score by tier
    score = 50  # default for unknown domains

    if any(domain == t or domain.endswith("." + t) for t in _TIER1):
        score = 92
    elif any(domain == t or domain.endswith("." + t) for t in _TIER2):
        score = 80
    elif any(domain == t or domain.endswith("." + t) for t in _TIER3):
        score = 62

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

    year_now = str(datetime.utcnow().year)
    if year_now in url:
        score = min(score + 3, 100)   # URL contains current year

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
            timeout=12,
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

    # Step 1: Query rewriting
    queries = rewrite_queries(query)
    print(f"📝 [Rewriter] Queries: {queries}")

    # Step 2: Parallel search across all rewritten queries
    all_raw: list[dict] = []
    for q in queries:
        # Run both engines — use asyncio.run for clean sync calling
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

    print(f"📊 [Pipeline] Raw results: {len(all_raw)}")

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

    # Step 4: Firecrawl enrichment (top crawlable results)
    enriched = enrich_with_firecrawl(deduped, top_n=FIRECRAWL_TOP_N)

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
