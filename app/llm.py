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
from dataclasses import dataclass

from openai import AsyncOpenAI, OpenAI

from .crashlog import scrub_text
from .skills import render as skills_render

log = logging.getLogger("easycad.llm")

# Prompts and generated code can be large. Keep diagnostics useful without
# turning application logs into an unbounded store of user/model text.
LOG_PREVIEW_CHARS = 500
LOG_PREVIEW_EVENTS = 3

# The synchronous BYOK validation request has no client-disconnect lifecycle, so
# it keeps a hard ceiling. Streaming generation is cancelled by its HTTP request.
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
      The worker provides exactly one supported font: `DejaVu Sans`. Always pass
      `font="DejaVu Sans"` to every `.text(...)` call; never use Arial or a
      host-specific font. Its signature is
      text(txt, fontsize, distance, combine='cut'|'a'|'s'|bool,
      halign=..., valign=...). fontsize = letter height; distance = extrude depth
      (sign sets direction along the workplane normal). To ENGRAVE recessed text
      into a face: select the face and cut inward, e.g.
        result = result.faces(">Z").workplane().text(S, H, -D, font="DejaVu Sans")
      To EMBOSS raised text on a face, extrude outward and add:
        result = result.faces(">Z").workplane().text(S, H, D, combine='a', font="DejaVu Sans")
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


class LLMEmptyResponse(LLMError):
    """The provider completed successfully but returned no usable text."""


@dataclass(frozen=True)
class StreamResult:
    content: str
    reasoning_chars: int
    finish_reason: str | None
    request_id: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    first_chunk_ms: int | None
    stream_events: int
    content_events: int
    reasoning_events: int


def _log_preview(value: str | None) -> str:
    """Return a bounded, credential-scrubbed value safe for application logs."""
    return scrub_text(value or "")[:LOG_PREVIEW_CHARS]


def resolve_model(provider: str, model: str | None) -> str:
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown provider: {provider}")
    return model or PROVIDERS[provider]["default_model"]


def _provider_key(provider: str, api_key: str | None) -> tuple[dict, str]:
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown provider: {provider}")
    cfg = PROVIDERS[provider]
    # BYOK: a caller-supplied key wins; env is only a local/dev fallback.
    key = api_key or os.getenv(cfg["api_key_env"])
    if not key:
        raise LLMError(f"No API key for provider '{provider}'. Add your key in settings.")
    return cfg, key


def make_client(provider: str, api_key: str | None = None) -> OpenAI:
    cfg, key = _provider_key(provider, api_key)
    return OpenAI(base_url=cfg["base_url"], api_key=key, timeout=LLM_TIMEOUT, max_retries=0)


def make_async_client(provider: str, api_key: str | None = None) -> AsyncOpenAI:
    """Streaming generation client: no deadline and no implicit retries.

    The request is instead cancelled when the client disconnects. An SDK retry
    would outlive that request and hide the real provider attempt in the trace.
    """
    cfg, key = _provider_key(provider, api_key)
    return AsyncOpenAI(base_url=cfg["base_url"], api_key=key, timeout=None, max_retries=0)


async def stream_completion(
    messages: list[dict],
    provider: str,
    model: str | None,
    *,
    temperature: float,
    max_tokens: int,
    api_key: str | None = None,
    operation: str,
    prompt_for_log: str,
) -> StreamResult:
    """Consume one OpenAI-compatible stream and retain diagnostic metadata only."""
    client = make_async_client(provider, api_key)
    resolved = resolve_model(provider, model)
    log.info(
        "llm.stream start operation=%s provider=%s model=%s prompt_chars=%d prompt=%r",
        operation, provider, resolved, len(prompt_for_log), _log_preview(prompt_for_log),
    )
    t0 = time.monotonic()
    try:
        stream = await client.chat.completions.create(
            model=resolved, messages=messages, temperature=temperature,
            max_tokens=max_tokens, stream=True,
        )
        content: list[str] = []
        reasoning_chars = 0
        finish_reason: str | None = None
        usage = None
        first_chunk_ms: int | None = None
        stream_events = 0
        content_events = 0
        reasoning_events = 0
        async for chunk in stream:
            stream_events += 1
            if first_chunk_ms is None:
                first_chunk_ms = int((time.monotonic() - t0) * 1000)
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            event_content_chars = 0
            event_content: list[str] = []
            event_reasoning_chars = 0
            event_finish_reasons: list[str] = []
            for choice in chunk.choices or []:
                if choice.finish_reason is not None:
                    finish_reason = choice.finish_reason
                    event_finish_reasons.append(choice.finish_reason)
                delta = choice.delta
                text = getattr(delta, "content", None)
                if text:
                    content.append(text)
                    event_content.append(text)
                    event_content_chars += len(text)
                    content_events += 1
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    reasoning_chars += len(reasoning)
                    event_reasoning_chars += len(reasoning)
                    reasoning_events += 1
            # Log every provider event: this makes a 200 response with no
            # generated content diagnosable. Provider reasoning is counted but
            # deliberately never logged because it may contain user context.
            log.info(
                "llm.stream event operation=%s provider=%s model=%s event=%d choices=%d "
                "content_chars=%d content_preview=%r reasoning_chars=%d "
                "finish_reasons=%s usage=%s",
                operation, provider, resolved, stream_events, len(chunk.choices or []),
                event_content_chars,
                _log_preview("".join(event_content))
                if stream_events <= LOG_PREVIEW_EVENTS else "<omitted>",
                event_reasoning_chars, event_finish_reasons or None, usage is not None,
            )
    except Exception as exc:  # noqa: BLE001 — normalize SDK/transport errors
        log.warning(
            "llm.stream fail operation=%s provider=%s model=%s dur_ms=%d err=%r",
            operation, provider, resolved, int((time.monotonic() - t0) * 1000),
            _log_preview(str(exc)),
        )
        raise LLMError(str(exc)) from exc
    finally:
        await client.close()

    request_id = getattr(stream, "_request_id", None)
    if request_id is None:
        request_id = getattr(getattr(stream, "response", None), "headers", {}).get("x-request-id")
    result = StreamResult(
        content="".join(content), reasoning_chars=reasoning_chars,
        finish_reason=finish_reason, request_id=request_id,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None), first_chunk_ms=first_chunk_ms,
        stream_events=stream_events, content_events=content_events,
        reasoning_events=reasoning_events,
    )
    log.info(
        "llm.stream done operation=%s provider=%s model=%s dur_ms=%d first_chunk_ms=%s "
        "request_id=%s finish_reason=%s prompt_tokens=%s completion_tokens=%s "
        "total_tokens=%s stream_events=%d content_events=%d reasoning_events=%d "
        "content_chars=%d content_preview=%r reasoning_chars=%d",
        operation, provider, resolved, int((time.monotonic() - t0) * 1000),
        result.first_chunk_ms, result.request_id, result.finish_reason,
        result.prompt_tokens, result.completion_tokens, result.total_tokens,
        result.stream_events, result.content_events, result.reasoning_events,
        len(result.content), _log_preview(result.content), result.reasoning_chars,
    )
    if not result.content.strip():
        raise LLMEmptyResponse("The provider returned an empty response.")
    return result


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


def _repair_hint(error: str | None) -> str | None:
    """Classify a failed-execution error and return a short, relevant fix hint for
    the in-turn repair prompt (SPEC16 §6). Covers the FIVE classes that a runtime
    error text can actually distinguish — missing `result`, non-existent API,
    NameError, syntax, kernel/BREP. The other text-to-cad taxonomy classes (scale,
    missing-feature, positioning, selector-fragility) are correctness failures that
    execute fine and never reach this loop, so there is no error to classify.

    Delivered only on the repair attempt that hit this error, so the guidance is
    conditional and relevant instead of bloating the base system prompt (a blanket
    version of this advice measured net-negative, SPEC16 §4.3). Returns None when
    the error doesn't match a known class — then the model just sees the raw error.
    """
    if not error:
        return None
    e = error.lower()
    if "result" in e and ("not defined" in e or "no 'result'" in e):
        return ("Your script ran but never assigned `result`. The final statement "
                "MUST bind the finished model to `result`, e.g. `result = part`.")
    if "unexpected keyword argument" in e or "has no attribute" in e:
        # General case: whatever name you used doesn't exist. Append a specific
        # swap ONLY when the error names a method we know the real spelling of, so
        # an unrelated attribute error doesn't get irrelevant slot/cbore advice.
        tip = ""
        if "slot" in e:
            tip = " There is no `.slot()` — cut an oversized rounded rectangle instead."
        elif "counterbore" in e or "cbore" in e:
            tip = " Counterbores are `.cboreHole(diameter, cboreDiameter, cboreDepth)`."
        elif "countersink" in e or "csk" in e:
            tip = " Countersinks are `.cskHole(diameter, cskDiameter, cskAngle)`."
        return ("You called a CadQuery method or keyword argument that does not "
                "exist — use ONLY the real fluent Workplane API and do not invent "
                "names." + tip)
    if "nameerror" in e:
        return ("A name is undefined. Declare every dimension as a constant in the "
                "Parameters block before use, and don't reference undefined variables.")
    if "syntaxerror" in e or "invalid syntax" in e or "never closed" in e:
        return "The code has a Python syntax error — return complete, valid Python."
    if "brep_api" in e or "command not done" in e or "stdfail" in e or "standard_" in e:
        return ("A geometry-kernel op failed. Likely causes: a fillet/chamfer radius "
                "larger than the local edge (reduce it, or fillet fewer edges "
                "separately); a cut tool face coincident/coplanar with a target face "
                "(extend the tool ~1 mm past both faces); or an unclosed profile "
                "(call `.close()` before `.extrude()`/`.revolve()`).")
    return None


async def generate_code(
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
    model only ever sees its own code + the measured error, never a reference. A
    matched error class also gets ONE targeted fix hint (`_repair_hint`, SPEC16 §6).
    """
    user_msg = (
        f"Current CadQuery code:\n```python\n{current_code}\n```\n\n"
        f"Modification request: {prompt}"
    )
    if feedback:
        err = feedback.get("error", "unknown error")
        user_msg += (
            "\n\nYour previous attempt this turn did NOT work — do not repeat the "
            "same mistake. Return corrected, complete code.\n"
            f"Failed attempt:\n```python\n{feedback.get('code', '')}\n```\n"
            f"Problem: {err}"
        )
        hint = _repair_hint(err)
        if hint:
            user_msg += f"\nHint: {hint}"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if replace_initial:
        messages.append({"role": "system", "content": INITIAL_REPLACEMENT_PROMPT})
    skill_prompt = skills_render(skills)
    if skill_prompt:
        messages.append({"role": "system", "content": skill_prompt})
    messages.append({"role": "user", "content": user_msg})
    result = await stream_completion(
        messages, provider, model, temperature=temperature, max_tokens=4096,
        api_key=api_key, operation="generate", prompt_for_log=prompt,
    )
    return strip_markdown_fences(result.content)
