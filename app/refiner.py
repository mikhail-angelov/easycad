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
language selected in the interface.
"""

import json
import re
import textwrap
from dataclasses import dataclass

from .llm import DEFAULT_PROVIDER, LLMError, stream_completion
from .skills import SKILL_MENU, clean_tags

VERDICTS = {"ready", "refine", "clarify", "invalid"}

TRIAGE_SYSTEM_PROMPT_TEMPLATE = textwrap.dedent("""\
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
    "reason" in {response_language}, regardless of the language of the user's
    request.

    SKILLS: independently of the verdict, decide which specialised generation
    skills the request needs, and return their tags in "skills". Available:
{skill_menu}
    Return only tags that clearly apply (usually none). Use [] when unsure.

    Return ONLY a JSON object, no markdown, of exactly this shape:
    {{
      "verdict": "ready" | "refine" | "clarify" | "invalid",
      "refined_prompt": "<only when verdict is 'refine'>",
      "questions": [ {{ "question": "<text>", "options": ["<opt1>", "<opt2>"] }} ],
      "reason": "<only when verdict is 'invalid'>",
      "skills": ["<tag>", ...]
    }}
    Include only the fields relevant to the chosen verdict; use [] / omit the rest.
""")


def _triage_system_prompt(response_language: str) -> str:
    language = "Russian" if response_language == "ru" else "English"
    return TRIAGE_SYSTEM_PROMPT_TEMPLATE.format(
        skill_menu=SKILL_MENU,
        response_language=language,
    )


@dataclass
class TriageResult:
    verdict: str  # ready | refine | clarify | invalid
    refined_prompt: str | None = None
    questions: list[dict] | None = None
    reason: str | None = None
    skills: list[str] | None = None  # generation skills to load (SPEC15)

    def __post_init__(self) -> None:
        if self.questions is None:
            self.questions = []
        if self.skills is None:
            self.skills = []


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text.strip(), re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _parse(raw: str, response_language: str = "en") -> TriageResult:
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

    skills = clean_tags(data.get("skills"))

    # Reconcile verdict with the payload actually provided.
    if verdict == "refine" and not refined:
        verdict = "ready"
    if verdict == "clarify" and not questions:
        verdict = "refine" if refined else "ready"
    if verdict == "invalid" and not reason:
        reason = (
            "Запрос, похоже, не соответствует текущей модели."
            if response_language == "ru"
            else "The request appears inconsistent with the current model."
        )

    return TriageResult(verdict, refined, questions, reason, skills)


async def triage(
    prompt: str,
    current_code: str,
    provider: str = DEFAULT_PROVIDER,
    model: str | None = None,
    api_key: str | None = None,
    response_language: str = "en",
) -> TriageResult:
    """Classify a user request against the current model (one LLM call)."""
    user_msg = (
        f"Current CadQuery code (with geometry info):\n```python\n{current_code}\n```\n\n"
        f"User request: {prompt}"
    )
    result = await stream_completion(
        [
            {"role": "system", "content": _triage_system_prompt(response_language)},
            {"role": "user", "content": user_msg},
        ],
        provider, model, temperature=0.1, max_tokens=16_384, api_key=api_key,
        operation="triage", prompt_for_log=prompt,
    )
    return _parse(result.content, response_language)
