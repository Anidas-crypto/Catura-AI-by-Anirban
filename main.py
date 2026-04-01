from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import requests
import os

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

HF_API_KEY = os.getenv("HF_API_KEY")

@app.get("/")
def home():
    return FileResponse("index.html")

@app.get("/chat")
def chat(prompt: str):
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openai/gpt-3.5-turbo",
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            }
        )

        data = response.json()

        print("RAW:", data)  # 🔍 debug

        # ✅ Handle success
        if "choices" in data:
            return data["choices"][0]["message"]["content"]

        # ❌ Handle error
        elif "error" in data:
            return {"error": data["error"]["message"]}

        else:
            return {"error": "Unknown response", "data": data}

    except Exception as e:
        return {"error": str(e)}