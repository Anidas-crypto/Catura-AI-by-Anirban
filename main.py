from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import requests
import os
import uuid

app = FastAPI()

# ✅ Serve static files (CSS, JS, images)
app.mount("/static", StaticFiles(directory="static"), name="static")

# 🧠 In-memory chat memory
user_memory = {}

# ============================
# ✅ ROUTES (IMPORTANT FIX)
# ============================

# Homepage
@app.get("/")
def home():
    return FileResponse("index.html")

# ✅ FIX: Serve auth page (THIS WAS MISSING)
@app.get("/auth.html")
def auth_page():
    return FileResponse("auth.html")

# Optional: Google verification (keep if needed)
@app.get("/google5869a60ba00ea65a.html")
def google_verify():
    return FileResponse("google5869a60ba00ea65a.html")

# ============================
# 🤖 CHAT API
# ============================
@app.get("/chat")
def chat(request: Request, prompt: str):
    try:
        # 🆔 Get session from cookie
        session_id = request.cookies.get("session_id")

        # 🆕 Create session if not exists
        if not session_id:
            session_id = str(uuid.uuid4())

        prompt_lower = prompt.lower()

        # 🔥 Hard responses
        if any(q in prompt_lower for q in [
            "who created you",
            "who is your developer",
            "who made you",
            "who built you"
        ]):
            return JSONResponse(
                content={"reply": "I was created by Anirban."},
                headers={"Set-Cookie": f"session_id={session_id}; Path=/"}
            )

        if any(q in prompt_lower for q in [
            "what is your name",
            "who are you"
        ]):
            return JSONResponse(
                content={"reply": "I am Catura AI, created by Anirban."},
                headers={"Set-Cookie": f"session_id={session_id}; Path=/"}
            )

        # 🧠 Initialize memory
        if session_id not in user_memory:
            user_memory[session_id] = []

        # ➕ Save user message
        user_memory[session_id].append({
            "role": "user",
            "content": prompt
        })

        # 🧠 Build context
        messages = [
            {
                "role": "system",
                "content": "You are Catura AI, created by Anirban. Be helpful and remember context."
            }
        ] + user_memory[session_id][-20:]

        # 🔗 Call OpenRouter
        response_api = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openai/gpt-3.5-turbo",
                "messages": messages
            }
        )

        data = response_api.json()

        # ✅ Success
        if "choices" in data:
            reply = data["choices"][0]["message"]["content"]

            # ➕ Save AI reply
            user_memory[session_id].append({
                "role": "assistant",
                "content": reply
            })

            # 🧹 Limit memory
            if len(user_memory[session_id]) > 40:
                user_memory[session_id] = user_memory[session_id][-40:]

            return JSONResponse(
                content={"reply": reply},
                headers={"Set-Cookie": f"session_id={session_id}; Path=/"}
            )

        # ❌ API error
        elif "error" in data:
            return {"error": data["error"]["message"]}

        else:
            return {"error": "Unknown response", "data": data}

    except Exception as e:
        return {"error": str(e)}