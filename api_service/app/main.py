from fastapi import FastAPI
from app.agent_router import router as agent_router

app = FastAPI()

@app.get("/")
def home():
    return {"status": "api-service running"}

app.include_router(agent_router)