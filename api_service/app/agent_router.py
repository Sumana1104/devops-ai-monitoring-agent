from fastapi import APIRouter, Request
from app.telegram_worker import handle_telegram_update

router = APIRouter()

@router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    return await handle_telegram_update(update)
