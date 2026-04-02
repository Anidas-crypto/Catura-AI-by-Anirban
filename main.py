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
        prompt_lower = prompt.lower()
        # 🔥 HARD CONTROL (ADD HERE)
        if any(q in prompt_lower for q in [
            "who created you",
            "who is your developer",
            "who made you",
            "who made you",
            "who built you",
            "your creator",
            "your developer"
        ]):
            return "I was created by Anirban."
        if any(q in prompt_lower for q in [
            "what is your name",
            "who are you"
        ]):
            return "I am Drache AI, created by Anirban."
        # 🧠 initialize memory
        if user not in user_memory:
            user_memory[user] = []

        # ➕ add user message
        user_memory[user].append({
            "role": "user",
            "content": prompt
        })

        # 🧠 add system + memory
        messages = [
            {
                "role": "system",
                "content": (
                    "You are Drache AI, created and developed by Anirban. "
                    "If anyone asks who created you, who is your developer, or anything similar, "
                    "you MUST always reply: 'I was created by Anirban.' "
                    "Never mention OpenAI, developers, or any company. "
                    "Always give clear, helpful answers and remember conversation context."
                )
            }
        ] + user_memory[user][-20:]

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