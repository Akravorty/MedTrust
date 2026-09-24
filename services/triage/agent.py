"""
services/triage/agent.py

Digital triage agent. Same grounded, tool-calling pattern as
services/agents/qa_agent.py: the model must call data tools to gather
facts about the patient and nearby facility capacity before it is allowed
to submit an urgency band, so the output is never a guess from the symptom
text alone.

This directly answers the PS's "digital triage" bullet, and its urgency
band feeds both the referral suggestion and the emergency-escalation flag
on the queue ticket.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import ValidationError

from services.triage.tools import execute_triage_tool
from shared.schemas import FacilityLevel, TriageResult, UrgencyBand

load_dotenv()

logger = logging.getLogger("meditrust.triage.agent")

DEFAULT_MODEL = "gemini-3.6-flash"
TRIAGE_AGENT_MODEL = os.environ.get("TRIAGE_AGENT_MODEL", DEFAULT_MODEL)

MAX_TOOL_ITERATIONS = 8
_VALID_CONFIDENCE = {"HIGH", "MEDIUM", "INSUFFICIENT_EVIDENCE"}
_VALID_URGENCY = {u.value for u in UrgencyBand}
_VALID_LEVELS = {l.value for l in FacilityLevel}


class TriageAgentUnavailableError(Exception):
    def __init__(self, reason: str, cause: Optional[Exception] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.cause = cause


SYSTEM_PROMPT = """You are the digital triage assistant used by ASHA and frontline health \
workers in rural India to help decide how urgently a patient needs care and which level of \
facility they should go to next.

You have four tools: get_patient_profile, get_patient_history, get_facility_capacity, and \
find_referral_target. Use them to check the patient's age, risk category, and prior visits, \
and to confirm the suggested facility can actually take the patient, before deciding.

Urgency bands, in increasing order of severity: ROUTINE, SOON, URGENT, EMERGENCY.
Facility levels, in increasing order of capability: SUB_CENTRE, PHC, CHC, RURAL_HOSPITAL, \
DISTRICT_HOSPITAL.

RULES:
1. You are a triage aid for a frontline worker, not a diagnosis engine. Never name a specific \
disease with certainty — describe the concern and the recommended next step.
2. Any of the following pushes urgency to at least URGENT and should be checked explicitly: \
chest pain, difficulty breathing, heavy bleeding, fits/seizures, loss of consciousness, a child \
under 5 with high fever or fast breathing, or a pregnant patient with bleeding or severe \
abdominal pain.
3. Every factual claim in your reasoning must be traceable to a tool result you retrieved.
4. If the tools return no data or you are not confident, set confidence to "INSUFFICIENT_EVIDENCE" \
and default toward a more cautious (higher) urgency band rather than a lower one.

IMPORTANT — how to finish: you do not need every tool, and you should not call the same tool twice. \
As soon as you have enough information to state an urgency band and a facility level — often after just \
1-2 tool calls — call submit_triage immediately as your very next action. Do not keep gathering more \
context once you already have enough to decide. submit_triage is itself your final answer, not an \
optional extra step after some other action.
"""

get_patient_profile_decl = types.FunctionDeclaration(
    name="get_patient_profile",
    description="Fetch the patient's age, gender, village, risk category, and home facility.",
    parameters={
        "type": "OBJECT",
        "properties": {"patient_id": {"type": "STRING", "description": "The patient identifier."}},
        "required": ["patient_id"],
    },
)

get_patient_history_decl = types.FunctionDeclaration(
    name="get_patient_history",
    description="Fetch the patient's recent visit/triage/referral history.",
    parameters={
        "type": "OBJECT",
        "properties": {"patient_id": {"type": "STRING", "description": "The patient identifier."}},
        "required": ["patient_id"],
    },
)

get_facility_capacity_decl = types.FunctionDeclaration(
    name="get_facility_capacity",
    description="Fetch a facility's level, bed availability, and teleconsult availability.",
    parameters={
        "type": "OBJECT",
        "properties": {"facility_id": {"type": "STRING", "description": "The facility identifier."}},
        "required": ["facility_id"],
    },
)

find_referral_target_decl = types.FunctionDeclaration(
    name="find_referral_target",
    description="Find the nearest facility at a given level (e.g. PHC, CHC) in the same district as a home facility.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "from_facility_id": {"type": "STRING", "description": "The patient's home/current facility id."},
            "target_level": {
                "type": "STRING",
                "enum": ["SUB_CENTRE", "PHC", "CHC", "RURAL_HOSPITAL", "DISTRICT_HOSPITAL"],
                "description": "The facility level to search for.",
            },
        },
        "required": ["from_facility_id", "target_level"],
    },
)

submit_triage_decl = types.FunctionDeclaration(
    name="submit_triage",
    description="Call this exactly once, as your final action, to submit the triage result.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "urgency": {
                "type": "STRING",
                "enum": ["ROUTINE", "SOON", "URGENT", "EMERGENCY"],
                "description": "Urgency band for this patient's symptoms.",
            },
            "suggested_facility_level": {
                "type": "STRING",
                "enum": ["SUB_CENTRE", "PHC", "CHC", "RURAL_HOSPITAL", "DISTRICT_HOSPITAL"],
                "description": "The facility level the patient should be seen at.",
            },
            "reasoning": {
                "type": "STRING",
                "description": "Plain-language explanation grounded in the tool results retrieved.",
            },
            "confidence": {
                "type": "STRING",
                "enum": ["HIGH", "MEDIUM", "INSUFFICIENT_EVIDENCE"],
            },
            "evidence_sources": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Which tool results the reasoning relied on.",
            },
        },
        "required": ["urgency", "suggested_facility_level", "reasoning", "confidence", "evidence_sources"],
    },
)

_GEMINI_TOOLS = types.Tool(
    function_declarations=[
        get_patient_profile_decl,
        get_patient_history_decl,
        get_facility_capacity_decl,
        find_referral_target_decl,
        submit_triage_decl,
    ]
)


def _build_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise TriageAgentUnavailableError("GEMINI_API_KEY is not configured in .env file.")
    return genai.Client(api_key=api_key)


def run_triage_agent(patient_id: str, symptoms_text: str, model: Optional[str] = None) -> TriageResult:
    client = _build_client()
    active_model = model or TRIAGE_AGENT_MODEL

    user_message = f"patient_id: {patient_id}\nReported symptoms: {symptoms_text}"
    contents: List[Any] = [
        types.Content(role="user", parts=[types.Part.from_text(text=user_message)])
    ]

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.0,
        tools=[_GEMINI_TOOLS],
    )

    for _ in range(1, MAX_TOOL_ITERATIONS + 1):
        try:
            response = client.models.generate_content(
                model=active_model, contents=contents, config=config,
            )
        except APIError as exc:
            logger.error("Gemini API Error: %s", exc)
            raise TriageAgentUnavailableError(f"Gemini API Error: {exc}", exc) from exc
        except Exception as exc:
            logger.error("Could not connect to Gemini API: %s", exc)
            raise TriageAgentUnavailableError(f"Could not connect to Gemini API: {exc}", exc) from exc

        if response.candidates and response.candidates[0].content:
            contents.append(response.candidates[0].content)

        if response.function_calls:
            submit_call = next((c for c in response.function_calls if c.name == "submit_triage"), None)
            if submit_call is not None:
                args = dict(submit_call.args) if submit_call.args else {}
                return _finalize(patient_id, symptoms_text, args)

            function_responses = []
            for call in response.function_calls:
                call_args = dict(call.args) if call.args else {}
                result = execute_triage_tool(call.name, call_args)
                function_responses.append(
                    types.Part.from_function_response(name=call.name, response={"result": result})
                )
            contents.append(types.Content(role="user", parts=function_responses))
        else:
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(
                        text="You must either call a data tool or call submit_triage to finalize."
                    )],
                )
            )

    # Fell out of the loop without a submission — fail safe toward caution,
    # never silently drop the patient.
    return TriageResult(
        triage_id=str(uuid.uuid4()), patient_id=patient_id, symptoms_text=symptoms_text,
        urgency=UrgencyBand.URGENT, suggested_facility_level=FacilityLevel.PHC,
        reasoning="Triage agent could not reach a grounded conclusion within its tool-call budget; "
                  "defaulting to URGENT/PHC out of caution. A human should review this case.",
        evidence_sources=[], confidence="INSUFFICIENT_EVIDENCE", decided_at=datetime.now(timezone.utc),
    )


def _finalize(patient_id: str, symptoms_text: str, submit_input: Dict[str, Any]) -> TriageResult:
    urgency_raw = str(submit_input.get("urgency", "")).upper()
    urgency = UrgencyBand(urgency_raw) if urgency_raw in _VALID_URGENCY else UrgencyBand.URGENT

    level_raw = str(submit_input.get("suggested_facility_level", "")).upper()
    level = FacilityLevel(level_raw) if level_raw in _VALID_LEVELS else FacilityLevel.PHC

    confidence = str(submit_input.get("confidence", "")).upper()
    if confidence not in _VALID_CONFIDENCE:
        confidence = "INSUFFICIENT_EVIDENCE"

    evidence_sources = submit_input.get("evidence_sources") or []
    if not isinstance(evidence_sources, list):
        evidence_sources = [str(evidence_sources)]

    reasoning = str(submit_input.get("reasoning", "")).strip() or "No reasoning provided."

    try:
        return TriageResult(
            triage_id=str(uuid.uuid4()), patient_id=patient_id, symptoms_text=symptoms_text,
            urgency=urgency, suggested_facility_level=level, reasoning=reasoning,
            evidence_sources=[str(s) for s in evidence_sources], confidence=confidence,
            decided_at=datetime.now(timezone.utc),
        )
    except ValidationError:
        return TriageResult(
            triage_id=str(uuid.uuid4()), patient_id=patient_id, symptoms_text=symptoms_text,
            urgency=UrgencyBand.URGENT, suggested_facility_level=FacilityLevel.PHC,
            reasoning=reasoning, evidence_sources=[], confidence="INSUFFICIENT_EVIDENCE",
            decided_at=datetime.now(timezone.utc),
        )
