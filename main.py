"""
main.py

FastAPI app entrypoint for MediTrust. Currently empty in the repo, so this
is a minimal scaffold: it mounts each service's router and initializes the
shared DB on startup. Person 1 / Person 2 / Person 4 should add their own
`app.include_router(...)` line here when their routers exist — this file
intentionally does not know anything about ledger internals.
"""

from fastapi import FastAPI

from shared.database import init_db
from services.ledger.router import router as ledger_router
from services.intake.router import router as intake_router
from services.agents.router import router as agent_router   # add this

app = FastAPI(title="MediTrust")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


app.include_router(ledger_router)
app.include_router(intake_router)
app.include_router(agent_router)   # add this


@app.get("/health")
def health():
    return {"status": "ok"}
