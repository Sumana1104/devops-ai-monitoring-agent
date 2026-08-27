from fastapi import FastAPI, Request
import os
import logging
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise Exception("Missing GROQ_API_KEY")

groq_client = Groq(api_key=GROQ_API_KEY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("ai-service")

app = FastAPI()

@app.get("/")
def home():
    return {"status": "AI Monitoring Agent Running"}

@app.post("/ai")
async def ai_endpoint(request: Request):
    data = await request.json()
    user_text = data.get("message", "")

    try:
        response = groq_client.chat.completions.create(
            model="mixtral-8x7b-32768",
            messages=[{"role": "user", "content": user_text}]
        )
        answer = response.choices[0].message.content
    except Exception as e:
        answer = f"AI Error: {e}"

    return {"reply": answer}
