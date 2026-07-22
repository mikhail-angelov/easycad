# SPEC11: CadQuery Chat — Incremental 3D Model Builder

## Overview

Desktop application for building 3D-printable models through incremental natural language prompts. The user describes modifications in short phrases; the system refines the prompt, generates CadQuery code, executes it, and displays the 3D result. Models evolve step by step — each prompt adds one feature to the existing geometry.

## Problem Statement

Direct generation of 3D models from text descriptions produces unpredictable results. LLMs cannot reliably reason about 3D geometry in a single pass. However, LLMs are good at writing code, and CadQuery (parametric CAD in Python) is well-represented in training data.

**Key insight from POC (10 iterations of testing):** LLMs can reliably modify CadQuery code if:
- Each step is small and incremental (one feature at a time)
- The current geometry coordinates are provided explicitly (auto-generated bounding box)
- Prompts are precise about directions, positions, and dimensions
- Existing code is preserved (append-only modification)
- The user can see the result immediately and correct course

## Target Use Cases

**Well suited:**
- Utility objects for 3D printing: boxes, enclosures, organizers, brackets, mounts
- Prismatic shapes with cuts, fillets, chamfers, extrusions
- Objects built from simple geometric operations (union, cut, fillet)
- Iterative refinement where each step adds one feature

**Not suited:**
- Organic/sculptural forms (CadQuery limitation)
- Precision engineering (threads, tight tolerances) — prompts can't convey enough detail
- Complex multi-body assemblies — coordinate confusion grows with complexity
- Models requiring more than ~15-20 incremental steps — code accumulates, context grows

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Frontend (Browser)                     │
│                                                             │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │  Code Editor  │  │   3D Viewer      │  │  Chat Panel  │  │
│  │  (Monaco)     │  │   (Three.js)     │  │              │  │
│  │              │  │                  │  │  user prompt  │  │
│  │  CadQuery    │  │  STL rendered    │  │  → refined    │  │
│  │  Python code │  │  with orbit      │  │  → result     │  │
│  │  (editable)  │  │  controls        │  │              │  │
│  │              │  │                  │  │  step history │  │
│  └──────────────┘  └──────────────────┘  └──────────────┘  │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Step Timeline: [0]──[1]──[2]──[3]──[4]──[5]          │ │
│  │                              ↑ current                 │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Backend (Python/FastAPI)               │
│                                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │  Prompt      │  │  Code        │  │  CadQuery         │  │
│  │  Refiner     │  │  Generator   │  │  Executor         │  │
│  │  (LLM #1)   │  │  (LLM #2)    │  │  (sandbox)        │  │
│  │             │  │              │  │                   │  │
│  │  short      │  │  refined     │  │  Python code      │  │
│  │  prompt     │  │  prompt      │  │  → STL binary     │  │
│  │  → refined  │  │  + code      │  │  → geometry info  │  │
│  │  prompt     │  │  → new code  │  │  → error report   │  │
│  └─────────────┘  └──────────────┘  └───────────────────┘  │
│                                                             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Step Store: code + STL + prompt at each step          │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## Two-Stage LLM Pipeline

### Stage 1: Prompt Refiner

Takes the user's short prompt and current geometry info. Identifies ambiguities and returns a precise, unambiguous prompt ready for code generation.

**Input:**
- User prompt: "Сделай бортик по верхней кромке"
- Current geometry info (auto-generated): bounding box, dimensions, wall thickness, feature list

**Output — refined prompt:**
```
Add an inner ledge (shelf) running along the full inner perimeter
of the box at the top edge. The ledge is a rectangular ring. Its
outer edge is flush with the inner surface of the wall. It protrudes
1.5mm inward toward the center of the box. The ledge thickness
(height) is 1.5mm. The top face of the ledge is flush with the top
edge of the wall.
```

**Optionally returns clarifying questions if the prompt is truly ambiguous:**
```json
{
  "needs_clarification": true,
  "questions": [
    {
      "question": "Ledge direction?",
      "options": ["Inward (toward center)", "Upward (above wall)"]
    }
  ]
}
```

**Refiner system prompt principles:**
- Knows CadQuery coordinate system and common operations
- Has access to current geometry info (bounding box, feature descriptions)
- Resolves directional ambiguity (inward/outward, which face, which axis)
- Adds explicit coordinates based on geometry info
- Specifies exact dimensions when user gives relative descriptions
- Uses CAD-specific terminology (fillet, chamfer, shell, extrude, cut)

### Stage 2: Code Generator

Takes the refined prompt and current CadQuery code, appends new operations.

**System prompt rules (proven in POC):**
1. Return ONLY valid Python code — no markdown, no explanations
2. Script must define `result` variable of type `cadquery.Workplane`
3. DO NOT modify or reorder existing code — append only
4. Use geometry info comment block for exact coordinates
5. `translate()` moves the CENTER: top at Z=T → translate(T - H/2)
6. Use `.edges("|Z")` for vertical edge selection
7. Oversized cutting blocks in non-critical dimensions

## Geometry Info System

After each successful code execution, the backend computes and appends a comment block to the code:

```python
# ── Geometry info (auto-generated, do not edit) ──
# Bounding box: X: -25.0..25.0, Y: -40.0..40.0, Z: -15.0..15.0
# Size: 50.0 x 80.0 x 30.0 mm
# Topology: 1 solid(s), 48 faces, 130 edges
```

This block is:
- Auto-generated after each successful execution
- Passed to both Prompt Refiner and Code Generator
- Not editable by the user (overwritten each time)
- Used by LLMs for precise coordinate-based positioning

## Step History & Undo

Each step stores:
- Step number
- User's original prompt
- Refined prompt (from Stage 1)
- CadQuery code (with geometry info)
- STL binary
- Execution status (success/error)
- Timestamp

**Undo:** user can click any previous step in the timeline to revert to that state. New prompts from that point create a new branch (previous forward steps are preserved but marked as "branched off").

**Retry:** user can re-send the same prompt (with different wording or same wording for a different LLM generation) from the current step.

## Frontend Panels

### Left: Code Editor (Monaco)
- Syntax-highlighted CadQuery Python code
- Editable — user can manually fix code
- "Run" button to execute manual changes
- Geometry info block shown but grayed out (auto-generated)
- Line numbers, basic Python linting

### Center: 3D Viewer (Three.js)
- Renders STL mesh with orbit controls (rotate, zoom, pan)
- Grid floor for scale reference
- Wireframe toggle
- Measurement tool (click two points → distance)
- Export button: download STL file
- Auto-updates when code execution succeeds

### Right: Chat Panel
- Text input for prompts
- Shows conversation history: user prompt → refined prompt → result
- Refined prompt shown in a collapsible block (user can see what was sent to code generator)
- Error messages shown inline when execution fails
- Suggested retry options when a step fails

### Bottom: Step Timeline
- Horizontal timeline showing all steps as numbered nodes
- Click any node to view/revert to that state
- Current step highlighted
- Failed steps shown in red
- Branch points shown when user reverts and takes a different path

## API Endpoints

### POST /api/refine
Refine a user prompt before code generation.

Request:
```json
{
  "prompt": "Сделай бортик по верхней кромке",
  "current_code": "import cadquery as cq\n...",
  "provider": "deepseek",
  "model": "deepseek-chat"
}
```

Response:
```json
{
  "refined_prompt": "Add an inner ledge...",
  "needs_clarification": false,
  "questions": [],
  "original_prompt": "Сделай бортик по верхней кромке"
}
```

### POST /api/generate
Generate modified CadQuery code from a refined prompt.

Request:
```json
{
  "prompt": "Add an inner ledge...",
  "current_code": "import cadquery as cq\n...",
  "provider": "deepseek",
  "model": "deepseek-chat"
}
```

Response:
```json
{
  "code": "import cadquery as cq\n...",
  "success": true
}
```

### POST /api/execute
Execute CadQuery code and return STL + geometry info.

Request:
```json
{
  "code": "import cadquery as cq\n..."
}
```

Response:
```json
{
  "success": true,
  "stl_base64": "...",
  "geometry_info": "# Bounding box: ...",
  "code_with_geometry": "import cadquery as cq\n...\n# ── Geometry info...",
  "error": null
}
```

### POST /api/chat
Combined endpoint: refine + generate + execute in one call.

Request:
```json
{
  "prompt": "Сделай бортик",
  "current_code": "...",
  "step_number": 3,
  "provider": "deepseek",
  "model": "deepseek-chat",
  "auto_refine": true
}
```

Response:
```json
{
  "step": 3,
  "original_prompt": "Сделай бортик",
  "refined_prompt": "Add an inner ledge...",
  "code": "...",
  "stl_base64": "...",
  "geometry_info": "...",
  "success": true,
  "error": null,
  "needs_clarification": false
}
```

### GET /api/steps
Get all steps for the current session.

### GET /api/steps/{step_id}
Get a specific step (code, STL, prompt).

### POST /api/steps/{step_id}/revert
Revert to a specific step and continue from there.

### POST /api/execute-manual
Execute manually edited code (no LLM involved).

### GET /api/export/{step_id}
Download STL file for a specific step.

## Technology Stack

**Backend:**
- Python 3.11+
- FastAPI
- CadQuery 2.x (code execution)
- OpenAI SDK (LLM calls via OpenAI-compatible API)
- Process isolation for CadQuery execution (subprocess or multiprocessing to prevent crashes from taking down the server)

**Frontend:**
- Vite + TypeScript
- Three.js (STL rendering)
- Monaco Editor (code editing)
- Vanilla CSS or Tailwind (layout)

**LLM Providers (via OpenAI-compatible API):**
- DeepSeek (deepseek-chat — best results in POC)
- OpenRouter (access to GPT-4o, Gemini, Claude, etc.)
- Direct OpenAI

**Configuration:**
- `.env` file for API keys
- Provider/model selectable in UI settings

## LLM Model Comparison (POC Results)

| Model | Step Success Rate | Code Quality | Notes |
|---|---|---|---|
| deepseek-chat | 5/5 | Best | Idiomatic CadQuery, correct coordinate math |
| deepseek-coder | 4/5 | Good | Failed on fillet (used non-existent API) |
| gpt-4o-mini | 5/5 | Moderate | All steps pass but geometry sometimes wrong |
| gpt-4o | 4/5 | Moderate | Failed on fillet, not better than mini |

Recommended default: **deepseek-chat**

## Error Handling

**CadQuery execution errors:**
- Catch exception, show error message in chat panel
- Preserve previous working code/STL
- Suggest retry with different wording

**LLM errors:**
- API timeouts → retry with exponential backoff
- Invalid code returned → show code in editor, highlight error
- Code runs but no `result` variable → show error, suggest fix

**Geometry validation (post-execution):**
- Check STL is non-empty
- Check bounding box is reasonable (not degenerate)
- Warn if topology changed unexpectedly (e.g., solid split into multiple bodies)

## POC-Proven Prompt Engineering Patterns

These patterns must be embedded in the system prompt and/or prompt refiner:

| Problem | Solution |
|---|---|
| LLM makes box open on wrong side | Specify floor position AND that cavity is flush with top |
| LLM confuses inward/outward direction | Specify "outer edge flush with inner wall surface" |
| LLM guesses coordinates | Provide auto-generated geometry info with exact bounding box |
| LLM uses translate() wrong (center vs edge) | Explicit formula: top at Z=T → translate(T - H/2) |
| LLM rewrites/reorders existing code | "Append only" rule in system prompt |
| LLM uses non-existent CadQuery methods | List allowed methods in system prompt or validate after generation |
| Ambiguous "depth" (which axis?) | Refiner resolves to explicit axis and coordinates |
| Cut block too small, partial cut | "Oversized in non-critical dimensions" rule |

## Session Persistence

Sessions stored locally (filesystem):
```
~/.easycad/sessions/
  {session_id}/
    session.json        # metadata, provider, model
    steps/
      step_0.json       # {prompt, refined_prompt, code, geometry_info}
      step_0.stl
      step_1.json
      step_1.stl
      ...
```

No database required. Sessions can be listed, resumed, duplicated, deleted.

## MVP Scope

**Phase 1 (MVP):**
- Three-panel UI (editor, viewer, chat)
- Single LLM pipeline (no prompt refiner yet — direct code generation)
- Step history with undo (click to revert)
- Manual code editing with "Run" button
- STL export
- deepseek-chat as default provider
- Auto-generated geometry info

**Phase 2:**
- Prompt Refiner (two-stage pipeline)
- Clarifying questions UI
- Multiple provider/model selection in UI
- Retry with variations (generate 3 options, user picks best)
- Session persistence and resume

**Phase 3:**
- Measurement tool in 3D viewer
- Code validation/linting before execution
- Template library (start from common shapes instead of empty box)
- Prompt history/suggestions based on past successful prompts
