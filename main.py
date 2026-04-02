from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import requests
import os

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

# 🧠 MEMORY STORAGE
user_memory = {}


@app.get("/")
def home():
    return FileResponse("index.html")


@app.get("/chat")
def chat(prompt: str, user: str = "guest"):
    try:
        # 🧠 initialize memory
        if user not in user_memory:
            user_memory[user] = []

        # ➕ add user message
        user_memory[user].append({
            "role": "user",
            "content": prompt
        })

        # 🧠 keep last 10 messages
        messages = user_memory[user][-10:]

        response = requests.post(
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

        data = response.json()

        print("RAW:", data)

        if "choices" in data:
            reply = data["choices"][0]["message"]["content"]

            # ➕ save AI reply
            user_memory[user].append({
                "role": "assistant",
                "content": reply
            })

            return reply

        elif "error" in data:
            return {"error": data["error"]["message"]}

        else:
            return {"error": "Unknown response", "data": data}

    except Exception as e:
        return {"error": str(e)}