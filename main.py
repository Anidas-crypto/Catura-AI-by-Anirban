from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
import uuid
import json
from datetime import datetime

app = FastAPI()

# ✅ CORS MIDDLEWARE
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ MOUNT STATIC FILES WITH CACHE CONTROL
app.mount("/static", StaticFiles(directory="static"), name="static")

# 🧠 In-memory session store
user_memory = {}

# ✅ YOUR RENDER APP URL — update this to your actual Render domain
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
    return FileResponse("index.html", media_type="text/html")

@app.get("/auth.html")
def auth_page():
    return FileResponse("auth.html", media_type="text/html")

@app.get("/manifest.json")
async def serve_manifest():
    path = os.path.join(os.path.dirname(__file__), "manifest.json")
    return FileResponse(path, media_type="application/manifest+json")

@app.get("/service-worker.js")
async def serve_sw():
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "service-worker.js"),
        media_type="application/javascript"
    )

@app.get("/ping")
def ping():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "version": "26.4.8"}

@app.get("/google5869a60ba00ea65a.html")
def google_verify():
    return FileResponse("google5869a60ba00ea65a.html", media_type="text/html")

@app.get("/health")
def health_check():
    return {"status": "healthy", "version": "26.4.8", "timestamp": datetime.utcnow().isoformat()}


# ✅ HELPER: Call OpenRouter with automatic fallback
def call_openrouter_stream(model_id, messages, api_key):
    """Attempt streaming request to OpenRouter. Returns (response, error_msg)."""
    try:
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
                "max_tokens": 2048,
            },
            stream=True,
            timeout=60,
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


# ✅ HELPER: Call HuggingFace Inference API (for Neit / Gemma 4 E4B)
def call_huggingface_stream(messages, api_key):
    """Call HuggingFace Inference API for google/gemma-4-E4B-it. Returns (response, error_msg)."""
    try:
        # Build Gemma chat prompt format
        prompt = ""
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                prompt += f"<start_of_turn>user\nSystem: {content}<end_of_turn>\n"
            elif role == "user":
                prompt += f"<start_of_turn>user\n{content}<end_of_turn>\n"
            elif role == "assistant":
                prompt += f"<start_of_turn>model\n{content}<end_of_turn>\n"
        prompt += "<start_of_turn>model\n"

        resp = requests.post(
            "https://api-inference.huggingface.co/models/google/gemma-4-E4B-it",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 2048,
                    "temperature": 0.3,
                    "return_full_text": False,
                },
                "stream": True,
            },
            stream=True,
            timeout=60,
        )

        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", f"HTTP {resp.status_code}")
            except Exception:
                err_msg = f"HTTP {resp.status_code}"
            print(f"❌ [HuggingFace/Neit] error: {err_msg}")
            return None, err_msg

        return resp, None

    except requests.exceptions.Timeout:
        print("❌ [HuggingFace/Neit] timed out")
        return None, "Request timed out"
    except Exception as e:
        print(f"❌ [HuggingFace/Neit] exception: {e}")
        return None, str(e)


@app.get("/chat")
def chat(request: Request, prompt: str, model: str = "dagr"):
    try:
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

        if any(q in prompt_lower for q in ["what is your name", "who are you"]):
            def quick():
                yield f"data: {json.dumps({'token': 'I am Catura AI, created by Anirban. I can switch between Dagr and Apep models.'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(quick(), media_type="text/event-stream",
                headers={"Set-Cookie": f"session_id={session_id}; Path=/; SameSite=Lax; Max-Age=31536000"})

        if session_id not in user_memory:
            user_memory[session_id] = []

        user_memory[session_id].append({"role": "user", "content": prompt})

        # ✅ MODEL POOLS — relay race style, loops between models
        # "huggingface" prefix = uses HuggingFace API instead of OpenRouter
        model_pools = {
            "dagr": ["openai/gpt-oss-20b:free", "openai/gpt-oss-120b:free"],
            "apep": ["openai/gpt-oss-120b:free", "openai/gpt-oss-20b:free"],
            "qwen": ["openai/gpt-oss-120b:free", "openai/gpt-oss-20b:free"],
            "neit": ["huggingface/gemma-4-E4B-it"],
        }

        model_key = model.lower().strip()
        model_pool = model_pools.get(model_key, model_pools["dagr"])

        # ✅ SYSTEM PROMPTS FOR EACH MODEL
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
            "qwen": (
                "You are Catura AI (Qwen), an ultra-advanced coding AI specialist created by Anirban, powered by Qwen3 Coder 480B. "
                "You are the most powerful coding model available in Catura AI. You specialize in complex programming tasks, "
                "large-scale system architecture, advanced algorithms, and cutting-edge technical solutions. "
                "When writing code, ALWAYS use proper indentation (4 spaces per level), put each statement on its own line, and wrap ALL code in a fenced "
                "markdown code block with the language specified at the top, like:\n"
                "```python\n<code here>\n```\n"
                "Always write production-quality code with detailed comments, error handling, and best practices. "
                "Provide thorough explanations and consider edge cases in all solutions."
            ),
            "neit": (
                "You are Catura AI (Neit), a smart and versatile AI assistant created by Anirban, powered by Google Gemma 4 E4B. "
                "You are fast, efficient, and great at reasoning, multilingual conversations, and everyday tasks. "
                "Always give clear, helpful, and accurate responses. Be concise yet thorough."
            )
        }

        system_prompt = system_prompts.get(model_key, system_prompts["dagr"])

        messages = [
            {"role": "system", "content": system_prompt}
        ] + user_memory[session_id][-20:]

        api_key = os.getenv("OPENROUTER_API_KEY")
        hf_api_key = os.getenv("HUGGINGFACE_API_KEY")

        def generate():
            full_reply = ""
            pool = model_pool  # e.g. [20b, 120b] or [huggingface/...]
            pool_index = 0
            max_switches = 10  # max number of model switches allowed
            switches = 0

            while switches <= max_switches:
                current_model = pool[pool_index % len(pool)]
                print(f"🔄 [{current_model}] starting (switch {switches}, reply so far: {len(full_reply)} chars)")

                # Build messages — if we already have partial reply, add it
                # so the next model continues from where previous left off
                if full_reply.strip():
                    continuation_messages = messages + [
                        {"role": "assistant", "content": full_reply},
                        {"role": "user", "content": "Continue exactly from where you left off. Do not repeat anything already said."}
                    ]
                else:
                    continuation_messages = messages

                # ✅ Route to correct API based on model prefix
                if current_model.startswith("huggingface/"):
                    resp, err = call_huggingface_stream(continuation_messages, hf_api_key)
                else:
                    resp, err = call_openrouter_stream(current_model, continuation_messages, api_key)

                if resp is None:
                    print(f"❌ [{current_model}] failed to connect: {err}. Switching model...")
                    pool_index += 1
                    switches += 1
                    continue

                # Stream tokens from current model
                stream_broke = False
                try:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if decoded.startswith("data:"):
                            payload = decoded[5:].strip()
                            if payload == "[DONE]":
                                break
                            try:
                                chunk = json.loads(payload)

                                # ✅ HuggingFace token format
                                if current_model.startswith("huggingface/"):
                                    token = chunk.get("token", {}).get("text", "")
                                    # HF sometimes sends special tokens — skip them
                                    if token and not token.startswith("<"):
                                        full_reply += token
                                        yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                                    continue

                                # ✅ OpenRouter token format
                                # Mid-stream error from OpenRouter → switch model
                                if "error" in chunk:
                                    err_msg = chunk["error"].get("message", "Unknown error")
                                    print(f"⚠️ [{current_model}] mid-stream error: {err_msg}. Switching model...")
                                    stream_broke = True
                                    break

                                choices = chunk.get("choices")
                                if not choices:
                                    continue

                                delta = choices[0].get("delta", {})
                                token = delta.get("content") or ""
                                if token:
                                    full_reply += token
                                    yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"

                            except json.JSONDecodeError:
                                continue
                            except Exception:
                                continue

                except Exception as e:
                    print(f"⚠️ [{current_model}] stream exception: {e}. Switching model...")
                    stream_broke = True

                # If stream finished cleanly with content → done!
                if not stream_broke and full_reply.strip():
                    print(f"✅ [{current_model}] completed response ({len(full_reply)} chars)")
                    break

                # Stream broke or returned empty → switch to next model and continue
                if stream_broke or not full_reply.strip():
                    pool_index += 1
                    switches += 1
                    print(f"🔁 Switching to next model. Reply so far: {len(full_reply)} chars")
                    continue

                break  # safety

            # After loop — check if we got anything at all
            if not full_reply.strip():
                yield f"data: {json.dumps({'error': 'All models failed to respond. Please try again in a moment.'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return

            # Save completed reply to memory
            user_memory[session_id].append({"role": "assistant", "content": full_reply})
            if len(user_memory[session_id]) > 40:
                user_memory[session_id] = user_memory[session_id][-40:]

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