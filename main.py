"""
main.py
FastAPI app entrypoint for MediTrust. Mounts each service's router and
initializes the shared DB on startup, per 00_INTEGRATION_MASTER.md Section 1
and Section 3 (API contract: Intake, Risk Engine, Ledger, Agents).
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.database import init_db, get_connection
from services.risk_engine.router import router as risk_router
from services.ledger.router import router as ledger_router
from services.intake.router import router as intake_router
from services.agents.router import router as agent_router
from services.alerts.router import router as alerts_router
from services.facilities.router import router as facilities_router
from services.patients.router import router as patients_router
from services.triage.router import router as triage_router
from services.referrals.router import router as referrals_router
from services.queue.router import router as queue_router
from services.teleconsult.router import router as teleconsult_router
from services.followups.router import router as followups_router
from services.dashboard.router import router as dashboard_router
from data.generate_supplier import generate_suppliers
from data.golden_batches import generate_batches
from data.seed_recall_demo import generate_recall_demo
from data.seed_care_access import generate_care_access_demo
from data.seed_realistic_batches import generate_realistic_batches
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    conn = get_connection()
    generate_suppliers(conn)         # idempotent — safe to call every startup
    generate_batches(conn)           # idempotent — safe to call every startup
    generate_realistic_batches(conn) # idempotent — realistic medicine seed data
    generate_recall_demo(conn)       # idempotent — safe to call every startup
    generate_care_access_demo(conn)  # idempotent — facilities/patients/queue demo data
    yield

app = FastAPI(title="MediTrust", lifespan=lifespan)

# Without this, every fetch() from the Vite dev server (localhost:5173) to
# this API (localhost:8000) is blocked by the browser before your code ever
# runs — Step 6 is invisible without it, and none of the errors it throws
# mention CORS by name, which is why it's easy to miss.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(risk_router)
app.include_router(ledger_router)
app.include_router(intake_router)
app.include_router(agent_router)
app.include_router(alerts_router)
app.include_router(facilities_router)
app.include_router(patients_router)
app.include_router(triage_router)
app.include_router(referrals_router)
app.include_router(queue_router)
app.include_router(teleconsult_router)
app.include_router(followups_router)
app.include_router(dashboard_router)

@app.get("/health")
def health():
    return {"status": "ok"}