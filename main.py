from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
import uuid
import json
from datetime import datetime, timedelta

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

# ✅ CACHE CONTROL MIDDLEWARE
@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    response = await call_next(request)
    
    # ✅ Never cache HTML
    if request.url.path == "/" or request.url.path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    
    # ✅ Cache static files for 1 year (they have version params)
    elif request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    
    # ✅ Cache manifest and service-worker
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
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "version": "27.0.0"}

@app.get("/google5869a60ba00ea65a.html")
def google_verify():
    return FileResponse("google5869a60ba00ea65a.html", media_type="text/html")

@app.get("/health")
def health_check():
    return {"status": "healthy", "version": "27.0.0", "timestamp": datetime.utcnow().isoformat()}

@app.get("/chat")
def chat(request: Request, prompt: str, model: str = "dagr"):
    try:
        session_id = request.cookies.get("session_id")
        if not session_id:
            session_id = str(uuid.uuid4())

        prompt_lower = prompt.lower()

        # Identity overrides
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

        # 🤖 MODEL MAPPING
        model_map = {
            "dagr": "openai/gpt-3.5-turbo",
            "apep": "meta-llama/llama-3.3-70b-instruct:free",
        }
        
        selected_model = model_map.get(model.lower(), "openai/gpt-3.5-turbo")

        # 🧠 SYSTEM PROMPTS FOR EACH MODEL
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
            )
        }

        # Get appropriate system prompt based on selected model
        model_key = model.lower()
        system_prompt = system_prompts.get(model_key, system_prompts["dagr"])

        messages = [
            {
                "role": "system",
                "content": system_prompt
            }
        ] + user_memory[session_id][-20:]

        def generate():
            full_reply = ""
            try:
                resp = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": selected_model,
                        "messages": messages,
                        "stream": True,
                        "temperature": 0.3,
                        "max_tokens": 2048
                    },
                    stream=True,
                    timeout=60
                )

                for line in resp.iter_lines():
                    if not line:
                        continue
                    decoded = line.decode("utf-8")
                    if decoded.startswith("data: "):
                        payload = decoded[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            delta = chunk["choices"][0].get("delta", {})
                            token = delta.get("content", "")
                            if token:
                                full_reply += token
                                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
                        except Exception:
                            continue

                user_memory[session_id].append({"role": "assistant", "content": full_reply})
                if len(user_memory[session_id]) > 40:
                    user_memory[session_id] = user_memory[session_id][-40:]

                yield "data: [DONE]\n\n"

            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Set-Cookie": f"session_id={session_id}; Path=/; SameSite=Lax; Max-Age=31536000"
            }
        )

    except Exception as e:
        return JSONResponse(content={"error": str(e)})