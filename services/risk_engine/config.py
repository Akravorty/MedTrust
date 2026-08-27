"""
services/risk_engine/config.py

Centralized configuration for the Risk Engine.

IMPORTANT — READ BEFORE EDITING:
This file intentionally holds ONLY Risk-Engine-local constants.
If ACCEPT/HOLD/REJECT thresholds already live in shared/constants.py in the
real repo, DO NOT duplicate them here — import from shared/constants.py
instead and delete the local copies below. This file exists as a safe
starting point in case shared/constants.py does not yet define them.

Every threshold below is a PROTOTYPE / DEMO value, not a clinical guideline.
That distinction should be stated explicitly anywhere these are surfaced
(README, judge Q&A, code comments).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Risk score → decision thresholds (per Integration Master, Person 1 doc)
# ---------------------------------------------------------------------------
ACCEPT_THRESHOLD = 0.3   # score < 0.3           -> ACCEPT
REJECT_THRESHOLD = 0.7   # score > 0.7           -> REJECT
# 0.3 <= score <= 0.7                             -> HOLD

# ---------------------------------------------------------------------------
# RULE 3 — severe identity mismatch (OCR vs QR)
# ---------------------------------------------------------------------------
# ocr_qr_match_score is 1.0 = exact match, lower = fuzzier/worse match.
# Below this threshold we treat the identity mismatch as "severe".
# CHOSEN OUTCOME: REJECT (documented rationale below).
#
# Rationale for REJECT over HOLD: a severe identity mismatch means we cannot
# be confident *what medicine this batch actually is* — that is an identity/
# safety problem, not a quality-of-evidence problem, so it is treated with
# the same severity as an expired batch rather than routed to manual review.
# This is a prototype judgment call — flag to the team if a different
# behavior (e.g. HOLD) is preferred; it's a one-line change (see
# SEVERE_MISMATCH_DECISION below).
OCR_QR_SEVERE_MISMATCH_THRESHOLD = 0.4
SEVERE_MISMATCH_DECISION = "REJECT"  # "REJECT" or "HOLD" — single source of truth

# ---------------------------------------------------------------------------
# RULE 2 — missing cold-chain evidence
# ---------------------------------------------------------------------------
# ASSUMPTION / KNOWN SCHEMA GAP (flagging per master-doc rule: "if something
# appears missing, STOP and explain the conflict before changing it"):
#
# The shared Batch schema has no explicit `requires_cold_chain: bool` field.
# Until the team adds one, cold-chain requirement is inferred from
# medicine_name against this configurable list. THIS IS A STAND-IN, not a
# silent schema change — nothing in shared/schemas.py is touched. If the team
# later adds a real field, delete COLD_CHAIN_MEDICINE_KEYWORDS and switch
# `rules.py` to read the real field instead (one function to change:
# `_requires_cold_chain`).
COLD_CHAIN_MEDICINE_KEYWORDS = (
    "insulin",
    "vaccine",
    "vaccination",
    "biologic",
    "erythropoietin",
    "immunoglobulin",
)

# Minimum number of temperature log entries considered "evidence present"
# for a cold-chain medicine. Zero entries = missing evidence -> RULE_MISSING_COLD_CHAIN.
MIN_TEMP_LOG_ENTRIES_REQUIRED = 1

# A temperature deviation is counted when a logged reading falls outside this
# band (Celsius). This is a demo band for vaccine/insulin-style cold chain
# (typically 2-8C); NOT a clinical specification.
COLD_CHAIN_SAFE_MIN_C = 2.0
COLD_CHAIN_SAFE_MAX_C = 8.0

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
MODEL_VERSION = "risk-xgb-v1.0"
MODEL_ARTIFACT_DIR = "services/risk_engine/model"
MODEL_ARTIFACT_FILENAME = "xgb_risk_model.json"
MODEL_METADATA_FILENAME = "model_metadata.json"

# Ordered feature list — order is load-bearing. Training and inference MUST
# use this exact order. Do not reorder without retraining.
FEATURE_ORDER = (
    "days_to_expiry",
    "temp_log_deviation_count",
    "supplier_reject_rate_3mo",
    "supplier_reject_rate_6mo",
    "ocr_qr_match_score",
    "physical_inspection_flag_count",
    "days_since_manufacture",
    "batch_size",
)

RANDOM_SEED = 42

# ---------------------------------------------------------------------------
# Named errors (must match Integration Master Section 5 exactly)
# ---------------------------------------------------------------------------
ERR_BATCH_NOT_FOUND = "Batch not found"
ERR_RISK_UNAVAILABLE = "Risk evaluation unavailable"
