"""
main.py

FastAPI app entrypoint for MediTrust. Mounts each service's router and
initializes the shared DB on startup, per 00_INTEGRATION_MASTER.md Section 1
and Section 3 (API contract: Intake, Risk Engine, Ledger, Agents).
"""

from fastapi import FastAPI

from shared.database import init_db
from services.risk_engine.router import router as risk_router
from services.ledger.router import router as ledger_router
from services.intake.router import router as intake_router
from services.agents.router import router as agent_router   

app = FastAPI(title="MediTrust")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


app.include_router(risk_router)
app.include_router(ledger_router)
app.include_router(intake_router)
app.include_router(agent_router)   # add this
app.include_router(risk_router)

@app.get("/health")
def health():
    return {"status": "ok"}