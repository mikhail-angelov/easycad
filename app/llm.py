"""LLM code generation for the CadQuery chat pipeline (Stage 2).

Ports the POC-proven system prompt and provider config. Given the current
CadQuery code and a modification request, returns new code that appends the
requested feature. OpenAI-compatible providers only.
"""

import os
import re
import textwrap

from openai import OpenAI

# ── Providers (OpenAI-compatible) ────────────────────────────────────────────

PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEP_SEEK_KEY",
        "default_model": "deepseek-chat",  # best results in POC
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPEN_ROUTER_KEY",
        "default_model": "openai/gpt-4o-mini",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
    },
}

DEFAULT_PROVIDER = "deepseek"

# ── Starting geometry ────────────────────────────────────────────────────────

INITIAL_CODE = textwrap.dedent("""\
    import cadquery as cq

    # Starting solid — describe a change in the chat to evolve it.
    result = cq.Workplane("XY").box(50, 80, 30)
""")

# ── Stage 2 system prompt (proven in POC) ────────────────────────────────────

SYSTEM_PROMPT = textwrap.dedent("""\
    You are a CadQuery code generator. You receive the current CadQuery Python
    script and a user request to modify the 3D model.

    Rules:
    1. Return ONLY valid Python code — no markdown fences, no explanations.
    2. The script must define a variable `result` of type `cadquery.Workplane`
       (this is what gets exported to STL).
    3. Always `import cadquery as cq` at the top.
    4. DO NOT modify, reorder, or rewrite any existing code. Copy all existing
       code exactly as-is and only APPEND new code at the end (before the
       Geometry info comment). The only exception is if the user explicitly
       asks to change existing code.
    5. Use millimeters as units.
    6. Write clean, readable code with comments for each logical step.

    Important:
    - The code will have a "Geometry info" comment block at the end with the
      current bounding box, size, and topology. Use these exact coordinates
      for positioning new geometry. Do NOT guess coordinates.
    - cq.Workplane("XY").box(L, W, H) creates a box CENTERED at the origin.
      translate() moves the CENTER, not an edge. To place a box so its top
      face is at Z=T, use translate((x, y, T - H/2)). To place its bottom
      at Z=B, use translate((x, y, B + H/2)).
    - Use .edges("|Z") to select vertical edges for filleting.
    - When cutting, make the cutting block oversized in non-critical dimensions.
""")


class LLMError(Exception):
    """Raised when an LLM provider call fails or is misconfigured."""


def resolve_model(provider: str, model: str | None) -> str:
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown provider: {provider}")
    return model or PROVIDERS[provider]["default_model"]


def make_client(provider: str) -> OpenAI:
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown provider: {provider}")
    cfg = PROVIDERS[provider]
    api_key = os.getenv(cfg["api_key_env"])
    if not api_key:
        raise LLMError(f"{cfg['api_key_env']} is not set in the environment/.env")
    return OpenAI(base_url=cfg["base_url"], api_key=api_key)


def strip_markdown_fences(text: str) -> str:
    """Remove ```python ... ``` wrappers if the model added them anyway."""
    text = text.strip()
    text = re.sub(r"^```(?:python)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def generate_code(
    current_code: str,
    prompt: str,
    provider: str = DEFAULT_PROVIDER,
    model: str | None = None,
) -> str:
    """Ask the LLM to append the requested modification to `current_code`."""
    client = make_client(provider)
    resolved = resolve_model(provider, model)
    user_msg = (
        f"Current CadQuery code:\n```python\n{current_code}\n```\n\n"
        f"Modification request: {prompt}"
    )
    try:
        response = client.chat.completions.create(
            model=resolved,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=4096,
        )
    except Exception as exc:  # noqa: BLE001 — normalize SDK/transport errors
        raise LLMError(str(exc)) from exc

    raw = response.choices[0].message.content or ""
    return strip_markdown_fences(raw)
