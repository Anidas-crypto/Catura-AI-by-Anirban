from fastapi import FastAPI,Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import requests
import os
import uuid

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

# 🧠 MEMORY STORAGE
user_memory = {}


@app.get("/")
def home():
    return FileResponse("index.html")


@app.get("/chat")
def chat(request: Request, prompt: str):
    try:
        # 🆔 GET SESSION ID
        session_id = request.cookies.get("session_id")

        # 🆕 CREATE IF NOT EXISTS
        if not session_id:
            session_id = str(uuid.uuid4())

        prompt_lower = prompt.lower()

        # 🔥 HARD CONTROL
        if any(q in prompt_lower for q in [
            "who created you",
            "who is your developer",
            "who made you",
            "who built you",
            "your creator",
            "your developer"
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
                content={"reply": "I am Drache AI, created by Anirban."},
                headers={"Set-Cookie": f"session_id={session_id}; Path=/"}
            )

        # 🧠 INIT MEMORY PER SESSION
        if session_id not in user_memory:
            user_memory[session_id] = []

        # ➕ USER MESSAGE
        user_memory[session_id].append({
            "role": "user",
            "content": prompt
        })

        # 🧠 CONTEXT
        messages = [
            {
                "role": "system",
                "content": (
                    "You are Drache AI, created by Anirban. "
                    "Always remember context and give helpful answers."
                )
            }
        ] + user_memory[session_id][-20:]

        # 🔗 API CALL
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

            # ➕ SAVE AI REPLY
            user_memory[session_id].append({
                "role": "assistant",
                "content": reply
            })

            # 🧹 LIMIT MEMORY
            if len(user_memory[session_id]) > 40:
                user_memory[session_id] = user_memory[session_id][-40:]

            return JSONResponse(
                content={"reply": reply},
                headers={"Set-Cookie": f"session_id={session_id}; Path=/"}
            )

        elif "error" in data:
            return {"error": data["error"]["message"]}

        else:
            return {"error": "Unknown response", "data": data}

    except Exception as e:
        return {"error": str(e)}