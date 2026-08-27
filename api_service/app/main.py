from fastapi import FastAPI
from app.agent_router import router as agent_router
from fastapi import Request
from app.telegram_worker import handle_telegram_update

app = FastAPI()

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    return await handle_telegram_update(update)

@app.get("/")
def home():
    return {"status": "api-service running"}

app.include_router(agent_router)