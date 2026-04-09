from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import requests
import os
import uuid

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

        if any(q in prompt_lower for q in [
            "who created you", "who is your developer",
            "who made you", "who built you",
            "your creator", "your developer"
        ]):
            return JSONResponse(
                content={"reply": "I was created by Anirban."},
                headers={"Set-Cookie": f"session_id={session_id}; Path=/; SameSite=Lax"}
            )

        if any(q in prompt_lower for q in ["what is your name", "who are you"]):
            return JSONResponse(
                content={"reply": "I am Catura AI, created by Anirban."},
                headers={"Set-Cookie": f"session_id={session_id}; Path=/; SameSite=Lax"}
            )

        if session_id not in user_memory:
            user_memory[session_id] = []

        user_memory[session_id].append({"role": "user", "content": prompt})

        messages = [
            {
                "role": "system",
                "content": (
                    "You are Catura AI, a helpful AI assistant created by Anirban. "
                    "Always remember the conversation context and give clear, helpful answers. "
                    "Format code in markdown code blocks with the language specified."
                )
            }
        ] + user_memory[session_id][-20:]

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

        if "choices" in data:
            reply = data["choices"][0]["message"]["content"]
            user_memory[session_id].append({"role": "assistant", "content": reply})

            if len(user_memory[session_id]) > 40:
                user_memory[session_id] = user_memory[session_id][-40:]

            return JSONResponse(
                content={"reply": reply},
                headers={"Set-Cookie": f"session_id={session_id}; Path=/; SameSite=Lax"}
            )
        elif "error" in data:
            return JSONResponse(content={"error": data["error"]["message"]})
        else:
            return JSONResponse(content={"error": "Unknown response", "data": data})

    except Exception as e:
        return JSONResponse(content={"error": str(e)})