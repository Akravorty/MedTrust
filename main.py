from fastapi import FastAPI
from services.agents.router import router as agents_router

app = FastAPI(title="MediTrust API")

app.include_router(agents_router)