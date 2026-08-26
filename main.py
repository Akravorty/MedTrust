from fastapi import FastAPI
from services.agents.router import router as agent_router

app = FastAPI(title="MediTrust API")

# Include Person 4's Agent Router
app.include_router(agent_router)

@app.get("/")
def root():
    return {"message": "MediTrust Agentic API is running"}