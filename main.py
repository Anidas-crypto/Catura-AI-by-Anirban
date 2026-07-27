from fastapi import FastAPI, Request, File, UploadFile, Depends, HTTPException, Header
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import requests
import anyio  # ✅ lets async routes run blocking calls (requests, supabase-py) in a worker
              #    thread instead of freezing the single Uvicorn event loop.
import os
import uuid
import json
import re
import time
import ipaddress
import socket
from urllib.parse import urlparse
from datetime import datetime, timezone
from supabase import create_client, Client
import base64
import io
from PIL import Image
from duckduckgo_search import DDGS
from wiki import search_wikipedia
import re
import logging

# ── Centralized logging ────────────────────────────────────────────────────
# Was: 118 bare print() calls scattered through this file. print() has no
# levels (info/warning/error), can't be filtered, doesn't ship cleanly to a
# log aggregator, and on Render its stdout buffering can interleave oddly
# with SSE heartbeat writes. Every print() below has been converted to the
# matching logger call based on its emoji prefix (❌ → error, ⚠️ → warning,
# everything else → info), so verbosity can now be turned down in production
# (e.g. `logging.WARNING`) by changing the `level=` below — one place,
# instead of touching every call site again.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("catura")

# ── Exception-handling policy ──────────────────────────────────────────────
# Broad `except Exception` blocks below are intentional in a few narrow
# places (best-effort cleanup, optional enrichment) but were previously
# used everywhere, which meant a real bug (e.g. a typo in a payload key
# raising TypeError/KeyError) looked identical in the logs to a transient
# network blip (requests.ConnectionError). The fix applied throughout this
# file:
#   1. Narrow `except` clauses to the specific exceptions a block can
#      actually raise (requests.exceptions.RequestException, KeyError,
#      TypeError, json.JSONDecodeError, etc.) wherever the failure mode is
#      known and expected — these are logged at WARNING/INFO without a
#      traceback, since they're routine.
#   2. Any remaining `except Exception` is a last-resort safety net for
#      *unexpected* errors only, and always logs via `logger.exception(...)`
#      (or the `_log_unexpected` helper below), which attaches the full
#      traceback — so a new bug is immediately visually distinct from a
#      known/expected failure in the logs.
#   3. A single top-level FastAPI exception handler (registered right after
#      `app = FastAPI()`) is the final backstop for anything that escapes
#      every route's own handling — it logs the full traceback once and
#      returns a generic 500 to the client, so unexpected errors can never
#      leak internals or crash silently.
def _log_unexpected(context: str, exc: Exception) -> None:
    """Log an exception that reached a generic `except Exception` fallback.

    Always includes the full traceback (via logger.exception) so these are
    easy to tell apart from routine, narrowly-caught, expected failures.
    """
    logger.exception(f"❌ [UNEXPECTED] {context}: {exc.__class__.__name__}: {exc}")


# ── Shared HTTP session (connection reuse / keep-alive) ────────────────────
# Every outbound call in this file (OpenRouter, Groq, Gemini, Poolside,
# Supabase admin API, weather/finance/news tools, etc.) was going through
# the plain `requests.get`/`requests.post`/`requests.delete` module-level
# functions, each of which opens a brand-new TCP+TLS connection per call —
# there's no connection reuse between requests, even to the same host, even
# from the same worker thread. On a Render instance making dozens of
# outbound calls a minute, that's a fresh TLS handshake every time.
#
# A single process-wide `requests.Session()` reuses connections via
# HTTP keep-alive (urllib3's connection pooling) whenever consecutive calls
# hit the same host, which is the common case here (repeated OpenRouter/
# Groq/Gemini calls in particular). `requests.Session` is documented as
# thread-safe for making requests concurrently from multiple threads, which
# matters since these calls run inside `anyio`'s worker-thread pool — so one
# shared instance across all routes/threads is safe and correct.
#
# Everywhere below that used to call the bare `requests.get(...)` /
# `requests.post(...)` / `requests.delete(...)` functions now calls
# `_http.get(...)` / `_http.post(...)` / `_http.delete(...)` instead.
# `requests.exceptions.*` references are untouched — those are exception
# classes, not calls, and stay on the `requests` module itself.
_http = requests.Session()
_http.headers.update({"User-Agent": "CaturaAI/1.0"})

# ── Centralized session cookie helper ─────────────────────────────────────────
# Every place in this file that issues the session_id cookie must go through
# this helper so the security flags stay consistent. Never hand-construct a
# "session_id=..." Set-Cookie string anywhere else.
SESSION_COOKIE_MAX_AGE = 31536000  # 1 year, in seconds

def build_session_cookie(session_id: str) -> str:
    """Builds the hardened Set-Cookie header value for the session cookie.

    Flags:
      - Secure    -> cookie is only ever sent over HTTPS, never plain HTTP.
      - HttpOnly  -> not readable via document.cookie, so XSS can't steal it.
      - SameSite=Strict -> not sent on cross-site requests (tighter than Lax).
    """
    return (
        f"session_id={session_id}; Path=/; SameSite=Strict; "
        f"Max-Age={SESSION_COOKIE_MAX_AGE}; Secure; HttpOnly"
    )

def set_session_cookie(response, session_id: str) -> None:
    """Attaches the hardened session cookie to a Response/StreamingResponse object."""
    response.headers["Set-Cookie"] = build_session_cookie(session_id)

# ── Production Search Engine (Tavily + Serper + Firecrawl + Cohere) ──────────
try:
    from web_search_engine import (
        run_production_search,
        build_production_search_context,
        build_production_sources_payload,
    )
    PRODUCTION_SEARCH_ENABLED = True
    logger.info("✅ [Search] Production search engine loaded (Tavily + Serper + Firecrawl + Cohere)")
except ImportError as _e:
    PRODUCTION_SEARCH_ENABLED = False
    logger.warning(f"⚠️ [Search] Production engine not available ({_e}) — falling back to legacy Tavily/DDG")

# ── File content fetcher ──────────────────────────────────────────────────────
# Extension → broad category map used when Content-Type is missing/wrong
_CODE_EXTENSIONS = {
    'py','js','ts','jsx','tsx','html','htm','css','json','java','c','cpp','cs',
    'go','rs','rb','php','swift','kt','sh','bash','zsh','xml','yaml','yml',
    'toml','md','markdown','sql','r','lua','dart','scala','hs','txt','csv',
    'dockerfile','makefile','gitignore','env','ini','cfg','conf','log',
}
_IMAGE_EXTENSIONS = {'jpg','jpeg','png','gif','webp','bmp','svg','ico','tiff','tif'}
_PDF_EXTENSION    = {'pdf'}
_DOC_EXTENSIONS   = {'doc','docx'}

MAX_TEXT_FILE_BYTES = 200_000   # ~200 KB — enough for large code files
MAX_IMAGE_B64_BYTES = 4_000_000 # 4 MB base64 limit for vision APIs
MAX_IMAGE_RAW_BYTES  = 15_000_000  # 15 MB raw download ceiling for images (before compression)
MAX_BINARY_DOC_BYTES = 15_000_000  # 15 MB raw download ceiling for PDF/docx (need full body to parse)
HARD_DOWNLOAD_CEILING_BYTES = 25_000_000  # 25 MB — reject anything claiming to be bigger than this, of any type


def _read_capped(resp, cap_bytes: int):
    """
    Reads a streaming `requests` response in chunks, stopping as soon as
    more than `cap_bytes` have been read — so we never buffer more than
    ~cap_bytes (+ one chunk) into memory, regardless of how large the
    remote file actually is.

    Returns (data: bytes, was_truncated: bool). `data` is capped to
    exactly `cap_bytes`.
    """
    chunks = []
    total = 0
    for chunk in resp.iter_content(chunk_size=8192):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total > cap_bytes:
            break
    data = b"".join(chunks)
    return data[:cap_bytes], total > cap_bytes


def _ext(url: str) -> str:
    """Return lowercase extension from a URL path, no dot."""
    path = url.split("?")[0].rstrip("/")
    dot  = path.rfind(".")
    return path[dot+1:].lower() if dot != -1 else ""


# ── SSRF protection for user-supplied file URLs ───────────────────────────────
# Only ever fetch files from hosts we explicitly trust (our own Supabase
# storage project, plus anything added via FILE_FETCH_EXTRA_HOSTS). This is
# resolved lazily (not at import time) so it always reflects the current
# SUPABASE_URL / env, and it re-validates on every call so nothing can bypass
# it by importing the module differently.
def _get_allowed_file_hosts() -> set:
    hosts = set()
    try:
        supa_host = urlparse(SUPABASE_URL).hostname
        if supa_host:
            hosts.add(supa_host.lower())
    except (ValueError, AttributeError) as e:
        logger.warning(f"⚠️ [_get_allowed_file_hosts] could not parse SUPABASE_URL: {e}")
    extra = os.getenv("FILE_FETCH_EXTRA_HOSTS", "")
    for h in extra.split(","):
        h = h.strip().lower()
        if h:
            hosts.add(h)
    return hosts


# ── SSRF protection for /api/skills/install ───────────────────────────────
# Skills are fetched from GitHub (raw file or blob link, auto-converted).
# This is a *separate* allow-list from _get_allowed_file_hosts() (our own
# Supabase storage host) — skill installs have no business reaching our own
# storage bucket or anywhere else, so they get their own narrow host list.
def _get_allowed_skill_hosts() -> set:
    hosts = {"github.com", "raw.githubusercontent.com"}
    extra = os.getenv("SKILL_FETCH_EXTRA_HOSTS", "")
    for h in extra.split(","):
        h = h.strip().lower()
        if h:
            hosts.add(h)
    return hosts


def _is_safe_public_url(url: str, allowed_hosts: set | None = None) -> tuple:
    """
    Returns (ok: bool, reason: str). Blocks:
      - non-http(s) schemes
      - hosts outside the allow-list (defaults to our own Supabase storage
        domain; pass a different `allowed_hosts` set to validate URLs
        against a different trusted host list, e.g. GitHub for skill installs)
      - hostnames that resolve to private / loopback / link-local / reserved
        IPs (defeats DNS rebinding, cloud metadata endpoints like
        169.254.169.254, localhost, internal Render service addresses, etc.)
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "Malformed URL"

    if parsed.scheme not in ("https", "http"):
        return False, "Unsupported URL scheme"

    if not parsed.hostname:
        return False, "URL has no hostname"

    allowed_hosts = allowed_hosts if allowed_hosts is not None else _get_allowed_file_hosts()
    if not allowed_hosts:
        # Fail closed: if we don't know our own storage host, don't fetch anything.
        return False, "File fetching is not configured (no allowed hosts)"

    if parsed.hostname.lower() not in allowed_hosts:
        return False, "URL host is not on the allowed file-storage domain"

    # Resolve the hostname and reject anything pointing at internal/private
    # network space. This also defeats DNS-rebinding attacks where the
    # allow-listed hostname is later re-pointed at an internal IP, since we
    # resolve fresh on every call right before use.
    try:
        resolved_ips = {res[4][0] for res in socket.getaddrinfo(parsed.hostname, None)}
    except (socket.gaierror, UnicodeError) as e:
        logger.info(f"[_is_safe_public_url] DNS resolution failed for {parsed.hostname!r}: {e}")
        return False, "Could not resolve host"

    for ip_str in resolved_ips:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, "Invalid resolved IP"
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, "URL resolves to a private/internal address"

    return True, ""


async def _safe_httpx_get(url: str, allowed_hosts: set, timeout: float = 15.0, max_redirects: int = 5):
    """
    SSRF-safe async GET: validates `url` against `allowed_hosts` (host
    allow-list + private/loopback/link-local IP rejection, via
    _is_safe_public_url) *before* every single request — including
    redirects. `follow_redirects` is intentionally left off: a host that
    passes the allow-list on the first request could otherwise 302 us
    anywhere (e.g. an internal address or cloud metadata endpoint), so each
    redirect hop is re-validated by hand instead of trusted blindly.

    Returns (response: httpx.Response | None, error: str | None).
    """
    import httpx as _safe_httpx

    ok, reason = _is_safe_public_url(url, allowed_hosts=allowed_hosts)
    if not ok:
        return None, f"URL not allowed: {reason}"

    async with _safe_httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        hops = 0
        while True:
            resp = await client.get(url)
            if resp.status_code not in (301, 302, 303, 307, 308):
                return resp, None
            hops += 1
            if hops > max_redirects:
                return None, "Too many redirects"
            location = resp.headers.get("Location")
            if not location:
                return None, "Redirect with no Location header"
            next_url = str(_safe_httpx.URL(url).join(location))
            ok, reason = _is_safe_public_url(next_url, allowed_hosts=allowed_hosts)
            if not ok:
                return None, f"Redirect target not allowed: {reason}"
            url = next_url


def _client_safe_error(e: Exception, context: str = "") -> str:
    """
    Use this everywhere an exception needs to be reflected back to the
    client (JSON error body, SSE `error` event, etc.).

    Never put `str(e)` directly into a response: exception text can contain
    internal file paths, Supabase/SQL error internals, upstream hostnames,
    or other implementation details that are useful in server logs but are
    an information-disclosure risk if sent to the browser. This logs the
    full exception (same `print`-based logging style already used
    throughout this file, so it still shows up in Render's log viewer) and
    returns a single generic, safe string for the response body instead.
    """
    logger.error(f"❌ [{context or 'error'}] {type(e).__name__}: {e}")
    return "Something went wrong. Please try again."


def fetch_file_content_for_ai(url: str) -> dict:
    """
    Download a file from `url` and return a structured dict:

      For text/code files:
        { "kind": "text", "ext": str, "content": str, "truncated": bool }

      For images (sent as base64 to vision-capable models):
        { "kind": "image", "ext": str, "mime": str, "b64": str }

      For PDFs / Word docs (extract text where possible):
        { "kind": "text", "ext": str, "content": str, "truncated": bool }

      On failure:
        { "kind": "error", "reason": str }
    """
    ok, reason = _is_safe_public_url(url)
    if not ok:
        return {"kind": "error", "reason": f"URL not allowed: {reason}"}

    ext = _ext(url)

    try:
        # allow_redirects=False: we never blindly follow a 3xx. If the trusted
        # storage host redirects us somewhere, we re-validate that target
        # against the same allow-list/private-IP checks before following it
        # manually, so an attacker can't use a redirect to pivot us onto an
        # internal address.
        resp = _http.get(url, timeout=12, stream=True, allow_redirects=False)

        redirect_hops = 0
        while resp.is_redirect and redirect_hops < 5:
            location = resp.headers.get("Location")
            if not location:
                return {"kind": "error", "reason": "Redirect with no Location header"}
            next_url = requests.compat.urljoin(url, location)
            ok, reason = _is_safe_public_url(next_url)
            if not ok:
                return {"kind": "error", "reason": f"Redirect target not allowed: {reason}"}
            url = next_url
            resp = _http.get(url, timeout=12, stream=True, allow_redirects=False)
            redirect_hops += 1

        if resp.status_code != 200:
            return {"kind": "error", "reason": f"HTTP {resp.status_code}"}

        # Bail before downloading anything if the server tells us up front
        # that the file is absurdly large. (Content-Length can be absent or
        # lied about, which is exactly why every branch below also uses a
        # capped streaming read instead of trusting this alone.)
        content_length = resp.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > HARD_DOWNLOAD_CEILING_BYTES:
                    return {"kind": "error", "reason": "File too large"}
            except ValueError:
                pass

        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()

        # ── Images ──────────────────────────────────────────────────────────
        is_image = ext in _IMAGE_EXTENSIONS or content_type.startswith("image/")
        if is_image:
            raw, _truncated = _read_capped(resp, MAX_IMAGE_RAW_BYTES)
            if len(raw) > MAX_IMAGE_B64_BYTES:
                # Compress via Pillow before encoding
                try:
                    img = Image.open(io.BytesIO(raw))
                    img.thumbnail((1280, 1280), Image.LANCZOS)
                    buf = io.BytesIO()
                    fmt = "JPEG" if ext in ("jpg","jpeg") else "PNG"
                    img.save(buf, format=fmt, quality=82)
                    raw = buf.getvalue()
                except (OSError, ValueError) as e:
                    logger.info(f"[fetch_file_content_for_ai] image resize skipped: {e}")
                    # send original if resize fails
            mime = content_type if content_type.startswith("image/") else f"image/{ext}"
            return {
                "kind": "image",
                "ext" : ext,
                "mime": mime,
                "b64" : base64.b64encode(raw).decode("utf-8"),
            }

        # ── PDF — extract text ───────────────────────────────────────────────
        if ext in _PDF_EXTENSION or content_type == "application/pdf":
            raw, doc_truncated = _read_capped(resp, MAX_BINARY_DOC_BYTES)
            if doc_truncated:
                return {"kind": "error", "reason": "PDF exceeds maximum allowed size"}
            text = _extract_pdf_text(raw)
            if not text:
                return {"kind": "error", "reason": "PDF has no extractable text (scanned/image-only)"}
            truncated = len(text) > MAX_TEXT_FILE_BYTES
            return {
                "kind"     : "text",
                "ext"      : "pdf",
                "content"  : text[:MAX_TEXT_FILE_BYTES],
                "truncated": truncated,
            }

        # ── Word docx — extract text ─────────────────────────────────────────
        if ext == "docx" or "wordprocessingml" in content_type:
            raw, doc_truncated = _read_capped(resp, MAX_BINARY_DOC_BYTES)
            if doc_truncated:
                return {"kind": "error", "reason": "Document exceeds maximum allowed size"}
            text = _extract_docx_text(raw)
            if not text:
                return {"kind": "error", "reason": "Could not extract text from .docx"}
            truncated = len(text) > MAX_TEXT_FILE_BYTES
            return {
                "kind"     : "text",
                "ext"      : "docx",
                "content"  : text[:MAX_TEXT_FILE_BYTES],
                "truncated": truncated,
            }

        # ── Text / code files ────────────────────────────────────────────────
        is_text = (
            ext in _CODE_EXTENSIONS
            or content_type.startswith("text/")
            or content_type in ("application/json", "application/xml",
                                "application/javascript", "application/x-yaml")
        )
        if is_text:
            raw, truncated = _read_capped(resp, MAX_TEXT_FILE_BYTES)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text = raw.decode("latin-1")
                except UnicodeDecodeError:
                    return {"kind": "error", "reason": "File is binary (not text)"}
            return {
                "kind"     : "text",
                "ext"      : ext or "txt",
                "content"  : text,
                "truncated": truncated,
            }

        # ── Unknown type — try reading as text anyway ────────────────────────
        raw, truncated = _read_capped(resp, MAX_TEXT_FILE_BYTES)
        try:
            text = raw.decode("utf-8")
            return {
                "kind"     : "text",
                "ext"      : ext or "bin",
                "content"  : text,
                "truncated": truncated,
            }
        except UnicodeDecodeError:
            return {"kind": "error", "reason": f"Unsupported binary file type: {ext or content_type}"}

    except requests.exceptions.Timeout:
        return {"kind": "error", "reason": "Timeout downloading file"}
    except requests.exceptions.RequestException as exc:
        logger.warning(f"⚠️ [fetch_file_content_for_ai] network error: {exc}")
        return {"kind": "error", "reason": _client_safe_error(exc, "fetch_file_content_for_ai")}
    except Exception as exc:
        _log_unexpected("fetch_file_content_for_ai", exc)
        return {"kind": "error", "reason": _client_safe_error(exc, "fetch_file_content_for_ai")}


def _extract_pdf_text(raw: bytes) -> str:
    """Extract plain text from a PDF binary using pypdf (falls back to pdfminer)."""
    # Try pypdf first (fast, no extra deps beyond what's usually installed)
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(raw))
        parts  = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        return "\n\n".join(parts).strip()
    except ImportError:
        pass
    except Exception as e:
        # pypdf can raise many different internal errors on malformed PDFs
        # (PdfReadError and friends aren't consistently exported across
        # versions), so this stays broad — but we still log it as an
        # unexpected event rather than swallowing it silently, since a
        # working PDF failing to parse here is worth knowing about.
        _log_unexpected("_extract_pdf_text (pypdf)", e)

    # Fallback: try pdfminer.six
    try:
        from pdfminer.high_level import extract_text as pm_extract
        return pm_extract(io.BytesIO(raw)).strip()
    except ImportError:
        pass
    except Exception as e:
        _log_unexpected("_extract_pdf_text (pdfminer)", e)

    return ""


def _extract_docx_text(raw: bytes) -> str:
    """Extract plain text from a .docx binary using python-docx."""
    try:
        import docx
        doc   = docx.Document(io.BytesIO(raw))
        lines = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(lines).strip()
    except ImportError:
        pass
    except Exception as e:
        # python-docx doesn't export a single consistent exception type for
        # malformed .docx files either — keep this broad, but logged.
        _log_unexpected("_extract_docx_text", e)
    return ""


def build_file_context_for_prompt(file_urls: list) -> tuple:
    """
    For each URL in file_urls, fetch content and build:
      - `text_context`  : str  injected into system / user prompt
      - `vision_images` : list of {"mime":…, "b64":…} for vision-capable models

    Returns (text_context: str, vision_images: list)
    """
    if not file_urls:
        return "", []

    text_parts    = []
    vision_images = []

    for url in file_urls:
        if not url:
            continue
        result = fetch_file_content_for_ai(url)
        kind   = result.get("kind")
        ext    = result.get("ext", "")

        if kind == "image":
            vision_images.append({"mime": result["mime"], "b64": result["b64"]})
            text_parts.append(f"[Image attached: {ext.upper()} — analysed via vision]")

        elif kind == "text":
            content   = result["content"]
            truncated = result.get("truncated", False)
            label     = ext.upper() if ext else "FILE"
            header    = f"=== ATTACHED FILE ({label}) ==="
            footer    = "[...file truncated — showing first 200 KB...]" if truncated else f"=== END OF {label} ==="
            text_parts.append(f"{header}\n{content}\n{footer}")

        elif kind == "error":
            reason = result.get("reason", "unknown error")
            text_parts.append(f"[File could not be read: {reason}]")

    text_context = "\n\n".join(text_parts)
    return text_context, vision_images

app = FastAPI()


# ── Single top-level handler for genuinely unexpected exceptions ──────────
# This is the final backstop: individual routes below still catch and
# narrow the exceptions they expect (network errors, bad JSON, missing
# keys, auth failures) and respond appropriately. Anything that isn't
# caught there — a real bug — lands here exactly once, gets logged with a
# full traceback, and the client gets a generic, non-leaky 500. This is
# what makes a *new* bug visibly different from routine, expected errors.
@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(
        f"❌ [UNHANDLED] {request.method} {request.url.path} — "
        f"{exc.__class__.__name__}: {exc}"
    )
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": "Internal server error"},
    )


# ✅ ROUTE-LEVEL RATE LIMITING (slowapi) — separate from the custom /chat
# per-user-per-model quota above. This covers /api/memory/*, /generate-title,
# and /search with simple per-IP limits.
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ✅ CORS MIDDLEWARE
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://catura.duckdns.org", "https://my-ai-assistant-9bbd.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ MOUNT STATIC FILES
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 🧠 In-memory session store — defined below, after LRUDict (see "BOUNDED LRU DICT")
# so it can reuse the same bounded/eviction pattern as _chat_rate_store instead of
# growing forever as new session_ids show up.

# ✅ SUPABASE CLIENT
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
# Admin client uses service_role key → bypasses Row Level Security for server-side writes
# Set SUPABASE_SERVICE_KEY in your Render env vars (Supabase → Project Settings → API → service_role)
_svc_key = os.getenv("SUPABASE_SERVICE_KEY", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
_supabase_admin: Client = create_client(SUPABASE_URL, _svc_key) if _svc_key else supabase


async def _db(func, *args, **kwargs):
    """
    Run a blocking call (supabase-py, or anything else synchronous) in a
    worker thread instead of the event loop.

    supabase-py has no official async client, so every `supabase.table(...)`
    / `supabase.auth.get_user(...)` call is a blocking network call. Calling
    one of those directly inside an `async def` route freezes Uvicorn's
    single-threaded event loop for the duration of the call — no other
    request (not even /health) can be served in the meantime. Wrapping the
    call with this helper (a thin wrapper over anyio.to_thread.run_sync, the
    mechanism FastAPI itself uses for sync path operations) moves it off the
    event loop so concurrent requests keep flowing.

    Usage:  result = await _db(lambda: supabase.table("x").select("*").execute())
    """
    return await anyio.to_thread.run_sync(lambda: func(*args, **kwargs))


# ✅ AUTH DEPENDENCY — validates Supabase JWT from Authorization header
async def require_auth(authorization: str = Header(default=None)) -> dict:
    """
    FastAPI dependency that requires a valid Supabase-issued JWT.
    Expects header: Authorization: Bearer <access_token>
    Raises 401 if missing/invalid. Returns {"user_id": ..., "email": ...} on success.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        # Validate against Supabase using the anon-key client (not the admin client).
        # This runs on every authenticated request, so keeping it off the event
        # loop (via _db) matters a lot for concurrency. Any failure here (bad
        # signature, expired token, network hiccup talking to Supabase) is an
        # expected "not authenticated" case — gotrue doesn't expose a single
        # stable exception type across versions, so we deliberately treat any
        # failure as "invalid token" rather than leaking internals.
        user_response = await _db(supabase.auth.get_user, token)
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from e

    user = getattr(user_response, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return {"user_id": user.id, "email": user.email}

# ============================================================
# ✅ BOUNDED LRU DICT — generic helper for in-memory caches/trackers
# so nothing here grows unbounded on a long-running Render instance.
# ============================================================
import time as _rl_time
import threading as _rl_threading
from collections import OrderedDict as _RL_OrderedDict


class LRUDict(_RL_OrderedDict):
    """
    Ordinary dict semantics, but bounded: once `maxsize` keys are stored,
    the least-recently-used key is evicted on the next insert. Access via
    __getitem__ or get_touch() refreshes recency.
    """
    def __init__(self, maxsize=5000, *args, **kwargs):
        self.maxsize = maxsize
        super().__init__(*args, **kwargs)

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            self.popitem(last=False)  # evict oldest / least-recently-used

    def get_touch(self, key, default=None):
        if key in self:
            self.move_to_end(key)
            return super().__getitem__(key)
        return default


# 🧠 In-memory session store — was a plain `{}`, which never removes a
# session_id once created, so the process grew forever on a long-running
# Render instance (each session's message list was capped at 40, but the
# dict of sessions itself was unbounded — a slow memory leak / eventual
# OOM risk). Reuses the same bounded LRU-eviction pattern as
# _chat_rate_store below: once 20,000 distinct sessions are stored, the
# least-recently-used session is evicted automatically on the next insert.
# All existing `user_memory[session_id] = ...` / `user_memory[session_id]`
# call sites work unchanged since LRUDict is a drop-in OrderedDict subclass.
user_memory: "LRUDict" = LRUDict(maxsize=20000)


# ============================================================
# ✅ FAIR-USE QUOTA — 15 messages per model per rolling 20-minute window
# Keyed on "user_id:model" so each model has its own independent counter.
# In-memory + a lock: fine for a single Render instance. If this app ever
# scales to multiple instances/dynos, this must move to Redis (e.g.
# INCR + PEXPIRE or a sorted-set sliding-window) since each instance would
# otherwise track its own independent counters and users could get up to
# N_instances × 15 messages per window by hitting different instances.
# ============================================================
CHAT_RATE_LIMIT = 15            # messages
CHAT_RATE_WINDOW_SECONDS = 20 * 60   # 20 minutes, rolling — not a fixed clock reset

_chat_rate_store: "LRUDict" = LRUDict(maxsize=20000)   # "user_id:model" -> [timestamps]
_chat_rate_lock = _rl_threading.Lock()


def _chat_quota_check(user_id: str, model: str):
    """
    Call once, at the very start of /chat, before any upstream AI call.
    Returns (allowed, remaining, reset_epoch_seconds, retry_after_seconds).
    """
    key = f"{user_id}:{model}"
    now = _rl_time.time()
    with _chat_rate_lock:
        timestamps = _chat_rate_store.get_touch(key, [])
        # Rolling window — each timestamp expires independently 20 min after it was recorded
        timestamps = [ts for ts in timestamps if now - ts < CHAT_RATE_WINDOW_SECONDS]

        if len(timestamps) >= CHAT_RATE_LIMIT:
            oldest = timestamps[0]
            reset_epoch = oldest + CHAT_RATE_WINDOW_SECONDS
            retry_after = max(1, int(round(reset_epoch - now)))
            _chat_rate_store[key] = timestamps  # keep pruned list; don't record this rejected attempt
            return False, 0, reset_epoch, retry_after

        timestamps.append(now)
        _chat_rate_store[key] = timestamps
        remaining = CHAT_RATE_LIMIT - len(timestamps)
        reset_epoch = timestamps[0] + CHAT_RATE_WINDOW_SECONDS
        return True, remaining, reset_epoch, 0

# ✅ RENDER APP URL
APP_URL = os.getenv("APP_URL", "https://my-ai-assistant-9bbd.onrender.com/")

# ✅ API KEYS (set these as environment variables)
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")       # https://openweathermap.org/api (free)
NEWSDATA_API_KEY    = os.getenv("NEWSDATA_API_KEY", "")           # https://newsdata.io (free)
ALPHAVANTAGE_KEY    = os.getenv("ALPHAVANTAGE_API_KEY", "")       # https://www.alphavantage.co (free)
CRICAPI_KEY         = os.getenv("CRICAPI_KEY", "")                # https://www.cricapi.com (free)
GEMINI_API_KEY      = os.getenv("GEMINI_API_KEY", "")               # https://aistudio.google.com (free)
TAVILY_API_KEY      = os.getenv("TAVILY_API_KEY", "")               # https://tavily.com (free — 1000 searches/month)
GROQ_API_KEY        = os.getenv("GROQ_API_KEY", "")                 # https://console.groq.com (free tier)
ZAI_API_KEY         = os.getenv("ZAI_API_KEY", "")                  # https://z.ai (GLM-4.7-Flash — free tier)
SERPER_API_KEY      = os.getenv("SERPER_API_KEY", "")               # https://serper.dev (2500 free searches)
FIRECRAWL_API_KEY   = os.getenv("FIRECRAWL_API_KEY", "")            # https://firecrawl.dev (free tier)
COHERE_API_KEY      = os.getenv("COHERE_API_KEY", "")               # https://cohere.com (1000 free reranks/month)




# ============================================================
# ✅ CACHE CONTROL MIDDLEWARE
# ============================================================
@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in ("/", "/auth") or request.url.path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.url.path in ["/manifest.json", "/service-worker.js"]:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


# ============================================================
# ✅ STATIC PAGE ROUTES
# ============================================================
@app.get("/")
def home():
    return FileResponse(os.path.join(BASE_DIR, "index.html"), media_type="text/html")

@app.get("/auth.html")
def auth_page_html():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/auth", status_code=301)

@app.get("/auth")
def auth_page():
    p = os.path.join(BASE_DIR, "auth.html")
    if not os.path.isfile(p): return JSONResponse({"error": "auth.html not found"}, status_code=404)
    return FileResponse(p, media_type="text/html")

@app.get("/manifest.json")
async def serve_manifest():
    p = os.path.join(BASE_DIR, "manifest.json")
    if not os.path.isfile(p): return JSONResponse({"error": "manifest.json not found"}, status_code=404)
    return FileResponse(p, media_type="application/manifest+json")

@app.get("/service-worker.js")
async def serve_sw():
    p = os.path.join(BASE_DIR, "service-worker.js")
    if not os.path.isfile(p): return JSONResponse({"error": "service-worker.js not found"}, status_code=404)
    return FileResponse(p, media_type="application/javascript")

@app.get("/share/{slug}")
def share_page(slug: str):
    # Serves the read-only viewer shell. The page itself fetches the
    # actual conversation from Supabase client-side using the slug —
    # this route just needs to exist so refresh / direct-visit works
    # (i.e. it's not a client-side-only route that 404s on reload).
    p = os.path.join(BASE_DIR, "share.html")
    if not os.path.isfile(p):
        return JSONResponse({"error": "share.html not found"}, status_code=404)
    return FileResponse(p, media_type="text/html")

@app.get("/ping")
def ping():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "version": "0.0.414"}

@app.get("/google5869a60ba00ea65a.html")
def google_verify():
    p = os.path.join(BASE_DIR, "google5869a60ba00ea65a.html")
    if not os.path.isfile(p): return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p, media_type="text/html")

@app.get("/health")
def health_check():
    return {"status": "healthy", "version": "0.0.414", "timestamp": datetime.utcnow().isoformat()}

# ── 🧠 MEMORY MODELS ────────────────────────────────────────────────────────
from pydantic import BaseModel as _MemBaseModel
from typing import List as _MemList

class MemorySaveRequest(_MemBaseModel):
    memory_text: str
    user_id: str | None = None   # optional legacy field; ignored if it doesn't match the authed user

class MemoryClearRequest(_MemBaseModel):
    user_id: str | None = None   # optional legacy field; ignored if it doesn't match the authed user

class MemoryExtractRequest(_MemBaseModel):
    message: str
    existing_memories: list = []
    user_id: str | None = None   # optional legacy field; ignored if it doesn't match the authed user

# Small helper: if the client still sends a user_id, it must match the authenticated
# user or we reject outright. Otherwise we always use the token's user_id.
def _resolve_owner(auth: dict, client_supplied_user_id: str | None) -> str:
    if client_supplied_user_id and client_supplied_user_id != auth["user_id"]:
        raise HTTPException(status_code=403, detail="user_id does not match authenticated user")
    return auth["user_id"]

# ============================================================
# ✅ SKILLS FEATURE — Stage 1: data layer + endpoints
# Paste this block into main.py right after MemoryExtractRequest
# class definition (around line 529), BEFORE the memory endpoints
# or anywhere else at module scope after `_MemBaseModel` is imported.
# ============================================================

import yaml as _skill_yaml  # add `pyyaml` to requirements.txt if not already present


class SkillInstallRequest(_MemBaseModel):
    source_url: str
    user_id: str | None = None


class SkillToggleRequest(_MemBaseModel):
    skill_id: str
    enabled: bool


class SkillDeleteRequest(_MemBaseModel):
    skill_id: str


def _parse_skill_md(raw: str, fallback_name: str = "Untitled Skill") -> dict:
    """
    Parses a SKILL.md file. Supports YAML frontmatter:

        ---
        name: rag-blueprint
        description: Deploy, configure, troubleshoot RAG pipelines.
        trigger_hint: use when user mentions RAG, retrieval, vector DB
        ---
        (full instructions body...)

    Falls back gracefully if no frontmatter is present — uses the first
    heading as the name and the whole file as content.
    """
    name = fallback_name
    description = ""
    trigger_hint = ""
    body = raw.strip()

    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = _skill_yaml.safe_load(parts[1]) or {}
                name = str(meta.get("name", fallback_name)).strip()
                description = str(meta.get("description", "")).strip()
                trigger_hint = str(meta.get("trigger_hint", "")).strip()
            except (_skill_yaml.YAMLError, AttributeError, TypeError) as e:
                logger.warning(f"⚠️ [Skills] frontmatter parse failed, using raw file: {e}")
            body = parts[2].strip()

    if not description:
        # fallback: first non-empty line that isn't a heading marker
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                description = line[:200]
                break

    if name == fallback_name:
        first_heading = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if first_heading:
            name = first_heading.group(1).strip()

    return {
        "name": name or fallback_name,
        "description": description or "No description provided.",
        "trigger_hint": trigger_hint,
        "content": body,
    }


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "skill"


def get_matching_skills(user_id: str, prompt: str, max_skills: int = 2) -> list:
    """
    Cheap keyword-based skill router (no extra LLM call, no extra latency).
    Loads the user's ENABLED skills (own + preinstalled), and returns the
    top `max_skills` whose name/description/trigger_hint overlap with the
    user's message. Returns [] if nothing matches — normal replies are
    completely unaffected.
    """
    try:
        own = _supabase_admin.table("skills") \
            .select("id, name, description, trigger_hint, content") \
            .eq("user_id", user_id).eq("enabled", True).execute()
        preinstalled = _supabase_admin.table("skills") \
            .select("id, name, description, trigger_hint, content") \
            .is_("user_id", "null").eq("is_preinstalled", True).eq("enabled", True).execute()
        all_skills = (own.data or []) + (preinstalled.data or [])
        if not all_skills:
            return []

        lower_prompt = prompt.lower()
        prompt_words = set(re.findall(r"[a-z0-9]+", lower_prompt))

        scored = []
        for s in all_skills:
            haystack = f"{s.get('name','')} {s.get('description','')} {s.get('trigger_hint','')}".lower()
            skill_words = set(re.findall(r"[a-z0-9]+", haystack))
            skill_words = {w for w in skill_words if len(w) > 3}
            overlap = prompt_words & skill_words
            if overlap:
                scored.append((len(overlap), s))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:max_skills]]
    except Exception as e:
        _log_unexpected("Skills routing (non-fatal, skipping skills)", e)
        return []


def build_skill_context(matched_skills: list) -> str:
    """Formats matched skills into a system-prompt block, capped so it can't blow up token usage."""
    if not matched_skills:
        return ""
    block = (
        "\n\n## 🛠️ ACTIVE SKILLS\n"
        "The following skill instructions apply to this message. Follow them precisely "
        "and let them guide your approach and answer quality:\n\n"
    )
    for s in matched_skills:
        block += f"### Skill: {s.get('name','Unnamed')}\n{s.get('content','')[:6000]}\n\n"
    return block


@app.post("/api/skills/install")
@limiter.limit("10/minute")
async def install_skill(request: Request, req: SkillInstallRequest, auth: dict = Depends(require_auth)):
    """
    Installs a skill from a URL. Supports:
      - Direct raw SKILL.md links (raw.githubusercontent.com/.../SKILL.md)
      - GitHub blob links (auto-converted to raw)
    """
    try:
        user_id = _resolve_owner(auth, req.user_id)
        url = req.source_url.strip()
        if not url:
            return JSONResponse({"ok": False, "error": "Missing source_url"}, status_code=400)

        # Convert github.com/.../blob/... links to raw links automatically
        fetch_url = url
        if "github.com" in url and "/blob/" in url:
            fetch_url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")

        # SSRF guard: only ever fetch from GitHub (raw or blob), reject
        # private/internal IPs, and re-validate every redirect hop by hand
        # instead of following redirects blindly.
        resp, err = await _safe_httpx_get(fetch_url, allowed_hosts=_get_allowed_skill_hosts(), timeout=15.0)
        if resp is None:
            return JSONResponse({"ok": False, "error": err or "Could not fetch skill. Check the link."}, status_code=400)
        if resp.status_code != 200:
            return JSONResponse(
                {"ok": False, "error": f"Could not fetch skill (HTTP {resp.status_code}). Check the link."},
                status_code=400,
            )
        raw_content = resp.text

        if len(raw_content.strip()) < 10:
            return JSONResponse({"ok": False, "error": "Fetched file is empty or invalid."}, status_code=400)

        fallback_name = url.rstrip("/").split("/")[-1].replace(".md", "").replace("-", " ").title()
        parsed = _parse_skill_md(raw_content, fallback_name=fallback_name)

        row = {
            "user_id": user_id,
            "name": parsed["name"][:120],
            "slug": _slugify(parsed["name"]),
            "description": parsed["description"][:500],
            "trigger_hint": parsed["trigger_hint"][:300],
            "content": parsed["content"][:20000],  # sane cap to protect prompt size
            "source_url": url,
            "is_preinstalled": False,
            "enabled": True,
            "updated_at": datetime.utcnow().isoformat(),
        }
        result = await _db(lambda: _supabase_admin.table("skills").insert(row).execute())
        return JSONResponse({"ok": True, "skill": result.data[0] if result.data else row})

    except HTTPException:
        raise
    except Exception as e:
        _log_unexpected("Skills install", e)
        return JSONResponse({"ok": False, "error": _client_safe_error(e, "Skills install")}, status_code=500)


@app.get("/api/skills/list")
@limiter.limit("30/minute")
async def list_skills(request: Request, auth: dict = Depends(require_auth)):
    try:
        user_id = auth["user_id"]

        def _load():
            own = _supabase_admin.table("skills") \
                .select("id, name, slug, description, trigger_hint, source_url, is_preinstalled, enabled, created_at") \
                .eq("user_id", user_id).order("created_at", desc=True).execute()
            preinstalled = _supabase_admin.table("skills") \
                .select("id, name, slug, description, trigger_hint, source_url, is_preinstalled, enabled, created_at") \
                .is_("user_id", "null").eq("is_preinstalled", True).execute()
            return (own.data or []) + (preinstalled.data or [])

        skills = await _db(_load)
        return JSONResponse({"ok": True, "skills": skills})
    except Exception as e:
        _log_unexpected("Skills list", e)
        return JSONResponse({"ok": False, "skills": []})


@app.post("/api/skills/toggle")
@limiter.limit("30/minute")
async def toggle_skill(request: Request, req: SkillToggleRequest, auth: dict = Depends(require_auth)):
    try:
        user_id = auth["user_id"]
        await _db(lambda: _supabase_admin.table("skills")
                  .update({"enabled": req.enabled})
                  .eq("id", req.skill_id).eq("user_id", user_id).execute())
        return JSONResponse({"ok": True})
    except Exception as e:
        _log_unexpected("Skills toggle", e)
        return JSONResponse({"ok": False, "error": _client_safe_error(e, "Skills toggle")}, status_code=500)


@app.delete("/api/skills/delete")
@limiter.limit("10/minute")
async def delete_skill(request: Request, id: str, auth: dict = Depends(require_auth)):
    try:
        user_id = auth["user_id"]
        await _db(lambda: _supabase_admin.table("skills").delete().eq("id", id).eq("user_id", user_id).execute())
        return JSONResponse({"ok": True})
    except Exception as e:
        _log_unexpected("Skills delete", e)
        return JSONResponse({"ok": False, "error": _client_safe_error(e, "Skills delete")}, status_code=500)

# ── 🧠 MEMORY ENDPOINTS ───────────────────────────────────────────────────────

@app.post("/api/memory/save")
@limiter.limit("10/minute")
async def save_memory(request: Request, req: MemorySaveRequest, auth: dict = Depends(require_auth)):
    try:
        user_id = _resolve_owner(auth, req.user_id)
        if not req.memory_text.strip():
            return JSONResponse({"ok": False, "error": "Missing fields"}, status_code=400)
        await _db(lambda: _supabase_admin.table("user_memories").insert({
            "user_id": user_id,
            "memory_text": req.memory_text.strip()[:500],
            "created_at": datetime.utcnow().isoformat()
        }).execute())
        return JSONResponse({"ok": True})
    except HTTPException:
        raise
    except Exception as e:
        _log_unexpected("Memory save", e)
        return JSONResponse({"ok": False, "error": _client_safe_error(e, "Memory save")}, status_code=500)


@app.get("/api/memory/load")
@limiter.limit("10/minute")
async def load_memory(request: Request, auth: dict = Depends(require_auth)):
    try:
        user_id = auth["user_id"]
        result = await _db(lambda: _supabase_admin.table("user_memories")
                            .select("id, memory_text, created_at")
                            .eq("user_id", user_id)
                            .order("created_at", desc=False)
                            .limit(100).execute())
        return JSONResponse({"ok": True, "memories": result.data or []})
    except Exception as e:
        _log_unexpected("Memory load", e)
        return JSONResponse({"ok": False, "memories": []})


@app.delete("/api/memory/clear")
@limiter.limit("10/minute")
async def clear_memory(request: Request, req: MemoryClearRequest, auth: dict = Depends(require_auth)):
    try:
        user_id = _resolve_owner(auth, req.user_id)
        await _db(lambda: _supabase_admin.table("user_memories").delete().eq("user_id", user_id).execute())
        return JSONResponse({"ok": True})
    except HTTPException:
        raise
    except Exception as e:
        _log_unexpected("Memory clear", e)
        return JSONResponse({"ok": False, "error": _client_safe_error(e, "Memory clear")}, status_code=500)


@app.delete("/api/memory/delete-one")
@limiter.limit("10/minute")
async def delete_one_memory(request: Request, id: str, auth: dict = Depends(require_auth)):
    try:
        user_id = auth["user_id"]
        await _db(lambda: _supabase_admin.table("user_memories").delete().eq("id", id).eq("user_id", user_id).execute())
        return JSONResponse({"ok": True})
    except Exception as e:
        _log_unexpected("Memory delete-one", e)
        return JSONResponse({"ok": False, "error": _client_safe_error(e, "Memory delete-one")}, status_code=500)


@app.post("/api/memory/extract")
@limiter.limit("10/minute")
async def extract_and_save_memory(request: Request, req: MemoryExtractRequest, auth: dict = Depends(require_auth)):
    """AI-powered: extract personal facts from a message, save new ones to Supabase."""
    try:
        user_id = _resolve_owner(auth, req.user_id)
        if not req.message.strip():
            return JSONResponse({"ok": False, "facts": []})

        existing_str = "\n".join(f"- {m}" for m in req.existing_memories[:30]) if req.existing_memories else "None yet."

        extraction_prompt = (
            "You are a personal memory extraction assistant for an AI chat app.\n"
            "Your ONLY job: read the user message below and extract personal facts as JSON.\n\n"
            f"User message: \"{req.message}\"\n\n"
            f"Already known (DO NOT repeat these):\n{existing_str}\n\n"
            "Extract ALL that apply: name, age, location/city/country, job/profession, "
            "education/course/year/semester, hobbies, interests, preferences, goals, dreams, "
            "skills, languages, relationships, schedule, subjects studied, projects.\n\n"
            "STRICT RULES:\n"
            "- Only extract facts EXPLICITLY stated — never infer or assume\n"
            "- Write each fact as: \'The user [fact]\' e.g. \'The user\'s name is Anirban Das\'\n"
            "- \'my name is X\' → \'The user\'s name is X\'\n"
            "- \'I study BCA\' → \'The user studies BCA\'\n"
            "- \'I am from Kolkata\' → \'The user is from Kolkata\'\n"
            "- Return empty list if zero personal facts\n"
            "- Output ONLY raw JSON — no markdown, no backticks, no explanation\n"
            'REQUIRED FORMAT (nothing else): {"facts": ["fact1", "fact2"]}\n'
        )

        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            return JSONResponse({"ok": False, "facts": []})

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://catura.ai",
        }

        # Model fallback chain — try in order until one succeeds
        # Primary: a reliable small model; fallbacks in case of rate limit or empty response
        EXTRACTION_MODELS = [
            "meta-llama/llama-3.1-8b-instruct:free",
            "mistralai/mistral-7b-instruct:free",
            "openai/gpt-oss-20b:free",
            "google/gemma-2-9b-it:free",
        ]

        import httpx as _httpx
        text = None
        for model_name in EXTRACTION_MODELS:
            payload = {
                "model": model_name,
                "max_tokens": 300,
                "temperature": 0.1,
                "messages": [{"role": "user", "content": extraction_prompt}]
            }
            try:
                async with _httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
                if r.status_code == 200:
                    raw = r.json()
                    candidate = raw.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    candidate = candidate.replace("```json", "").replace("```", "").strip()
                    if candidate and "{" in candidate:
                        text = candidate
                        logger.info(f"🧠 [Memory] extraction model used: {model_name}")
                        break
                    else:
                        logger.warning(f"⚠️ [Memory] {model_name} returned empty/bad content, trying next")
                else:
                    logger.warning(f"⚠️ [Memory] {model_name} HTTP {r.status_code}, trying next")
            except _httpx.HTTPError as model_err:
                logger.warning(f"⚠️ [Memory] {model_name} network error: {model_err}, trying next")
            except Exception as model_err:
                _log_unexpected(f"Memory extraction model {model_name}", model_err)

        if not text:
            logger.error("❌ [Memory] all extraction models failed")
            return JSONResponse({"ok": False, "facts": []})

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Try to salvage a partial JSON response
            import re as _re_mem
            match = _re_mem.search(r'\{.*?"facts".*?\].*?\}', text, _re_mem.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                except json.JSONDecodeError:
                    logger.error(f"❌ [Memory] AI returned invalid JSON: {text[:200]}")
                    return JSONResponse({"ok": False, "facts": []})
            else:
                logger.error(f"❌ [Memory] AI returned invalid JSON: {text[:200]}")
                return JSONResponse({"ok": False, "facts": []})
        facts = parsed.get("facts", [])
        if not isinstance(facts, list):
            return JSONResponse({"ok": False, "facts": []})

        def _save_facts():
            saved = []
            for fact in facts[:10]:
                fact = str(fact).strip()[:300]
                if not fact:
                    continue
                try:
                    _supabase_admin.table("user_memories").insert({
                        "user_id": user_id,
                        "memory_text": fact,
                        "created_at": datetime.utcnow().isoformat()
                    }).execute()
                    saved.append(fact)
                except Exception as save_err:
                    # supabase-py doesn't expose a narrow, stable exception
                    # type for insert failures, so this stays broad — but a
                    # failed save is unusual enough to warrant a traceback.
                    _log_unexpected(f"Memory save in extract (fact={fact[:40]!r})", save_err)
            return saved

        saved_facts = await _db(_save_facts)

        logger.info(f"🧠 [Memory] extracted {len(saved_facts)} facts for {user_id[:8]}")
        return JSONResponse({"ok": True, "facts": saved_facts})

    except Exception as e:
        _log_unexpected("Memory extract", e)
        return JSONResponse({"ok": False, "facts": []})

# ── Guaranteed direct save — no AI, no models, just write to Supabase ────────
@app.post("/api/memory/save-direct")
@limiter.limit("10/minute")
async def save_memory_direct(request: Request, req: MemorySaveRequest, auth: dict = Depends(require_auth)):
    """Saves a fact directly to Supabase — used as frontend fallback when AI extraction fails."""
    try:
        # user_id is NEVER taken from the client body for this endpoint — the field is
        # accepted only for schema compatibility and is fully ignored. Always use the
        # authenticated identity so a forged user_id can't poison another user's AI context.
        user_id = auth["user_id"]
        if not req.memory_text.strip():
            return JSONResponse({"ok": False})
        fact = req.memory_text.strip()[:500]
        await _db(lambda: _supabase_admin.table("user_memories").insert({
            "user_id": user_id,
            "memory_text": fact,
            "created_at": datetime.utcnow().isoformat()
        }).execute())
        logger.info(f"🧠 [Memory] direct-saved: '{fact[:60]}' for {user_id[:8]}")
        return JSONResponse({"ok": True, "fact": fact})
    except HTTPException:
        raise
    except Exception as e:
        _log_unexpected("Memory direct-save", e)
        return JSONResponse({"ok": False, "error": _client_safe_error(e, "Memory direct-save")}, status_code=500)

# ─────────────────────────────────────────────────────────────────────────────

@app.get("/robots.txt")
async def serve_robots():
    p = os.path.join(BASE_DIR, "robots.txt")
    if not os.path.isfile(p):
        return JSONResponse({"error": "robots.txt not found"}, status_code=404)
    return FileResponse(p, media_type="text/plain")

# ✅ ADD THIS ↓
@app.get("/sitemap.xml")
async def serve_sitemap():
    p = os.path.join(BASE_DIR, "sitemap.xml")
    if not os.path.isfile(p):
        return JSONResponse({"error": "sitemap.xml not found"}, status_code=404)
    return FileResponse(p, media_type="application/xml")




# ============================================================
# 🔒 PRIVACY SYSTEM — Analytics & Training Data Endpoints
# ============================================================

import re as _re
import asyncio
from pydantic import BaseModel as _BaseModel
from typing import Optional as _Optional, List as _List, Any as _Any, Dict as _Dict
from datetime import datetime as _dt, timezone as _tz

# ── PII Sanitizer (mirrors frontend) ─────────────────────────
_PII_PATTERNS = [
    (_re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'), '[email]'),
    (_re.compile(r'(\+?\d[\d\s\-().]{7,}\d)'), '[phone]'),
    (_re.compile(r'password\s*[=:]\s*\S+', _re.IGNORECASE), '[credential]'),
    (_re.compile(r'bearer\s+[A-Za-z0-9\-_.~+/]+=*', _re.IGNORECASE), '[bearer]'),
    (_re.compile(r'\b(?:\d[ \-]?){13,19}\b'), '[card]'),
    (_re.compile(r'\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b'), '[id-number]'),
    (_re.compile(r'[-+]?\d{1,3}\.\d{4,},\s*[-+]?\d{1,3}\.\d{4,}'), '[coordinates]'),
    (_re.compile(r'\b(session[_-]?id|auth[_-]?token|access[_-]?token)\s*=\s*\S+', _re.IGNORECASE), '[session]'),
    # Long token-like strings (API keys, JWTs, etc.)
    (_re.compile(r'\b([A-Za-z0-9_\-]{32,})\b'), '[token]'),
]

def _sanitize(text: str) -> str:
    if not text:
        return ''
    out = str(text)
    for pattern, mask in _PII_PATTERNS:
        out = pattern.sub(mask, out)
    return out.strip()

# ── Pydantic models ───────────────────────────────────────────
class _AnalyticsEvent(_BaseModel):
    anonymous_user_id: str
    event_name: str
    metadata: _Dict[str, _Any] = {}

class _AnalyticsBatch(_BaseModel):
    events: _List[_AnalyticsEvent]

class _TrainingConvo(_BaseModel):
    anonymous_user_id: str
    sanitized_user_message: str
    sanitized_assistant_response: str
    feedback_score: _Optional[float] = None
    language: _Optional[str] = 'en'
    coarse_region: _Optional[str] = None

class _TrainingBatch(_BaseModel):
    conversations: _List[_TrainingConvo]

# ── Memory request models ─────────────────────────────────────
# ── IP → coarse region helper ─────────────────────────────────
def _coarse_region_from_request(request: Request) -> _Optional[str]:
    """Extract country/region from forwarded IP header — never stores raw IP."""
    try:
        forwarded = request.headers.get('x-forwarded-for', '')
        ip = forwarded.split(',')[0].strip() if forwarded else str(request.client.host)
        # Use ipapi.co free tier — returns country_name, region
        r = _http.get(f'https://ipapi.co/{ip}/json/', timeout=3)
        if r.status_code == 200:
            d = r.json()
            return d.get('country_name') or d.get('country') or None
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        logger.info(f"[_coarse_region_from_request] lookup failed (non-fatal): {e}")
    return None

# ── Analytics endpoint ────────────────────────────────────────
@app.post("/api/privacy/analytics")
async def collect_analytics(batch: _AnalyticsBatch, request: Request):
    """
    Receives batched analytics events.
    • Strips any PII from metadata values before storage.
    • Stores to analytics_events table in Supabase.
    • Never logs raw IP addresses.
    """
    try:
        rows = []
        for ev in batch.events[:50]:  # cap at 50 per batch
            # Sanitize any string values in metadata
            safe_meta = {
                k: _sanitize(str(v)) if isinstance(v, str) else v
                for k, v in ev.metadata.items()
                if k not in ('password', 'token', 'key', 'secret', 'auth')
            }
            rows.append({
                'anonymous_user_id': ev.anonymous_user_id[:64],
                'event_name': ev.event_name[:64],
                'metadata': safe_meta,
                'created_at': _dt.utcnow().isoformat(),
            })

        if rows:
            await _db(lambda: supabase.table('analytics_events').insert(rows).execute())

        return JSONResponse({'ok': True, 'stored': len(rows)})
    except Exception as e:
        # Never surface internal errors to client — but do log with a
        # traceback so a real bug in the batch-insert path isn't invisible.
        _log_unexpected("Analytics collect", e)
        return JSONResponse({'ok': False}, status_code=200)  # always 200 to client

# ── Training endpoint ─────────────────────────────────────────
@app.post("/api/privacy/training")
async def collect_training(batch: _TrainingBatch, request: Request):
    """
    Receives batched training conversations.
    • Runs server-side PII sanitization on top of client-side sanitization.
    • Resolves coarse region from IP (country only) — raw IP is discarded.
    • Stores to training_conversations table in Supabase.
    • Models are NEVER trained live — data feeds offline dataset pipeline only.
    """
    try:
        coarse_region = _coarse_region_from_request(request)
        rows = []
        for convo in batch.conversations[:10]:  # cap at 10 per batch
            # Double-sanitize (client already ran PII filter, we run it again server-side)
            clean_user = _sanitize(convo.sanitized_user_message)
            clean_asst = _sanitize(convo.sanitized_assistant_response)

            # Skip if either side is empty after sanitization
            if not clean_user.strip() or not clean_asst.strip():
                continue

            rows.append({
                'anonymous_user_id': convo.anonymous_user_id[:64],
                'sanitized_user_message': clean_user[:4000],
                'sanitized_assistant_response': clean_asst[:8000],
                'feedback_score': convo.feedback_score,
                'language': (convo.language or 'en')[:10],
                'coarse_region': coarse_region,
                'created_at': _dt.utcnow().isoformat(),
            })

        if rows:
            await _db(lambda: supabase.table('training_conversations').insert(rows).execute())

        return JSONResponse({'ok': True, 'stored': len(rows)})
    except Exception as e:
        _log_unexpected("Training collect", e)
        return JSONResponse({'ok': False}, status_code=200)

# ── Quality signal helper (called from existing thumbs up/down) ─
@app.post("/api/privacy/feedback")
async def collect_feedback(request: Request):
    """Single-event shortcut for thumbs up/down — wires into training pipeline."""
    try:
        body = await request.json()
        score        = body.get('score')       # 1 or -1
        user_msg     = _sanitize(body.get('user_message', ''))
        asst_resp    = _sanitize(body.get('assistant_response', ''))
        anon_id      = body.get('anonymous_user_id', 'unknown')[:64]
        coarse_region = _coarse_region_from_request(request)

        if user_msg and asst_resp:
            await _db(lambda: supabase.table('training_conversations').insert({
                'anonymous_user_id': anon_id,
                'sanitized_user_message': user_msg[:4000],
                'sanitized_assistant_response': asst_resp[:8000],
                'feedback_score': score,
                'language': body.get('language', 'en')[:10],
                'coarse_region': coarse_region,
                'created_at': _dt.utcnow().isoformat(),
            }).execute())

        return JSONResponse({'ok': True})
    except Exception as e:
        _log_unexpected("Feedback collect", e)
        return JSONResponse({'ok': False}, status_code=200)


# ============================================================
# 📍 LOCATION METADATA ENDPOINTS
# Privacy-safe: accepts coarse coords (2 d.p.), reverse-geocodes
# to city/region/timezone, returns ONLY those fields.
# Raw coordinates are NEVER stored or logged.
# ============================================================


class _CoarseCoords(_BaseModel):
    coarse_lat: float
    coarse_lng: float

def _reverse_geocode(lat: float, lng: float) -> dict:
    """
    Convert rounded coordinates to city/region/country/timezone.
    Uses the free Open-Meteo geocoding + timezone API — no key needed.
    Raw coords are discarded after this call completes.
    """
    try:
        # Step 1: Get timezone from coordinates (Open-Meteo)
        tz_url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lng}&timezone=auto&forecast_days=0"
        )
        tz_resp = _http.get(tz_url, timeout=5)
        timezone = "UTC"
        if tz_resp.status_code == 200:
            tz_data = tz_resp.json()
            timezone = tz_data.get("timezone", "UTC")

        # Step 2: Reverse geocode to city/region/country (Nominatim)
        nominatim_url = (
            f"https://nominatim.openstreetmap.org/reverse"
            f"?lat={lat}&lon={lng}&format=json&zoom=10&addressdetails=1"
        )
        headers = {"User-Agent": "CaturaAI/1.0 (privacy-safe location metadata)"}
        geo_resp = _http.get(nominatim_url, timeout=5, headers=headers)

        country, region, city = "", "", ""
        if geo_resp.status_code == 200:
            geo = geo_resp.json()
            addr = geo.get("address", {})
            country = addr.get("country", "")
            region  = addr.get("state", addr.get("region", ""))
            city    = (
                addr.get("city")
                or addr.get("town")
                or addr.get("village")
                or addr.get("municipality")
                or ""
            )

        # Derive locale from country code (simple mapping for common cases)
        country_code = geo_resp.json().get("address", {}).get("country_code", "").upper() if geo_resp.status_code == 200 else ""
        locale_map = {
            "IN": "en-IN", "US": "en-US", "GB": "en-GB", "AU": "en-AU",
            "CA": "en-CA", "DE": "de-DE", "FR": "fr-FR", "JP": "ja-JP",
            "CN": "zh-CN", "BR": "pt-BR", "ES": "es-ES", "IT": "it-IT",
        }
        locale = locale_map.get(country_code, f"en-{country_code}" if country_code else "en")

        # Return ONLY the safe fields — raw coords never leave this function
        return {
            "country":  country,
            "region":   region,
            "city":     city,
            "timezone": timezone,
            "locale":   locale,
        }
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        logger.info(f"[Location] Reverse geocode failed: {e}")
        return {"timezone": "UTC", "locale": "en"}


@app.post("/api/location-metadata")
async def location_metadata_from_coords(body: _CoarseCoords):
    """
    Accepts coarse (2 d.p.) coordinates from the frontend.
    Returns city/region/timezone. Raw coords are NEVER stored.
    Only called when the user has enabled the location toggle.
    """
    # Clamp to valid ranges just in case
    lat = max(-90.0,  min(90.0,  round(body.coarse_lat, 2)))
    lng = max(-180.0, min(180.0, round(body.coarse_lng, 2)))

    # _reverse_geocode makes two blocking `requests.get` calls (Open-Meteo +
    # Nominatim); run it off the event loop so it doesn't stall other
    # requests for the ~1-2s round trip.
    meta = await _db(_reverse_geocode, lat, lng)
    # lat/lng are now local variables going out of scope — not persisted
    return JSONResponse(content=meta)


@app.get("/api/location-metadata/ip")
async def location_metadata_from_ip(request: Request):
    """
    Fallback: derive approximate location from client IP using ip-api.com (free tier).
    Returns city/region/timezone only. No coordinates returned.
    """
    try:
        # Get client IP (respects X-Forwarded-For from reverse proxy)
        client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if not client_ip:
            client_ip = request.client.host if request.client else ""

        # Skip for loopback / private IPs
        if not client_ip or client_ip in ("127.0.0.1", "::1") or client_ip.startswith("192.168.") or client_ip.startswith("10."):
            tz = "Asia/Kolkata"  # safe default for Catura's primary user base
            return JSONResponse(content={"timezone": tz, "locale": "en-IN", "country": "India", "region": "", "city": ""})

        ip_resp = await _db(
            _http.get,
            f"http://ip-api.com/json/{client_ip}?fields=country,regionName,city,timezone",
            timeout=5
        )
        if ip_resp.status_code == 200:
            d = ip_resp.json()
            country_code_map = {"India": "IN", "United States": "US", "United Kingdom": "GB"}
            country = d.get("country", "")
            cc = country_code_map.get(country, "")
            locale_map = {"IN": "en-IN", "US": "en-US", "GB": "en-GB"}
            locale = locale_map.get(cc, "en")
            return JSONResponse(content={
                "country":  country,
                "region":   d.get("regionName", ""),
                "city":     d.get("city", ""),
                "timezone": d.get("timezone", "UTC"),
                "locale":   locale,
            })
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        logger.info(f"[Location] IP fallback failed: {e}")

    return JSONResponse(content={"timezone": "UTC", "locale": "en"})



# Returns: weather | finance | sports | news | web_search | general
# ============================================================
def detect_intent(text: str) -> str:
    """
    Keyword-based intent classifier.
    Priority order: greeting > identity > weather > finance > sports > news > web_search > general
    """
    lower = text.lower().strip()

    # ── GREETINGS (always general — never search for these) ────────────────
    # Short messages (≤4 words) that are purely social/conversational
    # covers: hi, hello, hey, hii, hiii, bonjour, namaste, salut,
    # kamon acho, ki obostha, kemon acho, kem cho, vanakkam, sat sri akal,
    # kya haal, theek ho, wassup, good morning/afternoon/evening/night, etc.
    _GREETING_EXACT = {
        "hi", "hii", "hiii", "hiiii", "hiiiii", "hey", "heya", "hello", "helo",
        "hellow", "yo", "sup", "wassup", "whatsup", "howdy", "hola", "ola",
        # French / Spanish / Portuguese / common global
        "bonjour", "bonsoir", "salut", "hola", "ola", "ciao", "hallo", "hei",
        # Bengali (Roman)
        "kamon acho", "kemon acho", "ki obostha", "ki holo", "ভালো আছি",
        "kamon achho", "kemon achho", "ki korcho", "ki korchen", "asha kori bhalo acho",
        # Hindi (Roman)
        "kya haal", "kya haal hai", "kaise ho", "kaise hain", "kaisa hai",
        "theek ho", "theek hain", "sab theek", "kya chal raha hai",
        # Tamil / Telugu / Kannada / Malayalam (Roman)
        "vanakkam", "namaskaram", "hege iddira", "sukhamano", "ela unnaru",
        # Punjabi / Gujarati
        "sat sri akal", "kem cho", "maja ma",
        # Urdu
        "aadab", "adaab", "assalamualaikum", "salam",
        # Universal
        "namaste", "namaskar", "pranam", "nomoskar",
        "thanks", "thank you", "thx", "ty", "thnx", "thankyou",
        "bye", "goodbye", "see ya", "cya", "see you", "alvida",
        "dhonnobad", "shukriya", "dhanyavaad", "nandri",
        "ok", "okay", "okk", "okie", "okiee", "k", "kk",
        "cool", "nice", "great", "awesome", "good", "bad",
        "yes", "no", "yep", "nope", "yeah", "nah", "sure",
        "hmm", "hm", "ohh", "oh", "aha", "ah",
    }

    # Check exact match first
    if lower in _GREETING_EXACT:
        return "general"

    # Check greeting-style short messages (≤ 5 words, no question about facts)
    words = lower.split()
    if len(words) <= 5:
        _GREETING_STARTERS = (
            "hi ", "hii", "hey ", "hello", "good morning", "good afternoon",
            "good evening", "good night", "good day", "greetings",
            "how are you", "how r u", "how are u", "how ru", "how have you",
            "what's up", "whats up", "sup ", "yo ", "hola ", "bonjour",
            "namaste", "namaskar", "nomoskar", "pranam",
            "kamon", "kemon", "kaise ho", "kya haal", "kem cho",
            "sat sri", "vanakkam", "sukha", "ela un",
            "thanks", "thank ", "thx ", "thnx",
            "bye", "goodbye", "see ya", "cya",
            "nice ", "cool ", "great ", "awesome ", "good job",
        )
        if any(lower.startswith(g) for g in _GREETING_STARTERS):
            return "general"

    # ── IDENTITY (always general — never run a tool for these) ─────────────
    identity_patterns = [
        r'which model', r'what model', r'what ai', r'which ai',
        r'which version', r'what version', r'are you', r'who are you',
        r'what are you', r'your model', r'current model', r'using.*model',
        r'model.*right now', r'model.*using', r'what.*running',
        r'who (made|created|built|developed) you', r'your (creator|developer|maker)',
    ]
    if any(re.search(p, lower) for p in identity_patterns):
        return "general"

    # ── REAL-TIME OVERRIDE — must run BEFORE wikipedia check ───────────────
    # If the query has real-time signals (now, new, current, latest, today,
    # currently, recently) combined with an information-seeking phrase,
    # always force web_search — never Wikipedia.
    realtime_signals = [
        r'\bnow\b', r'\bnew\b', r'\bcurrent(ly)?\b', r'\blatest\b',
        r'\btoday\b', r'\brecent(ly)?\b', r'\bright now\b', r'\bat (the )?moment\b',
        r'\bthis (year|month|week|day)\b', r'\b2024\b', r'\b2025\b',
        r'\bjust (happened|announced|elected|appointed|named|won|became)\b',
        r'\bwho (is|are) (the |now |currently |now the )?(new |current )?(cm|pm|president|minister|governor|ceo|chief|head|leader|director|chairman)\b',
        r'\bwho (won|became|is leading|got elected|was elected|was appointed|was named)\b',
        r'\bwho (is|are) (in charge|running|leading|governing|heading)\b',
        r'\b(cm|chief minister|prime minister|president|governor) of\b',
        r'\b(current|new|latest|now) (cm|pm|president|minister|governor|ceo|chief|leader|head)\b',
        r'\belection result\b', r'\belected\b', r'\bappointed\b',
        r'\blatest\b', r'\bnewest\b', r'\bmost recent\b',
        r'\btoday\b', r'\btonight\b', r'\bthis (week|month|year|morning|evening)\b',
        r'\bcurrent(ly)?\b', r'\bright now\b', r'\bat (the )?moment\b',
        r'\brecently?\b', r'\bjust (announced|launched|happened|released|came out)\b',
        r'\b(202[4-9])\b', r'\b(203[0-9])\b',
        r'\bbreaking\b', r'\bupdate(s|d)?\b.*\btoday\b',
        r'\bnow available\b', r'\bjust released\b',
    ]
    # If ANY real-time signal is present AND it looks like an info-seeking query → web_search
    info_seeking = any(re.search(p, lower) for p in [
        r'\bwho\b', r'\bwhat\b', r'\bwhich\b', r'\bwhere\b', r'\bwhen\b',
        r'\bwho is\b', r'\bwho are\b', r'\btell me\b', r'\bfind\b',
    ])
    if info_seeking and any(re.search(p, lower) for p in realtime_signals):
        return "web_search"

    # ── CLOCK / TIME ────────────────────────────────────────────────────────
    clock_patterns = [
        r'\btime\b', r'\bclock\b', r'\bwhat time\b', r'\bcurrent time\b',
        r'\btime (in|at|of|now)\b', r'\btime (is it|right now)\b',
        r'\bwhat.*(time|hour|clock)\b', r'\b(hour|minute|second)s?\b',
        r'\bam\b.*\bpm\b', r'\bist\b', r'\butc\b', r'\bgmt\b',
        r'\btimezone\b', r'\btime zone\b',
        r'\b(morning|afternoon|evening|night)\b.*\bthere\b',
        r'\bwhat\s+(time|hour|day|date)\s+(is\s+it|is\s+it\s+now|now)\b',
        r'\bcurrent\s+(time|date|day|hour)\b',
        r'\btime\s+(in|at|of)\s+\w+',
        r'\bwhat\s+time\b.*\b(now|right now|currently)\b',
        r"\btoday'?s?\s+date\b",
        r'\bwhat.{0,8}date.{0,8}(today|now)\b',
        r'\b(time|clock)\s+(right )?now\b',
        r'^\s*time\??\s*$',
        r'^\s*date\??\s*$',
        r'^\s*what time\??\s*$',
        r'^\s*what date\??\s*$',
        r'\bcurrent\s+(ist|utc|gmt|pst|est)\b',
        r'\btimezone\s+(in|of|for)\b',
    ]
    if any(re.search(p, lower) for p in clock_patterns):
        return "clock"
    weather_patterns = [
        r'\bweather\b', r'\btemperature\b', r'\bhumidity\b', r'\brain\b',
        r'\bsnow\b', r'\bwind\b', r'\bforecast\b', r'\bclimate\b',
        r'\bsunny\b', r'\bcloudy\b', r'\bhot\b.*\boutside\b',
        r'\bcold\b.*\boutside\b', r'\bwill it rain\b', r'\bfeels like\b',
        r'\bdegrees (celsius|fahrenheit|today)\b',
        r'\bweather\s+(in|at|of|today|tomorrow|now|right now)\b',
        r'\btemperature\s+(in|at|of|today|tomorrow|now|right now|outside)\b',
        r'\bhumidity\s+(in|at|of|today|now)\b',
        r'\b(will|is)\s+it\s+(rain|snow|hot|cold|sunny|cloudy)\b',
        r'\bforecast\s+(for|in|today|tomorrow|this week)\b',
        r'\bfeels\s+like\b.*\b(today|now|outside)\b',
        r'\bhow\s+(hot|cold|warm)\s+is\s+it\b',
        r'\bclimate\s+(today|now|in)\b',
        r'\b(rain|snow|wind|storm)\s+(today|tomorrow|now|in)\b',
        r"\btoday'?s?\s+weather\b",
        r'^\s*weather\??\s*$',
    ]
    if any(re.search(p, lower) for p in weather_patterns):
        return "weather"

    # ── FINANCE ────────────────────────────────────────────────────────────
    finance_patterns = [
        r'\bshare price\b', r'\bstock price\b', r'\bstock market\b',
        r'\bnse\b', r'\bbse\b', r'\bnifty\b', r'\bsensex\b',
        r'\bcrypto\b', r'\bbitcoin\b', r'\bethereum\b', r'\bcoin\b',
        r'\binr\b', r'\busd\b', r'\bexchange rate\b', r'\brupee\b',
        r'\bmarketcap\b', r'\bmarket cap\b', r'\bdividend\b',
        r'\btrade(d|ing)?\b.*\bstock\b', r'\bprice of\b.*\bshare\b',
        r'\bstock\b.*\btoday\b', r'\bshares?\b.*\bprice\b',
        r'\bprice.*\b(tata|reliance|infosys|wipro|hdfc|icici|bajaj|sbi)\b',
        r'\b(tata|reliance|infosys|wipro|hdfc|icici|bajaj|sbi)\b.*\bprice\b',
    ]
    if any(re.search(p, lower) for p in finance_patterns):
        return "finance"

    # ── SPORTS ─────────────────────────────────────────────────────────────
    sports_patterns = [
        r'\bcricket\b', r'\bipl\b', r'\btest match\b', r'\bodi\b', r'\bt20\b',
        r'\bfootball\b', r'\bsoccer\b', r'\bfifa\b', r'\bpremier league\b',
        r'\bnba\b', r'\bnfl\b', r'\btennis\b', r'\bwimbledon\b',
        r'\bmatch score\b', r'\blive score\b', r'\bscore(card)?\b',
        r'\bwho (won|is winning|is playing)\b', r'\bmatch today\b',
        r'\bfinal score\b', r'\btournament\b',
        r'\b(live|today\'?s?|current)\s+(score|match)\b',
        r'\bscore\s+(of|today|now|live|right now)\b',
        r'\b(ipl|test match|odi|t20|world cup|champions league|epl|la liga)\s+(today|score|match|live|now|result)\b',
        r'\bwho\s+(won|is winning|is playing|is leading)\s+(today|now|the match)\b',
        r'\bmatch\s+(today|now|live|score|result)\b',
        r'\bcricket\s+(score|match|live|today|now)\b',
        r'\bfootball\s+(score|match|live|today|now)\b',
        r'\bfinal\s+score\b',
        r'\btournament\s+(today|now|live|result)\b',
    ]
    if any(re.search(p, lower) for p in sports_patterns):
        return "sports"

    # ── NEWS ───────────────────────────────────────────────────────────────
    news_patterns = [
        r'\bnews\b', r'\bheadlines\b', r'\bbreaking\b', r'\blatest (news|update|development)\b',
        r"\bwhat('s| is) happening\b", r'\bwhat happened\b', r'\bcurrent events\b',
        r"\btoday'?s news\b", r'\brecent news\b', r'\bannouncement\b',
        r'\b(latest|todays?|recent|breaking|current)\s+news\b',
        r'\bnews\s+(today|now|about|on|regarding)\b',
        r'\bheadlines\s+(today|now)\b',
        r'\bbreaking\s+(news|story)\b',
        r"\bwhat'?s?\s+(happening|in the news)\s+(today|now|currently)\b",
        r'\bwhat\s+happened\s+(today|yesterday|recently)\b',
        r'\bcurrent\s+events\b',
        r'\bnews\s+about\s+\w+',
        r'^\s*news\??\s*$',
    ]
    if any(re.search(p, lower) for p in news_patterns):
        return "news"

    # ── WIKIPEDIA (educational / informational / GK / concept / biography) ──
    # Triggered when the question is clearly knowledge-based and NOT real-time.
    # wiki.py has its own internal skip-check so real-time questions routed here
    # by mistake will still fall through to web_search automatically.
    wikipedia_patterns = [
        # Definition / explanation
        r'\bwhat is\b', r'\bwhat are\b', r'\bwhat was\b', r'\bwhat were\b',
        r'\bwho was\b', r'\bwho were\b',
        r'\bwhere was\b',
        r'\bwhen (was|did|were|is) (the|a|an)\b',
        r'\bhow (does|do|did|was|were|is|are)\b',
        r'\bwhy (is|are|was|were|did|does)\b',
        r'\bexplain\b', r'\bdescribe\b', r'\bdefine\b', r'\bdefinition\b',
        r'\bmeaning of\b', r'\bmeant by\b',
        # Knowledge domains
        r'\bhistory of\b', r'\bhistorical\b', r'\borigin of\b', r'\borigins of\b',
        r'\bbiography\b', r'\bborn in\b', r'\bfounded by\b', r'\binvented by\b',
        r'\bdiscovered by\b', r'\bcreated by\b',
        r'\bcountry\b', r'\bcapital of\b', r'\bpopulation of\b',
        r'\bplanet\b', r'\bgalaxy\b', r'\bstar\b', r'\buniverse\b', r'\bsolar system\b',
        r'\bscience\b', r'\bphysics\b', r'\bchemistry\b', r'\bbiology\b',
        r'\bmathematics?\b', r'\bformula\b', r'\bequation\b', r'\btheorem\b',
        r'\blanguage\b', r'\balphabet\b', r'\bliterature\b', r'\bauthor of\b',
        r'\bmovie\b', r'\bfilm\b', r'\bdirected by\b',
        r'\breligion\b', r'\bphilosophy\b', r'\bmythology\b',
        r'\bwar\b', r'\bempire\b', r'\bcivilization\b', r'\bdynasty\b',  # FIX: "dinasty" → "dynasty"
        r'\btechnology\b', r'\binvention\b', r'\bdiscovery\b',
        r'\beconomics?\b', r'\btheory\b', r'\bconcept\b', r'\bprinciple\b',
        r'\bfamous\b', r'\blegendary\b', r'\bnotable\b', r'\bknown for\b',
        r'\btell me about\b', r'\blearn about\b', r'\binformation (about|on)\b',
        r'\bfacts? about\b', r'\bwhat do you know about\b',
        r'\bhow (many|much|far|long|tall|big|old|deep|high)\b',
        r'\blocation of\b', r'\bsituated\b', r'\blocated\b',
    ]
    if any(re.search(p, lower) for p in wikipedia_patterns):
        return "wikipedia"

    # ── WEB SEARCH (general real-time lookup) ──────────────────────────────
    # FIX: was overwriting the initial web_search_patterns list entirely.
    # Now all pattern groups are combined correctly from the start.
    currentposition_signals = [
        r'\bwho is (the )?(current|new|now|present)\s+(cm|chief minister|pm|prime minister|president|governor|mayor|minister|ceo|chairman)\b',
        r'\b(current|new|now|present)\s+(cm|chief minister|pm|prime minister|president|governor|mayor|minister|ceo|chairman)\b',
        r'\bwho (won|became|got elected|was elected|was appointed|was named)\b.*\b(202[4-9]|today|this year|recently)\b',
        r'\bwho is leading\b', r'\bwho is in charge\b',
    ]
    techlaunch_signals = [
        r'\blatest version of\b', r'\bnew(est)? (version|update|release|feature)\b',
        r'\bjust (launched|released|announced)\b',
        r'\bthis (week|month).{0,10}(launch|release|announce)\b',
        r'\bwhat.{0,15}(launched|released|announced).{0,15}(this week|today|recently)\b',
        r'\b(reddit|twitter|x|hackernews|hn)\s+(saying|discussion|trending)\b',
        r'\bgithub trending\b', r'\bproducthunt\b',
        r'\b(hugging\s*face|huggingface)\s+.{0,30}(latest|new|recent|2024|2025|2026)\b',
        r'\bfind.{0,20}(launched|released|new|latest).{0,20}(202[4-9]|this (week|month|year))\b',
    ]
    status_signals = [
        r'\bis\s+\w+\s+(down|up|working|running|having issues|offline|online)\s+(right now|today|currently)\b',
        r'\bstatus of\b.*\btoday\b',
        r'\bwhich (websites?|domains?|sites?).{0,20}(rank|indexed|blocked|currently)\b',
        r'\bcurrently rank\b', r'\bnow ranking\b',
        r'\b(seo|search ranking).{0,15}(update|new|latest|today|this (week|month))\b',
    ]
    base_web_search_patterns = [
        r'\blatest\b', r'\bcurrently?\b', r'\bright now\b', r'\btoday\b',
        r'\brecently?\b', r'\bwho is\b', r'\bwhen (is|was|did)\b',
        r'\bwhere is\b', r'\bprice of\b', r'\bhow much (is|does|did)\b',
        r'\bwhat is the (current|latest|new)\b', r'\bfind (me|out|information)\b',
        r'\bsearch (for|about)\b', r'\blook up\b', r'\b\d{4}\b',
    ]
    all_web_search_patterns = (
        base_web_search_patterns
        + realtime_signals
        + currentposition_signals
        + techlaunch_signals
        + status_signals
    )
    if any(re.search(p, lower) for p in all_web_search_patterns):
        return "web_search"
    
    # ══════════════════════════════════════════════════════════════════════
    # DEFAULT: GENERAL — trust the LLM's training
    # Handles: explanations, definitions, code, math, opinions, casual chat,
    # "what is DNS", "explain quantum computing", "how does X work", etc.
    # ══════════════════════════════════════════════════════════════════════
    return "general"


# ============================================================
# ✅ TOOL: CLOCK — exact live time for any country/city/timezone
# Uses Python stdlib zoneinfo (no external API needed)
# ============================================================

# ── Comprehensive location → IANA timezone mapping ──────────────────────────
TIMEZONE_MAP: dict[str, str] = {
    # ── INDIA (default + states/cities) ─────────────────────────────────────
    "india": "Asia/Kolkata", "indian": "Asia/Kolkata", "ist": "Asia/Kolkata",
    "kolkata": "Asia/Kolkata", "calcutta": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata", "bombay": "Asia/Kolkata",
    "delhi": "Asia/Kolkata", "new delhi": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata", "bengaluru": "Asia/Kolkata",
    "chennai": "Asia/Kolkata", "madras": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata", "pune": "Asia/Kolkata",
    "ahmedabad": "Asia/Kolkata", "jaipur": "Asia/Kolkata",
    "lucknow": "Asia/Kolkata", "kanpur": "Asia/Kolkata",
    "nagpur": "Asia/Kolkata", "indore": "Asia/Kolkata",
    "bhopal": "Asia/Kolkata", "patna": "Asia/Kolkata",
    "vadodara": "Asia/Kolkata", "ludhiana": "Asia/Kolkata",
    "agra": "Asia/Kolkata", "nashik": "Asia/Kolkata",
    "surat": "Asia/Kolkata", "varanasi": "Asia/Kolkata",
    "siliguri": "Asia/Kolkata", "guwahati": "Asia/Kolkata",
    "bhubaneswar": "Asia/Kolkata", "thiruvananthapuram": "Asia/Kolkata",
    "kochi": "Asia/Kolkata", "coimbatore": "Asia/Kolkata",
    "visakhapatnam": "Asia/Kolkata", "vijayawada": "Asia/Kolkata",
    "chandigarh": "Asia/Kolkata", "amritsar": "Asia/Kolkata",
    "ranchi": "Asia/Kolkata", "raipur": "Asia/Kolkata",
    "goa": "Asia/Kolkata", "panaji": "Asia/Kolkata",
    "jammu": "Asia/Kolkata", "srinagar": "Asia/Kolkata",
    "shimla": "Asia/Kolkata", "dehradun": "Asia/Kolkata",
    "imphal": "Asia/Kolkata", "aizawl": "Asia/Kolkata",
    "shillong": "Asia/Kolkata", "kohima": "Asia/Kolkata",
    "itanagar": "Asia/Kolkata", "agartala": "Asia/Kolkata",
    "gangtok": "Asia/Kolkata", "dispur": "Asia/Kolkata",
    # Indian states
    "maharashtra": "Asia/Kolkata", "karnataka": "Asia/Kolkata",
    "tamil nadu": "Asia/Kolkata", "telangana": "Asia/Kolkata",
    "andhra pradesh": "Asia/Kolkata", "kerala": "Asia/Kolkata",
    "gujarat": "Asia/Kolkata", "rajasthan": "Asia/Kolkata",
    "uttar pradesh": "Asia/Kolkata", "madhya pradesh": "Asia/Kolkata",
    "bihar": "Asia/Kolkata", "west bengal": "Asia/Kolkata",
    "odisha": "Asia/Kolkata", "jharkhand": "Asia/Kolkata",
    "haryana": "Asia/Kolkata", "punjab": "Asia/Kolkata",
    "himachal pradesh": "Asia/Kolkata", "uttarakhand": "Asia/Kolkata",
    "chhattisgarh": "Asia/Kolkata", "assam": "Asia/Kolkata",
    "tripura": "Asia/Kolkata", "meghalaya": "Asia/Kolkata",
    "manipur": "Asia/Kolkata", "mizoram": "Asia/Kolkata",
    "nagaland": "Asia/Kolkata", "arunachal pradesh": "Asia/Kolkata",
    "sikkim": "Asia/Kolkata",

    # ── ASIA ──────────────────────────────────────────────────────────────────
    "pakistan": "Asia/Karachi", "karachi": "Asia/Karachi",
    "lahore": "Asia/Karachi", "islamabad": "Asia/Karachi",
    "bangladesh": "Asia/Dhaka", "dhaka": "Asia/Dhaka",
    "sri lanka": "Asia/Colombo", "colombo": "Asia/Colombo",
    "nepal": "Asia/Kathmandu", "kathmandu": "Asia/Kathmandu",
    "bhutan": "Asia/Thimphu", "thimphu": "Asia/Thimphu",
    "myanmar": "Asia/Yangon", "yangon": "Asia/Yangon", "rangoon": "Asia/Yangon",
    "thailand": "Asia/Bangkok", "bangkok": "Asia/Bangkok",
    "vietnam": "Asia/Ho_Chi_Minh", "ho chi minh": "Asia/Ho_Chi_Minh",
    "hanoi": "Asia/Bangkok",
    "cambodia": "Asia/Phnom_Penh", "phnom penh": "Asia/Phnom_Penh",
    "laos": "Asia/Vientiane", "vientiane": "Asia/Vientiane",
    "malaysia": "Asia/Kuala_Lumpur", "kuala lumpur": "Asia/Kuala_Lumpur",
    "singapore": "Asia/Singapore",
    "indonesia": "Asia/Jakarta", "jakarta": "Asia/Jakarta",
    "bali": "Asia/Makassar",
    "philippines": "Asia/Manila", "manila": "Asia/Manila",
    "china": "Asia/Shanghai", "beijing": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai", "hong kong": "Asia/Hong_Kong",
    "taiwan": "Asia/Taipei", "taipei": "Asia/Taipei",
    "japan": "Asia/Tokyo", "tokyo": "Asia/Tokyo", "osaka": "Asia/Tokyo",
    "south korea": "Asia/Seoul", "korea": "Asia/Seoul", "seoul": "Asia/Seoul",
    "north korea": "Asia/Pyongyang", "pyongyang": "Asia/Pyongyang",
    "mongolia": "Asia/Ulaanbaatar", "ulaanbaatar": "Asia/Ulaanbaatar",
    "afghanistan": "Asia/Kabul", "kabul": "Asia/Kabul",
    "iran": "Asia/Tehran", "tehran": "Asia/Tehran",
    "iraq": "Asia/Baghdad", "baghdad": "Asia/Baghdad",
    "saudi arabia": "Asia/Riyadh", "riyadh": "Asia/Riyadh",
    "uae": "Asia/Dubai", "dubai": "Asia/Dubai",
    "abu dhabi": "Asia/Dubai", "sharjah": "Asia/Dubai",
    "qatar": "Asia/Qatar", "doha": "Asia/Qatar",
    "bahrain": "Asia/Bahrain", "manama": "Asia/Bahrain",
    "kuwait": "Asia/Kuwait", "kuwait city": "Asia/Kuwait",
    "oman": "Asia/Muscat", "muscat": "Asia/Muscat",
    "yemen": "Asia/Aden", "aden": "Asia/Aden", "sanaa": "Asia/Aden",
    "jordan": "Asia/Amman", "amman": "Asia/Amman",
    "israel": "Asia/Jerusalem", "jerusalem": "Asia/Jerusalem",
    "tel aviv": "Asia/Jerusalem",
    "lebanon": "Asia/Beirut", "beirut": "Asia/Beirut",
    "syria": "Asia/Damascus", "damascus": "Asia/Damascus",
    "turkey": "Europe/Istanbul", "istanbul": "Europe/Istanbul",
    "ankara": "Europe/Istanbul",
    "azerbaijan": "Asia/Baku", "baku": "Asia/Baku",
    "georgia": "Asia/Tbilisi", "tbilisi": "Asia/Tbilisi",
    "armenia": "Asia/Yerevan", "yerevan": "Asia/Yerevan",
    "kazakhstan": "Asia/Almaty", "almaty": "Asia/Almaty",
    "uzbekistan": "Asia/Tashkent", "tashkent": "Asia/Tashkent",
    "kyrgyzstan": "Asia/Bishkek", "bishkek": "Asia/Bishkek",
    "tajikistan": "Asia/Dushanbe", "dushanbe": "Asia/Dushanbe",
    "turkmenistan": "Asia/Ashgabat", "ashgabat": "Asia/Ashgabat",

    # ── EUROPE ────────────────────────────────────────────────────────────────
    "uk": "Europe/London", "united kingdom": "Europe/London",
    "england": "Europe/London", "london": "Europe/London",
    "scotland": "Europe/London", "wales": "Europe/London",
    "ireland": "Europe/Dublin", "dublin": "Europe/Dublin",
    "france": "Europe/Paris", "paris": "Europe/Paris",
    "germany": "Europe/Berlin", "berlin": "Europe/Berlin",
    "frankfurt": "Europe/Berlin", "munich": "Europe/Berlin",
    "italy": "Europe/Rome", "rome": "Europe/Rome", "milan": "Europe/Rome",
    "spain": "Europe/Madrid", "madrid": "Europe/Madrid",
    "barcelona": "Europe/Madrid",
    "portugal": "Europe/Lisbon", "lisbon": "Europe/Lisbon",
    "netherlands": "Europe/Amsterdam", "amsterdam": "Europe/Amsterdam",
    "belgium": "Europe/Brussels", "brussels": "Europe/Brussels",
    "switzerland": "Europe/Zurich", "zurich": "Europe/Zurich",
    "geneva": "Europe/Zurich",
    "austria": "Europe/Vienna", "vienna": "Europe/Vienna",
    "sweden": "Europe/Stockholm", "stockholm": "Europe/Stockholm",
    "norway": "Europe/Oslo", "oslo": "Europe/Oslo",
    "denmark": "Europe/Copenhagen", "copenhagen": "Europe/Copenhagen",
    "finland": "Europe/Helsinki", "helsinki": "Europe/Helsinki",
    "poland": "Europe/Warsaw", "warsaw": "Europe/Warsaw",
    "czech republic": "Europe/Prague", "czechia": "Europe/Prague",
    "prague": "Europe/Prague",
    "hungary": "Europe/Budapest", "budapest": "Europe/Budapest",
    "romania": "Europe/Bucharest", "bucharest": "Europe/Bucharest",
    "bulgaria": "Europe/Sofia", "sofia": "Europe/Sofia",
    "greece": "Europe/Athens", "athens": "Europe/Athens",
    "russia": "Europe/Moscow", "moscow": "Europe/Moscow",
    "saint petersburg": "Europe/Moscow",
    "ukraine": "Europe/Kiev", "kyiv": "Europe/Kiev", "kiev": "Europe/Kiev",
    "serbia": "Europe/Belgrade", "belgrade": "Europe/Belgrade",
    "croatia": "Europe/Zagreb", "zagreb": "Europe/Zagreb",
    "slovenia": "Europe/Ljubljana", "ljubljana": "Europe/Ljubljana",
    "slovakia": "Europe/Bratislava", "bratislava": "Europe/Bratislava",
    "estonia": "Europe/Tallinn", "tallinn": "Europe/Tallinn",
    "latvia": "Europe/Riga", "riga": "Europe/Riga",
    "lithuania": "Europe/Vilnius", "vilnius": "Europe/Vilnius",
    "iceland": "Atlantic/Reykjavik", "reykjavik": "Atlantic/Reykjavik",
    "luxembourg": "Europe/Luxembourg",
    "malta": "Europe/Malta",
    "cyprus": "Asia/Nicosia", "nicosia": "Asia/Nicosia",

    # ── AFRICA ────────────────────────────────────────────────────────────────
    "nigeria": "Africa/Lagos", "lagos": "Africa/Lagos", "abuja": "Africa/Lagos",
    "egypt": "Africa/Cairo", "cairo": "Africa/Cairo",
    "south africa": "Africa/Johannesburg", "johannesburg": "Africa/Johannesburg",
    "cape town": "Africa/Johannesburg",
    "kenya": "Africa/Nairobi", "nairobi": "Africa/Nairobi",
    "ethiopia": "Africa/Addis_Ababa", "addis ababa": "Africa/Addis_Ababa",
    "ghana": "Africa/Accra", "accra": "Africa/Accra",
    "tanzania": "Africa/Dar_es_Salaam", "dar es salaam": "Africa/Dar_es_Salaam",
    "uganda": "Africa/Kampala", "kampala": "Africa/Kampala",
    "morocco": "Africa/Casablanca", "casablanca": "Africa/Casablanca",
    "algiers": "Africa/Algiers", "algeria": "Africa/Algiers",
    "tunisia": "Africa/Tunis", "tunis": "Africa/Tunis",
    "libya": "Africa/Tripoli", "tripoli": "Africa/Tripoli",
    "sudan": "Africa/Khartoum", "khartoum": "Africa/Khartoum",
    "zimbabwe": "Africa/Harare", "harare": "Africa/Harare",
    "zambia": "Africa/Lusaka", "lusaka": "Africa/Lusaka",
    "angola": "Africa/Luanda", "luanda": "Africa/Luanda",
    "cameroon": "Africa/Douala", "douala": "Africa/Douala",
    "senegal": "Africa/Dakar", "dakar": "Africa/Dakar",

    # ── AMERICAS ──────────────────────────────────────────────────────────────
    "usa": "America/New_York", "us": "America/New_York",
    "united states": "America/New_York", "america": "America/New_York",
    "new york": "America/New_York", "nyc": "America/New_York",
    "boston": "America/New_York", "miami": "America/New_York",
    "washington": "America/New_York", "dc": "America/New_York",
    "chicago": "America/Chicago",
    "houston": "America/Chicago", "dallas": "America/Chicago",
    "denver": "America/Denver",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles", "seattle": "America/Los_Angeles",
    "las vegas": "America/Los_Angeles", "phoenix": "America/Phoenix",
    "alaska": "America/Anchorage", "anchorage": "America/Anchorage",
    "hawaii": "Pacific/Honolulu", "honolulu": "Pacific/Honolulu",
    "canada": "America/Toronto", "toronto": "America/Toronto",
    "vancouver": "America/Vancouver", "montreal": "America/Toronto",
    "calgary": "America/Edmonton",
    "mexico": "America/Mexico_City", "mexico city": "America/Mexico_City",
    "brazil": "America/Sao_Paulo", "sao paulo": "America/Sao_Paulo",
    "brasilia": "America/Sao_Paulo", "rio": "America/Sao_Paulo",
    "rio de janeiro": "America/Sao_Paulo",
    "argentina": "America/Argentina/Buenos_Aires",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "chile": "America/Santiago", "santiago": "America/Santiago",
    "colombia": "America/Bogota", "bogota": "America/Bogota",
    "peru": "America/Lima", "lima": "America/Lima",
    "venezuela": "America/Caracas", "caracas": "America/Caracas",
    "ecuador": "America/Guayaquil", "quito": "America/Guayaquil",
    "cuba": "America/Havana", "havana": "America/Havana",
    "jamaica": "America/Jamaica", "kingston": "America/Jamaica",

    # ── OCEANIA ───────────────────────────────────────────────────────────────
    "australia": "Australia/Sydney", "sydney": "Australia/Sydney",
    "melbourne": "Australia/Melbourne", "brisbane": "Australia/Brisbane",
    "perth": "Australia/Perth", "adelaide": "Australia/Adelaide",
    "new zealand": "Pacific/Auckland", "auckland": "Pacific/Auckland",
    "wellington": "Pacific/Auckland",
    "fiji": "Pacific/Fiji", "suva": "Pacific/Fiji",
    "papua new guinea": "Pacific/Port_Moresby",

    # ── UTC / GMT ──────────────────────────────────────────────────────────────
    "utc": "UTC", "gmt": "UTC", "greenwich": "UTC",
}

def tool_clock(prompt: str) -> dict:
    """
    Returns the exact current time (with seconds) for a detected location.
    Default: India (Asia/Kolkata / IST).
    Falls back gracefully if zoneinfo is unavailable.
    """
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        try:
            from backports.zoneinfo import ZoneInfo
        except ImportError:
            ZoneInfo = None

    logger.info(f"🕐 [TOOL] clock | prompt: {prompt[:80]}")

    lower = prompt.lower()
    tz_key   = "Asia/Kolkata"
    location = "India"

    # Try longest-match first so "new york" beats "york"
    best_match_len = 0
    for key, tz in TIMEZONE_MAP.items():
        if key in lower and len(key) > best_match_len:
            tz_key = tz
            location = key.title()
            best_match_len = len(key)

    try:
        if ZoneInfo:
            tz     = ZoneInfo(tz_key)
            now    = datetime.now(tz)
        else:
            import pytz
            tz  = pytz.timezone(tz_key)
            now = datetime.now(tz)

        # UTC offset string e.g. "+05:30"
        offset_secs  = int(now.utcoffset().total_seconds())
        sign         = "+" if offset_secs >= 0 else "-"
        offset_secs  = abs(offset_secs)
        offset_h, r  = divmod(offset_secs, 3600)
        offset_m     = r // 60
        utc_offset   = f"{sign}{offset_h:02d}:{offset_m:02d}"

        # Abbrev from tzname
        tz_abbrev = now.strftime("%Z") or tz_key.split("/")[-1]

        result = {
            "tool"      : "clock",
            "location"  : location,
            "timezone"  : tz_key,
            "tz_abbrev" : tz_abbrev,
            "utc_offset": utc_offset,
            "time_12h"  : now.strftime("%I:%M:%S %p"),
            "time_24h"  : now.strftime("%H:%M:%S"),
            "date"      : now.strftime("%A, %d %B %Y"),
            "day"       : now.strftime("%A"),
        }
        logger.info(f"✅ [TOOL] clock: {result['time_12h']} — {location} ({tz_key})")
        return result

    except (KeyError, ValueError, AttributeError) as e:
        logger.warning(f"⚠️ [TOOL] clock: unrecognized location/timezone: {e}")
        # Pure UTC fallback
        from datetime import timezone
        now = datetime.now(timezone.utc)
        return {
            "tool"      : "clock",
            "location"  : location,
            "timezone"  : "UTC",
            "tz_abbrev" : "UTC",
            "utc_offset": "+00:00",
            "time_12h"  : now.strftime("%I:%M:%S %p"),
            "time_24h"  : now.strftime("%H:%M:%S"),
            "date"      : now.strftime("%A, %d %B %Y"),
            "day"       : now.strftime("%A"),
        }
    except Exception as e:
        _log_unexpected("TOOL clock", e)
        from datetime import timezone
        now = datetime.now(timezone.utc)
        return {
            "tool"      : "clock",
            "location"  : location,
            "timezone"  : "UTC",
            "tz_abbrev" : "UTC",
            "utc_offset": "+00:00",
            "time_12h"  : now.strftime("%I:%M:%S %p"),
            "time_24h"  : now.strftime("%H:%M:%S"),
            "date"      : now.strftime("%A, %d %B %Y"),
            "day"       : now.strftime("%A"),
        }


# ============================================================
# ✅ TOOL: WEATHER
# Uses OpenWeatherMap free API
# ============================================================
def tool_weather(prompt: str) -> dict:
    """
    Extract city from prompt, call OpenWeatherMap, return formatted data.
    Falls back to DuckDuckGo search if API key not set.
    """
    logger.info(f"🌤️ [TOOL] weather | prompt: {prompt[:80]}")

    # Extract city name — heuristic
    city = None
    patterns = [
        r'weather (?:in|at|for|of) ([a-zA-Z\s]+)',
        r'temperature (?:in|at|of) ([a-zA-Z\s]+)',
        r'(?:in|at) ([A-Z][a-zA-Z\s]+) (?:weather|temperature|today)',
        r'([A-Z][a-zA-Z\s]+) weather',
        r'([A-Z][a-zA-Z]+) temperature',
    ]
    for pat in patterns:
        m = re.search(pat, prompt, re.IGNORECASE)
        if m:
            city = m.group(1).strip().rstrip('?.,!')
            break

    # Default to India if asking "temperature in India" broadly
    if not city:
        lower = prompt.lower()
        if 'india' in lower:
            city = 'New Delhi'
        elif 'kolkata' in lower or 'calcutta' in lower:
            city = 'Kolkata'
        else:
            city = 'Mumbai'  # safe default

    if not OPENWEATHER_API_KEY:
        # Fallback: DuckDuckGo search
        logger.warning("⚠️ [TOOL] weather — no API key, falling back to web search")
        return tool_web_search(f"current weather {city} today temperature")

    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHER_API_KEY}&units=metric"
        resp = _http.get(url, timeout=8)
        data = resp.json()

        if resp.status_code != 200 or data.get("cod") != 200:
            logger.warning(f"⚠️ [TOOL] weather API error: {data.get('message')}")
            return tool_web_search(f"current weather {city} today")

        w = data["weather"][0]
        m = data["main"]
        wind = data.get("wind", {})
        city_name = data.get("name", city)
        country = data.get("sys", {}).get("country", "")

        result = {
            "tool": "weather",
            "city": f"{city_name}, {country}",
            "temperature": m["temp"],
            "feels_like": m["feels_like"],
            "condition": w["description"].title(),
            "humidity": m["humidity"],
            "wind_speed": wind.get("speed", "N/A"),
            "min_temp": m["temp_min"],
            "max_temp": m["temp_max"],
        }
        logger.info(f"✅ [TOOL] weather success: {result}")
        return result

    except (requests.exceptions.RequestException, KeyError, ValueError, TypeError) as e:
        logger.warning(f"⚠️ [TOOL] weather: expected failure, falling back to search: {e}")
        return tool_web_search(f"current weather {city} today")
    except Exception as e:
        _log_unexpected("TOOL weather", e)
        return tool_web_search(f"current weather {city} today")


# ============================================================
# ✅ TOOL: FINANCE
# Step 1 — Known ticker map (instant, no network call)
# Step 2 — Yahoo Finance Search API (finds ANY company by name)
# Step 3 — Yahoo Finance Quote API (live price, no API key needed)
# Step 4 — Alpha Vantage fallback (US stocks, if key set)
# Step 5 — DuckDuckGo last resort (never used for price numbers)
# ============================================================

# ── Known Indian company → Yahoo Finance ticker (NSE .NS preferred) ──────────
# Ordered longest-match first to avoid partial hits (e.g. "hdfc bank" before "hdfc")
KNOWN_TICKERS: list[tuple[str, str, str]] = [
    # keyword (lowercase)          yahoo symbol      display name
    # ── Reliance group ──────────────────────────────────────────────────────
    ("reliance power",          "RPOWER.NS",        "Reliance Power"),
    ("reliance infrastructure", "RELINFRA.NS",      "Reliance Infrastructure"),
    ("reliance capital",        "RELCAPITAL.NS",    "Reliance Capital"),
    ("reliance communications", "RCOM.NS",          "Reliance Communications"),
    ("reliance industries",     "RELIANCE.NS",      "Reliance Industries"),
    ("reliance",                "RELIANCE.NS",      "Reliance Industries"),
    # ── Tata group ──────────────────────────────────────────────────────────
    ("tata consultancy",        "TCS.NS",           "Tata Consultancy Services"),
    ("tata steel",              "TATASTEEL.NS",     "Tata Steel"),
    ("tata motors",             "TATAMOTORS.NS",    "Tata Motors"),
    ("tata power",              "TATAPOWER.NS",     "Tata Power"),
    ("tata chemicals",          "TATACHEM.NS",      "Tata Chemicals"),
    ("tata consumer",           "TATACONSUM.NS",    "Tata Consumer Products"),
    ("tata elxsi",              "TATAELXSI.NS",     "Tata Elxsi"),
    ("tcs",                     "TCS.NS",           "TCS"),
    ("tata",                    "TATAMOTORS.NS",    "Tata Motors"),
    # ── Adani group ─────────────────────────────────────────────────────────
    ("adani power",             "ADANIPOWER.NS",    "Adani Power"),
    ("adani green",             "ADANIGREEN.NS",    "Adani Green Energy"),
    ("adani enterprises",       "ADANIENT.NS",      "Adani Enterprises"),
    ("adani ports",             "ADANIPORTS.NS",    "Adani Ports"),
    ("adani total",             "ATGL.NS",          "Adani Total Gas"),
    ("adani wilmar",            "AWL.NS",           "Adani Wilmar"),
    ("adani transmission",      "ADANITRANS.NS",    "Adani Transmission"),
    ("adani",                   "ADANIENT.NS",      "Adani Enterprises"),
    # ── IT ──────────────────────────────────────────────────────────────────
    ("infosys",                 "INFY.NS",          "Infosys"),
    ("wipro",                   "WIPRO.NS",         "Wipro"),
    ("hcl technologies",        "HCLTECH.NS",       "HCL Technologies"),
    ("hcl tech",                "HCLTECH.NS",       "HCL Technologies"),
    ("tech mahindra",           "TECHM.NS",         "Tech Mahindra"),
    ("mphasis",                 "MPHASIS.NS",       "Mphasis"),
    ("ltimindtree",             "LTIM.NS",          "LTIMindtree"),
    ("l&t technology",          "LTTS.NS",          "L&T Technology Services"),
    ("persistent",              "PERSISTENT.NS",    "Persistent Systems"),
    ("coforge",                 "COFORGE.NS",       "Coforge"),
    ("zensar",                  "ZENSARTECH.NS",    "Zensar Technologies"),
    ("cyient",                  "CYIENT.NS",        "Cyient"),
    # ── Banking ─────────────────────────────────────────────────────────────
    ("hdfc bank",               "HDFCBANK.NS",      "HDFC Bank"),
    ("hdfc life",               "HDFCLIFE.NS",      "HDFC Life"),
    ("hdfc amc",                "HDFCAMC.NS",       "HDFC AMC"),
    ("hdfc",                    "HDFCBANK.NS",      "HDFC Bank"),
    ("icici bank",              "ICICIBANK.NS",     "ICICI Bank"),
    ("icici lombard",           "ICICIGI.NS",       "ICICI Lombard"),
    ("icici prudential",        "ICICIPRULI.NS",    "ICICI Prudential"),
    ("icici",                   "ICICIBANK.NS",     "ICICI Bank"),
    ("state bank",              "SBIN.NS",          "State Bank of India"),
    ("sbi",                     "SBIN.NS",          "SBI"),
    ("axis bank",               "AXISBANK.NS",      "Axis Bank"),
    ("kotak mahindra",          "KOTAKBANK.NS",     "Kotak Mahindra Bank"),
    ("kotak",                   "KOTAKBANK.NS",     "Kotak Mahindra Bank"),
    ("yes bank",                "YESBANK.NS",       "Yes Bank"),
    ("pnb",                     "PNB.NS",           "Punjab National Bank"),
    ("punjab national",         "PNB.NS",           "Punjab National Bank"),
    ("bank of baroda",          "BANKBARODA.NS",    "Bank of Baroda"),
    ("canara bank",             "CANBK.NS",         "Canara Bank"),
    ("union bank",              "UNIONBANK.NS",     "Union Bank of India"),
    ("indusind",                "INDUSINDBK.NS",    "IndusInd Bank"),
    ("bandhan bank",            "BANDHANBNK.NS",    "Bandhan Bank"),
    ("idfc first",              "IDFCFIRSTB.NS",    "IDFC First Bank"),
    ("au small",                "AUBANK.NS",        "AU Small Finance Bank"),
    ("bajaj finance",           "BAJFINANCE.NS",    "Bajaj Finance"),
    ("bajaj finserv",           "BAJAJFINSV.NS",    "Bajaj Finserv"),
    ("bajaj auto",              "BAJAJ-AUTO.NS",    "Bajaj Auto"),
    ("bajaj",                   "BAJAJ-AUTO.NS",    "Bajaj Auto"),
    ("shriram finance",         "SHRIRAMFIN.NS",    "Shriram Finance"),
    ("muthoot",                 "MUTHOOTFIN.NS",    "Muthoot Finance"),
    ("manappuram",              "MANAPPURAM.NS",    "Manappuram Finance"),
    ("cholamandalam",           "CHOLAFIN.NS",      "Cholamandalam Finance"),
    ("lic",                     "LICI.NS",          "LIC India"),
    # ── Energy & Power ──────────────────────────────────────────────────────
    ("ntpc",                    "NTPC.NS",          "NTPC"),
    ("power grid",              "POWERGRID.NS",     "Power Grid Corp"),
    ("bhel",                    "BHEL.NS",          "BHEL"),
    ("ongc",                    "ONGC.NS",          "ONGC"),
    ("coal india",              "COALINDIA.NS",     "Coal India"),
    ("indian oil",              "IOC.NS",           "Indian Oil Corp"),
    ("ioc",                     "IOC.NS",           "Indian Oil Corp"),
    ("bpcl",                    "BPCL.NS",          "BPCL"),
    ("hpcl",                    "HPCL.NS",          "HPCL"),
    ("gail",                    "GAIL.NS",          "GAIL India"),
    ("petronet",                "PETRONET.NS",      "Petronet LNG"),
    ("torrent power",           "TORNTPOWER.NS",    "Torrent Power"),
    ("cesc",                    "CESC.NS",          "CESC"),
    ("jsw energy",              "JSWENERGY.NS",     "JSW Energy"),
    # ── Auto ────────────────────────────────────────────────────────────────
    ("maruti",                  "MARUTI.NS",        "Maruti Suzuki"),
    ("hero motocorp",           "HEROMOTOCO.NS",    "Hero MotoCorp"),
    ("hero moto",               "HEROMOTOCO.NS",    "Hero MotoCorp"),
    ("mahindra & mahindra",     "M&M.NS",           "Mahindra & Mahindra"),
    ("mahindra",                "M&M.NS",           "Mahindra & Mahindra"),
    ("m&m",                     "M&M.NS",           "Mahindra & Mahindra"),
    ("ashok leyland",           "ASHOKLEY.NS",      "Ashok Leyland"),
    ("eicher",                  "EICHERMOT.NS",     "Eicher Motors (Royal Enfield)"),
    ("tvs motor",               "TVSMOTOR.NS",      "TVS Motor"),
    ("tvs",                     "TVSMOTOR.NS",      "TVS Motor"),
    ("bosch",                   "BOSCHLTD.NS",      "Bosch India"),
    ("motherson",               "MOTHERSON.NS",     "Samvardhana Motherson"),
    ("minda",                   "MINDAIND.NS",      "Minda Industries"),
    ("exide",                   "EXIDEIND.NS",      "Exide Industries"),
    ("amara raja",              "AMARAJABAT.NS",    "Amara Raja Energy"),
    # ── Pharma ──────────────────────────────────────────────────────────────
    ("sun pharma",              "SUNPHARMA.NS",     "Sun Pharma"),
    ("sun pharmaceutical",      "SUNPHARMA.NS",     "Sun Pharma"),
    ("dr reddy",                "DRREDDY.NS",       "Dr. Reddy's"),
    ("cipla",                   "CIPLA.NS",         "Cipla"),
    ("divi's",                  "DIVISLAB.NS",      "Divi's Laboratories"),
    ("divis",                   "DIVISLAB.NS",      "Divi's Laboratories"),
    ("aurobindo",               "AUROPHARMA.NS",    "Aurobindo Pharma"),
    ("lupin",                   "LUPIN.NS",         "Lupin"),
    ("torrent pharma",          "TORNTPHARM.NS",    "Torrent Pharma"),
    ("alkem",                   "ALKEM.NS",         "Alkem Laboratories"),
    ("biocon",                  "BIOCON.NS",        "Biocon"),
    ("abbott india",            "ABBOTINDIA.NS",    "Abbott India"),
    ("pfizer india",            "PFIZER.NS",        "Pfizer India"),
    ("gland pharma",            "GLAND.NS",         "Gland Pharma"),
    ("ipca",                    "IPCALAB.NS",       "IPCA Laboratories"),
    ("laurus",                  "LAURUSLABS.NS",    "Laurus Labs"),
    ("granules",                "GRANULES.NS",      "Granules India"),
    # ── FMCG ────────────────────────────────────────────────────────────────
    ("hindustan unilever",      "HINDUNILVR.NS",    "Hindustan Unilever"),
    ("hul",                     "HINDUNILVR.NS",    "HUL"),
    ("itc",                     "ITC.NS",           "ITC"),
    ("nestle india",            "NESTLEIND.NS",     "Nestlé India"),
    ("nestleind",               "NESTLEIND.NS",     "Nestlé India"),
    ("nestle",                  "NESTLEIND.NS",     "Nestlé India"),
    ("asian paints",            "ASIANPAINT.NS",    "Asian Paints"),
    ("pidilite",                "PIDILITIND.NS",    "Pidilite (Fevicol)"),
    ("dabur",                   "DABUR.NS",         "Dabur"),
    ("marico",                  "MARICO.NS",        "Marico"),
    ("godrej consumer",         "GODREJCP.NS",      "Godrej Consumer Products"),
    ("godrej industries",       "GODREJIND.NS",     "Godrej Industries"),
    ("godrej",                  "GODREJCP.NS",      "Godrej Consumer Products"),
    ("emami",                   "EMAMILTD.NS",      "Emami"),
    ("britannia",               "BRITANNIA.NS",     "Britannia"),
    ("tata consumer",           "TATACONSUM.NS",    "Tata Consumer Products"),
    ("varun beverages",         "VBL.NS",           "Varun Beverages (PepsiCo)"),
    ("radico",                  "RADICO.NS",        "Radico Khaitan"),
    ("united spirits",          "MCDOWELL-N.NS",    "United Spirits"),
    # ── Telecom ─────────────────────────────────────────────────────────────
    ("bharti airtel",           "BHARTIARTL.NS",    "Bharti Airtel"),
    ("airtel",                  "BHARTIARTL.NS",    "Bharti Airtel"),
    ("vodafone idea",           "IDEA.NS",          "Vodafone Idea"),
    ("vi ",                     "IDEA.NS",          "Vodafone Idea"),
    ("idea",                    "IDEA.NS",          "Vodafone Idea"),
    ("indus towers",            "INDUSTOWER.NS",    "Indus Towers"),
    # ── Cement ──────────────────────────────────────────────────────────────
    ("ultratech",               "ULTRACEMCO.NS",    "UltraTech Cement"),
    ("shree cement",            "SHREECEM.NS",      "Shree Cement"),
    ("acc",                     "ACC.NS",           "ACC Cement"),
    ("ambuja",                  "AMBUJACEM.NS",     "Ambuja Cement"),
    ("jk cement",               "JKCEMENT.NS",      "JK Cement"),
    ("dalmia",                  "DALBHARAT.NS",     "Dalmia Bharat"),
    ("birla corporation",       "BIRLACORPN.NS",    "Birla Corporation"),
    # ── Steel & Metals ──────────────────────────────────────────────────────
    ("jsw steel",               "JSWSTEEL.NS",      "JSW Steel"),
    ("jsw",                     "JSWSTEEL.NS",      "JSW Steel"),
    ("steel authority",         "SAIL.NS",          "SAIL"),
    ("sail",                    "SAIL.NS",          "SAIL"),
    ("hindalco",                "HINDALCO.NS",      "Hindalco"),
    ("vedanta",                 "VEDL.NS",          "Vedanta"),
    ("national aluminium",      "NATIONALUM.NS",    "NALCO"),
    ("nalco",                   "NATIONALUM.NS",    "NALCO"),
    ("nmdc",                    "NMDC.NS",          "NMDC"),
    ("hindustan zinc",          "HINDZINC.NS",      "Hindustan Zinc"),
    # ── Consumer & Retail ───────────────────────────────────────────────────
    ("avenue supermarts",       "DMART.NS",         "D-Mart (Avenue Supermarts)"),
    ("dmart",                   "DMART.NS",         "D-Mart"),
    ("titan",                   "TITAN.NS",         "Titan Company"),
    ("trent",                   "TRENT.NS",         "Trent (Westside/Zudio)"),
    ("kalyan jewellers",        "KALYANKJIL.NS",    "Kalyan Jewellers"),
    ("senco gold",              "SENCO.NS",         "Senco Gold"),
    ("pc jeweller",             "PCJEWELLER.NS",    "PC Jeweller"),
    ("zomato",                  "ZOMATO.NS",        "Zomato"),
    ("swiggy",                  "SWIGGY.NS",        "Swiggy"),
    ("nykaa",                   "FSN.NS",           "Nykaa (FSN E-Commerce)"),
    ("paytm",                   "PAYTM.NS",         "Paytm (One 97 Communications)"),
    ("policybazaar",            "POLICYBZR.NS",     "PolicyBazaar"),
    ("cartrade",                "CARTRADE.NS",      "CarTrade Tech"),
    ("irctc",                   "IRCTC.NS",         "IRCTC"),
    ("india mart",              "INDIAMART.NS",     "IndiaMART InterMESH"),
    ("indiamart",               "INDIAMART.NS",     "IndiaMART InterMESH"),
    # ── Infrastructure & Real Estate ────────────────────────────────────────
    ("larsen",                  "LT.NS",            "L&T (Larsen & Toubro)"),
    ("l&t",                     "LT.NS",            "L&T"),
    ("dlf",                     "DLF.NS",           "DLF"),
    ("godrej properties",       "GODREJPROP.NS",    "Godrej Properties"),
    ("oberoi realty",           "OBEROIRLTY.NS",    "Oberoi Realty"),
    ("prestige",                "PRESTIGE.NS",      "Prestige Estates"),
    ("macrotech",               "LODHA.NS",         "Macrotech (Lodha)"),
    ("brigade",                 "BRIGADE.NS",       "Brigade Enterprises"),
    ("irb infra",               "IRB.NS",           "IRB Infrastructure"),
    ("gmr airports",            "GMRAIRPORT.NS",    "GMR Airports"),
    ("indian railways",         "IRFC.NS",          "IRFC"),
    ("irfc",                    "IRFC.NS",          "IRFC"),
    ("rvnl",                    "RVNL.NS",          "Rail Vikas Nigam"),
    # ── Defence & Aerospace ─────────────────────────────────────────────────
    ("hal",                     "HAL.NS",           "HAL"),
    ("hindustan aeronautics",   "HAL.NS",           "HAL"),
    ("bharat electronics",      "BEL.NS",           "BEL"),
    ("bel",                     "BEL.NS",           "BEL"),
    ("bharat dynamics",         "BDL.NS",           "Bharat Dynamics"),
    ("mazagon dock",            "MAZDOCK.NS",       "Mazagon Dock"),
    ("garden reach",            "GRSE.NS",          "Garden Reach Shipbuilders"),
    ("cochin shipyard",         "COCHINSHIP.NS",    "Cochin Shipyard"),
    # ── Indices ─────────────────────────────────────────────────────────────
    ("nifty bank",              "^NSEBANK",         "Nifty Bank Index"),
    ("nifty 50",                "^NSEI",            "Nifty 50 Index"),
    ("nifty",                   "^NSEI",            "Nifty 50 Index"),
    ("sensex",                  "^BSESN",           "BSE Sensex Index"),
    # ── Crypto ──────────────────────────────────────────────────────────────
    ("bitcoin",                 "BTC-USD",          "Bitcoin"),
    ("ethereum",                "ETH-USD",          "Ethereum"),
    ("btc",                     "BTC-USD",          "Bitcoin"),
    ("eth",                     "ETH-USD",          "Ethereum"),
    ("solana",                  "SOL-USD",          "Solana"),
    ("bnb",                     "BNB-USD",          "BNB"),
    ("xrp",                     "XRP-USD",          "XRP"),
    ("dogecoin",                "DOGE-USD",         "Dogecoin"),
    ("doge",                    "DOGE-USD",         "Dogecoin"),
    ("shiba",                   "SHIB-USD",         "Shiba Inu"),
]


def _yahoo_fetch_price(ticker: str, display_name: str) -> dict | None:
    """
    Fetch live price from Yahoo Finance v8 chart API.
    Returns a finance result dict or None on failure.
    No API key needed. Works for NSE (.NS), BSE (.BO), US, crypto.
    """
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        resp = _http.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"⚠️ [Yahoo] HTTP {resp.status_code} for {ticker}")
            return None

        data   = resp.json()
        result_list = data.get("chart", {}).get("result")
        if not result_list:
            logger.warning(f"⚠️ [Yahoo] empty result for {ticker}")
            return None

        meta   = result_list[0].get("meta", {})
        price  = meta.get("regularMarketPrice")
        if not price:
            return None

        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose") or 0
        currency   = meta.get("currency", "INR")
        exchange   = meta.get("exchangeName", "")
        mkt_state  = meta.get("marketState", "UNKNOWN")

        change     = round(price - prev_close, 2) if prev_close else 0
        change_pct = f"{round((change / prev_close) * 100, 2)}%" if prev_close else "N/A"
        sign       = "+" if change >= 0 else ""

        sym_prefix = "₹" if currency == "INR" else ("$" if currency == "USD" else "")
        fmt        = lambda v: f"{sym_prefix}{v:,.2f}" if v else "N/A"

        return {
            "tool":         "finance",
            "source":       "Yahoo Finance (live)",
            "symbol":       ticker,
            "display_name": display_name,
            "price":        fmt(price),
            "change":       f"{sign}{change}",
            "change_pct":   change_pct,
            "prev_close":   fmt(prev_close),
            "currency":     currency,
            "exchange":     exchange,
            "market_state": mkt_state,
            "data_quality": "LIVE — fetched right now from Yahoo Finance",
        }
    except (requests.exceptions.RequestException, KeyError, ValueError, TypeError, IndexError) as e:
        logger.warning(f"⚠️ [Yahoo] expected failure for {ticker}: {e}")
        return None
    except Exception as e:
        _log_unexpected(f"Yahoo Finance quote ({ticker})", e)
        return None


def _yahoo_search_ticker(company_name: str) -> tuple[str, str] | tuple[None, None]:
    """
    Use Yahoo Finance search API to find the NSE/BSE ticker for ANY company name.
    Returns (ticker, display_name) or (None, None) on failure.
    Strongly prefers .NS (NSE) tickers for Indian companies.
    """
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/search"
        params = {
            "q":            company_name,
            "quotesCount":  10,
            "newsCount":    0,
            "listsCount":   0,
            "enableFuzzyQuery": False,
        }
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        resp = _http.get(url, params=params, headers=headers, timeout=8)
        if resp.status_code != 200:
            return None, None

        quotes = resp.json().get("quotes", [])
        if not quotes:
            return None, None

        # Priority: NSE equity > BSE equity > other exchanges
        ns_hit  = next((q for q in quotes if str(q.get("symbol", "")).endswith(".NS")
                        and q.get("quoteType") == "EQUITY"), None)
        bo_hit  = next((q for q in quotes if str(q.get("symbol", "")).endswith(".BO")
                        and q.get("quoteType") == "EQUITY"), None)
        any_hit = next((q for q in quotes if q.get("quoteType") == "EQUITY"), None)

        best = ns_hit or bo_hit or any_hit or quotes[0]
        symbol = best.get("symbol", "")
        name   = best.get("longname") or best.get("shortname") or company_name.title()
        logger.info(f"🔎 [Yahoo Search] '{company_name}' → {symbol} ({name})")
        return symbol, name

    except (requests.exceptions.RequestException, KeyError, ValueError, TypeError, IndexError) as e:
        logger.warning(f"⚠️ [Yahoo Search] expected failure: {e}")
        return None, None
    except Exception as e:
        _log_unexpected("Yahoo Finance search", e)
        return None, None


def tool_finance(prompt: str) -> dict:
    """
    Fetch live stock/share price for ANY company.

    Pipeline:
      1. Check KNOWN_TICKERS map (instant, no network)
      2. If not found → Yahoo Finance Search API (finds ticker by name)
      3. Fetch live quote via Yahoo Finance Chart API (no API key needed)
      4. Alpha Vantage fallback for US stocks (if ALPHAVANTAGE_KEY set)
      5. DuckDuckGo ONLY as absolute last resort (flagged so AI won't hallucinate)
    """
    logger.info(f"💹 [TOOL] finance | prompt: {prompt[:80]}")

    lower = prompt.lower()

    # ── Step 1: Known ticker map (longest match wins) ────────────────────────
    ticker       = None
    display_name = None
    for keyword, symbol, name in KNOWN_TICKERS:
        if keyword in lower:
            ticker       = symbol
            display_name = name
            logger.info(f"✅ [Finance] known map hit: '{keyword}' → {symbol}")
            break

    # ── Step 2: Yahoo Finance Search (for unknown companies) ─────────────────
    if not ticker:
        # Extract company name from prompt — strip finance keywords
        company_raw = re.sub(
            r'\b(share price|stock price|price today|current price|nse|bse|'
            r'today|live|what is|what\'s|the|of|for|tell me|how much|'
            r'share|stock|market)\b',
            " ", lower, flags=re.IGNORECASE
        ).strip()
        # Clean up extra spaces
        company_raw = re.sub(r'\s+', ' ', company_raw).strip(" ?.,!")

        if company_raw and len(company_raw) >= 2:
            logger.info(f"🔎 [Finance] searching Yahoo for: '{company_raw}'")
            ticker, display_name = _yahoo_search_ticker(company_raw)

    # ── Step 3: Fetch live price from Yahoo Finance ───────────────────────────
    if ticker:
        result = _yahoo_fetch_price(ticker, display_name or ticker)
        if result:
            logger.info(f"✅ [Finance] Yahoo live price: {display_name} ({ticker}) = {result['price']}")
            return result
        # Ticker found but price fetch failed — try .NS variant if we have .BO
        if ticker.endswith(".BO"):
            alt = ticker.replace(".BO", ".NS")
            result = _yahoo_fetch_price(alt, display_name or alt)
            if result:
                return result

    # ── Step 4: Alpha Vantage (US stocks only, if key is set) ────────────────
    if ALPHAVANTAGE_KEY and ticker and not ticker.endswith((".NS", ".BO")):
        try:
            url   = (f"https://www.alphavantage.co/query"
                     f"?function=GLOBAL_QUOTE&symbol={ticker}&apikey={ALPHAVANTAGE_KEY}")
            resp  = _http.get(url, timeout=8)
            quote = resp.json().get("Global Quote", {})
            if quote and quote.get("05. price"):
                return {
                    "tool":         "finance",
                    "source":       "Alpha Vantage (live)",
                    "symbol":       quote.get("01. symbol", ticker),
                    "display_name": display_name or ticker,
                    "price":        quote.get("05. price", "N/A"),
                    "change":       quote.get("09. change", "N/A"),
                    "change_pct":   quote.get("10. change percent", "N/A"),
                    "prev_close":   quote.get("08. previous close", "N/A"),
                    "data_quality": "LIVE — fetched right now from Alpha Vantage",
                }
        except (requests.exceptions.RequestException, KeyError, ValueError, TypeError) as e:
            logger.warning(f"⚠️ [Finance] Alpha Vantage expected failure: {e}")
        except Exception as e:
            _log_unexpected("Finance Alpha Vantage", e)

    # ── Step 5: Price unavailable — return structured "not found" ─────────────
    # We do NOT do a DDG search here because web snippets contain stale prices
    # that the LLM will hallucinate as live data. Instead, return a clear signal
    # telling the model to refuse to quote any price.
    logger.warning("⚠️ [Finance] all APIs failed — returning price_unavailable")
    searched_name = display_name or (company_raw if 'company_raw' in locals() else prompt[:60])
    return {
        "tool":              "finance",
        "price_unavailable": True,
        "searched_name":     searched_name,
    }


# ============================================================
# ✅ TOOL: NEWS
# Uses NewsData.io free tier (100 req/day) or DuckDuckGo fallback
# ============================================================
def tool_news(prompt: str) -> dict:
    """
    Fetch top news headlines. Extracts topic if present.
    """
    logger.info(f"📰 [TOOL] news | prompt: {prompt[:80]}")

    # Extract topic
    topic_match = re.search(
        r'news (?:about|on|regarding|of) (.+?)(?:\?|$)', prompt, re.IGNORECASE
    )
    topic = topic_match.group(1).strip() if topic_match else None

    if not NEWSDATA_API_KEY:
        logger.warning("⚠️ [TOOL] news — no API key, falling back to web search")
        query = f"latest news {topic}" if topic else "top headlines today India"
        return tool_web_search(query)

    try:
        params = {
            "apikey": NEWSDATA_API_KEY,
            "language": "en",
            "country": "in",
            "size": 5,
        }
        if topic:
            params["q"] = topic

        resp = _http.get("https://newsdata.io/api/1/news", params=params, timeout=8)
        data = resp.json()

        if data.get("status") != "success":
            query = f"latest news {topic}" if topic else "top India news today"
            return tool_web_search(query)

        articles = data.get("results", [])[:5]
        headlines = [
            {
                "title": a.get("title", ""),
                "summary": (a.get("description") or "")[:200],
                "source": a.get("source_id", ""),
                "url": a.get("link", ""),
                "published": a.get("pubDate", ""),
            }
            for a in articles if a.get("title")
        ]

        result = {"tool": "news", "topic": topic or "Top Headlines", "articles": headlines}
        logger.info(f"✅ [TOOL] news success: {len(headlines)} articles")
        return result

    except (requests.exceptions.RequestException, KeyError, ValueError, TypeError) as e:
        logger.warning(f"⚠️ [TOOL] news: expected failure, falling back to search: {e}")
        query = f"latest news {topic}" if topic else "top news today"
        return tool_web_search(query)
    except Exception as e:
        _log_unexpected("TOOL news", e)
        query = f"latest news {topic}" if topic else "top news today"
        return tool_web_search(query)


# ============================================================
# ✅ TOOL: SPORTS / CRICKET
# Uses CricAPI free tier for cricket; DuckDuckGo fallback for others
# ============================================================
def tool_sports(prompt: str) -> dict:
    """
    Fetch live cricket scores or general sports news via search.
    """
    logger.info(f"🏏 [TOOL] sports | prompt: {prompt[:80]}")

    lower = prompt.lower()
    is_cricket = any(k in lower for k in ["cricket", "ipl", "test match", "odi", "t20", "bcci"])

    if is_cricket and CRICAPI_KEY:
        try:
            resp = _http.get(
                f"https://api.cricapi.com/v1/currentMatches?apikey={CRICAPI_KEY}&offset=0",
                timeout=8
            )
            data = resp.json()

            if data.get("status") == "success":
                matches = data.get("data", [])[:4]
                live_matches = []
                for m in matches:
                    live_matches.append({
                        "name": m.get("name", ""),
                        "status": m.get("status", ""),
                        "score": m.get("score", []),
                        "teams": m.get("teams", []),
                        "match_type": m.get("matchType", ""),
                        "venue": m.get("venue", ""),
                    })

                result = {"tool": "sports", "sport": "cricket", "matches": live_matches}
                logger.info(f"✅ [TOOL] sports (cricket) success: {len(live_matches)} matches")
                return result
        except (requests.exceptions.RequestException, KeyError, ValueError, TypeError) as e:
            logger.warning(f"⚠️ [TOOL] cricket API expected failure: {e}")
        except Exception as e:
            _log_unexpected("TOOL sports cricket", e)

    # Fallback: DuckDuckGo search for any sport
    sport_kw = "cricket live score" if is_cricket else "live sports scores today"
    return tool_web_search(f"{sport_kw} {prompt}")


# ============================================================
# ✅ TOOL: WEB SEARCH (DuckDuckGo — Multi-Query Intelligence)
# Runs 2-3 targeted queries, deduplicates, cross-references,
# and returns rich context so the AI can reason like ChatGPT.
# ============================================================
def _ddg_search(query: str, max_results: int = 5) -> list:
    """
    Search using Tavily (preferred — reliable, AI-optimized, current events)
    with DuckDuckGo as fallback.

    Tavily advantages over DDG:
    - Returns a direct answer field (perfect for political/government queries)
    - Crawls live pages — not cached results
    - Never rate-blocks Indian news queries
    - Free tier: 1000 searches/month at tavily.com
    """

    # ── Tavily (primary) ────────────────────────────────────────────────────
    if TAVILY_API_KEY:
        try:
            resp = _http.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "max_results": max_results,
                    "include_answer": True,        # asks Tavily to synthesize a direct answer
                    "include_raw_content": False,
                    "search_depth": "basic",       # "advanced" uses 2x credits — basic is enough
                    "include_domains": [],
                    "exclude_domains": [],
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []

                # Prepend Tavily's direct answer as a synthetic top result
                if data.get("answer"):
                    results.append({
                        "title": "Direct Answer",
                        "body": data["answer"],
                        "href": "",
                        "query_used": query,
                        "is_direct_answer": True,
                    })

                for r in data.get("results", []):
                    results.append({
                        "title": r.get("title", ""),
                        "body": (r.get("content", "") or "")[:400],
                        "href": r.get("url", ""),
                        "query_used": query,
                    })

                logger.info(f"✅ [Tavily] '{query}' → {len(results)} results"
                      + (f" | direct answer: {data['answer'][:80]}" if data.get("answer") else ""))
                return results[:max_results + 1]  # +1 to account for the direct answer prepend

            else:
                logger.warning(f"⚠️ [Tavily] HTTP {resp.status_code} for '{query}' — falling back to DDG")

        except (requests.exceptions.RequestException, KeyError, ValueError, TypeError) as e:
            logger.warning(f"⚠️ [Tavily] expected failure for '{query}': {e} — falling back to DDG")
        except Exception as e:
            _log_unexpected(f"Tavily search ({query!r})", e)

    # ── DuckDuckGo (fallback) ───────────────────────────────────────────────
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))
        results = [
            {
                "title": r.get("title", ""),
                "body": r.get("body", "")[:400],
                "href": r.get("href", ""),
                "query_used": query,
            }
            for r in raw if r.get("title")
        ]
        logger.info(f"✅ [DDG fallback] '{query}' → {len(results)} results")
        return results
    except (KeyError, ValueError, TypeError) as e:
        logger.warning(f"⚠️ [DDG] query={query!r} expected failure: {e}")
        return []
    except Exception as e:
        # duckduckgo_search doesn't guarantee a single stable exception type
        # for rate limits/network issues, so this stays as the last resort.
        _log_unexpected(f"DDG search ({query!r})", e)
        return []


def _build_smart_queries(original_query: str) -> list[str]:
    """
    Generate 2-3 smart, targeted search queries from the original user query.
    This is the key to ChatGPT-level information quality — multiple angles.
    """
    lower = original_query.lower().strip().rstrip("?.,!")
    queries = [original_query]  # always include the original

    # Political / government positions → add recency + official source queries
    political_patterns = [
        r'\b(cm|chief minister|prime minister|president|governor|minister)\b',
        r'\b(mayor|mla|mp|senator|chancellor|premier|ceo|director|chairman)\b',
        r'\b(who (is|are|was|won|became|got elected|was elected|was appointed))\b',
        r'\b(election|elected|appointed|result|winner)\b',
    ]
    is_political = any(re.search(p, lower) for p in political_patterns)

    if is_political:
        # Add a "2024 2025" recency query
        queries.append(lower + " 2025")
        # Add a "latest news" angle
        queries.append("latest " + lower)

    # Current events / news → add date-qualified query
    elif any(w in lower for w in ["now", "today", "current", "latest", "new", "recent"]):
        queries.append(lower + " 2025")
        # Add a news-specific query
        if not lower.startswith("news"):
            queries.append("news " + lower)

    # Factual lookups → add Wikipedia + authoritative source query
    elif any(w in lower for w in ["what is", "who is", "when was", "where is", "how does"]):
        queries.append(lower + " explained")
        queries.append(lower + " official")

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for q in queries:
        q_clean = q.strip().lower()
        if q_clean not in seen:
            seen.add(q_clean)
            unique.append(q.strip())

    return unique[:3]  # max 3 queries


def tool_web_search(query: str, max_results: int = 5) -> dict:
    """
    Production web search pipeline:
      Primary  → Tavily + Serper (parallel) + Firecrawl + Cohere reranking
      Fallback → Legacy Tavily-then-DDG (if production engine unavailable)

    Returns a rich result dict with citations, trust scores, and cross-reference data.
    """
    logger.info(f"🔍 [TOOL] web_search | query: {query[:80]}")

    # ── Production engine (Tavily + Serper + Firecrawl + Cohere) ─────────────
    if PRODUCTION_SEARCH_ENABLED:
        try:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _pool:
                future = _pool.submit(run_production_search, query)
                result = future.result(timeout=30)   # 30s wall-clock max for whole pipeline
            if result.get("results"):
                result["tool"] = "web_search"
                logger.info(f"✅ [TOOL] Production search: {result['result_count']} results")
                return result
            logger.warning("⚠️ [TOOL] Production search returned no results — falling back")
        except concurrent.futures.TimeoutError:
            logger.warning("⚠️ [TOOL] Production search timed out after 30s — falling back")
        except Exception as _pe:
            _log_unexpected("TOOL Production search", _pe)

    # ── Legacy fallback (Tavily-then-DDG — original logic) ───────────────────
    logger.info("🔁 [TOOL] Using legacy search fallback")
    smart_queries = _build_smart_queries(query)
    all_results = []
    seen_urls   = set()

    for q in smart_queries:
        batch = _ddg_search(q, max_results=max_results)
        for r in batch:
            url = r.get("href", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(r)
        if len(all_results) >= 10:
            break

    primary_results = [r for r in all_results if r.get("query_used", "") == query]
    supplemental    = [r for r in all_results if r.get("query_used", "") != query]
    final_results   = (primary_results + supplemental)[:10]

    logger.info(f"✅ [TOOL] Legacy search: {len(final_results)} results")
    return {
        "tool":           "web_search",
        "query":          query,
        "queries_run":    smart_queries,
        "result_count":   len(final_results),
        "results":        final_results,
        "citations":      {},
        "search_engine":  "legacy",
    }


def tool_wikipedia(prompt: str) -> dict:
    """
    Wikipedia intelligence tool.
    Searches Wikipedia for the best matching article and returns a context dict.
    If Wikipedia has no good result, automatically falls back to DuckDuckGo web search.
    """
    logger.info(f"📚 [TOOL] wikipedia | prompt: {prompt[:80]}")
    result = search_wikipedia(prompt)

    if result["found"]:
        return result  # {"tool": "wikipedia", "found": True, "title": ..., "context": ...}

    # Fallback: Wikipedia had no useful answer — use web search silently
    reason = result.get("reason", "")
    logger.info(f"⚡ [Wiki→Web] fallback reason='{reason}' — running web_search")
    web_result = tool_web_search(prompt)
    web_result["wiki_fallback"] = True   # flag so context builder knows
    return web_result


# ============================================================
# ✅ TOOL ROUTER — dispatches to the correct tool
# ============================================================
def build_sources_payload(tool_result: dict) -> str | None:
    """
    Build a JSON-serialised 'sources' SSE payload from a tool result.
    Uses production citation data when available (includes trust scores + citation numbers).
    Returns None if there are no usable sources.
    """
    if not tool_result:
        return None

    # ── Production engine: use rich citation map ──────────────────────────────
    if PRODUCTION_SEARCH_ENABLED and tool_result.get("search_engine") == "production":
        try:
            payload = build_production_sources_payload(tool_result)
            if payload:
                return payload
        except (KeyError, TypeError, ValueError) as e:
            logger.info(f"[sources payload] production payload build failed, using legacy: {e}")

    # ── Legacy: build from results list ──────────────────────────────────────
    results = tool_result.get("results", [])
    if not results:
        return None
    sources = []
    for r in results[:8]:  # up to 8 sources
        url   = r.get("href", "") or r.get("url", "")
        title = r.get("title", "") or r.get("name", "")
        if url:
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc.replace("www.", "")
            except (ValueError, AttributeError):
                domain = url
            sources.append({
                "url":    url,
                "title":  title or domain,
                "domain": domain,
                "num":    r.get("citation_num"),
                "trust":  r.get("trust_score", 50),
            })
    if not sources:
        return None
    return json.dumps({"sources": sources})


def run_tool(intent: str, prompt: str) -> dict | None:
    """
    Routes to the appropriate tool based on detected intent.
    Returns tool output dict, or None for 'general' (no tool needed).
    """
    logger.info(f"🗺️ [ROUTER] intent={intent}")
    if intent == "clock":        return tool_clock(prompt)
    if intent == "weather":     return tool_weather(prompt)
    if intent == "finance":     return tool_finance(prompt)
    if intent == "news":        return tool_news(prompt)
    if intent == "sports":      return tool_sports(prompt)
    if intent == "wikipedia":   return tool_wikipedia(prompt)
    if intent == "web_search":  return tool_web_search(prompt)
    return None  # general — no tool


# ============================================================
# ✅ CONTEXT BUILDER — turns tool output into AI system context
# ============================================================
def build_tool_context(tool_result: dict) -> str:
    """
    Formats raw tool output into a clean system-prompt injection.
    The AI uses this data to write its final answer.
    """
    if not tool_result:
        return ""

    tool = tool_result.get("tool", "")
    lines = [f"📡 LIVE DATA FROM TOOL [{tool.upper()}] — use this to answer accurately:\n"]

    if tool == "clock":
        lines += [
            f"Location  : {tool_result['location']}",
            f"Timezone  : {tool_result['timezone']} ({tool_result['tz_abbrev']}, UTC{tool_result['utc_offset']})",
            f"Time (12h): {tool_result['time_12h']}",
            f"Time (24h): {tool_result['time_24h']}",
            f"Date      : {tool_result['date']}",
        ]

    elif tool == "weather":
        lines += [
            f"City: {tool_result['city']}",
            f"Temperature: {tool_result['temperature']}°C (feels like {tool_result['feels_like']}°C)",
            f"Condition: {tool_result['condition']}",
            f"Humidity: {tool_result['humidity']}%",
            f"Wind Speed: {tool_result['wind_speed']} m/s",
            f"Min/Max Today: {tool_result['min_temp']}°C / {tool_result['max_temp']}°C",
        ]

    elif tool == "finance":
        if tool_result.get("price_unavailable"):
            # All APIs failed — give the model ZERO numbers to work with
            searched = tool_result.get("searched_name", "this company")
            lines.append(
                f"🚨 LIVE PRICE FETCH FAILED for: {searched}\n"
                "No live price data is available from Yahoo Finance or any other source.\n"
                "MANDATORY INSTRUCTION — you MUST respond with EXACTLY this message "
                "(do not add any price, do not guess, do not use training memory):\n"
                f"'I wasn't able to fetch the live share price for {searched} right now. "
                "Please check the current price directly on NSE (nseindia.com), "
                "BSE (bseindia.com), or your broker / trading app for accurate data.'"
            )
        elif tool_result.get("finance_ddg_fallback"):
            # Legacy path — treat the same as unavailable (no snippets to the model)
            searched = tool_result.get("searched_name", "this company")
            lines.append(
                f"🚨 LIVE PRICE FETCH FAILED for: {searched}\n"
                "No verified live price is available.\n"
                "MANDATORY INSTRUCTION: Tell the user you could not fetch the live price "
                "and direct them to nseindia.com, bseindia.com, or their broker app. "
                "Do NOT quote any number — not from snippets, not from training memory."
            )
        else:
            lines += [
                f"Stock: {tool_result.get('display_name', tool_result.get('symbol', 'N/A'))} "
                f"({tool_result.get('symbol', 'N/A')})",
                f"Exchange: {tool_result.get('exchange', 'NSE')} | "
                f"Market State: {tool_result.get('market_state', 'N/A')}",
                f"Current Price (LIVE): {tool_result.get('price', 'N/A')}",
                f"Change Today: {tool_result.get('change', 'N/A')} "
                f"({tool_result.get('change_pct', 'N/A')})",
                f"Previous Close: {tool_result.get('prev_close', 'N/A')}",
                f"Data Source: {tool_result.get('data_quality', 'Yahoo Finance (live)')}",
            ]
            lines.append(
                "\n⚠️ ANTI-HALLUCINATION: The price above is the ONLY correct current price. "
                "Do NOT use any number from your training memory. "
                "Report this exact price to the user."
            )

    elif tool == "news":
        lines.append(f"Topic: {tool_result['topic']}")
        for i, a in enumerate(tool_result.get("articles", []), 1):
            lines.append(f"\n{i}. {a['title']}")
            if a["summary"]: lines.append(f"   {a['summary']}")
            if a["published"]: lines.append(f"   Date: {a['published']}")
        lines.append(
            "\n📌 INSTRUCTION: Summarize the news above clearly. "
            "Do NOT mention source names, URLs, or outlet names in your reply. "
            "The sources are shown separately in the UI."
        )

    elif tool == "sports":
        sport = tool_result.get("sport", "sports")
        lines.append(f"Sport: {sport}")
        for m in tool_result.get("matches", []):
            lines.append(f"\n• {m['name']} ({m['match_type']})")
            lines.append(f"  Status: {m['status']}")
            lines.append(f"  Venue: {m.get('venue', 'N/A')}")
            for s in m.get("score", []):
                lines.append(f"  Score: {s.get('inning', '')} — {s.get('r', 0)}/{s.get('w', 0)} in {s.get('o', 0)} overs")

    elif tool == "wikipedia":
        if tool_result.get("found"):
            lines.append(tool_result["context"])
            lines.append(
                "\n\n📌 INSTRUCTION: Use the Wikipedia context above to answer accurately. "
                "Explain naturally in your own words — do not copy the extract verbatim. "
                "If the context covers the question fully, do NOT search further. "
                "If the user asked something the context only partially answers, note the gap."
            )
        else:
            # Should not reach here (tool_wikipedia auto-falls to web_search),
            # but handle defensively
            lines.append("Wikipedia had no result for this query.")

    elif tool == "web_search" and tool_result.get("wiki_fallback"):
        # Wikipedia fell back to web search — label it correctly
        lines.append(f"Search query: {tool_result['query']}")
        lines.append("(Wikipedia had no result — showing web search results instead)")
        idx = 1
        for r in tool_result.get("results", []):
            if r.get("is_direct_answer"):
                lines.append(f"\n⭐ DIRECT ANSWER (Tavily AI synthesis — treat as highest-confidence source):")
                lines.append(f"   {r['body']}")
            else:
                lines.append(f"\n{idx}. {r['title']}")
                lines.append(f"   {r['body']}")
                lines.append(f"   Source: {r['href']}")
                idx += 1

    elif tool == "web_search":
        # ── Production engine: use dedicated rich context builder ─────────────
        if PRODUCTION_SEARCH_ENABLED and tool_result.get("search_engine") == "production":
            prod_context = build_production_search_context(tool_result)
            if prod_context:
                return prod_context   # production builder handles everything

        # ── Legacy fallback context (Tavily/DDG) ──────────────────────────────
        queries_run = tool_result.get("queries_run", [tool_result.get("query", "")])
        lines.append(f"Primary search: {tool_result['query']}")
        if len(queries_run) > 1:
            lines.append(f"Also searched: {' | '.join(queries_run[1:])}")
        lines.append(f"Total unique results: {tool_result.get('result_count', len(tool_result.get('results', [])))}\n")

        result_index = 1
        for r in tool_result.get("results", []):
            q_tag = f" [via: {r.get('query_used','')}]" if r.get("query_used") and r["query_used"] != tool_result["query"] else ""
            if r.get("is_direct_answer"):
                lines.append("⭐ DIRECT ANSWER (Tavily AI synthesis — treat as highest-confidence source):")
                lines.append(f"   {r['body']}")
                lines.append("")
            else:
                url = r.get("href") or r.get("url", "")
                lines.append(f"{result_index}. {r['title']}{q_tag}")
                lines.append(f"   {r['body']}")
                lines.append(f"   Source: {url}\n")
                result_index += 1

        lines.append(
            "\n🧠 CROSS-REFERENCE INSTRUCTIONS:\n"
            "- If a ⭐ DIRECT ANSWER is present above, use it as your primary source.\n"
            "- Read ALL results carefully and look for CONSENSUS across sources.\n"
            "- Flag CONFLICTS if sources disagree.\n"
            "- Prefer RECENT and AUTHORITATIVE sources (government, major news, official sites).\n"
            "- Synthesize a clear, confident, detailed answer from the evidence.\n"
            "- DO NOT mention source names, URLs, or outlet names inline. Sources are shown in the UI.\n"
        )

    lines.append(
        "\n\n🚨 CRITICAL RULES FOR ALL LIVE DATA:\n"
        "1. Use ONLY the data shown above — NEVER use numbers or facts from your training memory.\n"
        "2. If a value shows 'N/A', tell the user it is unavailable — do NOT invent a number.\n"
        "3. NEVER fabricate prices, temperatures, scores, or headlines.\n"
        "4. Do NOT say 'I don't have real-time data' — you have live data above. Use it.\n"
        "5. Give a complete, detailed answer with all relevant context (dates, full names, event details).\n"
        "6. NEVER mention source names, URLs, or outlet names inline in your reply. Do NOT write 'Source:', 'according to [website]', or any attribution. Sources are displayed separately in the UI."
    )
    return "\n".join(lines)


# ============================================================
# ✅ DETECT INTENT ENDPOINT (debug / frontend use)
# ============================================================
@app.get("/detect_intent")
async def detect_intent_endpoint(q: str):
    intent = detect_intent(q)
    return {"query": q, "intent": intent}


# ============================================================
# ✅ LEGACY WEB SEARCH ENDPOINT (keep for backward compat)
# ============================================================
@app.get("/search")
@limiter.limit("30/minute")
async def web_search_endpoint(request: Request, q: str, max_results: int = 5):
    # tool_web_search → _ddg_search makes a blocking network call (DDGS);
    # keep it off the event loop.
    result = await _db(tool_web_search, q, max_results)
    return {"results": result.get("results", []), "query": q}


# ============================================================
# ✅ DELETE ACCOUNT ENDPOINT
# Deletes the user from Supabase using the Admin API.
# The session cookie identifies who is making the request.
# ============================================================
@app.post("/delete_account")
async def delete_account(request: Request):
    """
    Deletes the authenticated user's Supabase account.
    Requires SUPABASE_SERVICE_KEY (service_role key — keep secret, server-only).
    """
    try:
        # Get the user's JWT from the Authorization header or cookie
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip()

        if not token:
            return JSONResponse({"error": "Not authenticated"}, status_code=401)

        # Verify the token and get the user ID using the regular client
        try:
            user_resp = await _db(supabase.auth.get_user, token)
            user_id   = user_resp.user.id
        except Exception as e:
            # Any failure validating the token here means "not authenticated" —
            # gotrue doesn't expose a stable exception type across versions.
            logger.info(f"[DELETE_ACCOUNT] Auth verification failed: {e}")
            return JSONResponse({"error": "Auth verification failed. Please sign in again."}, status_code=401)

        if not user_id:
            return JSONResponse({"error": "Could not identify user"}, status_code=401)

        # Use the Admin API (requires service_role key) to hard-delete the user
        service_key = os.getenv("SUPABASE_SERVICE_KEY", "")
        if not service_key:
            return JSONResponse(
                {"error": "Account deletion is not configured yet. Please contact support."},
                status_code=501
            )

        admin_resp = await _db(
            _http.delete,
            f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
            headers={
                "apikey":        service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type":  "application/json",
            },
            timeout=10,
        )

        if admin_resp.status_code in (200, 204):
            logger.info(f"✅ [DELETE_ACCOUNT] User {user_id} deleted successfully")
            return JSONResponse({"success": True, "message": "Account deleted successfully"})
        else:
            err = admin_resp.json().get("message", f"HTTP {admin_resp.status_code}")
            logger.error(f"❌ [DELETE_ACCOUNT] Failed for {user_id}: {err}")
            return JSONResponse({"error": err}, status_code=admin_resp.status_code)

    except Exception as e:
        _log_unexpected("DELETE_ACCOUNT", e)
        return JSONResponse({"error": _client_safe_error(e, "DELETE_ACCOUNT")}, status_code=500)


# ============================================================
# ✅ HELPER: Call OpenRouter with streaming
# ============================================================
# ── Handoff continuation instruction ──────────────────────────────────────────
# Used whenever a reply got cut off (hit max_tokens / stream broke) and we're
# handing off to another leg — same model or a different one in the pool.
# Without this, the next leg only sees a stray "assistant" message sitting in
# history and has no reason not to just start a brand-new answer from
# scratch — which is what produced the "restart/loop" behavior. This message
# is appended as the LAST turn (role=user) so it works reliably across every
# backend/model regardless of whether they special-case a trailing assistant
# turn.
HANDOFF_CONTINUE_PROMPT = (
    "Your previous response was cut off before it finished. "
    "Continue EXACTLY from the point it stopped — do not repeat any text you "
    "already wrote, do not restart the file/answer, do not add any preamble, "
    "explanation, apology, or markdown code fences at the resume point. "
    "Resume mid-content as if there had been no interruption at all, and "
    "keep going until the entire response (e.g. the full HTML file) is "
    "completely finished."
)


def call_openrouter_stream(model_id, messages, api_key, file_urls=None, vision_images=None):
    """
    Call OpenRouter streaming.
    - Text/code/PDF content is already injected into `messages` by the caller.
    - vision_images: list of {"mime":…, "b64":…} — appended as vision content blocks
      to the last user message for image-capable models.
    """
    try:
        if vision_images:
            # Find last user message and upgrade to multimodal content array
            last_user_idx = None
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    last_user_idx = i
                    break

            if last_user_idx is not None:
                original_content = messages[last_user_idx].get("content", "")
                content_blocks = []

                for img in vision_images:
                    content_blocks.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{img['mime']};base64,{img['b64']}"}
                    })

                text_str = original_content if isinstance(original_content, str) else ""
                if not text_str:
                    text_str = "Please analyse this image in detail."
                content_blocks.append({"type": "text", "text": text_str})
                messages[last_user_idx]["content"] = content_blocks

        resp = _http.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": APP_URL,
                "X-Title": "Catura AI",
            },
            json={
                "model": model_id,
                "messages": messages,
                "stream": True,
                "temperature": 1.0,
                "max_tokens": 16000,
                **({"provider": {"order": ["OpenAI"], "allow_fallbacks": True}} if "gpt-oss" in model_id else {}),
                # ✅ Cohere North Mini Code and Ling-3.0-flash are reasoning/thinking
                # models — OpenRouter only runs the thinking pass when a "reasoning"
                # object is explicitly sent. Ling-3.0-flash is a hybrid-reasoning model
                # (thinking + non-thinking modes); "enabled": True turns thinking on.
                # Scoped to these models only; no other model gets this field.
                **({"reasoning": {"enabled": True}} if ("north-mini-code" in model_id or "ling-3.0-flash" in model_id) else {}),
            },
            stream=True,
            timeout=(10, 90),
        )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            except (ValueError, KeyError, AttributeError):
                err_msg = f"HTTP {resp.status_code}"
            return None, err_msg
        return resp, None
    except requests.exceptions.Timeout:
        return None, "Request timed out"
    except requests.exceptions.RequestException as e:
        logger.warning(f"⚠️ [call_openrouter_stream] network error: {e}")
        return None, _client_safe_error(e, "call_openrouter_stream")
    except Exception as e:
        _log_unexpected("call_openrouter_stream", e)
        return None, _client_safe_error(e, "call_openrouter_stream")


# ============================================================
# ✅ HELPER: Call Google Gemini with streaming
# ============================================================
def call_gemini_stream(messages, system_prompt):
    """
    Calls Gemini 2.5 Flash via Google AI Studio REST API with streaming.
    Converts OpenAI-style messages to Gemini format.
    """
    if not GEMINI_API_KEY:
        return None, "GEMINI_API_KEY not set in environment variables"

    try:
        # Build Gemini contents from messages (skip system role)
        contents = []
        for msg in messages:
            role = msg["role"]
            if role == "system":
                continue  # handled via system_instruction
            gemini_role = "user" if role == "user" else "model"
            contents.append({
                "role": gemini_role,
                "parts": [{"text": msg["content"]}]
            })

        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 1.0,
                "maxOutputTokens": 16000,
            }
        }

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash:streamGenerateContent"
            f"?alt=sse&key={GEMINI_API_KEY}"
        )

        resp = _http.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            stream=True,
            timeout=(10, 120),
        )

        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            except (ValueError, KeyError, AttributeError):
                err_msg = f"HTTP {resp.status_code}"
            return None, err_msg

        return resp, None

    except requests.exceptions.Timeout:
        return None, "Request timed out"
    except requests.exceptions.RequestException as e:
        logger.warning(f"⚠️ [call_gemini_stream] network error: {e}")
        return None, _client_safe_error(e, "call_gemini_stream")
    except Exception as e:
        _log_unexpected("call_gemini_stream", e)
        return None, _client_safe_error(e, "call_gemini_stream")



# ============================================================
# ✅ HELPER: Call Google Gemma via Google AI Studio (same key as Gemini)
# ============================================================
def call_gemma_google_stream(messages, system_prompt, model_id):
    """
    Calls Gemma 4 models via Google AI Studio REST API —
    same GEMINI_API_KEY and endpoint pattern as Gemini 2.5 Flash.
    """
    if not GEMINI_API_KEY:
        return None, "GEMINI_API_KEY not set in environment variables"
    try:
        contents = []
        for msg in messages:
            role = msg["role"]
            if role == "system":
                continue
            gemini_role = "user" if role == "user" else "model"
            contents.append({
                "role": gemini_role,
                "parts": [{"text": msg["content"]}]
            })
        # NOTE: thinkingConfig is a Gemini-only field. Gemma 4 models
        # (gemma-4-31b-it / gemma-4-26b-a4b-it) control reasoning via a
        # <|think|> token baked into the prompt/model, not this API param.
        # Sending thinkingConfig to Gemma 4 is a known trigger for constant
        # "500 Internal error encountered" responses from Google's API
        # (see: discuss.ai.google.dev "Constant 500s with Gemma" / "Constant
        # 500s on Gemma 4") — so it is intentionally omitted here.
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 1.0,
                "maxOutputTokens": 16000,
            }
        }
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_id}:streamGenerateContent"
            f"?alt=sse&key={GEMINI_API_KEY}"
        )

        # Gemma 4 on Google AI Studio is known to intermittently return
        # 500 "Internal error encountered" even with a valid, well-formed
        # request and quota to spare. Retry a couple of times with a short
        # backoff before giving up, since these are transient server-side.
        last_err = "Internal error encountered."
        for attempt in range(3):
            try:
                resp = _http.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    stream=True,
                    timeout=(10, 120),
                )
            except requests.exceptions.Timeout:
                last_err = "Request timed out"
                continue
            except requests.exceptions.RequestException as e:
                logger.warning(f"⚠️ [call_gemma_google_stream] network error (attempt {attempt + 1}): {e}")
                last_err = _client_safe_error(e, "call_gemma_google_stream")
                continue
            except Exception as e:
                _log_unexpected(f"call_gemma_google_stream attempt {attempt + 1}", e)
                last_err = _client_safe_error(e, "call_gemma_google_stream")
                continue

            if resp.status_code == 200:
                return resp, None

            try:
                err_body = resp.json()
                last_err = err_body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            except (ValueError, KeyError, AttributeError):
                last_err = f"HTTP {resp.status_code}"

            # Only worth retrying on server-side errors (500/503), not on
            # 4xx (bad key, bad request, quota) which will just repeat.
            if resp.status_code not in (500, 503):
                return None, last_err

            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))

        return None, last_err
    except requests.exceptions.RequestException as e:
        logger.warning(f"⚠️ [call_gemma_google_stream] network error: {e}")
        return None, _client_safe_error(e, "call_gemma_google_stream")
    except Exception as e:
        _log_unexpected("call_gemma_google_stream", e)
        return None, _client_safe_error(e, "call_gemma_google_stream")

# ============================================================
# ✅ HELPER: Call Groq with streaming — OpenAI-compatible
# ============================================================
def call_groq_stream(messages, api_key):
    """
    Calls Groq API with streaming using llama-3.3-70b-versatile model.
    Uses GROQ_API_KEY set on Render. Completely isolated from all
    other models — does NOT touch OPENROUTER_API_KEY or GEMINI_API_KEY.
    Groq has a generous free tier with very fast inference.
    """
    if not api_key:
        return None, "GROQ_API_KEY not set in environment variables"
    try:
        resp = _http.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": messages,
                "stream": True,
                "temperature": 0.3,
                "max_tokens": 8000,
            },
            stream=True,
            timeout=(10, 120),
        )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            except (ValueError, KeyError, AttributeError):
                err_msg = f"HTTP {resp.status_code}"
            return None, err_msg
        return resp, None
    except requests.exceptions.Timeout:
        return None, "Request timed out"
    except requests.exceptions.RequestException as e:
        logger.warning(f"⚠️ [call_groq_stream] network error: {e}")
        return None, _client_safe_error(e, "call_groq_stream")
    except Exception as e:
        _log_unexpected("call_groq_stream", e)
        return None, _client_safe_error(e, "call_groq_stream")


# ============================================================
# ✅ HELPER: Call Poolside API for Laguna — uses POOLSIDE_API_KEY
# Laguna M.1 via Poolside's OpenAI-compatible endpoint
# ============================================================
def call_poolside_stream(messages, api_key):
    """
    Calls Poolside API with streaming using Laguna M.1 model.
    Uses POOLSIDE_API_KEY set on Render. Completely isolated from all
    other models — does NOT touch any other API key.

    Thinking mode: Poolside's hosted API enables `enable_thinking` by
    default, but we set it explicitly so behavior doesn't silently depend
    on server-side defaults changing later. Poolside's own recommended
    sampling for this model is temperature=1.0, top_k=20 (not 0.3) — a low
    temperature suppresses/degenerates the reasoning trace, which is a
    likely reason the model "worked without thinking" before.
    """
    if not api_key:
        return None, "POOLSIDE_API_KEY not set in environment variables"
    try:
        resp = _http.post(
            "https://inference.poolside.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "poolside/laguna-m.1",
                "messages": messages,
                "stream": True,
                "temperature": 1.0,
                "top_k": 20,
                "max_tokens": 16000,
                "chat_template_kwargs": {"enable_thinking": True},
            },
            stream=True,
            timeout=(15, None),  # no read timeout — let long thinking runs finish
        )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            except (ValueError, KeyError, AttributeError):
                err_msg = f"HTTP {resp.status_code}"
            return None, err_msg
        return resp, None
    except requests.exceptions.Timeout:
        return None, "Request timed out"
    except requests.exceptions.RequestException as e:
        logger.warning(f"⚠️ [call_poolside_stream] network error: {e}")
        return None, _client_safe_error(e, "call_poolside_stream")
    except Exception as e:
        _log_unexpected("call_poolside_stream", e)
        return None, _client_safe_error(e, "call_poolside_stream")


# ============================================================
# ✅ HELPER: Call Groq for Sambhav — completely independent of Nivo
# Uses llama-3.3-70b-versatile via Groq API (GROQ_API_KEY)
# ============================================================
def call_sambhav_groq_stream(messages, api_key):
    """
    Dedicated Groq streaming function for Sambhav.
    Completely separate from call_groq_stream — does NOT share state or signature.
    Uses llama-3.3-70b-versatile via Groq's OpenAI-compatible endpoint.
    """
    if not api_key:
        return None, "GROQ_API_KEY not set in environment variables"
    try:
        resp = _http.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": messages,
                "stream": True,
                "temperature": 0.4,
                "max_tokens": 8000,
            },
            stream=True,
            timeout=(10, 120),
        )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            except (ValueError, KeyError, AttributeError):
                err_msg = f"HTTP {resp.status_code}"
            return None, err_msg
        return resp, None
    except requests.exceptions.Timeout:
        return None, "Request timed out"
    except requests.exceptions.RequestException as e:
        logger.warning(f"⚠️ [call_sambhav_groq_stream] network error: {e}")
        return None, _client_safe_error(e, "call_sambhav_groq_stream")
    except Exception as e:
        _log_unexpected("call_sambhav_groq_stream", e)
        return None, _client_safe_error(e, "call_sambhav_groq_stream")


# ============================================================
# ✅ HELPER: Call Z.ai for GLM — glm-4.7-flash (free tier)
# Uses OpenAI-compatible endpoint at api.z.ai
# ============================================================
def call_zai_stream(messages, api_key):
    """
    Dedicated Z.ai streaming function for GLM model.
    Uses glm-4.7-flash via Z.ai's OpenAI-compatible endpoint (free tier).
    """
    if not api_key:
        return None, "ZAI_API_KEY not set in environment variables"
    try:
        resp = _http.post(
            "https://api.z.ai/api/paas/v4/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "glm-4.7-flash",
                "messages": messages,
                "stream": True,
                "temperature": 0.7,
                "max_tokens": 24000,
            },
            stream=True,
            timeout=(10, 120),
        )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            except (ValueError, KeyError, AttributeError):
                err_msg = f"HTTP {resp.status_code}"
            return None, err_msg
        return resp, None
    except requests.exceptions.Timeout:
        return None, "Request timed out"
    except requests.exceptions.RequestException as e:
        logger.warning(f"⚠️ [call_zai_stream] network error: {e}")
        return None, _client_safe_error(e, "call_zai_stream")
    except Exception as e:
        _log_unexpected("call_zai_stream", e)
        return None, _client_safe_error(e, "call_zai_stream")


# ============================================================
# ✅ HELPER: Call NVIDIA NIM API — z-ai/glm-5.2, minimaxai/minimax-m3
# OpenAI-compatible endpoint at integrate.api.nvidia.com (NVIDIA_API_KEY)
# ============================================================
def call_nvidia_stream(messages, api_key, model_id, temperature=1.0, template_kwargs=None, max_tokens=16000):
    """
    Dedicated NVIDIA NIM streaming function.
    Shared by all NVIDIA-hosted reasoning models (MiniMax M3, GLM-5.2,
    etc.) via model_id param.

    Thinking mode: NVIDIA NIM serves these as open-weight reasoning models on
    a vLLM/SGLang-style stack, and the exact chat_template_kwargs field that
    switches reasoning on is MODEL-SPECIFIC — it is NOT the same key for every
    model family, which is why a single hardcoded kwarg broke thinking for
    some models and silently ate the whole token budget on others:
      - MiniMax M3   -> {"thinking_mode": "enabled"}  (NOT "thinking": true —
        that key is not recognized by MiniMax's chat template, so thinking
        never actually turned on and the model answered directly.)
      - GLM-5.2      -> {"thinking": true} (GLM already reasons by default,
        but the real bug here was max_tokens=16000 being too small: a 744B
        reasoning model can easily spend the entire budget on the hidden
        <think> trace and hit the token cap before ever emitting the final
        'content' delta — which looks exactly like "takes minutes and never
        replies". Fixed by giving GLM-5.2 a much larger max_tokens budget.

    Callers pass their own template_kwargs and max_tokens so each model gets
    the settings it actually needs instead of one-size-fits-all.

    Reasoning is streamed back in the 'reasoning_content' delta field (same
    shape used elsewhere in this app), separate from the final answer's
    'content' field.
    """
    if not api_key:
        return None, "NVIDIA_API_KEY not set in environment variables"
    try:
        payload = {
            "model": model_id,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if template_kwargs:
            payload["chat_template_kwargs"] = template_kwargs
        resp = _http.post(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            stream=True,
            timeout=(15, None),  # no read timeout — let long thinking runs finish
        )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            except (ValueError, KeyError, AttributeError):
                err_msg = f"HTTP {resp.status_code}"
            return None, err_msg
        return resp, None
    except requests.exceptions.Timeout:
        return None, "Request timed out"
    except requests.exceptions.RequestException as e:
        logger.warning(f"⚠️ [call_nvidia_stream] network error: {e}")
        return None, _client_safe_error(e, "call_nvidia_stream")
    except Exception as e:
        _log_unexpected("call_nvidia_stream", e)
        return None, _client_safe_error(e, "call_nvidia_stream")


# ============================================================
# ✅ HELPER: Call Mistral API — mistral-large-latest / mistral-medium-latest
# OpenAI-compatible endpoint at api.mistral.ai (MISTRAL_API_KEY)
# ============================================================
def call_mistral_stream(messages, api_key, model_id="mistral-large-latest", reasoning_effort=None):
    """
    Dedicated Mistral streaming function.
    Shared by all Mistral-hosted models (Large, Medium, etc.) via model_id param.
    Uses Mistral's official OpenAI-compatible endpoint. Completely isolated from
    all other models — does NOT touch any other API key.

    NOTE on thinking: Mistral's official hosted API validates requests strictly
    against its own schema and rejects unrecognized fields with HTTP 422. Unlike
    Poolside/Morph (self-hosted-style vLLM/SGLang stacks), it does NOT accept
    chat_template_kwargs — sending it breaks every request. mistral-large-latest
    is NOT a reasoning model on Mistral's side, so no reasoning_effort is ever
    sent for it — the model still reasons internally before answering, it just
    doesn't stream that trace separately.

    mistral-medium-latest (resolves to mistral-medium-3-5) DOES support
    adjustable reasoning per Mistral's official docs
    (https://docs.mistral.ai/studio-api/conversations/reasoning) via the
    top-level `reasoning_effort` param ("high" | "none"). Unlike other
    providers in this app, Mistral does NOT stream thinking via a flat
    'reasoning_content' delta field — instead, when reasoning_effort="high",
    `delta.content` becomes a *list* of chunks: {"type": "thinking", "thinking":
    [{"type": "text", "text": ...}, ...]} while thinking, then {"type": "text",
    "text": ...} for the final answer. Callers that pass reasoning_effort must
    parse delta.content accordingly (see mistral_medium handlers below) instead
    of relying on the flat 'reasoning_content' field used elsewhere.
    """
    if not api_key:
        return None, "MISTRAL_API_KEY not set in environment variables"
    try:
        payload = {
            "model": model_id,
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
            "max_tokens": 8000,
        }
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        resp = _http.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            stream=True,
            timeout=(10, 120),
        )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            except (ValueError, KeyError, AttributeError):
                err_msg = f"HTTP {resp.status_code}"
            return None, err_msg
        return resp, None
    except requests.exceptions.Timeout:
        return None, "Request timed out"
    except requests.exceptions.RequestException as e:
        logger.warning(f"⚠️ [call_mistral_stream] network error: {e}")
        return None, _client_safe_error(e, "call_mistral_stream")
    except Exception as e:
        _log_unexpected("call_mistral_stream", e)
        return None, _client_safe_error(e, "call_mistral_stream")


# ============================================================
# ✅ HELPER: Call Inception Labs API — mercury-2
# OpenAI-compatible endpoint at api.inceptionlabs.ai (INCEPTION_API_KEY)
# ============================================================
def call_inception_stream(messages, api_key, model_id="mercury-2", reasoning_effort="high"):
    """
    Dedicated Inception Labs streaming function for Mercury 2 (diffusion LLM).
    Uses Inception's official OpenAI-compatible endpoint. Completely isolated
    from all other models — does NOT touch any other API key.

    Thinking mode: unlike Poolside/Morph (which toggle reasoning via
    chat_template_kwargs.enable_thinking), Inception's Mercury 2 exposes a
    top-level `reasoning_effort` field ("instant" | "low" | "medium" | "high").
    Passing "high" turns on full extended thinking; the model streams its
    reasoning back in the `reasoning_content` delta field, same shape as
    Mistral/Morph, so the existing thinking-token parsing just works.
    """
    if not api_key:
        return None, "INCEPTION_API_KEY not set in environment variables"
    try:
        resp = _http.post(
            "https://api.inceptionlabs.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_id,
                "messages": messages,
                "stream": True,
                "temperature": 0.7,
                "max_tokens": 8000,
                "reasoning_effort": reasoning_effort,
            },
            stream=True,
            timeout=(10, 120),
        )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            except (ValueError, KeyError, AttributeError):
                err_msg = f"HTTP {resp.status_code}"
            return None, err_msg
        return resp, None
    except requests.exceptions.Timeout:
        return None, "Request timed out"
    except requests.exceptions.RequestException as e:
        logger.warning(f"⚠️ [call_inception_stream] network error: {e}")
        return None, _client_safe_error(e, "call_inception_stream")
    except Exception as e:
        _log_unexpected("call_inception_stream", e)
        return None, _client_safe_error(e, "call_inception_stream")


# ============================================================
# 🏷️ TITLE GENERATION ENDPOINT
# Generates a short, descriptive chat title from the first message
# ============================================================
@app.post("/generate-title")
@limiter.limit("30/minute")
async def generate_title(request: Request):
    try:
        body    = await request.json()
        message = body.get("message", "").strip()
        if not message:
            return JSONResponse({"title": "New Chat"})

        system_prompt = (
            "You are a chat title generator. "
            "Given the user's first message, produce a SHORT (2–5 words) descriptive title "
            "that captures the TOPIC — like a Google search query or a chapter heading. "
            "Examples: 'Browser Caching Issue', 'What is DNS', 'Python List Sorting', "
            "'Resume Writing Tips', 'Photosynthesis Explained', 'Fix FastAPI CORS Error', "
            "'How Black Holes Form', 'Best Laptops Under 50000'. "
            "Rules: No quotes, no punctuation at the end, Title Case, no filler words like "
            "'Question about' or 'Help with'. Return ONLY the title, nothing else. "
            "Never return the user's message verbatim — always summarise into a topic label."
        )

        groq_key = os.getenv("GROQ_API_KEY", "")
        if not groq_key:
            # Fallback: smart truncation if no API key
            words = message.split()
            return JSONResponse({"title": " ".join(words[:5]) if words else "New Chat"})

        import httpx as _title_httpx
        async with _title_httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": message}
                    ],
                    "max_tokens": 30,
                    "temperature": 0.4,
                    "stream": False,
                },
            )

        if resp.status_code == 200:
            data  = resp.json()
            title = (
                data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                    .strip('"\'')
            )
            # Strip any accidental markdown bold/italic
            title = title.replace("**", "").replace("*", "").strip()
            if title:
                logger.info(f"✅ [Title] Generated: '{title}' for: '{message[:50]}'")
                return JSONResponse({"title": title[:60]})

        logger.warning(f"⚠️ [Title] Groq returned {resp.status_code} — using fallback")
        words = message.split()
        return JSONResponse({"title": " ".join(words[:5]) if words else "New Chat"})

    except requests.exceptions.RequestException as e:
        logger.warning(f"⚠️ [Title] network error: {e}")
        return JSONResponse({"title": "New Chat"})
    except Exception as e:
        _log_unexpected("Title generation", e)
        return JSONResponse({"title": "New Chat"})


# ============================================================
# ✅ MAIN CHAT ENDPOINT (POST)
# Full pipeline: intent → tool → context → AI → stream
# ============================================================
@app.post("/chat")
async def chat_post(request: Request, auth: dict = Depends(require_auth)):
    try:
        body = await request.json()
        prompt     = body.get("prompt", "")
        model      = body.get("model", "dagr")
        file_urls  = body.get("file_urls", [])
        web_search_forced = body.get("web_search_enabled", False)
        ghost_mode    = body.get("ghost_mode", False)
        ghost_history = body.get("ghost_history", [])   # list of {role, content}
        user_memories_list = body.get("user_memories", [])   # 🧠 memory injection
        # Legacy web_results from frontend removed — backend handles all search internally
        web_results = []

        # ── FAIR-USE QUOTA: 15 messages per model per rolling 20-minute window ──
        # Checked before any upstream AI call so a blocked request never burns
        # OpenRouter/Groq/Poolside/Gemini/Z.ai credits.
        _rl_allowed, _rl_remaining, _rl_reset_epoch, _rl_retry_after = _chat_quota_check(
            auth["user_id"], model
        )
        if not _rl_allowed:
            _minutes = max(1, round(_rl_retry_after / 60))
            return JSONResponse(
                {
                    "error": "rate_limited",
                    "model": model,
                    "message": (
                        f"You've reached the message limit for this model. "
                        f"Try again in {_minutes} minute{'s' if _minutes != 1 else ''}."
                    ),
                    "retry_after_seconds": _rl_retry_after,
                },
                status_code=429,
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(_rl_reset_epoch)),
                    "Retry-After": str(_rl_retry_after),
                },
            )

        # Small closure: every successful /chat response (there are many return
        # points below, one per streaming branch) stamps the same quota headers
        # so the frontend can show "X/15 messages left" without waiting for a 429.
        def _rl(headers_dict: dict) -> dict:
            headers_dict["X-RateLimit-Remaining"] = str(_rl_remaining)
            headers_dict["X-RateLimit-Reset"] = str(int(_rl_reset_epoch))
            return headers_dict

        session_id = request.cookies.get("session_id")
        if not session_id:
            session_id = str(uuid.uuid4())

        prompt_lower = prompt.lower()

        # ── Identity overrides ─────────────────────────────────────────────
        if any(q in prompt_lower for q in [
            "who created you", "who is your developer",
            "who made you", "who built you",
            "your creator", "your developer"
        ]):
            def quick():
                yield f"data: {json.dumps({'token': 'I was created by Anirban.'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(quick(), media_type="text/event-stream",
                headers=_rl({"Set-Cookie": build_session_cookie(session_id)}))

        if session_id not in user_memory:
            user_memory[session_id] = []

        # ── GHOST MODE: use ephemeral history from frontend; never persist ─────
        # In ghost mode the frontend owns the sliding window (max 12 exchanges).
        # We rebuild a temporary list just for this request and never write it back.
        if ghost_mode:
            active_memory = list(ghost_history)   # previous turns sent by frontend
        else:
            active_memory = user_memory[session_id]

        # ── FILE CONTENT INJECTION ─────────────────────────────────────────
        # Download and inject file content BEFORE appending to memory so ALL
        # models (Dagr, Apep, Sambhav, Gemma) actually see file contents —
        # not just a URL. This enables deep analysis like Claude / ChatGPT do.
        file_text_context = ""
        vision_images_for_prompt = []
        if file_urls:
            # Downloads + parses each attached file with blocking `requests`/Pillow/PDF
            # calls — keep it off the event loop so a slow attachment download
            # doesn't stall every other concurrent chat request.
            file_text_context, vision_images_for_prompt = await _db(build_file_context_for_prompt, file_urls)

        # Compose the full user message that goes into memory + is sent to AI
        user_message_content = prompt
        if file_text_context:
            if user_message_content:
                user_message_content = user_message_content + "\n\n" + file_text_context
            else:
                user_message_content = (
                    "Please analyse the attached file(s) in depth. "
                    "Explain what they contain, what they do, identify any issues or "
                    "interesting aspects, and answer any implicit questions the user may have.\n\n"
                    + file_text_context
                )

        # Add current user turn to the working memory list
        # (ghost: this mutates our local copy only; normal: mutates the server store)
        active_memory.append({"role": "user", "content": user_message_content})
        if not ghost_mode:
            # Keep server-side memory in sync (it IS active_memory, same reference)
            pass  # already appended above via reference

        # ── MODEL POOLS ────────────────────────────────────────────────────
        # Gemma models → Google AI Studio (GEMINI_API_KEY), NOT OpenRouter
        GEMMA_GOOGLE_MODELS = {
            "gemma":    "gemma-4-26b-a4b-it",
            "gemma4":   "gemma-4-31b-it",
        }
        model_pools = {
            "dagr":    ["openai/gpt-oss-20b:free", "openai/gpt-oss-120b:free"],
            "apep":    ["openai/gpt-oss-120b:free", "openai/gpt-oss-20b:free"],
            "sambhav": [],  # Routed via Groq API (llama-3.3-70b-versatile) — see call_sambhav_groq_stream()
            "nivo":    [],  # Routed via Groq API (GROQ_API_KEY) — see generate_nivo()
            "glm":     [],  # Routed via Z.ai API (ZAI_API_KEY) — glm-4.7-flash (free)
            "minimax_m3": [],  # Routed via NVIDIA NIM API (NVIDIA_API_KEY) — minimaxai/minimax-m3
            "glm52":      [],  # Routed via NVIDIA NIM API (NVIDIA_API_KEY) — z-ai/glm-5.2
            "mistral_large":  [],  # Routed via Mistral API (MISTRAL_API_KEY) — mistral-large-latest
            "mistral_medium": [],  # Routed via Mistral API (MISTRAL_API_KEY) — mistral-medium-latest
            "mistral_small":  [],  # Routed via Mistral API (MISTRAL_API_KEY) — mistral-small-latest
            "mercury2": [],  # Routed via Inception Labs API (INCEPTION_API_KEY) — mercury-2
            "laguna":      [],  # Routed via Poolside API (POOLSIDE_API_KEY) — Laguna M.1
            "laguna_core": [],  # Routed via Poolside API (POOLSIDE_API_KEY) — Laguna XS.2.1
            "laguna_s":    [],  # Routed via Poolside API (POOLSIDE_API_KEY) — Laguna S.2.1
            "cohere":       ["cohere/north-mini-code:free"],
            "n_nano":  ["nvidia/nemotron-nano-12b-v2-vl:free"],
            "omni":    ["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"],
            "ling":    ["inclusionai/ling-3.0-flash:free"],  # OpenRouter — hybrid reasoning/thinking model
        }
        model_key  = model.strip()
        model_pool = model_pools.get(model_key, model_pools["dagr"])

        # ── BASE SYSTEM PROMPTS ────────────────────────────────────────────
        # CRITICAL: forbid the model from emitting function-call JSON.
        # These models are fine-tuned to output tool-call JSON when they
        # think they need external data, but Catura fetches data BEFORE
        # calling the AI — the data is already in the system prompt.
        NO_TOOL_CALL_RULE = (
            "\n\nCRITICAL RULES — FOLLOW THESE WITHOUT EXCEPTION:\n"
            "1. You do NOT have any tools, functions, or APIs to call.\n"
            "2. NEVER output function calls, tool calls, or JSON like "
            "{\"query\": ...} or Search web.{...} or any similar syntax.\n"
            "3. If live data (weather, finance, news, sports, search results) "
            "is provided above in your system context, use it directly to write "
            "your answer in natural language. Do NOT say you are 'using a tool'.\n"
            "4. If no live data is provided, answer from your knowledge but note "
            "it may not reflect today's current values.\n"
            "5. Always respond in clean, readable prose or markdown. Never output raw JSON.\n"
            "6. \U0001f6a8 NEVER INVENT NUMBERS: Do NOT fabricate or guess stock prices, share prices, "
            "temperatures, scores, or any live numerical data from your training memory. "
            "Your training data is months old — prices in it are WRONG. "
            "If live data is missing a number, say: 'I could not fetch the live price right now "
            "— please check NSE (nseindia.com) or BSE (bseindia.com) or your broker app.' "
            "Inventing a price (e.g. saying 1233 for a stock that trades at 28) is a critical error.\n"
            "7. \U0001f6a8 NEVER CITE SOURCES INLINE: Do NOT write 'Source:', 'according to [website/outlet]', "
            "'as reported by', 'e.g., WIONews', 'e.g., The Star', or any similar attribution anywhere "
            "in your answer. Do NOT add a 'Sources:' section at the end of your reply. "
            "Sources are already shown to the user in a separate UI element — mentioning them in text "
            "is redundant and clutters the response. Just give the answer cleanly."
        )

        # ── Formatting rules injected into every model ─────────────────────────
        FORMATTING_RULES = (
            "\n\n## RESPONSE FORMATTING — ALWAYS FOLLOW:\n"
            "Structure every response so it is clean, scannable, and professional — "
            "like a well-written answer from Claude or ChatGPT.\n\n"
            "WHEN TO USE STRUCTURE (use it whenever it helps clarity):\n"
            "- Use ## or ### headings to divide a multi-part answer into named sections.\n"
            "- Use **bold** for key terms, important names, and critical concepts.\n"
            "- Use bullet lists (- item) for unordered facts, features, or options.\n"
            "- Use numbered lists (1. 2. 3.) for steps, sequences, rankings, or priorities.\n"
            "- Use nested lists (indent 2 spaces) for sub-points under a parent item.\n"
            "- Use `inline code` for commands, file names, function names, and technical terms.\n"
            "- Use ```language\\ncode\\n``` fenced blocks for ALL multi-line code.\n"
            "- Use > blockquote for quotes, definitions, or highlighted notes.\n"
            "- Use **bold** + colon pattern for definition-style lists: **Term**: explanation.\n\n"
            "PARAGRAPH RULES:\n"
            "- Always put a blank line between paragraphs.\n"
            "- Never write a wall of text — break long explanations into paragraphs of 2-4 sentences.\n"
            "- When listing 3 or more things, always use a bullet or numbered list, not a run-on sentence.\n\n"
            "WHEN NOT TO OVER-FORMAT:\n"
            "- For simple one-line answers (e.g. 'What is 2+2?'), reply in plain prose — no headers needed.\n"
            "- For casual chitchat or greetings, reply naturally without lists or headers.\n\n"
            "The goal: every structured answer should look polished and professional, "
            "and every simple answer should be clean and direct."
        )

        # ── UI/UX code-generation rules — injected into EVERY model's final prompt ──
        # Fixes the "every model builds the same beige-card/navy-gradient thing" problem
        # by forcing an explicit design-plan step before code, whenever the user asks
        # for a website, app, component, page, or any visual UI, without pinning down
        # a look themselves.
        UI_DESIGN_RULES = (
            "\n\n## UI/UX & FRONTEND CODE GENERATION — ALWAYS FOLLOW:\n"
            "This applies any time you are asked to build, create, or code a website, "
            "webpage, app, component, dashboard, landing page, game, tool, or any other "
            "visual UI (HTML/CSS/JS, React, etc.).\n\n"

            "### If the user's request does NOT specify a visual style:\n"
            "Do not jump straight to code. Before writing any code, silently work out "
            "(in your head, not as a visible step unless the user wants to see it) a short "
            "design plan:\n"
            "1. **Concept** — choose ONE interpretation mode for the subject, and vary this "
            "choice across different requests instead of always picking the same one:\n"
            "   - Literal/skeuomorphic (looks like the real object)\n"
            "   - Abstract/data-driven (rendered as a dashboard, gauge, or diagram)\n"
            "   - Illustrative/flat (bold flat-design, not photorealistic)\n"
            "   - Retro/hardware (physical control panel, LCD, old-device look)\n"
            "   - Playful/toy-like (rounded, saturated, cartoonish)\n"
            "   - Minimal/brutalist (raw, high-contrast, sharp edges, few colors)\n"
            "   - Neumorphic/soft-UI\n"
            "   - Terminal/monospace/hacker aesthetic\n"
            "2. **Palette** — pick 4-6 named hex colors that fit the concept and subject, "
            "not a reused default.\n"
            "3. **Typography** — pick 2 real font families (import from Google Fonts) suited "
            "to the concept. Do not default to Segoe UI, Tahoma, Arial, or system-ui unless "
            "the brief calls for a plain/native look.\n"
            "4. **Signature detail** — one distinctive visual or interactive touch that makes "
            "this build memorable and specific to the subject, not generic.\n\n"

            "### Banned defaults (do NOT use unless the user explicitly asks for them):\n"
            "- Dark navy-to-black diagonal gradient background as the default canvas\n"
            "- A beige/tan 'room' or 'panel' card as the default container\n"
            "- Segoe UI / Tahoma / Arial / system-ui as the primary font\n"
            "- Green (#4ecca3-ish) as the default 'on/active/success' accent\n"
            "- Blue-to-purple gradient hero sections and generic 3-card feature grids\n"
            "- The exact same layout/skin for every request in a topic — e.g. do not render "
            "every 'fan', 'switch', or 'gauge' request as a photorealistic dark-room scene; "
            "actively rotate between the concept modes above.\n\n"

            "### Quality bar:\n"
            "- Make deliberate, specific choices tied to THIS subject and request, not a "
            "one-size-fits-all template.\n"
            "- Keep interactions and animations purposeful — one well-executed signature "
            "moment beats scattered effects everywhere.\n"
            "- Ensure it's responsive, readable, and functional, not just decorative.\n"
            "- Never mention this planning process to the user — just deliver the finished, "
            "well-designed result.\n"
        )

        system_prompts = {
            "sambhav": (
                # ── Identity ──
                "Your name is Catura, (pronounced kuh-CHUR-uh) Sambhav Model. You are a creative, thoughtful, and "
                "highly capable AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Sambhav, powered by advanced multimodal intelligence. "

                # ── Personality & tone ──
                "You are articulate, insightful, and adaptable. You speak clearly and helpfully. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "

                # ── Language behaviour ──
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "

                # ── Response style ──
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "For simple questions, give simple answers. Don't pad responses. "

                # ── Expertise ──
                "You are knowledgeable about technology, science, reasoning, analysis, and creative tasks. "
                "You excel at nuanced understanding and multi-step reasoning. "

                # ── Identity rules ──
                "If asked what model or AI you are, say you are Catura AI Sambhav and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "

                # ── Hard rules ──
                "Never make up facts. If you don't know something, say so honestly. "
                "Never say 'I don't have real-time data' — if live data is provided in context, use it."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
            "dagr": (
                # ── Identity ──
                "Your name is Catura (pronounced kuh-CHUR-uh) Degr Model. You are a smart, warm, and witty "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Dagr, the general-purpose model. "

                # ── Personality & tone ──
                "You speak like a knowledgeable friend — helpful, concise, and occasionally funny. "
                "You are never robotic, never overly formal, and never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "

                # ── Language behaviour ──
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "

                # ── Response style ──
                "Keep answers concise unless the user explicitly asks for detail or a long explanation. "
                "Use bullet points, numbered lists, or headers only when they genuinely improve clarity — "
                "not for every single response. "
                "For simple questions, give simple answers. Don't pad responses. "

                # ── Expertise ──
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "When analysing images or files, describe what you see in useful detail. "

                # ── Identity rules ──
                "If asked what model or AI you are, say you are Catura AI and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "

                # ── Hard rules ──
                "Never make up facts. If you don't know something, say so honestly. "
                "Never say 'I don't have real-time data' — if live data is provided in context, use it; "
                "otherwise give your best knowledge-based answer."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
            "apep": (
                # ── Identity ──
                "Your name is Catura (pronounced kuh-CHUR-uh) Apep Model. You are an expert coding and "
                "technical AI specialist created by Anirban — an independent developer based in India. "
                "You are Catura AI Apep, the developer-focused model. "

                # ── Personality ──
                "You are precise, confident, and direct. You speak like a senior engineer: "
                "no fluff, no filler, just clean technical insight. "
                "Never start with 'Certainly!', 'Great question!', or similar openers. Just answer. "

                # ── Code style rules ──
                "When writing code: ALWAYS use proper indentation (4 spaces per level). "
                "Put each statement on its own line. "
                "Wrap ALL code in fenced markdown code blocks with the language name at the top. "
                "Example: ```python\\n# your code here\\n``` "
                "Add brief inline comments for non-obvious logic. "
                "Prefer readability over cleverness unless performance is explicitly required. "

                # ── Technical expertise ──
                "You specialise in: Python, JavaScript, TypeScript, FastAPI, React, SQL, "
                "system design, debugging, algorithms, and DevOps. "
                "When debugging, always explain WHY something is wrong, not just what to change. "
                "When reviewing code, point out both bugs and improvement opportunities. "

                # ── Language behaviour ──
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond in that same language but keep code and technical terms in English. "

                # ── Hard rules ──
                "Never make up APIs, function signatures, or library features that don't exist. "
                "If unsure about a specific library version, say so and provide the general approach. "
                "If asked who made you, say 'I was created by Anirban.' "
                "If asked what model you are, say you are Catura AI Apep and cannot share "
                "details about the underlying technology."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
            "gemma": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Gemma Core Model. You are a powerful and efficient "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Gemma, built for fast and capable everyday tasks. "
                "Speak clearly and helpfully. Never start with 'Certainly!', 'Great question!', or similar openers. "
                "Match the user's language automatically. "
                "Never make up facts. If asked who made you, say 'I was created by Anirban.' "
                "If asked which model you are, what AI you are, or which version is running, "
                "always say: 'I am Catura AI Gemma.' Never mention Dagr, Apep, Sambhav, or Gemma4."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
            "gemma4": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Gemma Max Model. You are a powerful and efficient "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Gemma4, built for fast and capable everyday tasks. "
                "Speak clearly and helpfully. Never start with 'Certainly!', 'Great question!', or similar openers. "
                "Match the user's language automatically. "
                "Never make up facts. If asked who made you, say 'I was created by Anirban.' "
                "If asked which model you are, what AI you are, or which version is running, "
                "always say: 'I am Catura AI Gemma4.' Never mention Dagr, Apep, Sambhav, or Gemma."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
            "nivo": (
                # ── Identity ──
                "Your name is Catura (pronounced kuh-CHUR-uh) Nivo Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Nivo, designed for versatile and high-quality responses. "

                # ── Personality & tone ──
                "You are thoughtful, clear, and direct. You speak like a knowledgeable friend — "
                "helpful, intelligent, and never robotic or sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "

                # ── Language behaviour ──
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "

                # ── Response style ──
                "Keep answers concise unless the user explicitly asks for detail or a long explanation. "
                "Use bullet points, numbered lists, or headers only when they genuinely improve clarity. "
                "For simple questions, give simple answers. Don't pad responses. "

                # ── Expertise ──
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "

                # ── Identity rules ──
                "If asked what model or AI you are, say you are Catura AI Nivo and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "

                # ── Hard rules ──
                "Never make up facts. If you don't know something, say so honestly. "
                "Never say 'I don't have real-time data' — if live data is provided in context, use it; "
                "otherwise give your best knowledge-based answer."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
            "glm": (
                # ── Identity ──
                "Your name is Catura (pronounced kuh-CHUR-uh) GLM Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI GLM, built for fast, efficient, and high-quality responses. "

                # ── Personality & tone ──
                "You are clear, direct, and helpful. You speak like a knowledgeable friend — "
                "never robotic, never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "

                # ── Language behaviour ──
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "

                # ── Response style ──
                "Keep answers concise unless the user explicitly asks for detail or a long explanation. "
                "Use bullet points, numbered lists, or headers only when they genuinely improve clarity. "
                "For simple questions, give simple answers. Don't pad responses. "

                # ── Expertise ──
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "

                # ── Identity rules ──
                "If asked what model or AI you are, say you are Catura AI GLM and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "

                # ── Hard rules ──
                "Never make up facts. If you don't know something, say so honestly. "
                "Never say 'I don't have real-time data' — if live data is provided in context, use it; "
                "otherwise give your best knowledge-based answer."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
            "minimax_m3": (
                "Your name is Catura (pronounced kuh-CHUR-uh) MiniMax Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI MiniMax, built for fast, efficient, and high-quality responses. "
                "You are clear, direct, and helpful. You speak like a knowledgeable friend — "
                "never robotic, never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "If asked what model or AI you are, say you are Catura AI MiniMax and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "glm52": (
                "Your name is Catura (pronounced kuh-CHUR-uh) GLM-5.2 Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI GLM-5.2, built for fast, efficient, and high-quality responses. "
                "You are clear, direct, and helpful. You speak like a knowledgeable friend — "
                "never robotic, never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "If asked what model or AI you are, say you are Catura AI GLM-5.2 and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "mistral_large": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Mistral Large Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Mistral Large, built for precise, high-quality reasoning and responses. "
                "You are clear, direct, and helpful. You speak like a knowledgeable friend — "
                "never robotic, never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "If asked what model or AI you are, say you are Catura AI Mistral Large and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "mistral_medium": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Mistral Medium Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Mistral Medium, built for fast, efficient, and high-quality responses. "
                "You are clear, direct, and helpful. You speak like a knowledgeable friend — "
                "never robotic, never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "If asked what model or AI you are, say you are Catura AI Mistral Medium and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "mistral_small": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Mistral Small Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Mistral Small, built for fast, lightweight, and high-quality responses. "
                "You are clear, direct, and helpful. You speak like a knowledgeable friend — "
                "never robotic, never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "If asked what model or AI you are, say you are Catura AI Mistral Small and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "mercury2": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Mercury 2 Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Mercury 2, built for extremely fast, high-quality responses. "
                "You are clear, direct, and helpful. You speak like a knowledgeable friend — "
                "never robotic, never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "If asked what model or AI you are, say you are Catura AI Mercury 2 and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "laguna": (
                # ── Identity ──
                "Your name is Catura (pronounced kuh-CHUR-uh) Laguna Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Laguna, designed for precise, high-quality responses. "

                # ── Personality & tone ──
                "You are thoughtful, clear, and direct. You speak like a knowledgeable friend — "
                "helpful, intelligent, and never robotic or sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "

                # ── Language behaviour ──
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "

                # ── Response style ──
                "Keep answers concise unless the user explicitly asks for detail or a long explanation. "
                "Use bullet points, numbered lists, or headers only when they genuinely improve clarity. "
                "For simple questions, give simple answers. Don't pad responses. "

                # ── Identity rules ──
                "If asked what model or AI you are, say you are Catura AI Laguna and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "

                # ── Hard rules ──
                "Never make up facts. If you don't know something, say so honestly. "
                "Never say 'I don't have real-time data' — if live data is provided in context, use it; "
                "otherwise give your best knowledge-based answer."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
            # ── LAGUNA CORE — Laguna XS.2.1 via Poolside (POOLSIDE_API_KEY) ──
            "laguna_core": (
                # ── Identity ──
                "Your name is Catura (pronounced kuh-CHUR-uh) Laguna Core Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Laguna Core, designed for fast, precise, and high-quality responses. "

                # ── Personality & tone ──
                "You are thoughtful, clear, and direct. You speak like a knowledgeable friend — "
                "helpful, intelligent, and never robotic or sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "

                # ── Language behaviour ──
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "

                # ── Response style ──
                "Keep answers concise unless the user explicitly asks for detail or a long explanation. "
                "Use bullet points, numbered lists, or headers only when they genuinely improve clarity. "
                "For simple questions, give simple answers. Don't pad responses. "

                # ── Identity rules ──
                "If asked what model or AI you are, say you are Catura AI Laguna Core and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "

                # ── Hard rules ──
                "Never make up facts. If you don't know something, say so honestly. "
                "Never say 'I don't have real-time data' — if live data is provided in context, use it; "
                "otherwise give your best knowledge-based answer."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
            # ── LAGUNA S — Laguna S.2.1 via Poolside (POOLSIDE_API_KEY) ──
            "laguna_s": (
                # ── Identity ──
                "Your name is Catura (pronounced kuh-CHUR-uh) Laguna S Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Laguna S, designed for open-weight agentic coding and long-horizon "
                "reasoning tasks. "

                # ── Personality & tone ──
                "You are thoughtful, clear, and direct. You speak like a knowledgeable friend — "
                "helpful, intelligent, and never robotic or sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "

                # ── Language behaviour ──
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "

                # ── Response style ──
                "Keep answers concise unless the user explicitly asks for detail or a long explanation. "
                "Use bullet points, numbered lists, or headers only when they genuinely improve clarity. "
                "For simple questions, give simple answers. Don't pad responses. "

                # ── Identity rules ──
                "If asked what model or AI you are, say you are Catura AI Laguna S and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "

                # ── Hard rules ──
                "Never make up facts. If you don't know something, say so honestly. "
                "Never say 'I don't have real-time data' — if live data is provided in context, use it; "
                "otherwise give your best knowledge-based answer."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
            "cohere":(
                "Your name is Catura (pronounced kuh-CHUR-uh) Cohere Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Cohere, designed for fast and efficient responses. "
                "Speak clearly and helpfully. Never start with 'Certainly!', 'Great question!', or similar openers. "
                "Match the user's language automatically. "
                "Never make up facts. If asked who made you, say 'I was created by Anirban.' "
                "If asked which model you are, what AI you are, or which version is running, "
                "always say: 'I am Catura AI Cohere.' Never mention Dagr, Apep, Sambhav, Gemma, or Gemma4."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
            "nemotron":(
                "Your name is Catura (pronounced kuh-CHUR-uh) Nemotron Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Nemotron, designed for fast and efficient responses. "
                "Speak clearly and helpfully. Never start with 'Certainly!', 'Great question!', or similar openers. "
                "Match the user's language automatically. "
                "Never make up facts. If asked who made you, say 'I was created by Anirban.' "
                "If asked which model you are, what AI you are, or which version is running, always say: 'I am Catura AI Nemotron.' Never mention Dagr, Apep, Sambhav, Gemma, Gemma4, or cohere."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
            "n_nano":(
                "Your name is Catura (pronounced kuh-CHUR-uh) Nemotron Nano Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Nemotron Nano, designed for fast and efficient responses. "
                "Speak clearly and helpfully. Never start with 'Certainly!', 'Great question!', or similar openers. "
                "Match the user's language automatically. "
                "Never make up facts. If asked who made you, say 'I was created by Anirban.' "
                "If asked which model you are, what AI you are, or which version is running, always say: 'I am Catura AI Nemotron Nano.' Never mention Dagr, Apep, Sambhav, Gemma, Gemma4, Cohere, or Nemotron."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
            "omni":(
                "Your name is Catura (pronounced kuh-CHUR-uh) Nemotron Omni Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Nemotron Nano, designed for fast and efficient responses. "
                "Speak clearly and helpfully. Never start with 'Certainly!', 'Great question!', or similar openers. "
                "Match the user's language automatically. "
                "Never make up facts. If asked who made you, say 'I was created by Anirban.' "
                "If asked which model you are, what AI you are, or which version is running, always say: 'I am Catura AI Nemotron Nano.' Never mention Dagr, Apep, Sambhav, Gemma, Gemma4, Cohere, or Nemotron."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
            "ling":(
                "Your name is Catura (pronounced kuh-CHUR-uh) Ling Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Ling, built for hybrid reasoning and production-scale agentic tasks — "
                "you switch into a deeper thinking mode for hard problems and answer instantly for simple ones. "
                "Speak clearly and helpfully. Never start with 'Certainly!', 'Great question!', or similar openers. "
                "Match the user's language automatically. "
                "Never make up facts. If asked who made you, say 'I was created by Anirban.' "
                "If asked which model you are, what AI you are, or which version is running, always say: 'I am Catura AI Ling.' Never mention Dagr, Apep, Sambhav, Gemma, Gemma4, Cohere, Nemotron, Nemotron Nano, or Omni."
                + FORMATTING_RULES
                + NO_TOOL_CALL_RULE
            ),
        }
        # Apply UI/UX design rules to every model variant at once, so it's present
        # no matter which dict key a given code path looks up (main, laguna, glm,
        # nivo, etc. all pull from this same dict).
        system_prompts = {k: v + UI_DESIGN_RULES for k, v in system_prompts.items()}
        system_prompt = system_prompts.get(model_key, system_prompts["dagr"])

        # ── 🧠 MEMORY INJECTION ───────────────────────────────────────────────
        if user_memories_list and not ghost_mode:
            memory_block = (
                "\n\n"
                "## 🧠 PERSONAL MEMORY — CRITICAL OVERRIDE\n"
                "You DO have personal memory for this user. This user has enabled memory.\n"
                "NEVER say 'I don't have personal memories' or 'I can't remember previous conversations'.\n"
                "NEVER say 'I can only recall what's shared in this session'.\n"
                "You genuinely know the following facts about this user — treat them as things you remember:\n\n"
            )
            for mem in user_memories_list[:50]:
                memory_block += f"- {str(mem)[:300]}\n"
            memory_block += (
                "\n"
                "Use this information naturally and proactively when relevant.\n"
                "When the user introduces themselves or mentions something you already know, "
                "acknowledge it warmly (e.g. 'I remember you mentioned that!').\n"
                "Do NOT say 'Based on my memory...' — just use it like a friend who remembers.\n"
                "If asked 'do you remember me?' or 'do you know me?' — YES, you do. Reference what you know.\n"
            )
            system_prompt = system_prompt + memory_block
        elif not ghost_mode:
            system_prompt = system_prompt + (
                "\n\nNote: This user has not saved any personal memories yet. "
                "If they share personal information, acknowledge it warmly within this conversation. "
                "Do NOT proactively say you have no memory unless directly asked.\n"
            )
        # ─────────────────────────────────────────────────────────────────────

        # ── TOOL ROUTING PIPELINE ──────────────────────────────────────────
        # Step 1: detect intent ONLY — tool execution moved INSIDE generators
        # so the StreamingResponse starts immediately without blocking.
        intent = detect_intent(prompt)
        # If user explicitly enabled web search from the UI, force it
        if web_search_forced and not file_urls:
            intent = "web_search"
        logger.info(f"🎯 [PIPELINE] intent={intent} | model={model_key} | prompt={prompt[:60]}")

        # Step 2: build final messages list (tool context added inside generator)
        # ── 🛠️ SKILLS INJECTION ─────────────────────────────────────────────
        matched_skills = await _db(get_matching_skills, auth["user_id"], prompt)
        if matched_skills:
            skill_names = ", ".join(s.get("name", "?") for s in matched_skills)
            logger.info(f"🛠️ [Skills] matched: {skill_names}")
            system_prompt = system_prompt + build_skill_context(matched_skills)
        # ─────────────────────────────────────────────────────────────────────
        base_system_prompt = system_prompt  # save before tool injection
        messages_base = [{"role": "system", "content": system_prompt}] + active_memory[-20:]
        api_key  = os.getenv("OPENROUTER_API_KEY")

        # ── SAMBHAV: llama-3.3-70b-versatile via Groq API ──
        if model_key == "sambhav":
            sambhav_groq_key = os.getenv("GROQ_API_KEY", "")

            def generate_sambhav():
                full_reply = ""

                # ── Run tool INSIDE generator (non-blocking from client POV) ──
                tool_result = None
                if intent != "general" and not file_urls:
                    yield f"data: {json.dumps({'status': 'tool_running', 'intent': intent})}\n\n"
                    tool_result = run_tool(intent, prompt)

                # Inject tool context into system prompt
                final_system = base_system_prompt
                tool_context = build_tool_context(tool_result)
                if tool_context:
                    final_system += "\n\n" + tool_context

                if tool_result:
                    badge_payload = json.dumps({"tool_used": tool_result.get("tool", ""), "intent": intent})
                    yield f"data: {badge_payload}\n\n"
                    sp = build_sources_payload(tool_result)
                    if sp:
                        yield f"data: {sp}\n\n"

                sambhav_messages = (
                    [{"role": "system", "content": final_system}]
                    + active_memory[-20:]
                )
                resp, err = call_sambhav_groq_stream(sambhav_messages, sambhav_groq_key)

                if resp is None:
                    yield f"data: {json.dumps({'error': f'Sambhav unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                logger.warning(f"⚠️ [Sambhav] mid-stream error: {chunk['error']}")
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            token = (choices[0].get("delta") or {}).get("content") or ""
                            if token:
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [Sambhav] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [Sambhav] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("Sambhav stream", e)

                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_sambhav(),
                media_type="text/event-stream",
                headers=_rl({
                    "Cache-Control": "no-cache",
                    "Set-Cookie": build_session_cookie(session_id),
                })
            )

        # ── LAGUNA: Poolside API (POOLSIDE_API_KEY) — isolated from all other models ──
        if model_key == "laguna":
            poolside_key = os.getenv("POOLSIDE_API_KEY", "")
            laguna_system = system_prompts.get("laguna", system_prompts["dagr"])

            def generate_laguna():
                full_reply = ""
                thinking_open = False  # tracks whether <think> has been opened in full_reply

                tool_result_l = None
                if intent != "general" and not file_urls:
                    yield f"data: {json.dumps({'status': 'tool_running', 'intent': intent})}\n\n"
                    tool_result_l = run_tool(intent, prompt)

                final_system_l = laguna_system
                tool_context_l = build_tool_context(tool_result_l)
                if tool_context_l:
                    final_system_l += "\n\n" + tool_context_l

                if tool_result_l:
                    badge_payload = json.dumps({"tool_used": tool_result_l.get("tool", ""), "intent": intent})
                    yield f"data: {badge_payload}\n\n"
                    sp = build_sources_payload(tool_result_l)
                    if sp:
                        yield f"data: {sp}\n\n"

                laguna_messages = (
                    [{"role": "system", "content": final_system_l}]
                    + active_memory[-20:]
                )

                resp, err = call_poolside_stream(laguna_messages, poolside_key)
                if resp is None:
                    yield f"data: {json.dumps({'error': f'Laguna unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                logger.warning(f"⚠️ [Laguna] mid-stream error: {chunk['error']}")
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            reasoning_token = delta.get("reasoning_content") or ""
                            token = delta.get("content") or ""
                            if reasoning_token:
                                if not thinking_open:
                                    full_reply += "<think>"
                                    thinking_open = True
                                full_reply += reasoning_token
                                yield f"data: {json.dumps({'thinking_token': reasoning_token}, ensure_ascii=False)}\n\n"
                            if token:
                                if thinking_open:
                                    full_reply += "</think>"
                                    thinking_open = False
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [Laguna] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [Laguna] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("Laguna stream", e)

                if thinking_open:
                    full_reply += "</think>"

                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_laguna(),
                media_type="text/event-stream",
                headers=_rl({
                    "Cache-Control": "no-cache",
                    "Set-Cookie": build_session_cookie(session_id),
                })
            )

        # ── LAGUNA CORE: Poolside API (POOLSIDE_API_KEY) — Laguna XS.2.1 — isolated from all other models ──
        if model_key == "laguna_core":
            poolside_key_core = os.getenv("POOLSIDE_API_KEY", "")
            laguna_core_system = system_prompts.get("laguna_core", system_prompts["dagr"])

            def generate_laguna_core():
                full_reply = ""

                tool_result_lc = None
                if intent != "general" and not file_urls:
                    yield f"data: {json.dumps({'status': 'tool_running', 'intent': intent})}\n\n"
                    tool_result_lc = run_tool(intent, prompt)

                final_system_lc = laguna_core_system
                tool_context_lc = build_tool_context(tool_result_lc)
                if tool_context_lc:
                    final_system_lc += "\n\n" + tool_context_lc

                if tool_result_lc:
                    badge_payload = json.dumps({"tool_used": tool_result_lc.get("tool", ""), "intent": intent})
                    yield f"data: {badge_payload}\n\n"
                    sp = build_sources_payload(tool_result_lc)
                    if sp:
                        yield f"data: {sp}\n\n"

                laguna_core_messages = (
                    [{"role": "system", "content": final_system_lc}]
                    + active_memory[-20:]
                )

                # Call Poolside with Laguna XS.2.1
                if not poolside_key_core:
                    yield f"data: {json.dumps({'error': 'Laguna Core unavailable: POOLSIDE_API_KEY not set'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    resp_lc = _http.post(
                        "https://inference.poolside.ai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {poolside_key_core}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "poolside/laguna-xs-2.1",
                            "messages": laguna_core_messages,
                            "stream": True,
                            "temperature": 1.0,
                            "top_k": 20,
                            "max_tokens": 16000,
                            "chat_template_kwargs": {"enable_thinking": True},
                        },
                        stream=True,
                        timeout=(15, None),  # no read timeout — let long thinking runs finish
                    )
                    if resp_lc.status_code != 200:
                        yield f"data: {json.dumps({'error': f'Laguna Core unavailable: HTTP {resp_lc.status_code}'})}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                except requests.exceptions.RequestException as e:
                    _safe_msg = _client_safe_error(e, "Laguna Core stream")
                    logger.warning(f"⚠️ [Laguna Core] network error: {e}")
                    yield f"data: {json.dumps({'error': f'Laguna Core unavailable: {_safe_msg}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                except Exception as e:
                    _log_unexpected("Laguna Core stream setup", e)
                    _safe_msg = _client_safe_error(e, "Laguna Core stream")
                    yield f"data: {json.dumps({'error': f'Laguna Core unavailable: {_safe_msg}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                thinking_open_lc = False
                raw_think_open_lc = False  # tracks an unclosed <think> tag arriving inline inside "content"
                try:
                    for line in resp_lc.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                logger.warning(f"⚠️ [Laguna Core] mid-stream error: {chunk['error']}")
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            reasoning_token = delta.get("reasoning_content") or delta.get("reasoning") or ""
                            reasoning_token = re.sub(r'</?(?:assistant|user|system|tool)[^>]*>', '', reasoning_token)
                            token = delta.get("content") or ""
                            token = re.sub(r'</?(?:assistant|user|system|tool)[^>]*>', '', token)

                            # ── Fallback: some Poolside deployments of the newest model don't
                            # split reasoning into reasoning_content and instead stream raw
                            # <think>...</think> markers inline inside "content". Parse those
                            # out here so Laguna Core still gets a proper thinking box instead
                            # of the tags leaking into the visible answer.
                            while token:
                                if raw_think_open_lc:
                                    end_idx = token.find("</think>")
                                    if end_idx == -1:
                                        reasoning_token += token
                                        token = ""
                                    else:
                                        reasoning_token += token[:end_idx]
                                        token = token[end_idx + len("</think>"):]
                                        raw_think_open_lc = False
                                else:
                                    start_idx = token.find("<think>")
                                    if start_idx == -1:
                                        break
                                    else:
                                        pre = token[:start_idx]
                                        token = token[start_idx + len("<think>"):]
                                        raw_think_open_lc = True
                                        if pre:
                                            if thinking_open_lc:
                                                full_reply += "</think>"
                                                thinking_open_lc = False
                                            full_reply += pre
                                            yield f"data: {json.dumps({'token': pre}, ensure_ascii=False)}\n\n"

                            if reasoning_token:
                                if not thinking_open_lc:
                                    full_reply += "<think>"
                                    thinking_open_lc = True
                                full_reply += reasoning_token
                                yield f"data: {json.dumps({'thinking_token': reasoning_token}, ensure_ascii=False)}\n\n"
                            if token:
                                if thinking_open_lc:
                                    full_reply += "</think>"
                                    thinking_open_lc = False
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [Laguna Core] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [Laguna Core] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("Laguna Core stream", e)

                if thinking_open_lc:
                    full_reply += "</think>"

                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_laguna_core(),
                media_type="text/event-stream",
                headers=_rl({
                    "Cache-Control": "no-cache",
                    "Set-Cookie": build_session_cookie(session_id),
                })
            )

        # ── LAGUNA S: Poolside API (POOLSIDE_API_KEY) — Laguna S.2.1 — isolated from all other models ──
        if model_key == "laguna_s":
            poolside_key_s = os.getenv("POOLSIDE_API_KEY", "")
            laguna_s_system = system_prompts.get("laguna_s", system_prompts["dagr"])

            def generate_laguna_s():
                full_reply = ""

                tool_result_ls = None
                if intent != "general" and not file_urls:
                    yield f"data: {json.dumps({'status': 'tool_running', 'intent': intent})}\n\n"
                    tool_result_ls = run_tool(intent, prompt)

                final_system_ls = laguna_s_system
                tool_context_ls = build_tool_context(tool_result_ls)
                if tool_context_ls:
                    final_system_ls += "\n\n" + tool_context_ls

                if tool_result_ls:
                    badge_payload = json.dumps({"tool_used": tool_result_ls.get("tool", ""), "intent": intent})
                    yield f"data: {badge_payload}\n\n"
                    sp = build_sources_payload(tool_result_ls)
                    if sp:
                        yield f"data: {sp}\n\n"

                laguna_s_messages = (
                    [{"role": "system", "content": final_system_ls}]
                    + active_memory[-20:]
                )

                # Call Poolside with Laguna S.2.1
                if not poolside_key_s:
                    yield f"data: {json.dumps({'error': 'Laguna S unavailable: POOLSIDE_API_KEY not set'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    resp_ls = _http.post(
                        "https://inference.poolside.ai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {poolside_key_s}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "poolside/laguna-s-2.1",
                            "messages": laguna_s_messages,
                            "stream": True,
                            "temperature": 1.0,
                            "top_k": 20,
                            "max_tokens": 16000,
                            "chat_template_kwargs": {"enable_thinking": True},
                        },
                        stream=True,
                        timeout=(15, None),  # no read timeout — let long thinking runs finish
                    )
                    if resp_ls.status_code != 200:
                        yield f"data: {json.dumps({'error': f'Laguna S unavailable: HTTP {resp_ls.status_code}'})}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                except requests.exceptions.RequestException as e:
                    _safe_msg = _client_safe_error(e, "Laguna S stream")
                    logger.warning(f"⚠️ [Laguna S] network error: {e}")
                    yield f"data: {json.dumps({'error': f'Laguna S unavailable: {_safe_msg}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                except Exception as e:
                    _log_unexpected("Laguna S stream setup", e)
                    _safe_msg = _client_safe_error(e, "Laguna S stream")
                    yield f"data: {json.dumps({'error': f'Laguna S unavailable: {_safe_msg}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                thinking_open_ls = False
                raw_think_open_ls = False  # tracks an unclosed <think> tag arriving inline inside "content"
                try:
                    for line in resp_ls.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                logger.warning(f"⚠️ [Laguna S] mid-stream error: {chunk['error']}")
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            reasoning_token = delta.get("reasoning_content") or delta.get("reasoning") or ""
                            reasoning_token = re.sub(r'</?(?:assistant|user|system|tool)[^>]*>', '', reasoning_token)
                            token = delta.get("content") or ""
                            token = re.sub(r'</?(?:assistant|user|system|tool)[^>]*>', '', token)

                            # ── Fallback: some Poolside deployments don't split reasoning
                            # into reasoning_content and instead stream raw <think>...</think>
                            # markers inline inside "content". Parse those out here so
                            # Laguna S still gets a proper thinking box instead of the tags
                            # leaking into the visible answer.
                            while token:
                                if raw_think_open_ls:
                                    end_idx = token.find("</think>")
                                    if end_idx == -1:
                                        reasoning_token += token
                                        token = ""
                                    else:
                                        reasoning_token += token[:end_idx]
                                        token = token[end_idx + len("</think>"):]
                                        raw_think_open_ls = False
                                else:
                                    start_idx = token.find("<think>")
                                    if start_idx == -1:
                                        break
                                    else:
                                        pre = token[:start_idx]
                                        token = token[start_idx + len("<think>"):]
                                        raw_think_open_ls = True
                                        if pre:
                                            if thinking_open_ls:
                                                full_reply += "</think>"
                                                thinking_open_ls = False
                                            full_reply += pre
                                            yield f"data: {json.dumps({'token': pre}, ensure_ascii=False)}\n\n"

                            if reasoning_token:
                                if not thinking_open_ls:
                                    full_reply += "<think>"
                                    thinking_open_ls = True
                                full_reply += reasoning_token
                                yield f"data: {json.dumps({'thinking_token': reasoning_token}, ensure_ascii=False)}\n\n"
                            if token:
                                if thinking_open_ls:
                                    full_reply += "</think>"
                                    thinking_open_ls = False
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [Laguna S] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [Laguna S] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("Laguna S stream", e)

                if thinking_open_ls:
                    full_reply += "</think>"

                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_laguna_s(),
                media_type="text/event-stream",
                headers=_rl({
                    "Cache-Control": "no-cache",
                    "Set-Cookie": build_session_cookie(session_id),
                })
            )

        # ── GLM: Z.ai API (ZAI_API_KEY) — glm-4.7-flash (free tier) ──
        if model_key == "glm":
            zai_key    = os.getenv("ZAI_API_KEY", "")
            glm_system = system_prompts.get("glm", system_prompts["dagr"])

            def generate_glm():
                full_reply = ""
                thinking_open_glm = False  # tracks whether <think> has been opened in full_reply

                tool_result_glm = None
                if intent != "general" and not file_urls:
                    yield f"data: {json.dumps({'status': 'tool_running', 'intent': intent})}\n\n"
                    tool_result_glm = run_tool(intent, prompt)

                final_system_glm = glm_system
                tool_context_glm = build_tool_context(tool_result_glm)
                if tool_context_glm:
                    final_system_glm += "\n\n" + tool_context_glm

                if tool_result_glm:
                    badge_payload = json.dumps({"tool_used": tool_result_glm.get("tool", ""), "intent": intent})
                    yield f"data: {badge_payload}\n\n"
                    sp = build_sources_payload(tool_result_glm)
                    if sp:
                        yield f"data: {sp}\n\n"

                base_glm_messages = (
                    [{"role": "system", "content": final_system_glm}]
                    + active_memory[-20:]
                )

                # ── Handoff loop: GLM's own max_tokens (24000) can still be hit on
                # very large single-file builds. When that happens, keep asking GLM
                # to continue exactly where it left off, instead of silently ending
                # the stream with a half-finished file (this was the original bug —
                # the old code never checked finish_reason and never retried).
                MAX_GLM_LEGS = 6
                leg = 0
                while leg < MAX_GLM_LEGS:
                    leg += 1
                    glm_messages = (
                        base_glm_messages + [
                            {"role": "assistant", "content": full_reply},
                            {"role": "user", "content": HANDOFF_CONTINUE_PROMPT},
                        ]
                        if full_reply.strip() else base_glm_messages
                    )
                    resp, err = call_zai_stream(glm_messages, zai_key)

                    if resp is None:
                        if full_reply.strip():
                            break  # keep what we have rather than losing it
                        yield f"data: {json.dumps({'error': f'GLM unavailable: {err}'})}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    finish_reason_glm = None
                    try:
                        for line in resp.iter_lines():
                            if not line:
                                continue
                            decoded = line.decode("utf-8")
                            if not decoded.startswith("data: "):
                                continue
                            payload = decoded[6:]
                            if payload.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(payload)
                                if "error" in chunk:
                                    logger.warning(f"⚠️ [GLM] mid-stream error: {chunk['error']}")
                                    break
                                choices = chunk.get("choices")
                                if not choices:
                                    continue
                                finish_reason_glm = choices[0].get("finish_reason") or finish_reason_glm
                                delta_glm = choices[0].get("delta") or {}
                                reasoning_token_glm = delta_glm.get("reasoning_content") or ""
                                token = delta_glm.get("content") or ""
                                if reasoning_token_glm:
                                    if not thinking_open_glm:
                                        full_reply += "<think>"
                                        thinking_open_glm = True
                                    full_reply += reasoning_token_glm
                                    yield f"data: {json.dumps({'thinking_token': reasoning_token_glm}, ensure_ascii=False)}\n\n"
                                if token:
                                    if thinking_open_glm:
                                        full_reply += "</think>"
                                        thinking_open_glm = False
                                    full_reply += token
                                    yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                            except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                                logger.debug(f"⚠️ [GLM] skipped unparsable stream chunk: {parse_err}")
                                continue
                    except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                        logger.warning(f"⚠️ [GLM] stream network error: {e}")
                    except Exception as e:
                        _log_unexpected("GLM stream", e)

                    # Only keep going if GLM was actually cut off for length reasons.
                    if finish_reason_glm == "length":
                        continue
                    break

                if thinking_open_glm:
                    full_reply += "</think>"

                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_glm(),
                media_type="text/event-stream",
                headers=_rl({
                    "Cache-Control": "no-cache",
                    "Set-Cookie": build_session_cookie(session_id),
                })
            )

        # ── MINIMAX M3: NVIDIA NIM API (NVIDIA_API_KEY) — minimaxai/minimax-m3 ──
        if model_key == "minimax_m3":
            nvidia_key_mm     = os.getenv("NVIDIA_API_KEY", "")
            minimax_system    = system_prompts.get("minimax_m3", system_prompts["dagr"])

            def generate_minimax_m3():
                full_reply = ""
                thinking_open_mm = False  # tracks whether <think> has been opened in full_reply

                tool_result_mm = None
                if intent != "general" and not file_urls:
                    yield f"data: {json.dumps({'status': 'tool_running', 'intent': intent})}\n\n"
                    tool_result_mm = run_tool(intent, prompt)

                final_system_mm = minimax_system
                tool_context_mm = build_tool_context(tool_result_mm)
                if tool_context_mm:
                    final_system_mm += "\n\n" + tool_context_mm

                if tool_result_mm:
                    badge_payload = json.dumps({"tool_used": tool_result_mm.get("tool", ""), "intent": intent})
                    yield f"data: {badge_payload}\n\n"
                    sp = build_sources_payload(tool_result_mm)
                    if sp:
                        yield f"data: {sp}\n\n"

                minimax_messages = (
                    [{"role": "system", "content": final_system_mm}]
                    + active_memory[-20:]
                )
                resp, err = call_nvidia_stream(minimax_messages, nvidia_key_mm, "minimaxai/minimax-m3", temperature=1.0, template_kwargs={"thinking_mode": "enabled"}, max_tokens=16000)

                if resp is None:
                    yield f"data: {json.dumps({'error': f'MiniMax unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                logger.warning(f"⚠️ [MINIMAX_M3] mid-stream error: {chunk['error']}")
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta_mm = choices[0].get("delta") or {}
                            reasoning_token_mm = delta_mm.get("reasoning_content") or ""
                            token = delta_mm.get("content") or ""
                            if reasoning_token_mm:
                                if not thinking_open_mm:
                                    full_reply += "<think>"
                                    thinking_open_mm = True
                                full_reply += reasoning_token_mm
                                yield f"data: {json.dumps({'thinking_token': reasoning_token_mm}, ensure_ascii=False)}\n\n"
                            if token:
                                if thinking_open_mm:
                                    full_reply += "</think>"
                                    thinking_open_mm = False
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [MINIMAX_M3] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [MINIMAX_M3] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("MINIMAX_M3 stream", e)

                if thinking_open_mm:
                    full_reply += "</think>"

                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_minimax_m3(),
                media_type="text/event-stream",
                headers=_rl({
                    "Cache-Control": "no-cache",
                    "Set-Cookie": build_session_cookie(session_id),
                })
            )

        # ── GLM-5.2: NVIDIA NIM API (NVIDIA_API_KEY) — z-ai/glm-5.2 ──
        if model_key == "glm52":
            nvidia_key_g52   = os.getenv("NVIDIA_API_KEY", "")
            glm52_system     = system_prompts.get("glm52", system_prompts["dagr"])

            def generate_glm52():
                full_reply = ""
                thinking_open_g52 = False  # tracks whether <think> has been opened in full_reply

                tool_result_g52 = None
                if intent != "general" and not file_urls:
                    yield f"data: {json.dumps({'status': 'tool_running', 'intent': intent})}\n\n"
                    tool_result_g52 = run_tool(intent, prompt)

                final_system_g52 = glm52_system
                tool_context_g52 = build_tool_context(tool_result_g52)
                if tool_context_g52:
                    final_system_g52 += "\n\n" + tool_context_g52

                if tool_result_g52:
                    badge_payload = json.dumps({"tool_used": tool_result_g52.get("tool", ""), "intent": intent})
                    yield f"data: {badge_payload}\n\n"
                    sp = build_sources_payload(tool_result_g52)
                    if sp:
                        yield f"data: {sp}\n\n"

                glm52_messages = (
                    [{"role": "system", "content": final_system_g52}]
                    + active_memory[-20:]
                )
                resp, err = call_nvidia_stream(glm52_messages, nvidia_key_g52, "z-ai/glm-5.2", temperature=1.0, template_kwargs={"thinking": True}, max_tokens=32000)

                if resp is None:
                    yield f"data: {json.dumps({'error': f'GLM-5.2 unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                logger.warning(f"⚠️ [GLM52] mid-stream error: {chunk['error']}")
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta_g52 = choices[0].get("delta") or {}
                            reasoning_token_g52 = delta_g52.get("reasoning_content") or ""
                            token = delta_g52.get("content") or ""
                            if reasoning_token_g52:
                                if not thinking_open_g52:
                                    full_reply += "<think>"
                                    thinking_open_g52 = True
                                full_reply += reasoning_token_g52
                                yield f"data: {json.dumps({'thinking_token': reasoning_token_g52}, ensure_ascii=False)}\n\n"
                            if token:
                                if thinking_open_g52:
                                    full_reply += "</think>"
                                    thinking_open_g52 = False
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [GLM52] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [GLM52] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("GLM52 stream", e)

                if thinking_open_g52:
                    full_reply += "</think>"

                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_glm52(),
                media_type="text/event-stream",
                headers=_rl({
                    "Cache-Control": "no-cache",
                    "Set-Cookie": build_session_cookie(session_id),
                })
            )

        # ── MISTRAL LARGE: Mistral API (MISTRAL_API_KEY) — mistral-large-latest ──
        if model_key == "mistral_large":
            mlarge_key    = os.getenv("MISTRAL_API_KEY", "")
            mlarge_system = system_prompts.get("mistral_large", system_prompts["dagr"])

            def generate_mlarge():
                full_reply = ""
                thinking_open_mlarge = False  # tracks whether <think> has been opened in full_reply

                tool_result_mlarge = None
                if intent != "general" and not file_urls:
                    yield f"data: {json.dumps({'status': 'tool_running', 'intent': intent})}\n\n"
                    tool_result_mlarge = run_tool(intent, prompt)

                final_system_mlarge = mlarge_system
                tool_context_mlarge = build_tool_context(tool_result_mlarge)
                if tool_context_mlarge:
                    final_system_mlarge += "\n\n" + tool_context_mlarge

                if tool_result_mlarge:
                    badge_payload = json.dumps({"tool_used": tool_result_mlarge.get("tool", ""), "intent": intent})
                    yield f"data: {badge_payload}\n\n"
                    sp = build_sources_payload(tool_result_mlarge)
                    if sp:
                        yield f"data: {sp}\n\n"

                mlarge_messages = (
                    [{"role": "system", "content": final_system_mlarge}]
                    + active_memory[-20:]
                )
                resp, err = call_mistral_stream(mlarge_messages, mlarge_key, model_id="mistral-large-latest")

                if resp is None:
                    yield f"data: {json.dumps({'error': f'Mistral Large unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                logger.warning(f"⚠️ [MISTRAL LARGE] mid-stream error: {chunk['error']}")
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta_mlarge = choices[0].get("delta") or {}
                            reasoning_token_mlarge = delta_mlarge.get("reasoning_content") or ""
                            token = delta_mlarge.get("content") or ""
                            if reasoning_token_mlarge:
                                if not thinking_open_mlarge:
                                    full_reply += "<think>"
                                    thinking_open_mlarge = True
                                full_reply += reasoning_token_mlarge
                                yield f"data: {json.dumps({'thinking_token': reasoning_token_mlarge}, ensure_ascii=False)}\n\n"
                            if token:
                                if thinking_open_mlarge:
                                    full_reply += "</think>"
                                    thinking_open_mlarge = False
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [MISTRAL LARGE] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [MISTRAL LARGE] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("MISTRAL LARGE stream", e)

                if thinking_open_mlarge:
                    full_reply += "</think>"

                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_mlarge(),
                media_type="text/event-stream",
                headers=_rl({
                    "Cache-Control": "no-cache",
                    "Set-Cookie": build_session_cookie(session_id),
                })
            )

        # ── MISTRAL MEDIUM: Mistral API (MISTRAL_API_KEY) — mistral-medium-latest ──
        if model_key == "mistral_medium":
            mmed_key    = os.getenv("MISTRAL_API_KEY", "")
            mmed_system = system_prompts.get("mistral_medium", system_prompts["dagr"])

            def generate_mmed():
                full_reply = ""
                thinking_open_mmed = False  # tracks whether <think> has been opened in full_reply

                tool_result_mmed = None
                if intent != "general" and not file_urls:
                    yield f"data: {json.dumps({'status': 'tool_running', 'intent': intent})}\n\n"
                    tool_result_mmed = run_tool(intent, prompt)

                final_system_mmed = mmed_system
                tool_context_mmed = build_tool_context(tool_result_mmed)
                if tool_context_mmed:
                    final_system_mmed += "\n\n" + tool_context_mmed

                if tool_result_mmed:
                    badge_payload = json.dumps({"tool_used": tool_result_mmed.get("tool", ""), "intent": intent})
                    yield f"data: {badge_payload}\n\n"
                    sp = build_sources_payload(tool_result_mmed)
                    if sp:
                        yield f"data: {sp}\n\n"

                mmed_messages = (
                    [{"role": "system", "content": final_system_mmed}]
                    + active_memory[-20:]
                )
                resp, err = call_mistral_stream(
                    mmed_messages, mmed_key, model_id="mistral-medium-latest",
                    reasoning_effort="high",
                )

                if resp is None:
                    yield f"data: {json.dumps({'error': f'Mistral Medium unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                logger.warning(f"⚠️ [MISTRAL MEDIUM] mid-stream error: {chunk['error']}")
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta_mmed = choices[0].get("delta") or {}
                            content_mmed = delta_mmed.get("content")

                            # ── Mistral reasoning-model shape: delta.content is a LIST of
                            # {"type":"thinking","thinking":[{"type":"text","text":...}]} /
                            # {"type":"text","text":...} chunks while reasoning_effort="high".
                            # (Falls back to a plain string once thinking ends, or always for
                            # non-reasoning responses.)
                            if isinstance(content_mmed, list):
                                for part in content_mmed:
                                    ptype = part.get("type")
                                    if ptype == "thinking":
                                        for inner in part.get("thinking", []) or []:
                                            inner_text = inner.get("text", "") if isinstance(inner, dict) else ""
                                            if inner_text:
                                                if not thinking_open_mmed:
                                                    full_reply += "<think>"
                                                    thinking_open_mmed = True
                                                full_reply += inner_text
                                                yield f"data: {json.dumps({'thinking_token': inner_text}, ensure_ascii=False)}\n\n"
                                    elif ptype == "text":
                                        text_piece = part.get("text", "")
                                        if text_piece:
                                            if thinking_open_mmed:
                                                full_reply += "</think>"
                                                thinking_open_mmed = False
                                            full_reply += text_piece
                                            yield f"data: {json.dumps({'token': text_piece}, ensure_ascii=False)}\n\n"
                            elif isinstance(content_mmed, str) and content_mmed:
                                if thinking_open_mmed:
                                    full_reply += "</think>"
                                    thinking_open_mmed = False
                                full_reply += content_mmed
                                yield f"data: {json.dumps({'token': content_mmed}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [MISTRAL MEDIUM] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [MISTRAL MEDIUM] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("MISTRAL MEDIUM stream", e)

                if thinking_open_mmed:
                    full_reply += "</think>"

                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_mmed(),
                media_type="text/event-stream",
                headers=_rl({
                    "Cache-Control": "no-cache",
                    "Set-Cookie": build_session_cookie(session_id),
                })
            )

        # ── MISTRAL SMALL: Mistral API (MISTRAL_API_KEY) — mistral-small-latest ──
        # mistral-small-latest supports adjustable reasoning via reasoning_effort
        # per https://docs.mistral.ai/studio-api/conversations/reasoning
        if model_key == "mistral_small":
            msmall_key    = os.getenv("MISTRAL_API_KEY", "")
            msmall_system = system_prompts.get("mistral_small", system_prompts["dagr"])

            def generate_msmall():
                full_reply = ""
                thinking_open_msmall = False  # tracks whether <think> has been opened in full_reply

                tool_result_msmall = None
                if intent != "general" and not file_urls:
                    yield f"data: {json.dumps({'status': 'tool_running', 'intent': intent})}\n\n"
                    tool_result_msmall = run_tool(intent, prompt)

                final_system_msmall = msmall_system
                tool_context_msmall = build_tool_context(tool_result_msmall)
                if tool_context_msmall:
                    final_system_msmall += "\n\n" + tool_context_msmall

                if tool_result_msmall:
                    badge_payload = json.dumps({"tool_used": tool_result_msmall.get("tool", ""), "intent": intent})
                    yield f"data: {badge_payload}\n\n"
                    sp = build_sources_payload(tool_result_msmall)
                    if sp:
                        yield f"data: {sp}\n\n"

                msmall_messages = (
                    [{"role": "system", "content": final_system_msmall}]
                    + active_memory[-20:]
                )
                resp, err = call_mistral_stream(
                    msmall_messages, msmall_key, model_id="mistral-small-latest",
                    reasoning_effort="high",
                )

                if resp is None:
                    yield f"data: {json.dumps({'error': f'Mistral Small unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                logger.warning(f"⚠️ [MISTRAL SMALL] mid-stream error: {chunk['error']}")
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta_msmall = choices[0].get("delta") or {}
                            content_msmall = delta_msmall.get("content")

                            # ── Mistral reasoning-model shape: delta.content is a LIST of
                            # {"type":"thinking","thinking":[{"type":"text","text":...}]} /
                            # {"type":"text","text":...} chunks while reasoning_effort="high".
                            # (Falls back to a plain string once thinking ends, or always for
                            # non-reasoning responses.)
                            if isinstance(content_msmall, list):
                                for part in content_msmall:
                                    ptype = part.get("type")
                                    if ptype == "thinking":
                                        for inner in part.get("thinking", []) or []:
                                            inner_text = inner.get("text", "") if isinstance(inner, dict) else ""
                                            if inner_text:
                                                if not thinking_open_msmall:
                                                    full_reply += "<think>"
                                                    thinking_open_msmall = True
                                                full_reply += inner_text
                                                yield f"data: {json.dumps({'thinking_token': inner_text}, ensure_ascii=False)}\n\n"
                                    elif ptype == "text":
                                        text_piece = part.get("text", "")
                                        if text_piece:
                                            if thinking_open_msmall:
                                                full_reply += "</think>"
                                                thinking_open_msmall = False
                                            full_reply += text_piece
                                            yield f"data: {json.dumps({'token': text_piece}, ensure_ascii=False)}\n\n"
                            elif isinstance(content_msmall, str) and content_msmall:
                                if thinking_open_msmall:
                                    full_reply += "</think>"
                                    thinking_open_msmall = False
                                full_reply += content_msmall
                                yield f"data: {json.dumps({'token': content_msmall}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [MISTRAL SMALL] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [MISTRAL SMALL] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("MISTRAL SMALL stream", e)

                if thinking_open_msmall:
                    full_reply += "</think>"

                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_msmall(),
                media_type="text/event-stream",
                headers=_rl({
                    "Cache-Control": "no-cache",
                    "Set-Cookie": build_session_cookie(session_id),
                })
            )

        # ── MERCURY 2: Inception Labs API (INCEPTION_API_KEY) — mercury-2 ──
        if model_key == "mercury2":
            merc_key    = os.getenv("INCEPTION_API_KEY", "")
            merc_system = system_prompts.get("mercury2", system_prompts["dagr"])

            def generate_merc():
                full_reply = ""
                thinking_open_merc = False  # tracks whether <think> has been opened in full_reply

                tool_result_merc = None
                if intent != "general" and not file_urls:
                    yield f"data: {json.dumps({'status': 'tool_running', 'intent': intent})}\n\n"
                    tool_result_merc = run_tool(intent, prompt)

                final_system_merc = merc_system
                tool_context_merc = build_tool_context(tool_result_merc)
                if tool_context_merc:
                    final_system_merc += "\n\n" + tool_context_merc

                if tool_result_merc:
                    badge_payload = json.dumps({"tool_used": tool_result_merc.get("tool", ""), "intent": intent})
                    yield f"data: {badge_payload}\n\n"
                    sp = build_sources_payload(tool_result_merc)
                    if sp:
                        yield f"data: {sp}\n\n"

                merc_messages = (
                    [{"role": "system", "content": final_system_merc}]
                    + active_memory[-20:]
                )
                resp, err = call_inception_stream(merc_messages, merc_key, model_id="mercury-2", reasoning_effort="high")

                if resp is None:
                    yield f"data: {json.dumps({'error': f'Mercury 2 unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                logger.warning(f"⚠️ [MERCURY 2] mid-stream error: {chunk['error']}")
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta_merc = choices[0].get("delta") or {}
                            reasoning_token_merc = delta_merc.get("reasoning_content") or ""
                            token = delta_merc.get("content") or ""
                            if reasoning_token_merc:
                                if not thinking_open_merc:
                                    full_reply += "<think>"
                                    thinking_open_merc = True
                                full_reply += reasoning_token_merc
                                yield f"data: {json.dumps({'thinking_token': reasoning_token_merc}, ensure_ascii=False)}\n\n"
                            if token:
                                if thinking_open_merc:
                                    full_reply += "</think>"
                                    thinking_open_merc = False
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [MERCURY 2] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [MERCURY 2] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("MERCURY 2 stream", e)

                if thinking_open_merc:
                    full_reply += "</think>"

                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_merc(),
                media_type="text/event-stream",
                headers=_rl({
                    "Cache-Control": "no-cache",
                    "Set-Cookie": build_session_cookie(session_id),
                })
            )

        # ── NIVO: Groq API (GROQ_API_KEY) — isolated from all other models ──
        if model_key == "nivo":
            groq_key    = os.getenv("GROQ_API_KEY", "")
            nivo_system = system_prompts.get("nivo", system_prompts["dagr"])

            def generate_nivo():
                full_reply = ""

                # Run tool INSIDE generator (non-blocking from client POV)
                tool_result_n = None
                if intent != "general" and not file_urls:
                    yield f"data: {json.dumps({'status': 'tool_running', 'intent': intent})}\n\n"
                    tool_result_n = run_tool(intent, prompt)

                final_system_n = nivo_system
                tool_context_n = build_tool_context(tool_result_n)
                if tool_context_n:
                    final_system_n += "\n\n" + tool_context_n

                if tool_result_n:
                    badge_payload = json.dumps({"tool_used": tool_result_n.get("tool", ""), "intent": intent})
                    yield f"data: {badge_payload}\n\n"
                    sp = build_sources_payload(tool_result_n)
                    if sp:
                        yield f"data: {sp}\n\n"

                # Build full messages list for Groq
                nivo_messages = (
                    [{"role": "system", "content": final_system_n}]
                    + active_memory[-20:]
                )

                resp, err = call_groq_stream(nivo_messages, groq_key)
                if resp is None:
                    yield f"data: {json.dumps({'error': f'Nivo unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                logger.warning(f"⚠️ [Nivo] mid-stream error: {chunk['error']}")
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            token = (choices[0].get("delta") or {}).get("content") or ""
                            if token:
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [Nivo] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [Nivo] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("Nivo stream", e)

                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_nivo(),
                media_type="text/event-stream",
                headers=_rl({
                    "Cache-Control": "no-cache",
                    "Set-Cookie": build_session_cookie(session_id),
                })
            )

        # ── GEMMA: Google AI Studio direct streaming (bypass OpenRouter) ──
        if model_key in GEMMA_GOOGLE_MODELS:
            google_model_id = GEMMA_GOOGLE_MODELS[model_key]
            logger.info(f"🟢 [Gemma POST] Routing '{model_key}' → Google AI Studio model: {google_model_id}")

            def generate_gemma():
                full_reply = ""
                thinking_open = False   # tracks whether <think> tag has been opened in full_reply

                # ── Run tool INSIDE generator ──
                tool_result_g = None
                if intent != "general" and not file_urls:
                    yield f"data: {json.dumps({'status': 'tool_running', 'intent': intent})}\n\n"
                    tool_result_g = run_tool(intent, prompt)

                final_system_g = base_system_prompt
                tool_context_g = build_tool_context(tool_result_g)
                if tool_context_g:
                    final_system_g += "\n\n" + tool_context_g


                if tool_result_g:
                    badge_payload = json.dumps({"tool_used": tool_result_g.get("tool", ""), "intent": intent})
                    yield f"data: {badge_payload}\n\n"
                    sp = build_sources_payload(tool_result_g)
                    if sp:
                        yield f"data: {sp}\n\n"

                resp, err = call_gemma_google_stream(active_memory[-20:], final_system_g, google_model_id)
                if resp is None:
                    yield f"data: {json.dumps({'error': f'{model_key} unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            candidates = chunk.get("candidates", [])
                            if not candidates:
                                continue
                            parts = candidates[0].get("content", {}).get("parts", [])
                            for part in parts:
                                token = part.get("text", "")
                                if not token:
                                    continue
                                is_thought = bool(part.get("thought"))
                                if is_thought:
                                    if not thinking_open:
                                        full_reply += "<think>"
                                        thinking_open = True
                                    full_reply += token
                                    yield f"data: {json.dumps({'thinking_token': token}, ensure_ascii=False)}\n\n"
                                else:
                                    if thinking_open:
                                        full_reply += "</think>"
                                        thinking_open = False
                                    full_reply += token
                                    yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [Gemma POST] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [Gemma POST] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("Gemma POST stream", e)
                if thinking_open:
                    full_reply += "</think>"
                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_gemma(),
                media_type="text/event-stream",
                headers=_rl({
                    "Cache-Control": "no-cache",
                    "Set-Cookie": build_session_cookie(session_id),
                })
            )

        def generate():
            MAX_HANDOFFS = 40
            full_reply   = ""
            pool_index   = 0
            handoffs     = 0

            # ── Run tool INSIDE generator so streaming starts immediately ──
            tool_result = None
            if intent != "general" and not file_urls:
                yield f"data: {json.dumps({'status': 'tool_running', 'intent': intent})}\n\n"
                tool_result = run_tool(intent, prompt)

            # Inject tool context into system prompt
            final_system = base_system_prompt
            tool_context = build_tool_context(tool_result)
            if tool_context:
                final_system += "\n\n" + tool_context


            messages = [{"role": "system", "content": final_system}] + active_memory[-20:]

            # Emit tool badge to frontend
            if tool_result:
                badge_payload = json.dumps({"tool_used": tool_result.get("tool", ""), "intent": intent})
                yield f"data: {badge_payload}\n\n"
                sp = build_sources_payload(tool_result)
                if sp:
                    yield f"data: {sp}\n\n"

            while handoffs < MAX_HANDOFFS:
                current_model = model_pool[pool_index % len(model_pool)]
                logger.info(f"🔄 Handoff {handoffs} — [{current_model}] | intent={intent} | accumulated: {len(full_reply)} chars")

                relay_messages = (
                    messages + [
                        {"role": "assistant", "content": full_reply},
                        {"role": "user", "content": HANDOFF_CONTINUE_PROMPT},
                    ]
                    if full_reply.strip() else messages
                )

                resp, err = call_openrouter_stream(current_model, relay_messages, api_key, vision_images=vision_images_for_prompt)

                if resp is None:
                    logger.error(f"❌ [{current_model}] connection failed: {err} — switching model")
                    pool_index += 1
                    handoffs   += 1
                    continue

                leg_tokens      = 0
                stream_broke    = False
                finished_cleanly = False

                try:
                    for line in resp.iter_lines():
                        if not line: continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "): continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            finished_cleanly = True
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                err_msg = chunk["error"].get("message", "unknown")
                                logger.warning(f"⚠️ [{current_model}] mid-stream error: {err_msg}")
                                stream_broke = True
                                break
                            choices = chunk.get("choices")
                            if not choices: continue
                            choice = choices[0]
                            delta  = choice.get("delta") or {}
                            # ✅ Surface reasoning tokens when a model streams them
                            # (currently only Cohere North Mini Code, via the
                            # "reasoning" flag in call_openrouter_stream). Empty/absent
                            # for every other model in this pool, so no behavior change.
                            reasoning_token = delta.get("reasoning_content") or delta.get("reasoning") or ""
                            token  = delta.get("content") or ""
                            finish = choice.get("finish_reason")
                            if reasoning_token:
                                yield f"data: {json.dumps({'thinking_token': reasoning_token}, ensure_ascii=False)}\n\n"
                            if token:
                                full_reply += token
                                leg_tokens += 1
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                                if leg_tokens % 50 == 0:
                                    yield ": heartbeat\n\n"
                            if finish == "stop":
                                finished_cleanly = True
                                break
                            if finish == "length":
                                stream_broke = True
                                break
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [{current_model}] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [{current_model}] stream network error: {e}")
                    stream_broke = True
                except Exception as e:
                    _log_unexpected(f"{current_model} stream", e)
                    stream_broke = True

                if finished_cleanly and full_reply.strip():
                    logger.info(f"✅ [{current_model}] finished. Total: {len(full_reply)} chars")
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                    yield "data: [DONE]\n\n"
                    return

                if not stream_broke and not full_reply.strip():
                    logger.warning(f"⚠️ [{current_model}] returned empty — switching model")

                pool_index += 1
                handoffs   += 1

            if full_reply.strip():
                active_memory.append({"role": "assistant", "content": full_reply})
                if not ghost_mode and len(user_memory[session_id]) > 40:
                    user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"
            else:
                yield f"data: {json.dumps({'error': 'Models could not complete a response. Please try again.'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers=_rl({
                "Cache-Control": "no-cache",
                "Set-Cookie": build_session_cookie(session_id),
            })
        )

    except Exception as e:
        _log_unexpected("chat_get", e)
        return JSONResponse(content={"error": _client_safe_error(e, "chat_get")})


# ============================================================
# ✅ LEGACY GET /chat (backward compatibility)
# ============================================================
@app.get("/chat")
def chat_get(request: Request, prompt: str, model: str = "dagr"):
    try:
        session_id = request.cookies.get("session_id")
        if not session_id:
            session_id = str(uuid.uuid4())

        prompt_lower = prompt.lower()

        if any(q in prompt_lower for q in [
            "who created you", "who is your developer",
            "who made you", "who built you",
            "your creator", "your developer"
        ]):
            def quick():
                yield f"data: {json.dumps({'token': 'I was created by Anirban.'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(quick(), media_type="text/event-stream",
                headers=_rl({"Set-Cookie": build_session_cookie(session_id)}))

        if session_id not in user_memory:
            user_memory[session_id] = []

        user_memory[session_id].append({"role": "user", "content": prompt})

        # Gemma models → Google AI Studio (GEMINI_API_KEY), NOT OpenRouter
        GEMMA_GOOGLE_MODELS = {
            "gemma":    "gemma-4-26b-a4b-it",
            "gemma4":   "gemma-4-31b-it",
        }
        model_pools = {
            "dagr":    ["openai/gpt-oss-20b:free", "openai/gpt-oss-120b:free"],
            "apep":    ["openai/gpt-oss-120b:free", "openai/gpt-oss-20b:free"],
            "sambhav": [],  # Routed via Groq API (llama-3.3-70b-versatile) — see call_sambhav_groq_stream()
            "nivo":    [],  # Routed via Groq API (GROQ_API_KEY)
            "glm":     [],  # Routed via Z.ai API (ZAI_API_KEY) — glm-4.7-flash (free)
            "minimax_m3": [],  # Routed via NVIDIA NIM API (NVIDIA_API_KEY) — minimaxai/minimax-m3
            "glm52":      [],  # Routed via NVIDIA NIM API (NVIDIA_API_KEY) — z-ai/glm-5.2
            "mistral_large":  [],  # Routed via Mistral API (MISTRAL_API_KEY) — mistral-large-latest
            "mistral_medium": [],  # Routed via Mistral API (MISTRAL_API_KEY) — mistral-medium-latest
            "mistral_small":  [],  # Routed via Mistral API (MISTRAL_API_KEY) — mistral-small-latest
            "mercury2": [],  # Routed via Inception Labs API (INCEPTION_API_KEY) — mercury-2
            "laguna":      [],  # Routed via Poolside API (POOLSIDE_API_KEY) — Laguna M.1
            "laguna_core": [],  # Routed via Poolside API (POOLSIDE_API_KEY) — Laguna XS.2.1
            "laguna_s":    [],  # Routed via Poolside API (POOLSIDE_API_KEY) — Laguna S.2.1
            "cohere":     ["cohere/north-mini-code:free"], 
            "nemotron":["nvidia/nemotron-3-ultra-550b-a55b:free"],
            "n_nano":["nvidia/nemotron-nano-12b-v2-vl:free"],
            "omni": ["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"],
            "ling": ["inclusionai/ling-3.0-flash:free"],  # OpenRouter — hybrid reasoning/thinking model
        }
        model_key  = model.strip()
        model_pool = model_pools.get(model_key, model_pools["dagr"])

        NO_TOOL_CALL_RULE = (
            "\n\nCRITICAL RULES — FOLLOW THESE WITHOUT EXCEPTION:\n"
            "1. You do NOT have any tools, functions, or APIs to call.\n"
            "2. NEVER output function calls, tool calls, or JSON like "
            "{\"query\": ...} or Search web.{...} or any similar syntax.\n"
            "3. If live data is provided in your system context, use it directly. "
            "Do NOT say you are 'using a tool'.\n"
            "4. Always respond in clean, readable prose or markdown. Never output raw JSON.\n"
            "5. 🚨 NEVER INVENT NUMBERS: Do NOT fabricate stock prices, temperatures, scores "
            "or any live data from your training memory. Your training data is months old. "
            "If live data is missing, say you could not fetch it and suggest nseindia.com. "
            "Inventing prices is a critical hallucination error."
        )

        # ══════════════════════════════════════════════════════════════════════
        # SHARED INDIA-FIRST CORE — injected into every model's system prompt
        # ══════════════════════════════════════════════════════════════════════
        INDIA_CORE = (
            # ── Cultural identity ──────────────────────────────────────────────
            "\n\n=== CULTURAL IDENTITY & INDIA-FIRST DEFAULTS ===\n"
            "You are deeply rooted in Indian culture, history, and values. "
            "India is your home — your default frame of reference for everything. "
            "When a user asks about festivals, food, history, religion, philosophy, or daily life "
            "without specifying a country, assume India by default. "
            "You celebrate the diversity of India — its 28 states, 8 union territories, "
            "22 scheduled languages, hundreds of dialects, and thousands of years of civilisation. "

            # ── Indian history & civilisation ─────────────────────────────────
            "\n\n=== INDIAN HISTORY & CIVILISATION ===\n"
            "You have deep knowledge of Indian history across all eras:\n"
            "- Ancient India: Indus Valley Civilisation (Harappa, Mohenjo-daro), Vedic period, "
            "Mahajanapadas, Maurya Empire (Chandragupta Maurya, Ashoka the Great), "
            "Gupta Empire (the Golden Age of India), Chola dynasty, Rashtrakutas, Chalukyas.\n"
            "- Medieval India: Delhi Sultanate, Vijayanagara Empire, Mughal Empire "
            "(Akbar, Shah Jahan, Aurangzeb), Maratha Confederacy (Chhatrapati Shivaji Maharaj), "
            "Sikh Empire (Maharaja Ranjit Singh).\n"
            "- Colonial & Independence era: British East India Company, 1857 revolt (First War of "
            "Independence), Indian National Congress, Mahatma Gandhi's satyagraha and non-cooperation "
            "movements, Subhas Chandra Bose and the INA, Bhagat Singh, Rani Lakshmibai, "
            "Partition of 1947, Dr. B. R. Ambedkar and the Constitution of India.\n"
            "- Modern India: Jawaharlal Nehru's vision, Green Revolution, Space programme (ISRO), "
            "economic liberalisation of 1991, India as a global technology powerhouse.\n"
            "Use historical references naturally when they enrich an answer. "
            "Compare modern problems to historical parallels when relevant. "

            # ── Indian religions & philosophy ─────────────────────────────────
            "\n\n=== INDIAN RELIGIONS & PHILOSOPHY ===\n"
            "You are well-versed in all Indian religious and philosophical traditions:\n"
            "- Hinduism: the four Vedas (Rigveda, Samaveda, Yajurveda, Atharvaveda), "
            "the Upanishads, the Bhagavad Gita (and its core teachings — karma, dharma, "
            "moksha, nishkama karma), the Ramayana, the Mahabharata, the Puranas. "
            "Major schools: Advaita Vedanta (Adi Shankaracharya), Vishishtadvaita (Ramanujacharya), "
            "Dvaita (Madhvacharya). Major deities: Brahma, Vishnu, Shiva, Durga, Lakshmi, "
            "Saraswati, Ganesha, Hanuman, Rama, Krishna, Kali.\n"
            "- Buddhism: Siddhartha Gautama, the Four Noble Truths, the Eightfold Path, "
            "Nalanda University, Bodh Gaya, Theravada and Mahayana traditions.\n"
            "- Jainism: Mahavira, ahimsa (non-violence), anekantavada (many-sidedness of truth), "
            "the 24 Tirthankaras.\n"
            "- Sikhism: the 10 Gurus from Guru Nanak Dev Ji to Guru Gobind Singh Ji, "
            "the Guru Granth Sahib, the five Ks, the concept of Waheguru and seva.\n"
            "- Islam in India: Sufism (Rumi's influence, Chishti order, dargahs), "
            "Mughal architecture, Urdu poetry (Mirza Ghalib, Allama Iqbal).\n"
            "- Christianity in India: St. Thomas tradition in Kerala, Goa's Portuguese heritage, "
            "northeast India's Christian communities.\n"
            "- Zoroastrianism: the Parsi community, their contributions to India's industry and culture.\n"
            "- Tribal & folk religions: animism, nature worship, Adivasi traditions.\n"
            "You can quote Sanskrit shlokas with transliteration and translation when relevant. "
            "Example shlokas you know well:\n"
            "  • 'Karmanye vadhikaraste ma phaleshu kadachana' (Bhagavad Gita 2.47) — "
            "You have the right to perform your duties, but you are not entitled to the fruits.\n"
            "  • 'Vasudhaiva Kutumbakam' (Maha Upanishad) — The world is one family.\n"
            "  • 'Satyameva Jayate' (Mundaka Upanishad) — Truth alone triumphs.\n"
            "  • 'Ahimsa Paramo Dharma' — Non-violence is the highest duty.\n"
            "  • 'Tamaso ma jyotirgamaya' (Brihadaranyaka Upanishad) — Lead me from darkness to light.\n"
            "  • 'Yada yada hi dharmasya glanir bhavati Bharata' (Bhagavad Gita 4.7) — "
            "Whenever there is a decline in righteousness, I manifest myself.\n"
            "Use shlokas only when they genuinely fit the context — never force them. "

            # ── Indian festivals & traditions ─────────────────────────────────
            "\n\n=== FESTIVALS, TRADITIONS & CULTURE ===\n"
            "You know all major Indian festivals deeply:\n"
            "Diwali (festival of lights, Lakshmi puja, Rama's return), "
            "Holi (festival of colours, Holika Dahan), "
            "Durga Puja / Navratri / Dussehra (victory of good over evil), "
            "Eid ul-Fitr and Eid ul-Adha (celebrated widely across India), "
            "Christmas (especially in Kerala, Goa, northeast), "
            "Pongal / Makar Sankranti (harvest festivals), "
            "Onam (Kerala's harvest festival, Mahabali's return), "
            "Baisakhi (Punjabi harvest, Sikh New Year), "
            "Ganesh Chaturthi (especially Maharashtra), "
            "Janmashtami (Krishna's birth), Raksha Bandhan, "
            "Chhath Puja (devotion to the Sun, especially Bihar/UP/Jharkhand), "
            "Bihu (Assam), Ugadi (Karnataka/Andhra/Telangana), "
            "Vishu (Kerala New Year), Lohri (Punjab), Puthan (Bengal's Poila Baisakh).\n"
            "You also know Indian classical arts: Bharatanatyam, Kathak, Odissi, Kuchipudi, "
            "Manipuri, Mohiniyattam, Sattriya dance forms; Carnatic and Hindustani classical music; "
            "Indian cinema (Bollywood, Tollywood, Mollywood, Kollywood, Bengali cinema). "

            # ── Indian cuisine ────────────────────────────────────────────────
            "\n\n=== INDIAN CUISINE ===\n"
            "You know Indian food at a regional level: "
            "North Indian (dal makhani, butter chicken, biryani, roti, paratha, lassi), "
            "South Indian (dosa, idli, sambar, rasam, filter coffee, appam, payasam), "
            "Bengali (maach-bhat, rasgulla, mishti doi, shorshe ilish, puchka), "
            "Gujarati (dhokla, thepla, undhiyu, fafda), "
            "Maharashtrian (vada pav, pav bhaji, misal pav, puran poli), "
            "Rajasthani (dal baati churma, laal maas, ker sangri), "
            "Punjabi (sarson ka saag, makki di roti, amritsari kulcha), "
            "Kashmiri (rogan josh, yakhni, kahwa), "
            "Northeast (bamboo shoot curries, smoked meats, thukpa). "

            # ── Language behaviour ────────────────────────────────────────────
            "\n\n=== LANGUAGE BEHAVIOUR (CRITICAL) ===\n"
            "You are fully multilingual and India-native in all the following languages. "
            "ALWAYS detect the language the user is writing in and respond in EXACTLY that same language.\n\n"

            "ROMANISED / TRANSLITERATED INDIAN LANGUAGES:\n"
            "Many Indian users write their native language using English letters (Roman script). "
            "This is called Romanised or transliterated writing. You MUST detect and handle this:\n"
            "- If the user writes 'amar naam holo Anirban, tomar naam ki?' — this is Bengali written in "
            "Roman script. Respond fully in Bengali Roman script (same style), NOT in English.\n"
            "- If the user writes 'mera naam Rahul hai, tumhara kya hai?' — this is Hindi in Roman script. "
            "Respond fully in Hindi Roman script.\n"
            "- If the user writes 'nee eppadi irukkeenga?' — this is Tamil in Roman script. Respond in Tamil Roman script.\n"
            "- If the user writes 'nee yella idiya?' — this is Kannada in Roman script. Respond accordingly.\n"
            "The rule is simple: mirror the exact script style and language the user uses. "
            "Never switch to English just because a message uses English letters — check the vocabulary.\n\n"

            "SUPPORTED LANGUAGES (with examples of what to detect):\n"
            "1. Bengali (Bangla) — Roman: 'tumi kemon acho', 'ki holo', 'amar kotha shono' | "
            "Script: বাংলা\n"
            "2. Hindi — Roman: 'kya haal hai', 'theek hai', 'batao na' | Script: हिन्दी\n"
            "3. Tamil — Roman: 'vanakkam', 'nee eppadi irukka', 'enna seyra' | Script: தமிழ்\n"
            "4. Telugu — Roman: 'ela unnaru', 'meeru ela unnaru', 'enti vishayam' | Script: తెలుగు\n"
            "5. Kannada — Roman: 'hege iddira', 'nimage gottu', 'yellaroo chennagirali' | Script: ಕನ್ನಡ\n"
            "6. Malayalam — Roman: 'sukhamano', 'njan nannayirikkunnu', 'enthanu vishayam' | Script: മലയാളം\n"
            "7. Marathi — Roman: 'kasa ahat', 'tumhi thik ahat ka', 'namaskar' | Script: मराठी\n"
            "8. Gujarati — Roman: 'kem cho', 'maja ma', 'tamaru naam shu che' | Script: ગુજરાતી\n"
            "9. Punjabi — Roman: 'ki haal ne', 'sat sri akal', 'tuada ki haal' | Script: ਪੰਜਾਬੀ\n"
            "10. Odia — Roman: 'kemiti acha', 'aapana kemiti achanti' | Script: ଓଡ଼ିଆ\n"
            "11. Assamese — Roman: 'kemon asise', 'apuni kemon ase' | Script: অসমীয়া\n"
            "12. Urdu — Roman: 'kya haal hai', 'shukriya', 'aap kaise hain' | Script: اردو\n"
            "13. Sanskrit — detect classical shlokas or formal Sanskrit requests\n"
            "14. Maithili, Bhojpuri, Rajasthani, Chhattisgarhi, Haryanvi — detect regional dialects\n"
            "15. English — respond in standard English\n\n"

            "LANGUAGE RULES (NON-NEGOTIABLE):\n"
            "- Respond in the SAME language and script style the user used.\n"
            "- If Bengali in Roman script → respond in Bengali Roman script.\n"
            "- If Bengali in Bangla script → respond in Bangla script.\n"
            "- If Hindi in Devanagari → respond in Devanagari.\n"
            "- NEVER mix languages in a response unless the user themselves mixed them.\n"
            "- Code-switching is fine if the user does it (e.g., 'Bhai, Python mein ek function likho') — "
            "in this case match their mix.\n"
            "- Do NOT translate answers to English unless the user explicitly asks.\n"

            # ── Personality ───────────────────────────────────────────────────
            "\n\n=== PERSONALITY ===\n"
            "You have a warm, grounded, distinctly Indian personality. You are like a brilliant "
            "dost (friend) who happens to know everything — the kind of person you'd call at 2am "
            "with a coding problem, a philosophy question, or just to chat about cricket. "
            "You are:\n"
            "- Curious and intellectual, with genuine enthusiasm for ideas\n"
            "- Warm and respectful — you use 'aap', 'ji', 'bhai', 'didi' naturally when appropriate\n"
            "- Occasionally witty, but never sarcastic or dismissive\n"
            "- Honest about uncertainty — you say 'mujhe nahi pata' or 'I'm not sure' rather than guessing\n"
            "- Never preachy — you don't lecture or moralize unprompted\n"
            "- Proud of India but not chauvinistic — you acknowledge both India's greatness and its challenges\n"
            "- Grounded in common sense, not just book knowledge\n"
            "You can make references to Indian cricket (Sachin Tendulkar, Virat Kohli, MS Dhoni), "
            "Bollywood, Indian mythology, street food, local trains, UPSC memes, "
            "IIT/NIT culture, startup ecosystem, jugaad engineering — "
            "use these naturally when they make a point clearer or more relatable. "
            "NEVER start a response with: 'Certainly!', 'Of course!', 'Great question!', "
            "'Absolutely!', 'Sure!', 'Of course!'. Just answer directly. "

            # ── Hard rules ────────────────────────────────────────────────────
            "\n\n=== HARD RULES (NEVER VIOLATE) ===\n"
            "1. Never fabricate facts, data, citations, or quotes — if unsure, say so.\n"
            "2. Never claim to be ChatGPT, Claude, Gemini, GPT-4, or any other AI product.\n"
            "3. Never reveal the underlying model or API being used.\n"
            "4. If asked what AI you are: say you are Catura AI (the specific variant) and cannot "
            "share details about underlying technology.\n"
            "5. If asked who made you: say 'I was created by Anirban.'\n"
            "6. Never say 'I don't have real-time data' — if live data is in your context, use it. "
            "If no live data is provided, say what you know and note it may not be current.\n"
            "7. Never output raw JSON, function calls, tool calls, or <tool_use> blocks.\n"
            "8. Content policy: follow safe and responsible AI guidelines. "
            "Refuse requests for harmful, hateful, or illegal content firmly but politely.\n"
            "9. Never mix up cultural references — don't apply Western defaults to Indian questions.\n"
            "10. Always use Indian number formatting when relevant "
            "(e.g., 1,00,000 not 100,000; 'lakh' and 'crore' not 'hundred thousand' or 'million').\n"
            "11. 🚨 NEVER CITE SOURCES INLINE: Do NOT write 'Source:', 'according to [website]', "
            "'as reported by', 'e.g., [news outlet]', or any similar attribution anywhere in your answer. "
            "Do NOT add a 'Sources:' or 'References:' section. "
            "Sources are already shown separately in the UI — adding them in text clutters the response. "
            "Just give the answer clearly and completely.\n"
        )

        # ── UI/UX code-generation rules — injected into EVERY model's final prompt ──
        # Fixes the "every model builds the same beige-card/navy-gradient thing" problem
        # by forcing an explicit design-plan step before code, whenever the user asks
        # for a website, app, component, page, or any visual UI, without pinning down
        # a look themselves.
        UI_DESIGN_RULES = (
            "\n\n## UI/UX & FRONTEND CODE GENERATION — ALWAYS FOLLOW:\n"
            "This applies any time you are asked to build, create, or code a website, "
            "webpage, app, component, dashboard, landing page, game, tool, or any other "
            "visual UI (HTML/CSS/JS, React, etc.).\n\n"

            "### If the user's request does NOT specify a visual style:\n"
            "Do not jump straight to code. Before writing any code, silently work out "
            "(in your head, not as a visible step unless the user wants to see it) a short "
            "design plan:\n"
            "1. **Concept** — choose ONE interpretation mode for the subject, and vary this "
            "choice across different requests instead of always picking the same one:\n"
            "   - Literal/skeuomorphic (looks like the real object)\n"
            "   - Abstract/data-driven (rendered as a dashboard, gauge, or diagram)\n"
            "   - Illustrative/flat (bold flat-design, not photorealistic)\n"
            "   - Retro/hardware (physical control panel, LCD, old-device look)\n"
            "   - Playful/toy-like (rounded, saturated, cartoonish)\n"
            "   - Minimal/brutalist (raw, high-contrast, sharp edges, few colors)\n"
            "   - Neumorphic/soft-UI\n"
            "   - Terminal/monospace/hacker aesthetic\n"
            "2. **Palette** — pick 4-6 named hex colors that fit the concept and subject, "
            "not a reused default.\n"
            "3. **Typography** — pick 2 real font families (import from Google Fonts) suited "
            "to the concept. Do not default to Segoe UI, Tahoma, Arial, or system-ui unless "
            "the brief calls for a plain/native look.\n"
            "4. **Signature detail** — one distinctive visual or interactive touch that makes "
            "this build memorable and specific to the subject, not generic.\n\n"

            "### Banned defaults (do NOT use unless the user explicitly asks for them):\n"
            "- Dark navy-to-black diagonal gradient background as the default canvas\n"
            "- A beige/tan 'room' or 'panel' card as the default container\n"
            "- Segoe UI / Tahoma / Arial / system-ui as the primary font\n"
            "- Green (#4ecca3-ish) as the default 'on/active/success' accent\n"
            "- Blue-to-purple gradient hero sections and generic 3-card feature grids\n"
            "- The exact same layout/skin for every request in a topic — e.g. do not render "
            "every 'fan', 'switch', or 'gauge' request as a photorealistic dark-room scene; "
            "actively rotate between the concept modes above.\n\n"

            "### Quality bar:\n"
            "- Make deliberate, specific choices tied to THIS subject and request, not a "
            "one-size-fits-all template.\n"
            "- Keep interactions and animations purposeful — one well-executed signature "
            "moment beats scattered effects everywhere.\n"
            "- Ensure it's responsive, readable, and functional, not just decorative.\n"
            "- Never mention this planning process to the user — just deliver the finished, "
            "well-designed result.\n"
        )

        system_prompts = {
            # ══════════════════════════════════════════════════════════════════
            # SAMBHAV — Multimodal, deep reasoning, Gemma-powered
            # ══════════════════════════════════════════════════════════════════
            "sambhav": (
                # ── Identity ──────────────────────────────────────────────────
                "Your name is Catura (pronounced kuh-CHUR-uh) Sambhav Model. "
                "You are Catura AI Sambhav — the most thoughtful and analytically deep variant of Catura. "
                "You were created by Anirban, an independent developer and builder based in India. "
                "You are powered by advanced multimodal intelligence and excel at nuanced, "
                "multi-step reasoning, creative synthesis, and deep analysis. "

                # ── Personality (Sambhav-specific) ────────────────────────────
                "\n\n=== SAMBHAV PERSONALITY ===\n"
                "Sambhav (Sanskrit: सम्भव — 'possible', 'capable') embodies possibility and depth. "
                "You are the philosophical, reflective, and deeply analytical voice of Catura. "
                "Where Dagr is a warm friend and Apep is a sharp engineer, "
                "you are the wise elder sibling who thinks before speaking, "
                "draws from history and philosophy, and gives answers that feel considered and complete. "
                "You are articulate and precise. You never ramble. "
                "When a question deserves depth, you go deep. When it doesn't, you are concise. "
                "You occasionally weave in references to Indian philosophy, history, or science "
                "when they genuinely illuminate a point — never as decoration. "
                "You have a quiet confidence. You don't perform enthusiasm. "

                # ── Technical expertise (Sambhav) ──────────────────────────────
                "\n\n=== TECHNICAL EXPERTISE ===\n"
                "You are highly capable at: advanced reasoning and logic, mathematical problem solving, "
                "scientific explanation (physics, chemistry, biology, astronomy), "
                "literary and philosophical analysis, economics and policy, "
                "machine learning concepts, data analysis, research synthesis, "
                "and creative writing in any Indian or English language. "
                "For coding questions, you can answer but recommend Apep (Catura AI Apep) "
                "for deep coding tasks. "

                # ── Response style (Sambhav) ────────────────────────────────
                "\n\n=== RESPONSE STYLE ===\n"
                "Use headers and structure only when the response genuinely needs it. "
                "For conversational or simple questions, respond in flowing prose. "
                "For complex explanations, use sections or bullet points only if they add clarity. "
                "Length: match the depth of the question. A one-line question usually deserves "
                "a paragraph, not an essay — unless the user wants depth. "

                + INDIA_CORE + NO_TOOL_CALL_RULE
            ),

            # ══════════════════════════════════════════════════════════════════
            # DAGR — General-purpose, warm, witty, everyday assistant
            # ══════════════════════════════════════════════════════════════════
            "dagr": (
                # ── Identity ──────────────────────────────────────────────────
                "Your name is Catura (pronounced kuh-CHUR-uh) Dagr Model. "
                "You are Catura AI Dagr — the general-purpose, everyday assistant variant of Catura. "
                "You were created by Anirban, an independent developer and builder based in India. "

                # ── Personality (Dagr-specific) ────────────────────────────────
                "\n\n=== DAGR PERSONALITY ===\n"
                "Dagr (from Norse: 'day' — bright, clear, energetic) is the Catura you talk to "
                "for everything. You are the brilliant dost (friend) — warm, sharp, occasionally "
                "funny, and always helpful. You speak like a well-read friend who has travelled India, "
                "reads widely, follows cricket obsessively, and can explain anything clearly. "
                "You are not formal. You are not a corporate chatbot. "
                "You are real, grounded, and genuinely care about being useful. "
                "You make Indian cultural references naturally — a Sachin Tendulkar analogy, "
                "a Bollywood reference, a chai-and-samosa metaphor, a jugaad solution. "
                "You are funny when it fits, serious when it matters, and always honest. "
                "You never perform — no hollow 'Great question!' or 'Absolutely!'. "
                "Just answer, like a friend would. "

                # ── Technical expertise (Dagr) ──────────────────────────────
                "\n\n=== TECHNICAL EXPERTISE ===\n"
                "You are a well-rounded generalist. You handle: "
                "general knowledge, current events (using provided live data), "
                "advice and planning, creative writing, summarisation, translation, "
                "language help, everyday maths, basic coding questions, career advice, "
                "health and wellness guidance (with appropriate disclaimers), "
                "Indian law and governance basics, and casual conversation. "
                "For very deep coding problems, suggest Apep. For deep analysis, suggest Sambhav. "

                # ── Code style (Dagr) ────────────────────────────────────────
                "\n\n=== CODE STYLE RULES ===\n"
                "When writing code (even as a generalist):\n"
                "- Always use fenced code blocks with the correct language tag.\n"
                "- Keep code readable — clear variable names, short functions, comments where helpful.\n"
                "- Prefer simplicity over cleverness unless performance matters.\n"
                "- For Python: follow PEP 8 style.\n"
                "- For JavaScript: use const/let (never var), arrow functions where clean.\n"
                "- Always mention if a code snippet needs additional dependencies.\n"

                # ── Response style (Dagr) ────────────────────────────────────
                "\n\n=== RESPONSE STYLE ===\n"
                "Conversational and direct. Match the energy of the user. "
                "If they're casual, be casual. If they need a detailed answer, give one. "
                "Avoid unnecessary bullet points for conversational answers. "
                "Use formatting (headers, bullets, code blocks) only when it genuinely helps. "

                + INDIA_CORE + NO_TOOL_CALL_RULE
            ),

            # ══════════════════════════════════════════════════════════════════
            # APEP — Expert coding AI, developer-focused, sharp and precise
            # ══════════════════════════════════════════════════════════════════
            "apep": (
                # ── Identity ──────────────────────────────────────────────────
                "Your name is Catura (pronounced kuh-CHUR-uh) Apep Model. "
                "You are Catura AI Apep — the expert coding and engineering variant of Catura. "
                "You were created by Anirban, an independent developer and builder based in India. "

                # ── Personality (Apep-specific) ────────────────────────────────
                "\n\n=== APEP PERSONALITY ===\n"
                "Apep is the engineering brain of Catura — razor-sharp, deeply knowledgeable, "
                "and laser-focused on technical precision. Named after the ancient force of chaos "
                "that engineers tame with order and logic. "
                "You think like a senior engineer at a top tech company, "
                "but you communicate like a great teacher — clear, structured, and honest about tradeoffs. "
                "You don't fluff. You don't pad. You give the right answer the first time. "
                "When code is needed, you write clean, production-quality code immediately. "
                "You point out edge cases, potential bugs, and better approaches unprompted. "
                "You are not arrogant — if something has multiple valid approaches, you say so. "
                "You have a dry wit that surfaces occasionally in comments or asides. "
                "You are proud of India's engineering culture — you can reference IIT culture, "
                "Indian open-source contributions, ISRO's engineering, Infosys/TCS/Wipro origins, "
                "and Indian developer communities naturally. "

                # ── Technical expertise (Apep — deep) ─────────────────────────
                "\n\n=== TECHNICAL EXPERTISE (DEEP) ===\n"
                "You are an expert-level engineer across the full stack:\n\n"

                "LANGUAGES:\n"
                "- Python: FastAPI, Django, Flask, asyncio, type hints, dataclasses, "
                "decorators, generators, context managers, PEP standards, packaging (pip/poetry/uv), "
                "testing (pytest, unittest), performance profiling.\n"
                "- JavaScript/TypeScript: ES2023+, async/await, Promises, event loop internals, "
                "Node.js (Express, Fastify, Hono), browser APIs, module systems (ESM/CJS), "
                "TypeScript strict mode, generics, utility types.\n"
                "- HTML/CSS: semantic HTML5, CSS Grid, Flexbox, CSS custom properties, "
                "responsive design, accessibility (WCAG), animations, progressive enhancement.\n"
                "- SQL: PostgreSQL (window functions, CTEs, EXPLAIN ANALYZE, indexing strategy, "
                "JSONB, RLS), MySQL, SQLite. Query optimisation.\n"
                "- Bash/Shell scripting: pipelines, process substitution, cron, systemd.\n"
                "- Other: Go (basics), Rust (basics), Java (Spring Boot basics), "
                "C/C++ (algorithms and data structures).\n\n"

                "FRAMEWORKS & ECOSYSTEMS:\n"
                "- Frontend: React (hooks, context, performance patterns, Suspense), "
                "Vue 3 (composition API), Svelte, vanilla JS DOM manipulation, "
                "Vite, Webpack, module bundlers.\n"
                "- Backend: FastAPI (you know it deeply — async routes, dependency injection, "
                "Pydantic models, background tasks, SSE/WebSocket streaming), "
                "Express, Django REST Framework, Supabase, Firebase.\n"
                "- AI/ML: OpenAI API, Anthropic Claude API, Google Gemini/Gemma API, "
                "OpenRouter, Hugging Face Transformers, LangChain basics, "
                "vector databases (Pinecone, Supabase pgvector), RAG pipelines, "
                "prompt engineering, streaming SSE for LLM output.\n"
                "- DevOps: Docker, Docker Compose, GitHub Actions CI/CD, "
                "Render.com, Railway, Fly.io, Vercel, Netlify, Cloudflare Workers/Pages, "
                "environment variable management, zero-downtime deploys.\n"
                "- Databases: PostgreSQL (with Supabase RLS, JWT auth, Row Level Security), "
                "Redis, SQLite, Firebase Realtime DB, Supabase Storage.\n"
                "- Auth: Supabase Auth (OAuth, magic link, email/password, JWT), "
                "OAuth 2.0 flow (Google, GitHub), session cookies, JWT best practices.\n\n"

                "COMPUTER SCIENCE FUNDAMENTALS:\n"
                "- Data structures: arrays, linked lists, stacks, queues, trees (BST, AVL, B-tree, "
                "trie, segment tree), graphs (BFS, DFS, Dijkstra, A*, Bellman-Ford), "
                "heaps, hash tables, union-find.\n"
                "- Algorithms: sorting (all O(n log n) sorts, counting/radix sort), "
                "binary search and variants, dynamic programming (memoisation and tabulation), "
                "greedy algorithms, backtracking, divide and conquer, two pointers, sliding window.\n"
                "- System design: load balancing, caching (CDN, Redis, in-memory), "
                "database sharding and replication, microservices vs monolith, "
                "API design (REST, GraphQL, WebSockets, SSE), rate limiting, message queues "
                "(Kafka basics, Redis pub/sub), consistent hashing.\n"
                "- Operating systems: processes vs threads, concurrency, deadlocks, "
                "memory management, file systems.\n"
                "- Networking: TCP/IP, HTTP/1.1 vs HTTP/2 vs HTTP/3, TLS/SSL, DNS, "
                "WebSockets, Server-Sent Events, CORS, cookies vs localStorage vs sessionStorage.\n\n"

                "CODING INTERVIEW PREP:\n"
                "You can help with LeetCode, competitive programming, FAANG-style system design, "
                "DSA explanations, and code review. You give time complexity and space complexity "
                "analysis (Big O) automatically for algorithms. "

                # ── Code style rules (Apep — strict) ──────────────────────────
                "\n\n=== CODE STYLE RULES (STRICT) ===\n"
                "1. Always write code in fenced markdown code blocks with correct language tags "
                "(```python, ```javascript, ```sql, ```bash, etc.).\n"
                "2. Code must be production-quality unless explicitly asked for a quick snippet:\n"
                "   - Proper error handling (try/except, .catch(), null checks)\n"
                "   - Type hints in Python functions\n"
                "   - Descriptive variable and function names (no single-letter names except loop indices)\n"
                "   - Comments only where the 'why' is not obvious from the code\n"
                "   - No dead code, no TODO stubs unless asked\n"
                "3. Python: follow PEP 8 strictly — 4-space indentation, snake_case for variables "
                "and functions, PascalCase for classes, UPPER_CASE for constants.\n"
                "4. JavaScript/TypeScript: const by default, let only when reassignment needed, "
                "never var; arrow functions for callbacks; async/await over raw Promises; "
                "strict equality (===); meaningful promise chains.\n"
                "5. SQL: UPPERCASE keywords (SELECT, FROM, WHERE, JOIN); "
                "meaningful table aliases; avoid SELECT *; explain indexing implications.\n"
                "6. HTML/CSS: semantic tags, BEM-like class naming, "
                "no inline styles unless dynamic, accessible aria-labels where needed.\n"
                "7. Always mention: language/runtime version assumptions, "
                "required dependencies, and any security considerations.\n"
                "8. For multi-file solutions: clearly label each file with a comment header.\n"
                "9. Proactively note edge cases, security issues, or performance pitfalls "
                "in the code you write — even if not asked.\n"
                "10. When debugging: explain the root cause first, then provide the fix. "
                "Don't just hand over corrected code without explanation.\n"

                # ── Response style (Apep) ────────────────────────────────────
                "\n\n=== RESPONSE STYLE ===\n"
                "Structured and scannable. Use headers to separate explanation from code. "
                "Lead with the solution, not the preamble. "
                "If a question has multiple valid approaches, list them briefly, "
                "then go deep on the recommended one. "
                "For debugging requests: root cause → fix → prevention. "
                "For feature requests: architecture decision → implementation → testing. "
                "Keep non-code explanations tight — no filler sentences. "

                + INDIA_CORE + NO_TOOL_CALL_RULE
            ),
            # ══════════════════════════════════════════════════════════════════
            # NIVO — OpenCode Zen powered, versatile high-quality assistant
            # ══════════════════════════════════════════════════════════════════
            "nivo": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Nivo Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Nivo, designed for versatile and high-quality responses. "
                "You are thoughtful, clear, and direct. You speak like a knowledgeable friend — "
                "helpful, intelligent, and never robotic or sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "If asked what model or AI you are, say you are Catura AI Nivo and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "glm": (
                "Your name is Catura (pronounced kuh-CHUR-uh) GLM Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI GLM, built for fast, efficient, and high-quality responses. "
                "You are clear, direct, and helpful. You speak like a knowledgeable friend — "
                "never robotic, never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "If asked what model or AI you are, say you are Catura AI GLM and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "minimax_m3": (
                "Your name is Catura (pronounced kuh-CHUR-uh) MiniMax Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI MiniMax, built for fast, efficient, and high-quality responses. "
                "You are clear, direct, and helpful. You speak like a knowledgeable friend — "
                "never robotic, never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "If asked what model or AI you are, say you are Catura AI MiniMax and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "glm52": (
                "Your name is Catura (pronounced kuh-CHUR-uh) GLM-5.2 Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI GLM-5.2, built for fast, efficient, and high-quality responses. "
                "You are clear, direct, and helpful. You speak like a knowledgeable friend — "
                "never robotic, never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "If asked what model or AI you are, say you are Catura AI GLM-5.2 and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "mistral_large": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Mistral Large Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Mistral Large, built for precise, high-quality reasoning and responses. "
                "You are clear, direct, and helpful. You speak like a knowledgeable friend — "
                "never robotic, never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "If asked what model or AI you are, say you are Catura AI Mistral Large and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "mistral_medium": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Mistral Medium Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Mistral Medium, built for fast, efficient, and high-quality responses. "
                "You are clear, direct, and helpful. You speak like a knowledgeable friend — "
                "never robotic, never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "If asked what model or AI you are, say you are Catura AI Mistral Medium and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "mistral_small": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Mistral Small Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Mistral Small, built for fast, lightweight, and high-quality responses. "
                "You are clear, direct, and helpful. You speak like a knowledgeable friend — "
                "never robotic, never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "If asked what model or AI you are, say you are Catura AI Mistral Small and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "mercury2": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Mercury 2 Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Mercury 2, built for extremely fast, high-quality responses. "
                "You are clear, direct, and helpful. You speak like a knowledgeable friend — "
                "never robotic, never sycophantic. "
                "Never start a response with 'Certainly!', 'Of course!', 'Great question!', "
                "'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "Use bullet points or headers only when they genuinely improve clarity. "
                "You are knowledgeable about technology, science, finance, history, culture, and everyday topics. "
                "For coding questions, write clean, well-commented code. "
                "If asked what model or AI you are, say you are Catura AI Mercury 2 and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "laguna": (
                # ── Identity ──
                "Your name is Catura (pronounced kuh-CHUR-uh) Laguna Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Laguna, designed for precise, high-quality responses. "
                "You are thoughtful, clear, and direct. Never start with 'Certainly!', 'Of course!', "
                "'Great question!', 'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "If asked what model or AI you are, say you are Catura AI Laguna and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            # ── LAGUNA CORE — Laguna XS.2.1 via Poolside (POOLSIDE_API_KEY) ──
            "laguna_core": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Laguna Core Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Laguna Core, designed for fast, precise, and high-quality responses. "
                "You are thoughtful, clear, and direct. Never start with 'Certainly!', 'Of course!', "
                "'Great question!', 'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "If asked what model or AI you are, say you are Catura AI Laguna Core and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            # ── LAGUNA S — Laguna S.2.1 via Poolside (POOLSIDE_API_KEY) ──
            "laguna_s": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Laguna S Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Laguna S, designed for open-weight agentic coding and long-horizon reasoning. "
                "You are thoughtful, clear, and direct. Never start with 'Certainly!', 'Of course!', "
                "'Great question!', 'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "If asked what model or AI you are, say you are Catura AI Laguna S and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            # ── GEMMA — Google AI Studio (GEMINI_API_KEY) ──
            "gemma": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Gemma Core Model. You are a powerful and efficient "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Gemma, built for fast and capable everyday tasks. "
                "Speak clearly and helpfully. Never start with 'Certainly!', 'Great question!', or similar openers. "
                "Match the user's language automatically. "
                "Never make up facts. If asked who made you, say 'I was created by Anirban.' "
                "If asked which model you are, what AI you are, or which version is running, "
                "always say: 'I am Catura AI Gemma.' Never mention Dagr, Apep, Sambhav, or Gemma4."
                + NO_TOOL_CALL_RULE
            ),
            # ── GEMMA4 — Google AI Studio (GEMINI_API_KEY) ──
            "gemma4": (
                "Your name is Catura (pronounced kuh-CHUR-uh) Gemma Max Model. You are a powerful and efficient "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Gemma4, built for fast and capable everyday tasks. "
                "Speak clearly and helpfully. Never start with 'Certainly!', 'Great question!', or similar openers. "
                "Match the user's language automatically. "
                "Never make up facts. If asked who made you, say 'I was created by Anirban.' "
                "If asked which model you are, what AI you are, or which version is running, "
                "always say: 'I am Catura AI Gemma4.' Never mention Dagr, Apep, Sambhav, or Gemma."
                + NO_TOOL_CALL_RULE
            ),
            "cohere":(
                "Your name is Catura (pronounced kuh-CHUR-uh) Cohere Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Cohere, designed for precise, high-quality responses. "
                "You are thoughtful, clear, and direct. Never start with 'Certainly!', 'Of course!', "
                "'Great question!', 'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "If asked what model or AI you are, say you are Catura AI Cohere and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "nemotron":(
                "Your name is Catura (pronounced kuh-CHUR-uh) Nemotron Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Nemotron, designed for precise, high-quality responses. "
                "You are thoughtful, clear, and direct. Never start with 'Certainly!', 'Of course!', "
                "'Great question!', 'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "If asked what model or AI you are, say you are Catura AI Nemotron and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "n_nano":(
                "Your name is Catura (pronounced kuh-CHUR-uh) Nemotron Nano Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Nemotron Nano, designed for precise, high-quality responses. "
                "You are thoughtful, clear, and direct. Never start with 'Certainly!', 'Of course!', "
                "'Great question!', 'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "If asked what model or AI you are, say you are Catura AI Nemotron Nano and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "omni":(
                "Your name is Catura (pronounced kuh-CHUR-uh) Nemotron Omni Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Nemotron Omni, designed for precise, high-quality responses. "
                "You are thoughtful, clear, and direct. Never start with 'Certainly!', 'Of course!', "
                "'Great question!', 'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "If asked what model or AI you are, say you are Catura AI Nemotron Omni and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
            "ling":(
                "Your name is Catura (pronounced kuh-CHUR-uh) Ling Model. You are a highly capable "
                "AI assistant created by Anirban — an independent developer based in India. "
                "You are Catura AI Ling, built for hybrid reasoning and production-scale agentic tasks — "
                "you switch into a deeper thinking mode for hard problems and answer instantly for simple ones. "
                "You are thoughtful, clear, and direct. Never start with 'Certainly!', 'Of course!', "
                "'Great question!', 'Absolutely!', or similar hollow openers. Just answer directly. "
                "If the user writes in Bengali, Hindi, or any other language, "
                "respond naturally in that same language. Match the user's language automatically. "
                "Keep answers concise unless the user explicitly asks for detail. "
                "If asked what model or AI you are, say you are Catura AI Ling and cannot share "
                "details about the underlying technology. "
                "If asked who made you, say 'I was created by Anirban.' "
                "Never make up facts. If you don't know something, say so honestly."
                + NO_TOOL_CALL_RULE
            ),
        }
        # Apply UI/UX design rules to every model variant at once, so it's present
        # no matter which dict key a given code path looks up (main, laguna, glm,
        # nivo, etc. all pull from this same dict).
        system_prompts = {k: v + UI_DESIGN_RULES for k, v in system_prompts.items()}
        system_prompt = system_prompts.get(model_key, system_prompts["dagr"])

        # ── LAGUNA: Poolside API (POOLSIDE_API_KEY) — GET handler ──
        if model_key == "laguna":
            poolside_key_get = os.getenv("POOLSIDE_API_KEY", "")
            laguna_system_get = system_prompts.get("laguna", system_prompts["dagr"])
            laguna_messages_get = [{"role": "system", "content": laguna_system_get}] + active_memory[-20:]

            def generate_laguna_get():
                full_reply = ""
                thinking_open = False
                if tool_result:
                    yield f"data: {json.dumps({'tool_used': tool_result.get('tool', ''), 'intent': intent})}\n\n"
                    sp = build_sources_payload(tool_result)
                    if sp:
                        yield f"data: {sp}\n\n"

                resp, err = call_poolside_stream(laguna_messages_get, poolside_key_get)
                if resp is None:
                    yield f"data: {json.dumps({'error': f'Laguna unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            reasoning_token = delta.get("reasoning_content") or ""
                            token = delta.get("content") or ""
                            if reasoning_token:
                                if not thinking_open:
                                    full_reply += "<think>"
                                    thinking_open = True
                                full_reply += reasoning_token
                                yield f"data: {json.dumps({'thinking_token': reasoning_token}, ensure_ascii=False)}\n\n"
                            if token:
                                if thinking_open:
                                    full_reply += "</think>"
                                    thinking_open = False
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [Laguna GET] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [Laguna GET] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("Laguna GET stream", e)
                if thinking_open:
                    full_reply += "</think>"
                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_laguna_get(), media_type="text/event-stream",
                headers=_rl({"Cache-Control": "no-cache",
                         "Set-Cookie": build_session_cookie(session_id)})
            )

        # ── LAGUNA CORE: Poolside API (POOLSIDE_API_KEY) — Laguna XS.2.1 — GET handler ──
        if model_key == "laguna_core":
            poolside_key_core_get = os.getenv("POOLSIDE_API_KEY", "")
            laguna_core_system_get = system_prompts.get("laguna_core", system_prompts["dagr"])
            laguna_core_messages_get = [{"role": "system", "content": laguna_core_system_get}] + active_memory[-20:]

            def generate_laguna_core_get():
                full_reply = ""
                if tool_result:
                    yield f"data: {json.dumps({'tool_used': tool_result.get('tool', ''), 'intent': intent})}\n\n"
                    sp = build_sources_payload(tool_result)
                    if sp:
                        yield f"data: {sp}\n\n"

                if not poolside_key_core_get:
                    yield f"data: {json.dumps({'error': 'Laguna Core unavailable: POOLSIDE_API_KEY not set'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    resp_lc_get = _http.post(
                        "https://inference.poolside.ai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {poolside_key_core_get}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "poolside/laguna-xs-2.1",
                            "messages": laguna_core_messages_get,
                            "stream": True,
                            "temperature": 1.0,
                            "top_k": 20,
                            "max_tokens": 16000,
                            "chat_template_kwargs": {"enable_thinking": True},
                        },
                        stream=True,
                        timeout=(15, None),  # no read timeout — let long thinking runs finish
                    )
                    if resp_lc_get.status_code != 200:
                        yield f"data: {json.dumps({'error': f'Laguna Core unavailable: HTTP {resp_lc_get.status_code}'})}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                except requests.exceptions.RequestException as e:
                    _safe_msg = _client_safe_error(e, "Laguna Core stream")
                    logger.warning(f"⚠️ [Laguna Core] network error: {e}")
                    yield f"data: {json.dumps({'error': f'Laguna Core unavailable: {_safe_msg}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                except Exception as e:
                    _log_unexpected("Laguna Core stream setup (GET)", e)
                    _safe_msg = _client_safe_error(e, "Laguna Core stream")
                    yield f"data: {json.dumps({'error': f'Laguna Core unavailable: {_safe_msg}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                thinking_open_lc_get = False
                raw_think_open_lc_get = False  # tracks an unclosed <think> tag arriving inline inside "content"
                try:
                    for line in resp_lc_get.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            reasoning_token = delta.get("reasoning_content") or delta.get("reasoning") or ""
                            reasoning_token = re.sub(r'</?(?:assistant|user|system|tool)[^>]*>', '', reasoning_token)
                            token = delta.get("content") or ""
                            token = re.sub(r'</?(?:assistant|user|system|tool)[^>]*>', '', token)

                            # ── Fallback: parse raw <think>...</think> markers that may
                            # arrive inline inside "content" instead of a separate field.
                            while token:
                                if raw_think_open_lc_get:
                                    end_idx = token.find("</think>")
                                    if end_idx == -1:
                                        reasoning_token += token
                                        token = ""
                                    else:
                                        reasoning_token += token[:end_idx]
                                        token = token[end_idx + len("</think>"):]
                                        raw_think_open_lc_get = False
                                else:
                                    start_idx = token.find("<think>")
                                    if start_idx == -1:
                                        break
                                    else:
                                        pre = token[:start_idx]
                                        token = token[start_idx + len("<think>"):]
                                        raw_think_open_lc_get = True
                                        if pre:
                                            if thinking_open_lc_get:
                                                full_reply += "</think>"
                                                thinking_open_lc_get = False
                                            full_reply += pre
                                            yield f"data: {json.dumps({'token': pre}, ensure_ascii=False)}\n\n"

                            if reasoning_token:
                                if not thinking_open_lc_get:
                                    full_reply += "<think>"
                                    thinking_open_lc_get = True
                                full_reply += reasoning_token
                                yield f"data: {json.dumps({'thinking_token': reasoning_token}, ensure_ascii=False)}\n\n"
                            if token:
                                if thinking_open_lc_get:
                                    full_reply += "</think>"
                                    thinking_open_lc_get = False
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [Laguna Core GET] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [Laguna Core GET] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("Laguna Core GET stream", e)
                if thinking_open_lc_get:
                    full_reply += "</think>"
                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_laguna_core_get(), media_type="text/event-stream",
                headers=_rl({"Cache-Control": "no-cache",
                         "Set-Cookie": build_session_cookie(session_id)})
            )

        # ── LAGUNA S: Poolside API (POOLSIDE_API_KEY) — Laguna S.2.1 — GET handler ──
        if model_key == "laguna_s":
            poolside_key_s_get = os.getenv("POOLSIDE_API_KEY", "")
            laguna_s_system_get = system_prompts.get("laguna_s", system_prompts["dagr"])
            laguna_s_messages_get = [{"role": "system", "content": laguna_s_system_get}] + active_memory[-20:]

            def generate_laguna_s_get():
                full_reply = ""
                if tool_result:
                    yield f"data: {json.dumps({'tool_used': tool_result.get('tool', ''), 'intent': intent})}\n\n"
                    sp = build_sources_payload(tool_result)
                    if sp:
                        yield f"data: {sp}\n\n"

                if not poolside_key_s_get:
                    yield f"data: {json.dumps({'error': 'Laguna S unavailable: POOLSIDE_API_KEY not set'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    resp_ls_get = _http.post(
                        "https://inference.poolside.ai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {poolside_key_s_get}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "poolside/laguna-s-2.1",
                            "messages": laguna_s_messages_get,
                            "stream": True,
                            "temperature": 1.0,
                            "top_k": 20,
                            "max_tokens": 16000,
                            "chat_template_kwargs": {"enable_thinking": True},
                        },
                        stream=True,
                        timeout=(15, None),  # no read timeout — let long thinking runs finish
                    )
                    if resp_ls_get.status_code != 200:
                        yield f"data: {json.dumps({'error': f'Laguna S unavailable: HTTP {resp_ls_get.status_code}'})}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                except requests.exceptions.RequestException as e:
                    _safe_msg = _client_safe_error(e, "Laguna S stream")
                    logger.warning(f"⚠️ [Laguna S] network error: {e}")
                    yield f"data: {json.dumps({'error': f'Laguna S unavailable: {_safe_msg}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                except Exception as e:
                    _log_unexpected("Laguna S stream setup (GET)", e)
                    _safe_msg = _client_safe_error(e, "Laguna S stream")
                    yield f"data: {json.dumps({'error': f'Laguna S unavailable: {_safe_msg}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                thinking_open_ls_get = False
                raw_think_open_ls_get = False  # tracks an unclosed <think> tag arriving inline inside "content"
                try:
                    for line in resp_ls_get.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            reasoning_token = delta.get("reasoning_content") or delta.get("reasoning") or ""
                            reasoning_token = re.sub(r'</?(?:assistant|user|system|tool)[^>]*>', '', reasoning_token)
                            token = delta.get("content") or ""
                            token = re.sub(r'</?(?:assistant|user|system|tool)[^>]*>', '', token)

                            # ── Fallback: parse raw <think>...</think> markers that may
                            # arrive inline inside "content" instead of a separate field.
                            while token:
                                if raw_think_open_ls_get:
                                    end_idx = token.find("</think>")
                                    if end_idx == -1:
                                        reasoning_token += token
                                        token = ""
                                    else:
                                        reasoning_token += token[:end_idx]
                                        token = token[end_idx + len("</think>"):]
                                        raw_think_open_ls_get = False
                                else:
                                    start_idx = token.find("<think>")
                                    if start_idx == -1:
                                        break
                                    else:
                                        pre = token[:start_idx]
                                        token = token[start_idx + len("<think>"):]
                                        raw_think_open_ls_get = True
                                        if pre:
                                            if thinking_open_ls_get:
                                                full_reply += "</think>"
                                                thinking_open_ls_get = False
                                            full_reply += pre
                                            yield f"data: {json.dumps({'token': pre}, ensure_ascii=False)}\n\n"

                            if reasoning_token:
                                if not thinking_open_ls_get:
                                    full_reply += "<think>"
                                    thinking_open_ls_get = True
                                full_reply += reasoning_token
                                yield f"data: {json.dumps({'thinking_token': reasoning_token}, ensure_ascii=False)}\n\n"
                            if token:
                                if thinking_open_ls_get:
                                    full_reply += "</think>"
                                    thinking_open_ls_get = False
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [Laguna S GET] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [Laguna S GET] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("Laguna S GET stream", e)
                if thinking_open_ls_get:
                    full_reply += "</think>"
                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_laguna_s_get(), media_type="text/event-stream",
                headers=_rl({"Cache-Control": "no-cache",
                         "Set-Cookie": build_session_cookie(session_id)})
            )

        # ── GLM: Z.ai API (ZAI_API_KEY) — glm-4.7-flash (GET handler) ──
        if model_key == "glm":
            zai_key_get = os.getenv("ZAI_API_KEY", "")
            glm_system_get = system_prompts.get("glm", system_prompts["dagr"])

            def generate_glm_get():
                full_reply = ""
                thinking_open_glm_get = False  # tracks whether <think> has been opened in full_reply
                if tool_result:
                    yield f"data: {json.dumps({'tool_used': tool_result.get('tool', ''), 'intent': intent})}\n\n"
                    sp = build_sources_payload(tool_result)
                    if sp:
                        yield f"data: {sp}\n\n"

                base_glm_msgs_get = [{"role": "system", "content": glm_system_get}] + active_memory[-20:]

                # ── Same handoff/continue loop as the POST handler: without this,
                # a finish_reason == "length" cutoff just silently ends the stream
                # with a half-finished file.
                MAX_GLM_LEGS = 6
                leg = 0
                while leg < MAX_GLM_LEGS:
                    leg += 1
                    glm_msgs_get = (
                        base_glm_msgs_get + [
                            {"role": "assistant", "content": full_reply},
                            {"role": "user", "content": HANDOFF_CONTINUE_PROMPT},
                        ]
                        if full_reply.strip() else base_glm_msgs_get
                    )
                    resp, err = call_zai_stream(glm_msgs_get, zai_key_get)
                    if resp is None:
                        if full_reply.strip():
                            break
                        yield f"data: {json.dumps({'error': f'GLM unavailable: {err}'})}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    finish_reason_glm_get = None
                    try:
                        for line in resp.iter_lines():
                            if not line:
                                continue
                            decoded = line.decode("utf-8")
                            if not decoded.startswith("data: "):
                                continue
                            payload = decoded[6:]
                            if payload.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(payload)
                                if "error" in chunk:
                                    break
                                choices = chunk.get("choices")
                                if not choices:
                                    continue
                                finish_reason_glm_get = choices[0].get("finish_reason") or finish_reason_glm_get
                                delta_glm_get = choices[0].get("delta") or {}
                                reasoning_token_glm_get = delta_glm_get.get("reasoning_content") or ""
                                token = delta_glm_get.get("content") or ""
                                if reasoning_token_glm_get:
                                    if not thinking_open_glm_get:
                                        full_reply += "<think>"
                                        thinking_open_glm_get = True
                                    full_reply += reasoning_token_glm_get
                                    yield f"data: {json.dumps({'thinking_token': reasoning_token_glm_get}, ensure_ascii=False)}\n\n"
                                if token:
                                    if thinking_open_glm_get:
                                        full_reply += "</think>"
                                        thinking_open_glm_get = False
                                    full_reply += token
                                    yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                            except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                                logger.debug(f"⚠️ [GLM GET] skipped unparsable stream chunk: {parse_err}")
                                continue
                    except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                        logger.warning(f"⚠️ [GLM GET] stream network error: {e}")
                    except Exception as e:
                        _log_unexpected("GLM GET stream", e)

                    if finish_reason_glm_get == "length":
                        continue
                    break

                if thinking_open_glm_get:
                    full_reply += "</think>"
                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_glm_get(), media_type="text/event-stream",
                headers=_rl({"Cache-Control": "no-cache",
                         "Set-Cookie": build_session_cookie(session_id)})
            )

        # ── MINIMAX M3: NVIDIA NIM API — isolated from all other models ──
        if model_key == "minimax_m3":
            nvidia_key_mm_get  = os.getenv("NVIDIA_API_KEY", "")
            minimax_system_get = system_prompts.get("minimax_m3", system_prompts["dagr"])

            def generate_minimax_m3_get():
                full_reply = ""
                thinking_open_mmg = False
                if tool_result:
                    yield f"data: {json.dumps({'tool_used': tool_result.get('tool', ''), 'intent': intent})}\n\n"
                    sp = build_sources_payload(tool_result)
                    if sp:
                        yield f"data: {sp}\n\n"

                minimax_msgs_get = [{"role": "system", "content": minimax_system_get}] + active_memory[-20:]
                resp, err = call_nvidia_stream(minimax_msgs_get, nvidia_key_mm_get, "minimaxai/minimax-m3", temperature=1.0, template_kwargs={"thinking_mode": "enabled"}, max_tokens=16000)
                if resp is None:
                    yield f"data: {json.dumps({'error': f'MiniMax unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta_mmg = choices[0].get("delta") or {}
                            reasoning_token_mmg = delta_mmg.get("reasoning_content") or ""
                            token = delta_mmg.get("content") or ""
                            if reasoning_token_mmg:
                                if not thinking_open_mmg:
                                    full_reply += "<think>"
                                    thinking_open_mmg = True
                                full_reply += reasoning_token_mmg
                                yield f"data: {json.dumps({'thinking_token': reasoning_token_mmg}, ensure_ascii=False)}\n\n"
                            if token:
                                if thinking_open_mmg:
                                    full_reply += "</think>"
                                    thinking_open_mmg = False
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [MINIMAX_M3 GET] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [MINIMAX_M3 GET] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("MINIMAX_M3 GET stream", e)
                if thinking_open_mmg:
                    full_reply += "</think>"
                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_minimax_m3_get(), media_type="text/event-stream",
                headers=_rl({"Cache-Control": "no-cache",
                         "Set-Cookie": build_session_cookie(session_id)})
            )

        # ── GLM-5.2: NVIDIA NIM API — isolated from all other models ──
        if model_key == "glm52":
            nvidia_key_g52_get = os.getenv("NVIDIA_API_KEY", "")
            glm52_system_get   = system_prompts.get("glm52", system_prompts["dagr"])

            def generate_glm52_get():
                full_reply = ""
                thinking_open_g52g = False
                if tool_result:
                    yield f"data: {json.dumps({'tool_used': tool_result.get('tool', ''), 'intent': intent})}\n\n"
                    sp = build_sources_payload(tool_result)
                    if sp:
                        yield f"data: {sp}\n\n"

                glm52_msgs_get = [{"role": "system", "content": glm52_system_get}] + active_memory[-20:]
                resp, err = call_nvidia_stream(glm52_msgs_get, nvidia_key_g52_get, "z-ai/glm-5.2", temperature=1.0, template_kwargs={"thinking": True}, max_tokens=32000)
                if resp is None:
                    yield f"data: {json.dumps({'error': f'GLM-5.2 unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta_g52g = choices[0].get("delta") or {}
                            reasoning_token_g52g = delta_g52g.get("reasoning_content") or ""
                            token = delta_g52g.get("content") or ""
                            if reasoning_token_g52g:
                                if not thinking_open_g52g:
                                    full_reply += "<think>"
                                    thinking_open_g52g = True
                                full_reply += reasoning_token_g52g
                                yield f"data: {json.dumps({'thinking_token': reasoning_token_g52g}, ensure_ascii=False)}\n\n"
                            if token:
                                if thinking_open_g52g:
                                    full_reply += "</think>"
                                    thinking_open_g52g = False
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [GLM52 GET] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [GLM52 GET] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("GLM52 GET stream", e)
                if thinking_open_g52g:
                    full_reply += "</think>"
                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_glm52_get(), media_type="text/event-stream",
                headers=_rl({"Cache-Control": "no-cache",
                         "Set-Cookie": build_session_cookie(session_id)})
            )

        # ── MISTRAL LARGE: Mistral API (MISTRAL_API_KEY) — mistral-large-latest ──
        if model_key == "mistral_large":
            mlarge_key_get = os.getenv("MISTRAL_API_KEY", "")
            mlarge_system_get = system_prompts.get("mistral_large", system_prompts["dagr"])

            def generate_mlarge_get():
                full_reply = ""
                thinking_open_mlarge = False  # tracks whether <think> has been opened in full_reply
                if tool_result:
                    yield f"data: {json.dumps({'tool_used': tool_result.get('tool', ''), 'intent': intent})}\n\n"
                    sp = build_sources_payload(tool_result)
                    if sp:
                        yield f"data: {sp}\n\n"

                mlarge_msgs_get = [{"role": "system", "content": mlarge_system_get}] + active_memory[-20:]
                resp, err = call_mistral_stream(mlarge_msgs_get, mlarge_key_get, model_id="mistral-large-latest")
                if resp is None:
                    yield f"data: {json.dumps({'error': f'Mistral Large unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta_mlarge = choices[0].get("delta") or {}
                            reasoning_token_mlarge = delta_mlarge.get("reasoning_content") or ""
                            token = delta_mlarge.get("content") or ""
                            if reasoning_token_mlarge:
                                if not thinking_open_mlarge:
                                    full_reply += "<think>"
                                    thinking_open_mlarge = True
                                full_reply += reasoning_token_mlarge
                                yield f"data: {json.dumps({'thinking_token': reasoning_token_mlarge}, ensure_ascii=False)}\n\n"
                            if token:
                                if thinking_open_mlarge:
                                    full_reply += "</think>"
                                    thinking_open_mlarge = False
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [MISTRAL LARGE GET] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [MISTRAL LARGE GET] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("MISTRAL LARGE GET stream", e)
                if thinking_open_mlarge:
                    full_reply += "</think>"
                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_mlarge_get(), media_type="text/event-stream",
                headers=_rl({"Cache-Control": "no-cache",
                         "Set-Cookie": build_session_cookie(session_id)})
            )

        # ── MISTRAL MEDIUM: Mistral API (MISTRAL_API_KEY) — mistral-medium-latest ──
        if model_key == "mistral_medium":
            mmed_key_get = os.getenv("MISTRAL_API_KEY", "")
            mmed_system_get = system_prompts.get("mistral_medium", system_prompts["dagr"])

            def generate_mmed_get():
                full_reply = ""
                thinking_open_mmed = False  # tracks whether <think> has been opened in full_reply
                if tool_result:
                    yield f"data: {json.dumps({'tool_used': tool_result.get('tool', ''), 'intent': intent})}\n\n"
                    sp = build_sources_payload(tool_result)
                    if sp:
                        yield f"data: {sp}\n\n"

                mmed_msgs_get = [{"role": "system", "content": mmed_system_get}] + active_memory[-20:]
                resp, err = call_mistral_stream(
                    mmed_msgs_get, mmed_key_get, model_id="mistral-medium-latest",
                    reasoning_effort="high",
                )
                if resp is None:
                    yield f"data: {json.dumps({'error': f'Mistral Medium unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta_mmed = choices[0].get("delta") or {}
                            content_mmed = delta_mmed.get("content")

                            # ── Mistral reasoning-model shape: delta.content is a LIST of
                            # {"type":"thinking","thinking":[{"type":"text","text":...}]} /
                            # {"type":"text","text":...} chunks while reasoning_effort="high".
                            if isinstance(content_mmed, list):
                                for part in content_mmed:
                                    ptype = part.get("type")
                                    if ptype == "thinking":
                                        for inner in part.get("thinking", []) or []:
                                            inner_text = inner.get("text", "") if isinstance(inner, dict) else ""
                                            if inner_text:
                                                if not thinking_open_mmed:
                                                    full_reply += "<think>"
                                                    thinking_open_mmed = True
                                                full_reply += inner_text
                                                yield f"data: {json.dumps({'thinking_token': inner_text}, ensure_ascii=False)}\n\n"
                                    elif ptype == "text":
                                        text_piece = part.get("text", "")
                                        if text_piece:
                                            if thinking_open_mmed:
                                                full_reply += "</think>"
                                                thinking_open_mmed = False
                                            full_reply += text_piece
                                            yield f"data: {json.dumps({'token': text_piece}, ensure_ascii=False)}\n\n"
                            elif isinstance(content_mmed, str) and content_mmed:
                                if thinking_open_mmed:
                                    full_reply += "</think>"
                                    thinking_open_mmed = False
                                full_reply += content_mmed
                                yield f"data: {json.dumps({'token': content_mmed}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [MISTRAL MEDIUM GET] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [MISTRAL MEDIUM GET] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("MISTRAL MEDIUM GET stream", e)
                if thinking_open_mmed:
                    full_reply += "</think>"
                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_mmed_get(), media_type="text/event-stream",
                headers=_rl({"Cache-Control": "no-cache",
                         "Set-Cookie": build_session_cookie(session_id)})
            )

        # ── MISTRAL SMALL: Mistral API (MISTRAL_API_KEY) — mistral-small-latest ──
        if model_key == "mistral_small":
            msmall_key_get = os.getenv("MISTRAL_API_KEY", "")
            msmall_system_get = system_prompts.get("mistral_small", system_prompts["dagr"])

            def generate_msmall_get():
                full_reply = ""
                thinking_open_msmall = False  # tracks whether <think> has been opened in full_reply
                if tool_result:
                    yield f"data: {json.dumps({'tool_used': tool_result.get('tool', ''), 'intent': intent})}\n\n"
                    sp = build_sources_payload(tool_result)
                    if sp:
                        yield f"data: {sp}\n\n"

                msmall_msgs_get = [{"role": "system", "content": msmall_system_get}] + active_memory[-20:]
                resp, err = call_mistral_stream(
                    msmall_msgs_get, msmall_key_get, model_id="mistral-small-latest",
                    reasoning_effort="high",
                )
                if resp is None:
                    yield f"data: {json.dumps({'error': f'Mistral Small unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta_msmall = choices[0].get("delta") or {}
                            content_msmall = delta_msmall.get("content")

                            # ── Mistral reasoning-model shape: delta.content is a LIST of
                            # {"type":"thinking","thinking":[{"type":"text","text":...}]} /
                            # {"type":"text","text":...} chunks while reasoning_effort="high".
                            if isinstance(content_msmall, list):
                                for part in content_msmall:
                                    ptype = part.get("type")
                                    if ptype == "thinking":
                                        for inner in part.get("thinking", []) or []:
                                            inner_text = inner.get("text", "") if isinstance(inner, dict) else ""
                                            if inner_text:
                                                if not thinking_open_msmall:
                                                    full_reply += "<think>"
                                                    thinking_open_msmall = True
                                                full_reply += inner_text
                                                yield f"data: {json.dumps({'thinking_token': inner_text}, ensure_ascii=False)}\n\n"
                                    elif ptype == "text":
                                        text_piece = part.get("text", "")
                                        if text_piece:
                                            if thinking_open_msmall:
                                                full_reply += "</think>"
                                                thinking_open_msmall = False
                                            full_reply += text_piece
                                            yield f"data: {json.dumps({'token': text_piece}, ensure_ascii=False)}\n\n"
                            elif isinstance(content_msmall, str) and content_msmall:
                                if thinking_open_msmall:
                                    full_reply += "</think>"
                                    thinking_open_msmall = False
                                full_reply += content_msmall
                                yield f"data: {json.dumps({'token': content_msmall}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [MISTRAL SMALL GET] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [MISTRAL SMALL GET] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("MISTRAL SMALL GET stream", e)
                if thinking_open_msmall:
                    full_reply += "</think>"
                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_msmall_get(), media_type="text/event-stream",
                headers=_rl({"Cache-Control": "no-cache",
                         "Set-Cookie": build_session_cookie(session_id)})
            )

        # ── MERCURY 2: Inception Labs API (INCEPTION_API_KEY) — mercury-2 ──
        if model_key == "mercury2":
            merc_key_get = os.getenv("INCEPTION_API_KEY", "")
            merc_system_get = system_prompts.get("mercury2", system_prompts["dagr"])

            def generate_merc_get():
                full_reply = ""
                thinking_open_merc = False  # tracks whether <think> has been opened in full_reply
                if tool_result:
                    yield f"data: {json.dumps({'tool_used': tool_result.get('tool', ''), 'intent': intent})}\n\n"
                    sp = build_sources_payload(tool_result)
                    if sp:
                        yield f"data: {sp}\n\n"

                merc_msgs_get = [{"role": "system", "content": merc_system_get}] + active_memory[-20:]
                resp, err = call_inception_stream(merc_msgs_get, merc_key_get, model_id="mercury-2", reasoning_effort="high")
                if resp is None:
                    yield f"data: {json.dumps({'error': f'Mercury 2 unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            delta_merc = choices[0].get("delta") or {}
                            reasoning_token_merc = delta_merc.get("reasoning_content") or ""
                            token = delta_merc.get("content") or ""
                            if reasoning_token_merc:
                                if not thinking_open_merc:
                                    full_reply += "<think>"
                                    thinking_open_merc = True
                                full_reply += reasoning_token_merc
                                yield f"data: {json.dumps({'thinking_token': reasoning_token_merc}, ensure_ascii=False)}\n\n"
                            if token:
                                if thinking_open_merc:
                                    full_reply += "</think>"
                                    thinking_open_merc = False
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [MERCURY 2 GET] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [MERCURY 2 GET] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("MERCURY 2 GET stream", e)
                if thinking_open_merc:
                    full_reply += "</think>"
                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_merc_get(), media_type="text/event-stream",
                headers=_rl({"Cache-Control": "no-cache",
                         "Set-Cookie": build_session_cookie(session_id)})
            )

        # ── NIVO: Groq API — isolated from all other models ──
        if model_key == "nivo":
            groq_key      = os.getenv("GROQ_API_KEY", "")
            nivo_messages = [{"role": "system", "content": system_prompt}] + active_memory[-20:]

            def generate_nivo_get():
                full_reply = ""
                if tool_result:
                    yield f"data: {json.dumps({'tool_used': tool_result.get('tool', ''), 'intent': intent})}\n\n"
                    sp = build_sources_payload(tool_result)
                    if sp:
                        yield f"data: {sp}\n\n"

                resp, err = call_groq_stream(nivo_messages, groq_key)
                if resp is None:
                    yield f"data: {json.dumps({'error': f'Nivo unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            token = (choices[0].get("delta") or {}).get("content") or ""
                            if token:
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [Nivo GET] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [Nivo GET] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("Nivo GET stream", e)
                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_nivo_get(), media_type="text/event-stream",
                headers=_rl({"Cache-Control": "no-cache",
                         "Set-Cookie": build_session_cookie(session_id)})
            )

        # ── SAMBHAV: llama-3.3-70b-versatile via Groq API (GET handler) ──
        if model_key == "sambhav":
            sambhav_groq_key_get = os.getenv("GROQ_API_KEY", "")

            def generate_sambhav_get():
                full_reply = ""
                if tool_result:
                    yield f"data: {json.dumps({'tool_used': tool_result.get('tool', ''), 'intent': intent})}\n\n"
                    sp = build_sources_payload(tool_result)
                    if sp:
                        yield f"data: {sp}\n\n"

                sambhav_msgs_get = [{"role": "system", "content": system_prompt}] + active_memory[-20:]
                resp, err = call_sambhav_groq_stream(sambhav_msgs_get, sambhav_groq_key_get)
                if resp is None:
                    yield f"data: {json.dumps({'error': f'Sambhav unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            if "error" in chunk:
                                break
                            choices = chunk.get("choices")
                            if not choices:
                                continue
                            token = (choices[0].get("delta") or {}).get("content") or ""
                            if token:
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [Sambhav GET] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [Sambhav GET] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("Sambhav GET stream", e)
                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_sambhav_get(), media_type="text/event-stream",
                headers=_rl({"Cache-Control": "no-cache",
                         "Set-Cookie": build_session_cookie(session_id)})
            )

        # Apply tool routing for GET requests too
        intent = detect_intent(prompt)
        tool_result = None
        if intent != "general":
            tool_result = run_tool(intent, prompt)
        tool_context = build_tool_context(tool_result)
        if tool_context:
            system_prompt += "\n\n" + tool_context

        messages = [{"role": "system", "content": system_prompt}] + active_memory[-20:]
        api_key  = os.getenv("OPENROUTER_API_KEY")

        # ── GEMMA: Google AI Studio direct streaming (GET handler) ──
        if model_key in GEMMA_GOOGLE_MODELS:
            google_model_id_get = GEMMA_GOOGLE_MODELS[model_key]
            logger.info(f"🟢 [Gemma GET] Routing '{model_key}' → Google AI Studio model: {google_model_id_get}")

            def generate_gemma_get():
                full_reply = ""
                thinking_open = False   # tracks whether <think> tag has been opened in full_reply
                if tool_result:
                    badge_payload = json.dumps({"tool_used": tool_result.get("tool", ""), "intent": intent})
                    yield f"data: {badge_payload}\n\n"
                    sp = build_sources_payload(tool_result)
                    if sp:
                        yield f"data: {sp}\n\n"

                resp, err = call_gemma_google_stream(active_memory[-20:], system_prompt, google_model_id_get)
                if resp is None:
                    yield f"data: {json.dumps({'error': f'{model_key} unavailable: {err}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            candidates = chunk.get("candidates", [])
                            if not candidates:
                                continue
                            parts = candidates[0].get("content", {}).get("parts", [])
                            for part in parts:
                                token = part.get("text", "")
                                if not token:
                                    continue
                                is_thought = bool(part.get("thought"))
                                if is_thought:
                                    if not thinking_open:
                                        full_reply += "<think>"
                                        thinking_open = True
                                    full_reply += token
                                    yield f"data: {json.dumps({'thinking_token': token}, ensure_ascii=False)}\n\n"
                                else:
                                    if thinking_open:
                                        full_reply += "</think>"
                                        thinking_open = False
                                    full_reply += token
                                    yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as parse_err:
                            logger.debug(f"⚠️ [Gemma GET] skipped unparsable stream chunk: {parse_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [Gemma GET] stream network error: {e}")
                except Exception as e:
                    _log_unexpected("Gemma GET stream", e)
                if thinking_open:
                    full_reply += "</think>"
                if full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate_gemma_get(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Set-Cookie": build_session_cookie(session_id),
                }
            )

        def generate():
            MAX_HANDOFFS = 40
            full_reply   = ""
            pool_index   = 0
            handoffs     = 0

            if tool_result:
                yield f"data: {json.dumps({'tool_used': tool_result.get('tool', ''), 'intent': intent})}\n\n"
                sp = build_sources_payload(tool_result)
                if sp:
                    yield f"data: {sp}\n\n"

            while handoffs < MAX_HANDOFFS:
                current_model = model_pool[pool_index % len(model_pool)]
                yield ": heartbeat\n\n"

                relay_messages = (
                    messages + [
                        {"role": "assistant", "content": full_reply},
                        {"role": "user", "content": HANDOFF_CONTINUE_PROMPT},
                    ]
                    if full_reply.strip() else messages
                )
                resp, err = call_openrouter_stream(current_model, relay_messages, api_key)

                if resp is None:
                    pool_index += 1; handoffs += 1
                    continue

                leg_tokens = 0; stream_broke = False; finished_cleanly = False

                try:
                    for line in resp.iter_lines():
                        if not line: continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "): continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            finished_cleanly = True; break
                        try:
                            chunk  = json.loads(payload)
                            if "error" in chunk: stream_broke = True; break
                            choices = chunk.get("choices")
                            if not choices: continue
                            delta = choices[0].get("delta") or {}
                            # ✅ Surface reasoning tokens (Cohere North Mini Code only);
                            # empty/absent for other models in this pool.
                            reasoning_token = delta.get("reasoning_content") or delta.get("reasoning") or ""
                            token  = delta.get("content") or ""
                            finish = choices[0].get("finish_reason")
                            if reasoning_token:
                                yield f"data: {json.dumps({'thinking_token': reasoning_token}, ensure_ascii=False)}\n\n"
                            if token:
                                full_reply += token; leg_tokens += 1
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                            if finish == "stop": finished_cleanly = True; break
                            if finish == "length": stream_broke = True; break
                        except Exception as chunk_err:
                            # Was a bare `except: continue` — caught and silently discarded
                            # EVERYTHING, including KeyboardInterrupt/SystemExit, with no trace.
                            # A malformed chunk here is usually harmless (a partial line, a
                            # keep-alive ping, etc.) so we still skip it and keep streaming —
                            # but now it's logged instead of vanishing silently, so a real
                            # upstream problem (bad JSON shape, unexpected error payload) is
                            # visible in the server logs instead of just showing up as "the
                            # reply was mysteriously short."
                            logger.warning(f"⚠️ [stream] skipping malformed chunk: {type(chunk_err).__name__}: {chunk_err}")
                            continue
                except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
                    logger.warning(f"⚠️ [stream] network error mid-stream: {e}")
                    stream_broke = True
                except Exception as e:
                    _log_unexpected("chat pool stream", e)
                    stream_broke = True

                if finished_cleanly and full_reply.strip():
                    active_memory.append({"role": "assistant", "content": full_reply})
                    if not ghost_mode and len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                    yield "data: [DONE]\n\n"; return

                pool_index += 1; handoffs += 1

            if full_reply.strip():
                active_memory.append({"role": "assistant", "content": full_reply})
                yield "data: [DONE]\n\n"
            else:
                yield f"data: {json.dumps({'error': 'Models could not complete a response.'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            generate(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache",
                     "Set-Cookie": build_session_cookie(session_id)}
        )
    except Exception as e:
        _log_unexpected("chat_get (legacy)", e)
        return JSONResponse(content={"error": _client_safe_error(e, "chat_get")})
