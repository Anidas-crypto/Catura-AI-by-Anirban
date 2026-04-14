from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import requests
import os
import uuid
import json

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# 🧠 In-memory session store
user_memory = {}

@app.get("/")
def home():
    return FileResponse("index.html")

@app.get("/auth.html")
def auth_page():
    return FileResponse("auth.html")

@app.get("/manifest.json")
async def serve_manifest():
    path = os.path.join(os.path.dirname(__file__), "manifest.json")
    return FileResponse(path)

@app.get("/service-worker.js")
async def serve_sw():
    return FileResponse(os.path.join(os.path.dirname(__file__), "service-worker.js"))

@app.get("/ping")
def ping():
    return {"status": "ok"}

@app.get("/google5869a60ba00ea65a.html")
def google_verify():
    return FileResponse("google5869a60ba00ea65a.html")

@app.get("/chat")
def chat(request: Request, prompt: str):
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
                headers={"Set-Cookie": f"session_id={session_id}; Path=/; SameSite=Lax"})

        if any(q in prompt_lower for q in ["what is your name", "who are you"]):
            def quick():
                yield f"data: {json.dumps({'token': 'I am Catura AI, created by Anirban.'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(quick(), media_type="text/event-stream",
                headers={"Set-Cookie": f"session_id={session_id}; Path=/; SameSite=Lax"})

        if session_id not in user_memory:
            user_memory[session_id] = []

        user_memory[session_id].append({"role": "user", "content": prompt})

        messages = [
            {
                "role": "system",
                "content": (
                    "You are Catura AI, a helpful AI assistant created by Anirban. "
                    "Always remember the conversation context and give clear, helpful answers. "
                    "When writing code, ALWAYS use proper indentation (4 spaces per level), "
                    "put each statement on its own line, and wrap ALL code in a fenced "
                    "markdown code block with the language specified, like:\n"
                    "```java\n<code here>\n```\n"
                    "Never compress code onto a single line. Always format code for readability."
                )
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
                        "model": "openai/gpt-3.5-turbo",
                        "messages": messages,
                        "stream": True,
                        # ✅ These ensure the model gives properly formatted code
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
                                # ✅ ensure_ascii=False preserves all characters exactly
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
                "Set-Cookie": f"session_id={session_id}; Path=/; SameSite=Lax"
            }
        )

    except Exception as e:
        return JSONResponse(content={"error": str(e)})
