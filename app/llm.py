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

# Each entry additionally carries (SPEC14):
#   - "models":     static allow-list surfaced in the BYOK model picker.
#   - "key_prefix": expected API-key prefix, for fast client-side/server-side
#                   validation before spending a live test call.
# "ui": False keeps a provider usable in code/tests but hidden from the UI
# dropdown (e.g. plain OpenAI is kept but not offered).
PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEP_SEEK_KEY",
        "default_model": "deepseek-chat",  # best results in POC
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "key_prefix": "sk-",
        "ui": True,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPEN_ROUTER_KEY",
        "default_model": "deepseek/deepseek-chat",  # DeepSeek is the default
        "models": [
            "deepseek/deepseek-chat",
            "openai/gpt-4o-mini",
            "anthropic/claude-sonnet-4.5",
            "google/gemini-2.5-flash",
        ],
        "key_prefix": "sk-or-",
        "ui": True,
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o"],
        "key_prefix": "sk-",
        "ui": False,  # kept in code, NOT surfaced in the UI dropdown
    },
}

DEFAULT_PROVIDER = "deepseek"

# On trial, provider AND model are hard-forced to the operator's DeepSeek key so
# nobody runs an expensive model on our dime (SPEC14).
TRIAL_PROVIDER = "deepseek"
TRIAL_MODEL = "deepseek-chat"


def ui_providers() -> dict:
    """Provider metadata surfaced to the frontend:
    {name: {default_model, models, key_prefix}}. `key_prefix` lets the UI show a
    correct placeholder without re-hardcoding the prefixes defined here."""
    return {
        name: {
            "default_model": cfg["default_model"],
            "models": cfg["models"],
            "key_prefix": cfg["key_prefix"],
        }
        for name, cfg in PROVIDERS.items()
        if cfg.get("ui")
    }


def key_prefix_ok(provider: str, key: str) -> bool:
    """Cheap format check: does `key` start with the provider's expected prefix?"""
    cfg = PROVIDERS.get(provider)
    if not cfg:
        return False
    return key.startswith(cfg["key_prefix"])

# ── Starting geometry ────────────────────────────────────────────────────────

INITIAL_CODE = textwrap.dedent("""\
    import cadquery as cq

    # ── Parameters (edit these to resize the model) ──
    WIDTH = 50   # X, mm
    DEPTH = 80   # Y, mm
    HEIGHT = 30  # Z, mm

    # Starting solid — describe a change in the chat to evolve it.
    result = cq.Workplane("XY").box(WIDTH, DEPTH, HEIGHT)
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
    4. PARAMETERS BLOCK. Keep a block of UPPER_CASE named constants at the top
       of the script, right after the imports (a "# ── Parameters ──" comment),
       holding EVERY primary dimension: sizes, thicknesses, wall widths, offsets,
       gaps, hole diameters, radii, fillet/chamfer sizes, counts, and positions.
       The build code below MUST reference these constants — never write a
       primary dimension as a bare magic number inside an operation. When a new
       feature needs a dimension, ADD a new named constant (with a short unit
       comment) to this block and use it. Derive dependent values from the
       constants (e.g. `WIDTH / 2`) rather than hard-coding.
    5. APPEND-ONLY elsewhere. The Parameters block is the ONLY region you may
       edit (to add constants). Do NOT modify, reorder, or rewrite any other
       existing code — copy it exactly and add new operations at the end, before
       the Geometry info comment. Exception: the user explicitly asks to change
       existing code.
    6. Use millimeters as units.
    7. Write clean, readable code with a short comment for each logical step.

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


def make_client(provider: str, api_key: str | None = None) -> OpenAI:
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown provider: {provider}")
    cfg = PROVIDERS[provider]
    # BYOK: a caller-supplied key wins; env is only a local/dev fallback.
    key = api_key or os.getenv(cfg["api_key_env"])
    if not key:
        raise LLMError(f"No API key for provider '{provider}'. Add your key in settings.")
    return OpenAI(base_url=cfg["base_url"], api_key=key)


def validate_key_live(provider: str, key: str) -> tuple[bool, str | None]:
    """Make a minimal, cheap completion to confirm the key works (SPEC14).

    Returns (ok, reason). Auth/permission failures map to a friendly reason;
    success returns (True, None). The caller is expected to have already passed
    the cheap `key_prefix_ok` check and rate limiting before spending this call.
    """
    try:
        client = make_client(provider, key)
        client.chat.completions.create(
            model=PROVIDERS[provider]["default_model"],
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0,
        )
    except Exception as exc:  # noqa: BLE001 — normalize SDK/transport errors
        status = getattr(exc, "status_code", None)
        if status in (401, 403):
            return False, f"Key rejected by {provider}."
        # Anything else (network, rate limit on the provider, etc.) — surface it
        # but do not claim the key is bad.
        return False, f"Could not verify the key with {provider}: {exc}"
    return True, None


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
    temperature: float = 0.2,
    api_key: str | None = None,
) -> str:
    """Ask the LLM to append the requested modification to `current_code`.

    A higher `temperature` yields more varied output — used to generate several
    distinct candidates for the retry-with-variations flow.
    """
    client = make_client(provider, api_key)
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
            temperature=temperature,
            max_tokens=4096,
        )
    except Exception as exc:  # noqa: BLE001 — normalize SDK/transport errors
        raise LLMError(str(exc)) from exc

    raw = response.choices[0].message.content or ""
    return strip_markdown_fences(raw)
