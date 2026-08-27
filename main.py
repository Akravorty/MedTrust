"""
main.py
FastAPI app entrypoint for MediTrust. Mounts each service's router and
initializes the shared DB on startup, per 00_INTEGRATION_MASTER.md Section 1
and Section 3 (API contract: Intake, Risk Engine, Ledger, Agents).
"""
from fastapi import FastAPI
from shared.database import init_db, get_connection
from services.risk_engine.router import router as risk_router
from services.ledger.router import router as ledger_router
from services.intake.router import router as intake_router
from services.agents.router import router as agent_router
from data.generate_supplier import generate_suppliers
from data.golden_batches import generate_batches
from data.seed_recall_demo import generate_recall_demo

app = FastAPI(title="MediTrust")


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    conn = get_connection()
    generate_suppliers(conn)   # idempotent — safe to call every startup
    generate_batches(conn)     # idempotent — safe to call every startup
    generate_recall_demo(conn) # idempotent — safe to call every startup

app.include_router(risk_router)
app.include_router(ledger_router)
app.include_router(intake_router)
app.include_router(agent_router)

@app.get("/health")
def health():
    return {"status": "ok"}