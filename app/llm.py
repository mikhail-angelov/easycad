"""LLM code generation for the CadQuery chat pipeline (Stage 2).

Ports the POC-proven system prompt and provider config. Given the current
CadQuery code and a modification request, returns new code that appends the
requested feature. OpenAI-compatible providers only.
"""

import logging
import os
import re
import textwrap
import time

from openai import OpenAI

from .skills import render as skills_render

log = logging.getLogger("easycad.llm")

# Hard ceiling on any single provider call. The OpenAI SDK defaults to 600s,
# which is far longer than the edge proxy's response timeout — so a slow
# generation used to hang until the proxy reset the (HTTP/2) connection
# (ERR_HTTP2_PROTOCOL_ERROR in the browser) with nothing logged. Timing out
# here turns that into a fast, logged LLMError the API can report cleanly.
LLM_TIMEOUT = float(os.getenv("EASYCAD_LLM_TIMEOUT", "90"))

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
        "default_model": "deepseek-v4-flash",  # best results in POC
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "key_prefix": "sk-",
        "ui": True,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPEN_ROUTER_KEY",
        "default_model": "deepseek/deepseek-v4-flash",  # DeepSeek is the default
        "models": [
            "deepseek/deepseek-v4-flash",
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
TRIAL_MODEL = "deepseek-v4-flash"


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
    - WORKPLANE CENTER. .workplane() defaults to centerOption="ProjectedOrigin",
      which projects the PARENT origin onto the face — NOT the face centre. When
      placing a feature on an off-centre face (e.g. a box moved with translate),
      pass centerOption="CenterOfBoundBox" (or .center(x, y)) or it lands in the
      wrong spot with no error.
    - PREFER FEATURE OPS over booleans when they fit: a hole is
      .faces(sel).workplane().hole(d), not .cut(a_cylinder); a hollow is .shell(-t),
      not a cut; rounded/bevelled edges are .fillet(r)/.chamfer(d), not unions.
      Boolean .cut()/.union() with an oversized tool is still fine for slots,
      pockets, and joining separate bodies.
    - CLOSE PROFILES: after .lineTo()/.threePointArc()/.spline(), call .close()
      before .extrude()/.revolve() unless the wire is meant to stay open.
    - .shell(): select the face(s) to REMOVE first, then .shell(-t) (negative =
      inward), e.g. .faces(">Z").shell(-2).
    - FILLET/CHAMFER radius MUST be smaller than the shortest adjacent edge, and
      neighbouring rounds must not overlap. If OCCT raises "BREP_API command not
      done", reduce the radius or fillet edge groups separately.
    - SELECTORS: >Z/<Z = highest/lowest face on that axis, |Z = edges parallel to
      Z, #Z = normal orthogonal to Z; combine with " and "/" or "/"not ". AVOID
      index selectors like ">>Z[1]" — the index shifts when earlier steps add
      geometry; prefer a geometric selector or .tag()/.faces(tag=...).
    - Fluent Workplane API only. Do NOT use the free-function API
      (`from cadquery.func import *`, or `a + b`/`a - b` on raw shapes) or mix them.
    - TEXT. Workplane.text(txt, fontsize, distance, ...) has NO `cut` argument.
      Its signature is text(txt, fontsize, distance, combine='cut'|'a'|'s'|bool,
      halign=..., valign=...). fontsize = letter height; distance = extrude depth
      (sign sets direction along the workplane normal). To ENGRAVE recessed text
      into a face: select the face and cut inward, e.g.
        result = result.faces(">Z").workplane().text(S, H, -D)   # combine='cut' default
      To EMBOSS raised text on a face, extrude outward and add:
        result = result.faces(">Z").workplane().text(S, H, D, combine='a')
      To build a standalone text solid (e.g. to .cut()/.union() yourself later),
      pass combine=False — NOT cut=False.
""")

INITIAL_REPLACEMENT_PROMPT = """\
This is the first request for a new project. The current box is only a starter
placeholder, not part of the user's model. Replace the starter code entirely
with code for the requested model; do not append features to the placeholder.
"""


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
    return OpenAI(base_url=cfg["base_url"], api_key=key, timeout=LLM_TIMEOUT)


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
    skills: list[str] | None = None,
    replace_initial: bool = False,
    feedback: dict | None = None,
) -> str:
    """Ask the LLM to append the requested modification to `current_code`.

    A higher `temperature` yields more varied output — used to generate several
    distinct candidates for the retry-with-variations flow. `skills` are
    specialised recipe tags (from triage) injected as an extra system message
    only when relevant — see `app/skills.py` (SPEC15).

    `feedback` (in-turn repair): when a prior attempt this turn failed,
    `{"code": <failed script>, "error": <message>}` is appended so the model can
    fix its own mistake — the text-mode equivalent of an agentic tool loop. The
    model only ever sees its own code + the measured error, never a reference.
    """
    client = make_client(provider, api_key)
    resolved = resolve_model(provider, model)
    user_msg = (
        f"Current CadQuery code:\n```python\n{current_code}\n```\n\n"
        f"Modification request: {prompt}"
    )
    if feedback:
        user_msg += (
            "\n\nYour previous attempt this turn did NOT work — do not repeat the "
            "same mistake. Return corrected, complete code.\n"
            f"Failed attempt:\n```python\n{feedback.get('code', '')}\n```\n"
            f"Problem: {feedback.get('error', 'unknown error')}"
        )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if replace_initial:
        messages.append({"role": "system", "content": INITIAL_REPLACEMENT_PROMPT})
    skill_prompt = skills_render(skills)
    if skill_prompt:
        messages.append({"role": "system", "content": skill_prompt})
    messages.append({"role": "user", "content": user_msg})
    log.info(
        "llm.generate start provider=%s model=%s code_len=%d prompt_len=%d temp=%s skills=%s",
        provider, resolved, len(current_code), len(prompt), temperature, skills or [],
    )
    _t0 = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=resolved,
            messages=messages,
            temperature=temperature,
            max_tokens=4096,
        )
    except Exception as exc:  # noqa: BLE001 — normalize SDK/transport errors
        log.warning(
            "llm.generate FAIL provider=%s model=%s dur_ms=%d err=%s",
            provider, resolved, int((time.monotonic() - _t0) * 1000), exc,
        )
        raise LLMError(str(exc)) from exc

    raw = response.choices[0].message.content or ""
    log.info(
        "llm.generate ok provider=%s model=%s dur_ms=%d out_len=%d",
        provider, resolved, int((time.monotonic() - _t0) * 1000), len(raw),
    )
    return strip_markdown_fences(raw)
