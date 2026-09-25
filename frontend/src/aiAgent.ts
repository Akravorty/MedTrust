/// <reference types="vite/client" />
// src/services/aiAgent.ts
//
// Isolated AI agent service for the MediTrust dashboard (Person 5 scope).
// Calls the backend /ai/complete proxy (which talks to Groq server-side).
// This file does NOT modify or depend on any other teammate's code —
// it only reads whatever decision/batch data you pass into it,
// so it's safe to drop in without touching shared/schemas.py consumers.

const API_BASE = import.meta.env.VITE_API_BASE as string;

interface AgentResponse {
  text: string;
  error?: string;
}

/**
 * Generic call to the backend AI proxy (POST /ai/complete).
 * The Groq API key lives on the server only; never put provider keys in a
 * VITE_* variable, because Vite bundles those into public browser JS.
 * Keep this as the single low-level function; build higher-level
 * helpers (below) on top of it so prompt logic stays in one place.
 */
async function callAI(systemPrompt: string, userPrompt: string): Promise<AgentResponse> {
  try {
    const response = await fetch(`${API_BASE}/ai/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ system_prompt: systemPrompt, user_prompt: userPrompt }),
    });

    if (!response.ok) {
      return { text: "", error: `AI service error ${response.status}` };
    }

    const data = await response.json();
    return { text: data.text ?? "" };
  } catch (err) {
    return { text: "", error: err instanceof Error ? err.message : "Unknown error calling AI service" };
  }
}

/**
 * Explains a batch's ACCEPT / HOLD / REJECT decision in plain language
 * for a pharmacist viewing the dashboard.
 *
 * Uses strict rules: the decision is final, no hallucination, data-only.
 */
export async function explainDecision(batchData: Record<string, unknown>): Promise<AgentResponse> {
  const systemPrompt =
    "You are a pharmacy quality-control assistant. Your ONLY job is to explain a " +
    "decision that has ALREADY been made by our system — you must NEVER change, " +
    "second-guess, or contradict that decision, even if you think a different " +
    "decision would be more appropriate. " +
    "STRICT RULES: " +
    "1) The 'decision' field below is final and correct. State it exactly as given. " +
    "2) Use ONLY the fields present in the JSON data. Do NOT invent numbers, scores, " +
    "risk factors, or reasons that are not explicitly present in the data. " +
    "3) If the data does not contain enough detail to explain the decision, say so " +
    "plainly instead of making something up. " +
    "4) Keep it to 2-3 clear, professional sentences for a hospital pharmacist.";

  const userPrompt =
    `Batch decision (already final, do not change): ${String(batchData.decision ?? batchData.status ?? "UNKNOWN")}\n\n` +
    `Full batch data:\n${JSON.stringify(batchData, null, 2)}\n\n` +
    `Explain why this exact decision was made, using only the data above.`;

  return callAI(systemPrompt, userPrompt);
}

/**
 * Summarizes a set of recent scans/decisions for the dashboard's activity feed.
 */
export async function summarizeActivity(recentBatches: Record<string, unknown>[]): Promise<AgentResponse> {
  const systemPrompt =
    "You are a pharmacy quality-control assistant. Summarize recent batch scan " +
    "activity in 2-4 sentences, highlighting any patterns (e.g. repeated holds/rejects " +
    "from the same supplier) a pharmacist should be aware of.";

  const userPrompt = `Recent batches:\n${JSON.stringify(recentBatches, null, 2)}`;

  return callAI(systemPrompt, userPrompt);
}
