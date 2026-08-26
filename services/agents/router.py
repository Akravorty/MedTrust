"""
services/agents/router.py

Person 4 (Agentic Layer) — FastAPI router exposing the evidence-grounded
Q&A agent and the supplier intelligence agent to the rest of MediTrust.
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

# Match prefix with integration specs (singular /agent)
router = APIRouter(prefix="/agent", tags=["agents"])

_AGENT_UNAVAILABLE_DETAIL = "Agent unavailable"
_SUPPORTED_LANGS = {"en", "hi", "or"}  # Includes English, Hindi, and Odia


class QARequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Natural-language question about a batch, decision, trace, or supplier.",
        examples=["Why was batch DEMO-HOLD put on hold?"],
    )


def _resolve_lang(lang: Optional[str], x_lang: Optional[str]) -> str:
    """
    Query param takes precedence over header when both are supplied.
    Defaults to 'en' when neither is set.
    """
    resolved = lang if lang is not None else x_lang
    if resolved is None:
        return "en"
    if resolved not in _SUPPORTED_LANGS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported lang '{resolved}'. Supported values: {sorted(_SUPPORTED_LANGS)}.",
        )
    return resolved


def _raise_agent_unavailable(exc: AgentUnavailableError) -> None:
    logger.error("Agent unavailable: %s (cause: %r)", getattr(exc, "reason", str(exc)), getattr(exc, "cause", None))
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=_AGENT_UNAVAILABLE_DETAIL,
    ) from exc


@router.post(
    "/qa",
    response_model=AgentResponse,
    status_code=status.HTTP_200_OK,
    summary="Ask the evidence-grounded Q&A agent a question.",
)
async def ask_qa(payload: QARequest) -> AgentResponse:
    try:
        return await run_in_threadpool(run_qa_agent, payload.query)
    except AgentUnavailableError as exc:
        _raise_agent_unavailable(exc)
    except Exception as exc:
        logger.exception("Unexpected error while running the QA agent.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error while processing the QA request.",
        ) from exc


@router.get(
    "/supplier/{supplier_id}/alert",
    response_model=SupplierAlert,
    status_code=status.HTTP_200_OK,
    summary="Evaluate supplier reject-rate trends and get an LLM-drafted escalation.",
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
        description="Optional 'hi' or 'or' to localize draft_escalation_message.",
    ),
) -> SupplierAlert:
    resolved_lang = _resolve_lang(lang, x_lang)

    try:
        return await run_in_threadpool(run_supplier_agent, supplier_id, resolved_lang)
    except UnsupportedLanguageError as exc:
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
    summary="Lightweight liveness check for the agentic layer.",
)
async def health() -> dict:
    return {"status": "ok", "component": "agents"}