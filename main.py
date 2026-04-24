from fastapi import FastAPI, Request, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
import uuid
import json
from datetime import datetime
from supabase import create_client, Client
import base64
import io
from PIL import Image

app = FastAPI()

# ✅ CORS MIDDLEWARE
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ MOUNT STATIC FILES — permanent fix using absolute path
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

# ✅ YOUR RENDER APP URL
APP_URL = os.getenv("APP_URL", "https://my-ai-assistant-9bbd.onrender.com/")

# ✅ CACHE CONTROL MIDDLEWARE
@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    response = await call_next(request)

    if request.url.path == "/" or request.url.path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    elif request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"

    elif request.url.path in ["/manifest.json", "/service-worker.js"]:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    return response


@app.get("/")
def home():
    return FileResponse(os.path.join(BASE_DIR, "index.html"), media_type="text/html")

@app.get("/auth.html")
def auth_page():
    p = os.path.join(BASE_DIR, "auth.html")
    if not os.path.isfile(p):
        return JSONResponse({"error": "auth.html not found"}, status_code=404)
    return FileResponse(p, media_type="text/html")

@app.get("/manifest.json")
async def serve_manifest():
    p = os.path.join(BASE_DIR, "manifest.json")
    if not os.path.isfile(p):
        return JSONResponse({"error": "manifest.json not found"}, status_code=404)
    return FileResponse(p, media_type="application/manifest+json")

@app.get("/service-worker.js")
async def serve_sw():
    p = os.path.join(BASE_DIR, "service-worker.js")
    if not os.path.isfile(p):
        return JSONResponse({"error": "service-worker.js not found"}, status_code=404)
    return FileResponse(p, media_type="application/javascript")

@app.get("/ping")
def ping():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "version": "26.4.22"}

@app.get("/google5869a60ba00ea65a.html")
def google_verify():
    p = os.path.join(BASE_DIR, "google5869a60ba00ea65a.html")
    if not os.path.isfile(p):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p, media_type="text/html")

@app.get("/health")
def health_check():
    return {"status": "healthy", "version": "26.4.22", "timestamp": datetime.utcnow().isoformat()}


# ✅ HELPER: Call OpenRouter with automatic fallback
def call_openrouter_stream(model_id, messages, api_key, file_urls=None):
    """Attempt streaming request to OpenRouter with file support."""
    try:
        # If files present, include them in system message
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


@app.post("/chat")
async def chat_post(request: Request):
    """POST endpoint for chat with file support"""
    try:
        body = await request.json()
        prompt = body.get("prompt", "")
        model = body.get("model", "dagr")
        file_urls = body.get("file_urls", [])

        session_id = request.cookies.get("session_id")
        if not session_id:
            session_id = str(uuid.uuid4())

        prompt_lower = prompt.lower()

        # ✅ Identity overrides
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

        # ✅ MODEL POOLS
        model_pools = {
            "dagr": ["openai/gpt-oss-20b:free", "openai/gpt-oss-120b:free"],
            "apep": ["openai/gpt-oss-120b:free", "openai/gpt-oss-20b:free"],
        }

        model_key = model.lower().strip()
        model_pool = model_pools.get(model_key, model_pools["dagr"])

        # ✅ SYSTEM PROMPTS
        system_prompts = {
            "dagr": (
                "You are Catura AI (Dagr), a helpful and friendly AI assistant created by Anirban. "
                "You are a general-purpose conversational AI. Always remember the conversation context and give clear, helpful answers. "
                "Be engaging, concise, and supportive in all interactions. "
                "When analyzing files or photos, provide detailed insights based on the content."
            ),
            "apep": (
                "You are Catura AI (Apep), an expert coding and technical problem-solving AI specialist created by Anirban. "
                "You specialize in programming, debugging, algorithms, system design, and technical solutions. "
                "When writing code, ALWAYS use proper indentation (4 spaces per level), put each statement on its own line, and wrap ALL code in a fenced "
                "markdown code block with the language specified at the top. "
                "When analyzing files or photos, provide detailed technical insights."
            ),
        }

        system_prompt = system_prompts.get(model_key, system_prompts["dagr"])

        messages = [
            {"role": "system", "content": system_prompt}
        ] + user_memory[session_id][-20:]

        api_key = os.getenv("OPENROUTER_API_KEY")

        def generate():
            MAX_HANDOFFS = 40
            full_reply = ""
            pool_index = 0
            handoffs = 0

            while handoffs < MAX_HANDOFFS:
                current_model = model_pool[pool_index % len(model_pool)]
                print(f"🔄 Handoff {handoffs} — [{current_model}] | Files: {len(file_urls)} | accumulated: {len(full_reply)} chars")

                yield ": heartbeat\n\n"

                if full_reply.strip():
                    relay_messages = messages + [
                        {"role": "assistant", "content": full_reply}
                    ]
                else:
                    relay_messages = messages

                resp, err = call_openrouter_stream(current_model, relay_messages, api_key, file_urls)

                if resp is None:
                    print(f"❌ [{current_model}] connection failed: {err} — switching model")
                    pool_index += 1
                    handoffs += 1
                    continue

                leg_tokens = 0
                stream_broke = False
                finished_cleanly = False

                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            finished_cleanly = True
                            break
                        try:
                            chunk = json.loads(payload)

                            if "error" in chunk:
                                err_msg = chunk["error"].get("message", "unknown")
                                print(f"⚠️ [{current_model}] mid-stream error: {err_msg} — handing off")
                                stream_broke = True
                                break

                            choices = chunk.get("choices")
                            if not choices:
                                continue

                            choice = choices[0]
                            token = (choice.get("delta") or {}).get("content") or ""
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
                                print(f"⚠️ [{current_model}] hit token limit — relay will continue")
                                stream_broke = True
                                break

                        except (json.JSONDecodeError, Exception):
                            continue

                except Exception as e:
                    print(f"⚠️ [{current_model}] stream exception: {e} — handing off")
                    stream_broke = True

                if finished_cleanly and full_reply.strip():
                    print(f"✅ [{current_model}] finished. Total: {len(full_reply)} chars, {handoffs + 1} leg(s)")
                    user_memory[session_id].append({"role": "assistant", "content": full_reply})
                    if len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                    yield "data: [DONE]\n\n"
                    return

                if not stream_broke and not full_reply.strip():
                    print(f"⚠️ [{current_model}] returned empty — switching model")

                print(f"🏃 Passing baton. Leg: {leg_tokens} tokens | Total: {len(full_reply)} chars")
                pool_index += 1
                handoffs += 1

            if full_reply.strip():
                print(f"⚠️ Hit MAX_HANDOFFS ({MAX_HANDOFFS}) — saving partial response")
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


# ✅ KEEP ORIGINAL GET /chat FOR BACKWARD COMPATIBILITY
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

        model_key = model.lower().strip()
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
                "When writing code, ALWAYS use proper indentation (4 spaces per level), put each statement on its own line, and wrap ALL code in a fenced "
                "markdown code block with the language specified at the top, like:\n"
                "```python\n<code here>\n```\n"
                "Never compress code onto a single line. Always format code for readability and add comments for complex logic. "
                "Provide detailed explanations for your code and suggest best practices."
            ),
        }

        system_prompt = system_prompts.get(model_key, system_prompts["dagr"])

        messages = [
            {"role": "system", "content": system_prompt}
        ] + user_memory[session_id][-20:]

        api_key = os.getenv("OPENROUTER_API_KEY")

        def generate():
            MAX_HANDOFFS = 40
            full_reply = ""
            pool_index = 0
            handoffs = 0

            while handoffs < MAX_HANDOFFS:
                current_model = model_pool[pool_index % len(model_pool)]
                print(f"🔄 Handoff {handoffs} — [{current_model}] | accumulated: {len(full_reply)} chars")

                yield ": heartbeat\n\n"

                if full_reply.strip():
                    relay_messages = messages + [
                        {"role": "assistant", "content": full_reply}
                    ]
                else:
                    relay_messages = messages

                resp, err = call_openrouter_stream(current_model, relay_messages, api_key)

                if resp is None:
                    print(f"❌ [{current_model}] connection failed: {err} — switching model")
                    pool_index += 1
                    handoffs += 1
                    continue

                leg_tokens = 0
                stream_broke = False
                finished_cleanly = False

                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if not decoded.startswith("data: "):
                            continue
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            finished_cleanly = True
                            break
                        try:
                            chunk = json.loads(payload)

                            if "error" in chunk:
                                err_msg = chunk["error"].get("message", "unknown")
                                print(f"⚠️ [{current_model}] mid-stream error: {err_msg} — handing off")
                                stream_broke = True
                                break

                            choices = chunk.get("choices")
                            if not choices:
                                continue

                            choice = choices[0]
                            token = (choice.get("delta") or {}).get("content") or ""
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
                                print(f"⚠️ [{current_model}] hit token limit — relay will continue")
                                stream_broke = True
                                break

                        except (json.JSONDecodeError, Exception):
                            continue

                except Exception as e:
                    print(f"⚠️ [{current_model}] stream exception: {e} — handing off")
                    stream_broke = True

                if finished_cleanly and full_reply.strip():
                    print(f"✅ [{current_model}] finished. Total: {len(full_reply)} chars, {handoffs + 1} leg(s)")
                    user_memory[session_id].append({"role": "assistant", "content": full_reply})
                    if len(user_memory[session_id]) > 40:
                        user_memory[session_id] = user_memory[session_id][-40:]
                    yield "data: [DONE]\n\n"
                    return

                if not stream_broke and not full_reply.strip():
                    print(f"⚠️ [{current_model}] returned empty — switching model")

                print(f"🏃 Passing baton. Leg: {leg_tokens} tokens | Total: {len(full_reply)} chars")
                pool_index += 1
                handoffs += 1

            if full_reply.strip():
                print(f"⚠️ Hit MAX_HANDOFFS ({MAX_HANDOFFS}) — saving partial response")
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
        return JSONResponse(content={"error": str(e)})