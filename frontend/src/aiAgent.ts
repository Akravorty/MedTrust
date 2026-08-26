/// <reference types="vite/client" />
// src/services/aiAgent.ts
//
// Isolated AI agent service for the MediTrust dashboard (Person 5 scope).
// Uses Groq's free-tier API (OpenAI-compatible REST format).
// This file does NOT modify or depend on any other teammate's code —
// it only reads whatever decision/batch data you pass into it,
// so it's safe to drop in without touching shared/schemas.py consumers.

const GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions";
const GROQ_API_KEY = import.meta.env.VITE_GROQ_API_KEY;

// Fast, free, solid quality model on Groq as of now.
// Swap the model string if Groq deprecates/renames it later.
const MODEL = "openai/gpt-oss-120b";

interface AgentResponse {
  text: string;
  error?: string;
}

/**
 * Generic call to the Groq chat completion endpoint.
 * Keep this as the single low-level function; build higher-level
 * helpers (below) on top of it so prompt logic stays in one place.
 */
async function callGroq(systemPrompt: string, userPrompt: string): Promise<AgentResponse> {
  if (!GROQ_API_KEY) {
    return { text: "", error: "Missing VITE_GROQ_API_KEY. Check your .env file." };
  }

  try {
    const response = await fetch(GROQ_API_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${GROQ_API_KEY}`,
      },
      body: JSON.stringify({
        model: MODEL,
        messages: [
          { role: "system", content: systemPrompt },
          { role: "user", content: userPrompt },
        ],
        temperature: 0.3,
        max_tokens: 500,
      }),
    });

    if (!response.ok) {
      const errBody = await response.text();
      return { text: "", error: `Groq API error ${response.status}: ${errBody}` };
    }

    const data = await response.json();
    const text = data.choices?.[0]?.message?.content ?? "";
    return { text };
  } catch (err) {
    return { text: "", error: err instanceof Error ? err.message : "Unknown error calling Groq" };
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

  return callGroq(systemPrompt, userPrompt);
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

  return callGroq(systemPrompt, userPrompt);
}
