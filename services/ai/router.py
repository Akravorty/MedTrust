"""
services/ai/router.py

Server-side proxy for the dashboard's Groq calls. The browser sends only
prompts to this route; the Groq API key never leaves the backend. (Any
VITE_* variable is compiled into the public JS bundle, so a key must not
live in the frontend.)
"""

from __future__ import annotations

import logging
import os

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

logger = logging.getLogger("meditrust.ai.router")

router = APIRouter(prefix="/ai", tags=["ai"])

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
_TIMEOUT_SECONDS = 30.0


class CompletionRequest(BaseModel):
    system_prompt: str = Field(..., min_length=1, max_length=4000)
    user_prompt: str = Field(..., min_length=1, max_length=20000)


class CompletionResponse(BaseModel):
    text: str


@router.post("/complete", response_model=CompletionResponse)
async def complete(req: CompletionRequest) -> CompletionResponse:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="AI service is not configured")

    payload = {
        "model": os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL),
        "messages": [
            {"role": "system", "content": req.system_prompt},
            {"role": "user", "content": req.user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 500,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                GROQ_API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError:
        logger.exception("Groq request failed")
        raise HTTPException(status_code=502, detail="AI provider unreachable")

    if resp.status_code != 200:
        # Log upstream detail server-side; do not leak it to the browser.
        logger.error("Groq returned %s: %s", resp.status_code, resp.text[:500])
        raise HTTPException(status_code=502, detail="AI provider error")

    text = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return CompletionResponse(text=text)
