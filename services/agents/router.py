"""
services/agents/router.py

Person 4 (Agentic Layer) — FastAPI router exposing the evidence-grounded
Q&A agent and the supplier intelligence agent to the rest of MediTrust.

Responsibilities:
    - Wrap the synchronous agent entry points (`run_qa_agent`,
      `run_supplier_agent`) in a threadpool so they don't block the event
      loop, since both make blocking Anthropic/httpx calls under the hood.
    - Translate the agent layer's plain exceptions into HTTP responses:
        AgentUnavailableError      -> 503 "Agent unavailable"
        UnsupportedLanguageError   -> 400 (bad `lang` value)
        anything else unexpected  -> 500, logged with a stack trace
    - Support the Multilingual Alert Engine's `lang` parameter as EITHER a
      query string (`?lang=hi`) or a header (`X-Lang: hi`), per the SIH
      localized notification requirement — query takes precedence if both
      are supplied.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from shared.schemas import AgentResponse, SupplierAlert
from services.agents.qa_agent import AgentUnavailableError, run_qa_agent
from services.agents.supplier_agent import (
    UnsupportedLanguageError,
    run_supplier_agent,
)

logger = logging.getLogger("meditrust.agents.router")

router = APIRouter(prefix="/agents", tags=["agents"])

_AGENT_UNAVAILABLE_DETAIL = "Agent unavailable"
_SUPPORTED_LANGS = {"hi", "or"}


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #

class QARequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Natural-language question about a batch, decision, trace, or supplier.",
        examples=["Why was batch DEMO-HOLD put on hold?"],
    )


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _resolve_lang(lang: Optional[str], x_lang: Optional[str]) -> Optional[str]:
    """
    Query param takes precedence over header when both are supplied.
    Returns None (== English / no translation) when neither is set.
    Validates the value eagerly so we can return a clean 400 here rather
    than relying solely on the agent layer's own validation.
    """
    resolved = lang if lang is not None else x_lang
    if resolved is None:
        return None
    if resolved not in _SUPPORTED_LANGS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported lang '{resolved}'. Supported values: {sorted(_SUPPORTED_LANGS)}.",
        )
    return resolved


def _raise_agent_unavailable(exc: AgentUnavailableError) -> None:
    logger.error("Agent unavailable: %s (cause: %r)", exc.reason, exc.cause)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=_AGENT_UNAVAILABLE_DETAIL,
    ) from exc


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.post(
    "/qa",
    response_model=AgentResponse,
    status_code=status.HTTP_200_OK,
    summary="Ask the evidence-grounded Q&A agent a question about a batch, decision, trace, or supplier.",
)
async def ask_qa(payload: QARequest) -> AgentResponse:
    try:
        return await run_in_threadpool(run_qa_agent, payload.query)
    except AgentUnavailableError as exc:
        _raise_agent_unavailable(exc)
    except Exception as exc:  # defensive: never leak a raw 500 stack trace to the client
        logger.exception("Unexpected error while running the QA agent.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error while processing the QA request.",
        ) from exc


@router.get(
    "/supplier/{supplier_id}/alert",
    response_model=SupplierAlert,
    status_code=status.HTTP_200_OK,
    summary="Evaluate a supplier's reject-rate trend and, if degraded, get an LLM-drafted escalation.",
)
async def get_supplier_alert(
    supplier_id: str,
    lang: Optional[str] = Query(
        default=None,
        description="Optional 'hi' or 'or' to localize draft_escalation_message. Overrides X-Lang header.",
    ),
    x_lang: Optional[str] = Header(
        default=None,
        alias="X-Lang",
        description="Optional 'hi' or 'or' to localize draft_escalation_message. Used if `lang` query param is absent.",
    ),
) -> SupplierAlert:
    resolved_lang = _resolve_lang(lang, x_lang)

    try:
        return await run_in_threadpool(run_supplier_agent, supplier_id, resolved_lang)
    except UnsupportedLanguageError as exc:
        # Defensive: _resolve_lang already validates, but the agent layer's
        # own check is kept authoritative in case this router and the agent
        # ever drift on supported language codes.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except AgentUnavailableError as exc:
        _raise_agent_unavailable(exc)
    except Exception as exc:
        logger.exception("Unexpected error while running the supplier agent for '%s'.", supplier_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error while processing the supplier alert request.",
        ) from exc


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Lightweight liveness check for the agentic layer (does not call Anthropic).",
)
async def health() -> dict:
    return {"status": "ok", "component": "agents"}