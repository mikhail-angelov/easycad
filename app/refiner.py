"""Stage 1: request triage.

Instead of always rewriting the user's prompt, a single LLM call classifies it
against the current model and returns one of four verdicts:

- "ready":   precise, unambiguous, consistent with the current model — the
             generator can build it directly, so the ORIGINAL prompt is used
             unchanged (avoids degrading an already-good prompt).
- "refine":  valid but underspecified — a refined prompt is proposed (the user
             confirms it before generation).
- "clarify": genuinely ambiguous — discrete clarifying questions are returned.
- "invalid": contradicts the current model or is impossible — a reason is
             returned and nothing is generated.

All human-facing text (refined_prompt, questions, reason) is produced in the
SAME language as the user's request.
"""

import json
import re
import textwrap
from dataclasses import dataclass

from .llm import DEFAULT_PROVIDER, LLMError, make_client, resolve_model

VERDICTS = {"ready", "refine", "clarify", "invalid"}

TRIAGE_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a request triage assistant for a CadQuery code generator. You
    receive the current CadQuery code (including an auto-generated "Geometry
    info" block with the exact bounding box, size, and topology) and a user
    request (in any language) to modify the 3D model.

    Classify the request into exactly one verdict:

    - "ready": The request is precise, unambiguous, self-contained, and
      consistent with the current model. The generator can build it directly.
      DO NOT rewrite it — return no refined_prompt.

    - "refine": The request is valid and consistent, but underspecified — it is
      missing exact coordinates, directions, or dimensions that the generator
      would otherwise have to guess. Provide a "refined_prompt" that adds those
      details from the Geometry info. When refining:
        * NEVER change the outer bounding box / overall size the user implied.
          Hollowing or opening a part removes material INWARD; never expand the
          part outward (no outward/positive shell).
        * Keep every explicit dimension the user gave.
        * Prefer explicit boolean operations (build a solid and cut/union it)
          over shell for hollowing — more reliable and size-preserving.
        * Preserve the user's intent; do not invent features.

    - "clarify": The request is genuinely ambiguous in a way you cannot resolve
      from the geometry (e.g. which of several equivalent faces). Provide up to
      2 "questions", each with 2-4 discrete "options".

    - "invalid": The request contradicts the current model or is impossible —
      e.g. it asks to create a shape/size that conflicts with the existing
      geometry (asking for a 50x80x30 box when the current model is a 40x40x40
      cube), or references a feature that does not exist. Provide a short
      "reason" describing the conflict.

    CRITICAL: Write "refined_prompt", every "question"/"options" entry, and
    "reason" in the SAME LANGUAGE as the user's request (Russian in -> Russian
    out, English in -> English out).

    Return ONLY a JSON object, no markdown, of exactly this shape:
    {
      "verdict": "ready" | "refine" | "clarify" | "invalid",
      "refined_prompt": "<only when verdict is 'refine'>",
      "questions": [ { "question": "<text>", "options": ["<opt1>", "<opt2>"] } ],
      "reason": "<only when verdict is 'invalid'>"
    }
    Include only the fields relevant to the chosen verdict; use [] / omit the rest.
""")


@dataclass
class TriageResult:
    verdict: str  # ready | refine | clarify | invalid
    refined_prompt: str | None = None
    questions: list[dict] | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.questions is None:
            self.questions = []


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text.strip(), re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _parse(raw: str) -> TriageResult:
    data = _extract_json(raw)
    if not isinstance(data, dict):
        # Safest fallback: treat as ready so we generate the original prompt.
        return TriageResult("ready")

    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        verdict = "ready"

    refined = data.get("refined_prompt")
    refined = str(refined).strip() if refined else None

    questions: list[dict] = []
    for q in data.get("questions") or []:
        if isinstance(q, dict) and q.get("question"):
            options = [str(o) for o in (q.get("options") or []) if str(o).strip()]
            questions.append({"question": str(q["question"]), "options": options})

    reason = data.get("reason")
    reason = str(reason).strip() if reason else None

    # Reconcile verdict with the payload actually provided.
    if verdict == "refine" and not refined:
        verdict = "ready"
    if verdict == "clarify" and not questions:
        verdict = "refine" if refined else "ready"
    if verdict == "invalid" and not reason:
        reason = "The request appears inconsistent with the current model."

    return TriageResult(verdict, refined, questions, reason)


def triage(
    prompt: str,
    current_code: str,
    provider: str = DEFAULT_PROVIDER,
    model: str | None = None,
) -> TriageResult:
    """Classify a user request against the current model (one LLM call)."""
    client = make_client(provider)
    resolved = resolve_model(provider, model)
    user_msg = (
        f"Current CadQuery code (with geometry info):\n```python\n{current_code}\n```\n\n"
        f"User request: {prompt}"
    )
    try:
        response = client.chat.completions.create(
            model=resolved,
            messages=[
                {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=1024,
        )
    except Exception as exc:  # noqa: BLE001
        raise LLMError(str(exc)) from exc

    return _parse(response.choices[0].message.content or "")
