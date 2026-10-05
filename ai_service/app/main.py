import os

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI()


# Describe the JSON that /ai expects.
class AIRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@app.get("/")
def root():
    return {"status": "AI Monitoring Agent Running"}


# A normal def suits the blocking requests library.
@app.post("/ai")
def ai_endpoint(data: AIRequest):
    user_text = data.message.strip()

    if not user_text:
        raise HTTPException(
            status_code=422,
            detail="Message cannot be blank."
        )

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="AI is not configured: GROQ_API_KEY is missing."
        )

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
                "max_completion_tokens": 1024,
                "messages": [
                    {"role": "user", "content": user_text}
                ]
            },
            timeout=(5, 30)
        )
        response.raise_for_status()

    except requests.Timeout:
        raise HTTPException(
            status_code=504,
            detail="AI provider timed out."
        ) from None
    except requests.RequestException:
        raise HTTPException(
            status_code=502,
            detail="AI provider request failed. Check credentials and provider availability."
        ) from None

    try:
        result = response.json()
        reply = result["choices"][0]["message"]["content"]

        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Missing reply")

    except (ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(
            status_code=502,
            detail="AI provider returned an invalid response."
        ) from None

    return {"reply": reply}
