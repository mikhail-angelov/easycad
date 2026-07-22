"""
POC: Incremental CadQuery model building via LLM prompts.

Applies a sequence of short prompts to evolve CadQuery code step by step.
Each step saves: the .py source and the .stl model.

Usage:
    source .venv-poc/bin/activate
    python poc_cadquery_chat.py [--provider deepseek|openrouter|openai] [--model MODEL]
"""

import argparse
import json
import os
import re
import sys
import textwrap
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ── LLM providers ──────────────────────────────────────────────────────────

PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEP_SEEK_KEY",
        "default_model": "deepseek-coder",
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

# ── System prompt ───────────────────────────────────────────────────────────

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

# ── Starting code ───────────────────────────────────────────────────────────

INITIAL_CODE = textwrap.dedent("""\
    import cadquery as cq

    # A solid box placeholder — will be replaced at step 1
    result = cq.Workplane("XY").box(50, 80, 30)
""")

# ── Prompts to apply in sequence ────────────────────────────────────────────

PROMPTS = [
    # Step 1: hollow open-top box
    "Make an open-top box 50mm x 80mm x 30mm with 1.5mm wall thickness. "
    "The box has a solid 1.5mm floor at the bottom. To make the top open, "
    "the inner cavity must reach the same Z as the top of the outer box "
    "(the inner cut is flush with the top, no material remains on top).",

    # Step 2: fillet vertical edges
    "Fillet (round) only the vertical edges of the box with an external radius of 3mm. "
    "Do not fillet horizontal edges.",

    # Step 3: inner ledge at top
    "Add an inner ledge (shelf) running along the full inner perimeter of the box "
    "at the top edge. The ledge is a rectangular ring. Its outer edge is flush "
    "with the inner surface of the wall. It protrudes 1.5mm inward toward the "
    "center of the box. The ledge thickness (height) is 1.5mm. The top face of "
    "the ledge is flush with the top edge of the wall.",

    # Step 4: duplicate ledge 3mm lower
    "Duplicate the inner ledge from the previous step and move the copy 3mm "
    "downward, so there are now two identical ledges: one at the top edge and "
    "one 3mm below it.",

    # Step 5: cut down one short wall
    "Cut the top of one short wall (the 50mm side). Remove a 3mm vertical "
    "strip from the top of that wall (from Z=12 to Z=15), across the full "
    "length of that wall. The cut must go 3mm deep inward (into the box "
    "cavity) to also remove any ledge material on that side. "
    "The other three walls remain unchanged.",
]

# ── Helpers ─────────────────────────────────────────────────────────────────


def make_client(provider_name: str) -> tuple[OpenAI, str]:
    """Create an OpenAI-compatible client for the given provider."""
    cfg = PROVIDERS[provider_name]
    api_key = os.getenv(cfg["api_key_env"])
    if not api_key:
        sys.exit(f"ERROR: {cfg['api_key_env']} not set in .env")
    client = OpenAI(base_url=cfg["base_url"], api_key=api_key)
    return client, cfg["default_model"]


def strip_markdown_fences(text: str) -> str:
    """Remove ```python ... ``` wrappers if present."""
    text = text.strip()
    text = re.sub(r"^```(?:python)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def llm_modify_code(client: OpenAI, model: str, current_code: str, prompt: str) -> str:
    """Send current code + modification prompt to LLM, return new code."""
    user_msg = (
        f"Current CadQuery code:\n```python\n{current_code}\n```\n\n"
        f"Modification request: {prompt}"
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
        max_tokens=4096,
    )
    raw = response.choices[0].message.content
    return strip_markdown_fences(raw)


def get_geometry_info(result) -> str:
    """Extract bounding box and key geometry info from a CadQuery result."""
    try:
        bb = result.val().BoundingBox()
        lines = [
            f"# ── Geometry info (auto-generated, do not edit) ──",
            f"# Bounding box: X: {bb.xmin:.1f}..{bb.xmax:.1f}, Y: {bb.ymin:.1f}..{bb.ymax:.1f}, Z: {bb.zmin:.1f}..{bb.zmax:.1f}",
            f"# Size: {bb.xmax - bb.xmin:.1f} x {bb.ymax - bb.ymin:.1f} x {bb.zmax - bb.zmin:.1f} mm",
        ]
        # Count faces, edges, solids
        solid = result.val()
        n_faces = len(solid.Faces())
        n_edges = len(solid.Edges())
        n_solids = len(solid.Solids()) if hasattr(solid, 'Solids') else 1
        lines.append(f"# Topology: {n_solids} solid(s), {n_faces} faces, {n_edges} edges")
        return "\n".join(lines)
    except Exception:
        return "# ── Geometry info: could not extract ──"


def append_geometry_comment(code: str, result) -> str:
    """Append geometry info comment to the end of the code."""
    # Remove any previous geometry info block
    code = re.sub(r"\n*# ── Geometry info.*?(?=\n[^#]|\Z)", "", code, flags=re.DOTALL).rstrip()
    info = get_geometry_info(result)
    return code + "\n\n" + info + "\n"


def execute_cadquery(code: str, stl_path: Path) -> tuple[bool, str, object]:
    """Execute CadQuery code and export result to STL. Returns (success, message, result_obj)."""
    try:
        namespace = {}
        exec(code, namespace)
        result = namespace.get("result")
        if result is None:
            return False, "Code executed but no 'result' variable found.", None
        import cadquery as cq
        cq.exporters.export(result, str(stl_path))
        return True, f"STL saved: {stl_path} ({stl_path.stat().st_size:,} bytes)", result
    except Exception as e:
        return False, f"Execution error: {e}", None


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="POC: CadQuery + LLM incremental builder")
    parser.add_argument(
        "--provider", choices=list(PROVIDERS.keys()), default="deepseek",
        help="LLM provider (default: deepseek)"
    )
    parser.add_argument("--model", default=None, help="Override default model")
    parser.add_argument(
        "--output-dir", default="poc_output", help="Directory for outputs"
    )
    args = parser.parse_args()

    client, default_model = make_client(args.provider)
    model = args.model or default_model
    out_dir = Path(args.output_dir) / f"{args.provider}_{model.replace('/', '_')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Provider: {args.provider} | Model: {model}")
    print(f"Output:   {out_dir}")
    print(f"Steps:    {len(PROMPTS)}")
    print("=" * 60)

    current_code = INITIAL_CODE

    # Save initial state (step 0)
    step_py = out_dir / "step_0_initial.py"
    step_stl = out_dir / "step_0_initial.stl"
    step_py.write_text(current_code)
    ok, msg, result_obj = execute_cadquery(current_code, step_stl)
    print(f"\n[Step 0] Initial box")
    print(f"  {msg}")
    if ok and result_obj:
        current_code = append_geometry_comment(current_code, result_obj)
        print(f"  {get_geometry_info(result_obj).split(chr(10))[1]}")  # print bbox line

    results = []

    for i, prompt in enumerate(PROMPTS, start=1):
        print(f"\n[Step {i}] Prompt: {prompt[:80]}...")
        t0 = time.time()

        # Ask LLM to modify code
        try:
            new_code = llm_modify_code(client, model, current_code, prompt)
        except Exception as e:
            print(f"  LLM ERROR: {e}")
            results.append({"step": i, "prompt": prompt, "llm_ok": False, "error": str(e)})
            continue

        elapsed = time.time() - t0
        print(f"  LLM responded in {elapsed:.1f}s")

        # Execute and export STL
        step_stl = out_dir / f"step_{i}.stl"
        ok, msg, result_obj = execute_cadquery(new_code, step_stl)
        print(f"  {'OK' if ok else 'FAIL'}: {msg}")

        # Append geometry info to code if execution succeeded
        if ok and result_obj:
            new_code = append_geometry_comment(new_code, result_obj)
            print(f"  {get_geometry_info(result_obj).split(chr(10))[1]}")  # print bbox

        # Save code (with geometry comment if available)
        step_py = out_dir / f"step_{i}.py"
        step_py.write_text(new_code)
        print(f"  Code saved: {step_py}")

        results.append({
            "step": i,
            "prompt": prompt,
            "llm_ok": True,
            "exec_ok": ok,
            "message": msg,
            "time_s": round(elapsed, 1),
        })

        if ok:
            current_code = new_code  # evolve with geometry info attached
        else:
            print(f"  (keeping previous code for next step)")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(results, indent=2))
    for r in results:
        status = "OK" if r.get("exec_ok") else "FAIL"
        print(f"  Step {r['step']}: [{status}] {r['prompt'][:60]}")
    print(f"\nAll outputs in: {out_dir}")


if __name__ == "__main__":
    main()
