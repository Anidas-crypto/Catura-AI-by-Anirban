from fastapi import FastAPI, Request, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
import uuid
import json
import re
from datetime import datetime
from supabase import create_client, Client
import base64
import io
from PIL import Image
from duckduckgo_search import DDGS

app = FastAPI()

# ✅ CORS MIDDLEWARE
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ MOUNT STATIC FILES
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 🧠 In-memory session store
user_memory = {}

# ✅ SUPABASE CLIENT
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ✅ RENDER APP URL
APP_URL = os.getenv("APP_URL", "https://my-ai-assistant-9bbd.onrender.com/")

# ✅ API KEYS (set these as environment variables)
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")       # https://openweathermap.org/api (free)
NEWSDATA_API_KEY    = os.getenv("NEWSDATA_API_KEY", "")           # https://newsdata.io (free)
ALPHAVANTAGE_KEY    = os.getenv("ALPHAVANTAGE_API_KEY", "")       # https://www.alphavantage.co (free)
CRICAPI_KEY         = os.getenv("CRICAPI_KEY", "")                # https://www.cricapi.com (free)


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
    p = os.path.join(BASE_DIR, "auth.html")
    if not os.path.isfile(p): return JSONResponse({"error": "auth.html not found"}, status_code=404)
    return FileResponse(p, media_type="text/html")

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

@app.get("/ping")
def ping():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "version": "26.4.30"}

@app.get("/google5869a60ba00ea65a.html")
def google_verify():
    p = os.path.join(BASE_DIR, "google5869a60ba00ea65a.html")
    if not os.path.isfile(p): return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p, media_type="text/html")

@app.get("/health")
def health_check():
    return {"status": "healthy", "version": "26.4.30", "timestamp": datetime.utcnow().isoformat()}


# ============================================================
# ✅ INTENT DETECTOR — keyword-based (fast, zero-latency)
# Returns: weather | finance | sports | news | web_search | general
# ============================================================
def detect_intent(text: str) -> str:
    """
    Keyword-based intent classifier.
    Priority order: weather > finance > sports > news > web_search > general
    """
    lower = text.lower()

    # ── WEATHER ────────────────────────────────────────────────────────────
    weather_patterns = [
        r'\bweather\b', r'\btemperature\b', r'\bhumidity\b', r'\brain\b',
        r'\bsnow\b', r'\bwind\b', r'\bforecast\b', r'\bclimate\b',
        r'\bsunny\b', r'\bcloudy\b', r'\bhot\b.*\boutside\b',
        r'\bcold\b.*\boutside\b', r'\bwill it rain\b', r'\bfeels like\b',
        r'\bdegrees (celsius|fahrenheit|today)\b',
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
    ]
    if any(re.search(p, lower) for p in sports_patterns):
        return "sports"

    # ── NEWS ───────────────────────────────────────────────────────────────
    news_patterns = [
        r'\bnews\b', r'\bheadlines\b', r'\bbreaking\b', r'\blatest (news|update|development)\b',
        r"\bwhat('s| is) happening\b", r'\bwhat happened\b', r'\bcurrent events\b',
        r"\btoday'?s news\b", r'\brecent news\b', r'\bannouncement\b',
    ]
    if any(re.search(p, lower) for p in news_patterns):
        return "news"

    # ── WEB SEARCH (general real-time lookup) ──────────────────────────────
    web_search_patterns = [
        r'\blatest\b', r'\bcurrently?\b', r'\bright now\b', r'\btoday\b',
        r'\brecently?\b', r'\bwho is\b', r'\bwhen (is|was|did)\b',
        r'\bwhere is\b', r'\bprice of\b', r'\bhow much (is|does|did)\b',
        r'\bwhat is the (current|latest|new)\b', r'\bfind (me|out|information)\b',
        r'\bsearch (for|about)\b', r'\blook up\b', r'\b\d{4}\b',
    ]
    if any(re.search(p, lower) for p in web_search_patterns):
        return "web_search"

    return "general"


# ============================================================
# ✅ TOOL: WEATHER
# Uses OpenWeatherMap free API
# ============================================================
def tool_weather(prompt: str) -> dict:
    """
    Extract city from prompt, call OpenWeatherMap, return formatted data.
    Falls back to DuckDuckGo search if API key not set.
    """
    print(f"🌤️ [TOOL] weather | prompt: {prompt[:80]}")

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
        print("⚠️ [TOOL] weather — no API key, falling back to web search")
        return tool_web_search(f"current weather {city} today temperature")

    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHER_API_KEY}&units=metric"
        resp = requests.get(url, timeout=8)
        data = resp.json()

        if resp.status_code != 200 or data.get("cod") != 200:
            print(f"⚠️ [TOOL] weather API error: {data.get('message')}")
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
        print(f"✅ [TOOL] weather success: {result}")
        return result

    except Exception as e:
        print(f"❌ [TOOL] weather exception: {e}")
        return tool_web_search(f"current weather {city} today")


# ============================================================
# ✅ TOOL: FINANCE
# Uses Alpha Vantage (stock) or DuckDuckGo fallback
# ============================================================
def tool_finance(prompt: str) -> dict:
    """
    Extract stock ticker or crypto symbol, fetch live price.
    """
    print(f"💹 [TOOL] finance | prompt: {prompt[:80]}")

    # Indian company → NSE ticker mapping
    indian_tickers = {
        "tata steel": "TATASTEEL.BSE", "tata": "TCS.BSE",
        "reliance": "RELIANCE.BSE", "infosys": "INFY",
        "wipro": "WIT", "hdfc": "HDB", "icici": "IBN",
        "bajaj": "BAJFINANCE.BSE", "sbi": "SBIN.BSE",
        "nifty": "NSEI", "sensex": "BSESN",
        "bitcoin": "BTC", "ethereum": "ETH", "crypto": "BTC",
    }

    ticker = None
    lower = prompt.lower()
    for company, sym in indian_tickers.items():
        if company in lower:
            ticker = sym
            break

    # Also check for explicit ticker symbols (e.g., RELIANCE, INFY)
    if not ticker:
        m = re.search(r'\b([A-Z]{2,6})\b', prompt)
        if m:
            ticker = m.group(1)

    if not ticker:
        # Generic finance search
        return tool_web_search(prompt + " stock price today")

    if not ALPHAVANTAGE_KEY:
        print("⚠️ [TOOL] finance — no API key, falling back to web search")
        return tool_web_search(f"{ticker} stock price today")

    try:
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={ticker}&apikey={ALPHAVANTAGE_KEY}"
        resp = requests.get(url, timeout=8)
        data = resp.json()
        quote = data.get("Global Quote", {})

        if not quote:
            return tool_web_search(f"{ticker} stock price today live")

        result = {
            "tool": "finance",
            "symbol": quote.get("01. symbol", ticker),
            "price": quote.get("05. price", "N/A"),
            "change": quote.get("09. change", "N/A"),
            "change_pct": quote.get("10. change percent", "N/A"),
            "volume": quote.get("06. volume", "N/A"),
            "latest_trading_day": quote.get("07. latest trading day", "N/A"),
            "previous_close": quote.get("08. previous close", "N/A"),
        }
        print(f"✅ [TOOL] finance success: {result}")
        return result

    except Exception as e:
        print(f"❌ [TOOL] finance exception: {e}")
        return tool_web_search(f"{ticker} stock price today")


# ============================================================
# ✅ TOOL: NEWS
# Uses NewsData.io free tier (100 req/day) or DuckDuckGo fallback
# ============================================================
def tool_news(prompt: str) -> dict:
    """
    Fetch top news headlines. Extracts topic if present.
    """
    print(f"📰 [TOOL] news | prompt: {prompt[:80]}")

    # Extract topic
    topic_match = re.search(
        r'news (?:about|on|regarding|of) (.+?)(?:\?|$)', prompt, re.IGNORECASE
    )
    topic = topic_match.group(1).strip() if topic_match else None

    if not NEWSDATA_API_KEY:
        print("⚠️ [TOOL] news — no API key, falling back to web search")
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

        resp = requests.get("https://newsdata.io/api/1/news", params=params, timeout=8)
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
        print(f"✅ [TOOL] news success: {len(headlines)} articles")
        return result

    except Exception as e:
        print(f"❌ [TOOL] news exception: {e}")
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
    print(f"🏏 [TOOL] sports | prompt: {prompt[:80]}")

    lower = prompt.lower()
    is_cricket = any(k in lower for k in ["cricket", "ipl", "test match", "odi", "t20", "bcci"])

    if is_cricket and CRICAPI_KEY:
        try:
            resp = requests.get(
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
                print(f"✅ [TOOL] sports (cricket) success: {len(live_matches)} matches")
                return result
        except Exception as e:
            print(f"❌ [TOOL] cricket API exception: {e}")

    # Fallback: DuckDuckGo search for any sport
    sport_kw = "cricket live score" if is_cricket else "live sports scores today"
    return tool_web_search(f"{sport_kw} {prompt}")


# ============================================================
# ✅ TOOL: WEB SEARCH (DuckDuckGo)
# ============================================================
def tool_web_search(query: str, max_results: int = 5) -> dict:
    """
    DuckDuckGo search with structured result output.
    """
    print(f"🔍 [TOOL] web_search | query: {query[:80]}")
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))

        results = [
            {
                "title": r.get("title", ""),
                "body": r.get("body", "")[:300],
                "href": r.get("href", ""),
            }
            for r in raw if r.get("title")
        ]
        print(f"✅ [TOOL] web_search success: {len(results)} results")
        return {"tool": "web_search", "query": query, "results": results}
    except Exception as e:
        print(f"❌ [TOOL] web_search exception: {e}")
        return {"tool": "web_search", "query": query, "results": [], "error": str(e)}


# ============================================================
# ✅ TOOL ROUTER — dispatches to the correct tool
# ============================================================
def run_tool(intent: str, prompt: str) -> dict | None:
    """
    Routes to the appropriate tool based on detected intent.
    Returns tool output dict, or None for 'general' (no tool needed).
    """
    print(f"🗺️ [ROUTER] intent={intent}")
    if intent == "weather":     return tool_weather(prompt)
    if intent == "finance":     return tool_finance(prompt)
    if intent == "news":        return tool_news(prompt)
    if intent == "sports":      return tool_sports(prompt)
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

    if tool == "weather":
        lines += [
            f"City: {tool_result['city']}",
            f"Temperature: {tool_result['temperature']}°C (feels like {tool_result['feels_like']}°C)",
            f"Condition: {tool_result['condition']}",
            f"Humidity: {tool_result['humidity']}%",
            f"Wind Speed: {tool_result['wind_speed']} m/s",
            f"Min/Max Today: {tool_result['min_temp']}°C / {tool_result['max_temp']}°C",
        ]

    elif tool == "finance":
        lines += [
            f"Symbol: {tool_result['symbol']}",
            f"Current Price: {tool_result['price']}",
            f"Change: {tool_result['change']} ({tool_result['change_pct']})",
            f"Previous Close: {tool_result['previous_close']}",
            f"Volume: {tool_result['volume']}",
            f"Latest Trading Day: {tool_result['latest_trading_day']}",
        ]

    elif tool == "news":
        lines.append(f"Topic: {tool_result['topic']}")
        for i, a in enumerate(tool_result.get("articles", []), 1):
            lines.append(f"\n{i}. {a['title']}")
            if a["summary"]: lines.append(f"   {a['summary']}")
            if a["source"]:  lines.append(f"   Source: {a['source']} | {a['published']}")

    elif tool == "sports":
        sport = tool_result.get("sport", "sports")
        lines.append(f"Sport: {sport}")
        for m in tool_result.get("matches", []):
            lines.append(f"\n• {m['name']} ({m['match_type']})")
            lines.append(f"  Status: {m['status']}")
            lines.append(f"  Venue: {m.get('venue', 'N/A')}")
            for s in m.get("score", []):
                lines.append(f"  Score: {s.get('inning', '')} — {s.get('r', 0)}/{s.get('w', 0)} in {s.get('o', 0)} overs")

    elif tool == "web_search":
        lines.append(f"Search query: {tool_result['query']}")
        for i, r in enumerate(tool_result.get("results", []), 1):
            lines.append(f"\n{i}. {r['title']}")
            lines.append(f"   {r['body']}")
            lines.append(f"   Source: {r['href']}")

    lines.append(
        "\n\nIMPORTANT: Use the above live data to give an accurate, up-to-date answer. "
        "Do NOT say 'I don't have real-time data'. Format the answer cleanly."
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
async def web_search_endpoint(q: str, max_results: int = 5):
    result = tool_web_search(q, max_results)
    return {"results": result.get("results", []), "query": q}


# ============================================================
# ✅ HELPER: Call OpenRouter with streaming
# ============================================================
def call_openrouter_stream(model_id, messages, api_key, file_urls=None):
    try:
        if file_urls:
            file_context = f"\n\n[User has shared {len(file_urls)} file(s): {', '.join(file_urls)}]"
            if messages and messages[0].get('role') == 'system':
                messages[0]['content'] += file_context

        resp = requests.post(
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
                "temperature": 0.3,
                "max_tokens": 16000,
            },
            stream=True,
            timeout=(10, 120),
        )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            except Exception:
                err_msg = f"HTTP {resp.status_code}"
            return None, err_msg
        return resp, None
    except requests.exceptions.Timeout:
        return None, "Request timed out"
    except Exception as e:
        return None, str(e)


# ============================================================
# ✅ MAIN CHAT ENDPOINT (POST)
# Full pipeline: intent → tool → context → AI → stream
# ============================================================
@app.post("/chat")
async def chat_post(request: Request):
    try:
        body = await request.json()
        prompt     = body.get("prompt", "")
        model      = body.get("model", "dagr")
        file_urls  = body.get("file_urls", [])
        # Legacy: frontend may still send pre-fetched web_results
        web_results = body.get("web_results", [])

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
                headers={"Set-Cookie": f"session_id={session_id}; Path=/; SameSite=Lax; Max-Age=31536000"})

        if session_id not in user_memory:
            user_memory[session_id] = []

        user_memory[session_id].append({"role": "user", "content": prompt})

        # ── MODEL POOLS ────────────────────────────────────────────────────
        model_pools = {
            "dagr": ["openai/gpt-oss-20b:free", "openai/gpt-oss-120b:free"],
            "apep": ["openai/gpt-oss-120b:free", "openai/gpt-oss-20b:free"],
        }
        model_key  = model.lower().strip()
        model_pool = model_pools.get(model_key, model_pools["dagr"])

        # ── BASE SYSTEM PROMPTS ────────────────────────────────────────────
        system_prompts = {
            "dagr": (
                "You are Catura AI (Dagr), a helpful and friendly AI assistant created by Anirban. "
                "You are a general-purpose conversational AI. Always remember the conversation context "
                "and give clear, helpful answers. Be engaging, concise, and supportive in all interactions. "
                "When analyzing files or photos, provide detailed insights based on the content."
            ),
            "apep": (
                "You are Catura AI (Apep), an expert coding and technical problem-solving AI specialist "
                "created by Anirban. You specialize in programming, debugging, algorithms, system design, "
                "and technical solutions. When writing code, ALWAYS use proper indentation (4 spaces per level), "
                "put each statement on its own line, and wrap ALL code in a fenced markdown code block with the "
                "language specified at the top. When analyzing files or photos, provide detailed technical insights."
            ),
        }
        system_prompt = system_prompts.get(model_key, system_prompts["dagr"])

        # ── TOOL ROUTING PIPELINE ──────────────────────────────────────────
        # Step 1: detect intent
        intent = detect_intent(prompt)
        print(f"🎯 [PIPELINE] intent={intent} | model={model_key} | prompt={prompt[:60]}")

        # Step 2: run tool (skip if general or file-only message)
        tool_result = None
        if intent != "general" and not file_urls:
            tool_result = run_tool(intent, prompt)

        # Step 3: build tool context string
        tool_context = build_tool_context(tool_result)

        # Step 4: also handle legacy web_results from frontend (backward compat)
        if not tool_context and web_results:
            search_context = "\n\n🌐 LIVE WEB SEARCH RESULTS (use these to answer accurately):\n"
            for i, r in enumerate(web_results, 1):
                title     = r.get("title", "")
                body_text = r.get("body", "")
                href      = r.get("href", "")
                search_context += f"{i}. {title}\n{body_text}\nSource: {href}\n\n"
            search_context += "Use the above search results to give an accurate, up-to-date answer."
            system_prompt += search_context
        elif tool_context:
            system_prompt += "\n\n" + tool_context

        # Step 5: build final messages list
        messages = [{"role": "system", "content": system_prompt}] + user_memory[session_id][-20:]
        api_key  = os.getenv("OPENROUTER_API_KEY")

        # ── STREAMING GENERATOR ────────────────────────────────────────────
        def generate():
            MAX_HANDOFFS = 40
            full_reply   = ""
            pool_index   = 0
            handoffs     = 0

            # Emit tool badge to frontend so it can show "🌤️ Weather tool used"
            if tool_result:
                badge_payload = json.dumps({"tool_used": tool_result.get("tool", ""), "intent": intent})
                yield f"data: {badge_payload}\n\n"

            while handoffs < MAX_HANDOFFS:
                current_model = model_pool[pool_index % len(model_pool)]
                print(f"🔄 Handoff {handoffs} — [{current_model}] | intent={intent} | accumulated: {len(full_reply)} chars")

                yield ": heartbeat\n\n"

                relay_messages = (
                    messages + [{"role": "assistant", "content": full_reply}]
                    if full_reply.strip() else messages
                )

                resp, err = call_openrouter_stream(current_model, relay_messages, api_key, file_urls)

                if resp is None:
                    print(f"❌ [{current_model}] connection failed: {err} — switching model")
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
                                print(f"⚠️ [{current_model}] mid-stream error: {err_msg}")
                                stream_broke = True
                                break
                            choices = chunk.get("choices")
                            if not choices: continue
                            choice = choices[0]
                            token  = (choice.get("delta") or {}).get("content") or ""
                            finish = choice.get("finish_reason")
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
                        except (json.JSONDecodeError, Exception):
                            continue
                except Exception as e:
                    print(f"⚠️ [{current_model}] stream exception: {e}")
                    stream_broke = True

                if finished_cleanly and full_reply.strip():
                    print(f"✅ [{current_model}] finished. Total: {len(full_reply)} chars")
                    user_memory[session_id].append({"role": "assistant", "content": full_reply})
                    if len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                    yield "data: [DONE]\n\n"
                    return

                if not stream_broke and not full_reply.strip():
                    print(f"⚠️ [{current_model}] returned empty — switching model")

                pool_index += 1
                handoffs   += 1

            if full_reply.strip():
                user_memory[session_id].append({"role": "assistant", "content": full_reply})
                if len(user_memory[session_id]) > 40:
                    user_memory[session_id] = user_memory[session_id][-40:]
                yield "data: [DONE]\n\n"
            else:
                yield f"data: {json.dumps({'error': 'Models could not complete a response. Please try again.'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Set-Cookie": f"session_id={session_id}; Path=/; SameSite=Lax; Max-Age=31536000",
            }
        )

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return JSONResponse(content={"error": str(e)})


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
                headers={"Set-Cookie": f"session_id={session_id}; Path=/; SameSite=Lax; Max-Age=31536000"})

        if session_id not in user_memory:
            user_memory[session_id] = []

        user_memory[session_id].append({"role": "user", "content": prompt})

        model_pools = {
            "dagr": ["openai/gpt-oss-20b:free", "openai/gpt-oss-120b:free"],
            "apep": ["openai/gpt-oss-120b:free", "openai/gpt-oss-20b:free"],
        }
        model_key  = model.lower().strip()
        model_pool = model_pools.get(model_key, model_pools["dagr"])

        system_prompts = {
            "dagr": (
                "You are Catura AI (Dagr), a helpful and friendly AI assistant created by Anirban. "
                "You are a general-purpose conversational AI. Always remember the conversation context and give clear, helpful answers. "
                "Be engaging, concise, and supportive in all interactions."
            ),
            "apep": (
                "You are Catura AI (Apep), an expert coding and technical problem-solving AI specialist created by Anirban. "
                "You specialize in programming, debugging, algorithms, system design, and technical solutions. "
                "When writing code, ALWAYS use proper indentation (4 spaces per level), put each statement on its own line, "
                "and wrap ALL code in a fenced markdown code block with the language specified at the top."
            ),
        }
        system_prompt = system_prompts.get(model_key, system_prompts["dagr"])

        # Apply tool routing for GET requests too
        intent = detect_intent(prompt)
        tool_result = None
        if intent != "general":
            tool_result = run_tool(intent, prompt)
        tool_context = build_tool_context(tool_result)
        if tool_context:
            system_prompt += "\n\n" + tool_context

        messages = [{"role": "system", "content": system_prompt}] + user_memory[session_id][-20:]
        api_key  = os.getenv("OPENROUTER_API_KEY")

        def generate():
            MAX_HANDOFFS = 40
            full_reply   = ""
            pool_index   = 0
            handoffs     = 0

            if tool_result:
                yield f"data: {json.dumps({'tool_used': tool_result.get('tool', ''), 'intent': intent})}\n\n"

            while handoffs < MAX_HANDOFFS:
                current_model = model_pool[pool_index % len(model_pool)]
                yield ": heartbeat\n\n"

                relay_messages = (
                    messages + [{"role": "assistant", "content": full_reply}]
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
                            token  = (choices[0].get("delta") or {}).get("content") or ""
                            finish = choices[0].get("finish_reason")
                            if token:
                                full_reply += token; leg_tokens += 1
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                            if finish == "stop": finished_cleanly = True; break
                            if finish == "length": stream_broke = True; break
                        except: continue
                except Exception as e:
                    stream_broke = True

                if finished_cleanly and full_reply.strip():
                    user_memory[session_id].append({"role": "assistant", "content": full_reply})
                    if len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                    yield "data: [DONE]\n\n"; return

                pool_index += 1; handoffs += 1

            if full_reply.strip():
                user_memory[session_id].append({"role": "assistant", "content": full_reply})
                yield "data: [DONE]\n\n"
            else:
                yield f"data: {json.dumps({'error': 'Models could not complete a response.'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            generate(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache",
                     "Set-Cookie": f"session_id={session_id}; Path=/; SameSite=Lax; Max-Age=31536000"}
        )
    except Exception as e:
        return JSONResponse(content={"error": str(e)})
