from fastapi import FastAPI, Request
import requests
import os

app = FastAPI()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

@app.get("/")
def root():
    return {"status": "AI Monitoring Agent Running"}

@app.post("/ai")
async def ai_endpoint(request: Request):
    data = await request.json()
    user_text = data.get("message", "")

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {"role": "user", "content": user_text}
                ]
            }
        )

        result = response.json()

        # Safe extraction
        if "choices" in result:
            reply = result["choices"][0]["message"]["content"]
            return {"reply": reply}
        else:
            return {"reply": f"AI Error: {result}"}

    except Exception as e:
        return {"reply": f"AI Error: {str(e)}"}
