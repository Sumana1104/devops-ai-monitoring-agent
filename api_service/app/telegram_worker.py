import os
import httpx

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL")

async def handle_telegram_update(update: dict):
    message = update.get("message", {}).get("text", "")

    if not message:
        return {"ok": True, "info": "no text message"}

    if not AI_SERVICE_URL:
        return {"ok": False, "error": "AI_SERVICE_URL not set"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{AI_SERVICE_URL}/ai",
            json={"message": message},
            timeout=30,
        )

    try:
        data = resp.json()
    except Exception:
        data = {"error": "invalid JSON from ai-service", "status_code": resp.status_code}

    return {"ok": True, "ai_response": data}
