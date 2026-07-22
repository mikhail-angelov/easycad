# Sketch → Parametric CAD

## Specification: Feature Graph to trusted CadQuery implementation

## 0. Canonical architecture decision

This section supersedes earlier generated-code, fallback, Docker-preference, and Python-export requirements where
they conflict with it.

The supported geometry path is:

```text
source drawing
  -> structured feature inventory and parameters
  -> validated Feature Graph
  -> trusted backend Feature Graph compiler
  -> local CadQuery subprocess worker
  -> semantic geometry verification
  -> STL/STEP export and multi-view renders
```

The language models return structured observations, parameters, Feature Graph operations, assumptions, and targeted
operation updates. They do not return executable Python as part of the supported application path.

The trusted compiler may emit internal CadQuery Python as an implementation detail. That source is backend-owned,
is not an AI response, is not an exported product artifact, and is tested with the compiler. Unsupported operations
must remain explicit `unsupported` or `unresolved` Feature Graph entries and put the project in `needs_review`; they
must not trigger generated-code fallback.

CadQuery executes in a separate local subprocess so native crashes and timeouts do not terminate the API process.
Docker is not selected automatically and is not required by the product or test architecture. A future deployment
may add an explicitly configured container runner, but it is outside the current scope.

**Status:** Draft
**Version:** 0.1
**Target:** Local-first prototype
**Primary language:** Python
**CAD engine:** CadQuery
**Frontend viewer:** Three.js
**Backend:** FastAPI
**Vision provider:** OpenRouter-compatible multimodal model
**Text provider:** DeepSeek-compatible chat model

---

# 1. Overview

Sketch → Parametric CAD is a local-first application that converts a technical drawing into an editable parametric CAD model.

The application accepts an image of a mechanical technical drawing, analyzes its geometry and dimensions using a multimodal language model, builds a validated Feature Graph, compiles it with trusted backend code, executes CadQuery in a separate local process, and displays the resulting 3D model.

The user can then modify recognized parameters such as:

* overall dimensions;
* thicknesses;
* diameters;
* hole positions;
* radii;
* chamfers;
* fillets;
* extrusion depths;
* revolution dimensions;
* repeated feature spacing;
* visible engraved or embossed text when it can be read from the drawing.

After changing a parameter, the model is rebuilt from the same Feature Graph and trusted compiler.

The application exports:

* STEP for editable solid geometry;
* STL for 3D printing and preview;
* project JSON containing parameters, Feature Graph, source drawing reference, verification status, and metadata.

---

# 2. Product goal

The product goal is:

> Convert a technical drawing into an editable parametric CAD model with minimum manual reconstruction.

The initial version does not guarantee fully automatic conversion of every possible engineering drawing.

The system should instead:

1. generate the best possible parametric model;
2. identify uncertain dimensions and assumptions;
3. show the user which parameters can be edited;
4. allow regeneration after parameter changes;
5. provide useful diagnostics if generation fails;
6. preserve the generated model as a reusable project.

---

# 3. Core product principles

## 3.1 No predefined part families

The system must not require a drawing to be classified as:

* bolt;
* bracket;
* flange;
* shaft;
* washer;
* enclosure;
* or another predefined part family.

Classification may be returned as optional metadata, but generation must not depend on a hardcoded model type.

The geometry is defined by generic Feature Graph operations compiled by trusted backend code.

## 3.2 Parameters are first-class data

All user-editable dimensions must exist in a separate parameter dictionary.

Generated code must read dimensions and editable markings from this dictionary.

Bad:

```python
result = cq.Workplane("XY").box(90, 50, 12)
```

Good:

```python
p = PARAMETERS

result = (
    cq.Workplane("XY")
    .box(
        p["overall_length"],
        p["overall_width"],
        p["body_thickness"],
    )
)
```

The generated code must not duplicate editable engineering dimensions as numeric literals.

Visible part markings are represented with the same parameter mechanism instead of a separate response format:

* `text_content` is a `text` parameter containing the exact visible lettering, preserving Cyrillic or Latin script;
* `text_mode` is a `choice` parameter with `none`, `engrave`, or `emboss`;
* `text_size` is a numeric millimeter parameter for letter height.

If text placement is obvious and the surface is stable, generated CadQuery may use `.text(...)` on that face. For the local CadQuery version, engraved text uses `combine='cut'` and embossed text uses `combine='a'`; generated code must not use the older `cut=` keyword. On a top face, recessed/engraved text must use negative distance into the solid, for example `.text(label, size, -depth, combine='cut')`; positive distance creates text outside the solid and may not remove material. If the face or text is ambiguous, the system must record an assumption rather than invent a complex placement pipeline.

## 3.3 Structured model output is untrusted

Model-produced JSON must always be treated as untrusted input and validated against the strict Feature Graph and
parameter schemas. Executable Python is not accepted from a model in the supported path.

Unvalidated model output must never be used:

* to select arbitrary Python APIs;
* to reference undeclared parameters or feature targets;
* to mark omitted high-confidence features as implemented;
* to bypass parameter, coverage, or semantic validation.

## 3.4 Human verification is expected

The system should distinguish between:

* dimensions directly visible in the drawing;
* dimensions derived from other dimensions;
* dimensions inferred from geometry;
* default or assumed dimensions;
* unresolved dimensions.

The UI must make assumptions visible.

## 3.5 The project remains editable

A successful generation result is not only an STL or STEP file.

It is a project containing:

* source image;
* extracted observations;
* editable parameters;
* generated CadQuery code;
* generated feature summary;
* generation history;
* warnings and assumptions.

---

# 4. Supported scope for the first version

## 4.1 Supported inputs

The first version should support:

* PNG;
* JPEG;
* WebP;
* a single drawing image;
* orthographic views;
* simple sectional views;
* dimensions in millimeters;
* a single mechanical part;
* readable dimension annotations.

## 4.2 Supported geometry

The generated CadQuery code may use:

* boxes;
* cylinders;
* cones;
* polygons;
* circles;
* rectangles;
* slots;
* polylines;
* arcs;
* extrusions;
* revolutions;
* cuts;
* unions;
* intersections;
* holes;
* pockets;
* chamfers;
* fillets;
* simple engraved or embossed text on a clear planar face;
* mirrors;
* linear patterns;
* polar patterns;
* translations;
* rotations.

## 4.3 Initially unsupported

The first version does not promise reliable support for:

* assemblies;
* freeform or organic surfaces;
* complex lofted surfaces;
* sheet-metal unfold operations;
* weldments;
* full GD&T interpretation;
* tolerance stack calculations;
* complete modeled threads;
* gears with exact involute profiles;
* drawings without enough dimensions;
* scanned drawings with severe distortion;
* handwritten dimensions;
* multiple unrelated parts on one sheet.

The system may attempt such drawings, but must clearly label the result as experimental.

---

# 5. High-level architecture

```text
Browser
  |
  | upload drawing
  v
FastAPI backend
  |
  | image + prompt
  v
OpenRouter vision model
  |
  | structured drawing analysis
  v
CAD planning model
  |
  | parameters + Feature Graph
  v
Schema and coverage validator
  |
  | validated Feature Graph
  v
Trusted Feature Graph compiler
  |
  | backend-owned CadQuery program
  v
Local CadQuery subprocess worker
  |
  | STL + STEP + diagnostics
  v
FastAPI backend
  |
  | project + preview
  v
Browser
```

## The existing application already contains the basic backend export path and a Three.js STL viewer. Those pieces should be retained, while predefined `MODELS` and fixed builders should be removed.

# 6. Processing pipeline

## 6.1 Stage 1: drawing analysis

The vision model analyzes the drawing and returns structured observations.

It should identify:

* visible views;
* section views;
* overall shape;
* dimension annotations;
* feature annotations;
* holes;
* slots;
* pockets;
* grooves;
* fillets;
* chamfers;
* symmetry;
* repeated features;
* likely construction strategy;
* uncertain or unreadable areas.

Example:

```json
{
  "title": "Bearing mounting block",
  "units": "mm",
  "views": [
    {
      "id": "front",
      "type": "front",
      "description": "Front orthographic view"
    },
    {
      "id": "top",
      "type": "top",
      "description": "Top orthographic view"
    }
  ],
  "dimensions": [
    {
      "id": "dim_1",
      "label": "Overall length",
      "symbol": "L",
      "value": 90,
      "confidence": 0.98,
      "source": "visible_annotation"
    },
    {
      "id": "dim_2",
      "label": "Main hole diameter",
      "symbol": "D",
      "value": 30,
      "confidence": 0.96,
      "source": "visible_annotation"
    }
  ],
  "features": [
    {
      "type": "through_hole",
      "count": 1,
      "confidence": 0.98
    },
    {
      "type": "rectangular_pocket",
      "count": 1,
      "confidence": 0.74
    }
  ],
  "uncertainties": [
    {
      "description": "Pocket depth annotation is unclear",
      "severity": "warning"
    }
  ]
}
```

This stage must not generate Python code.

## 6.2 Stage 2: CAD plan generation

A text model receives the structured drawing analysis and produces:

* editable parameters;
* derived parameters;
* feature summary;
* Feature Graph operations;
* assumptions;
* expected model bounds;
* generation confidence.

## 6.3 Stage 3: structured validation and trusted compilation

Before execution, the backend validates parameters, Feature Graph operation schemas, references, feature coverage,
and capability status. Trusted backend code then compiles supported operations into an internal CadQuery program.

## 6.4 Stage 4: isolated local execution

The backend-owned program is executed in a separate local subprocess worker with a timeout and a temporary job
directory. Docker is not part of the supported execution path.

The worker returns:

* success or failure;
* STL preview;
* STEP model;
* bounding box;
* volume;
* execution duration;
* stdout and stderr;
* structured error information.

## 6.5 Stage 5: repair loop

If execution fails, the system may perform a bounded automatic repair loop.

Maximum attempts:

```text
initial generation + 2 repair attempts
```

Each repair request receives:

* original drawing analysis;
* parameters;
* previous code;
* AST validation errors or CadQuery traceback;
* instruction to make the smallest necessary correction.

The repair call should use the same DeepSeek text provider as CAD plan generation.

The repair model must preserve parameter names whenever possible.

## 6.6 Stage 6: result presentation

The successful result is shown in the browser.

The user can:

* rotate and zoom the model;
* edit parameter values;
* regenerate;
* inspect assumptions;
* download STEP;
* download STL;
* save the project;
* view generated source;
* request AI correction.

---

# 7. Project data model

A project should use the following conceptual schema.

```json
{
  "version": 1,
  "id": "uuid",
  "title": "Bearing mounting block",
  "units": "mm",

  "source": {
    "filename": "drawing.png",
    "mime_type": "image/png",
    "image_data": "optional-data-url-or-reference"
  },

  "analysis": {
    "views": [],
    "dimensions": [],
    "features": [],
    "uncertainties": []
  },

  "parameters": {
    "overall_length": {
      "label": "Overall length",
      "value": 90,
      "type": "number",
      "unit": "mm",
      "min": 1,
      "max": 500,
      "step": 0.1,
      "source": "drawing",
      "confidence": 0.98,
      "editable": true
    },
    "body_thickness": {
      "label": "Body thickness",
      "value": 12,
      "type": "number",
      "unit": "mm",
      "min": 0.5,
      "max": 100,
      "step": 0.1,
      "source": "inferred",
      "confidence": 0.65,
      "editable": true
    },
    "inner_diameter": {
      "label": "Inner diameter",
      "type": "expression",
      "expression": "outer_diameter - 2 * wall_thickness",
      "unit": "mm",
      "editable": false
    }
  },

  "feature_summary": [
    {
      "id": "base_body",
      "name": "Main body",
      "type": "extrude",
      "description": "Rectangular base extrusion"
    },
    {
      "id": "main_hole",
      "name": "Main through hole",
      "type": "hole",
      "description": "Central cylindrical through hole"
    }
  ],

  "cad": {
    "language": "cadquery-python",
    "source": "p = PARAMETERS\n...",
    "entry_variable": "result",
    "generation_attempt": 1
  },

  "generation": {
    "status": "success",
    "warnings": [],
    "execution_time_ms": 1420,
    "bounding_box": {
      "x": 90,
      "y": 50,
      "z": 20
    },
    "volume_mm3": 71200
  },

  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

---

# 8. Parameter rules

## 8.1 Parameter identifiers

Parameter IDs must:

* use `snake_case`;
* contain only letters, numbers and underscores;
* start with a letter;
* be unique;
* describe engineering meaning.

Good:

```text
overall_length
wall_thickness
mounting_hole_diameter
slot_center_distance
fillet_radius
```

Bad:

```text
x1
size2
value
thing_width
a
```

Short conventional identifiers may be accepted when their meaning is explicit, such as `m`, `d1`, or `r2`, but descriptive names are preferred.

## 8.2 No hidden dimensions

Every dimension that the user may reasonably want to change must be represented as a parameter.

Fixed technical constants that are not drawing dimensions may remain literal only when necessary.

Allowed examples:

```python
.rotate((0, 0, 0), (0, 0, 1), 90)
```

```python
.workplane(offset=0)
```

Not allowed:

```python
.box(p["length"], 37, p["height"])
```

when `37` represents an engineering width.

## 8.3 Derived parameters

Derived dimensions should be expressed separately.

Example:

```json
{
  "inner_diameter": {
    "type": "expression",
    "expression": "outer_diameter - 2 * wall_thickness"
  }
}
```

Before execution, the backend evaluates derived parameters using a restricted expression evaluator.

Allowed operators:

* addition;
* subtraction;
* multiplication;
* division;
* parentheses;
* references to other parameters;
* selected math functions.

Forbidden:

* arbitrary Python;
* attribute access;
* function definitions;
* imports;
* file access.

## 8.4 Value provenance

Each parameter must record one of:

```text
drawing
derived
inferred
assumed
manual
```

The UI should visually distinguish these sources.

## 8.5 Confidence

Each non-manual parameter should include a confidence value from `0` to `1`.

Suggested UI:

* `0.85–1.00`: high confidence;
* `0.60–0.84`: medium confidence;
* below `0.60`: low confidence;
* assumed values: special warning state.

---

# 9. Generated CadQuery contract

The generated source must obey the following contract.

## 9.1 Available globals

The worker provides:

```python
cq
PARAMETERS
math
```

No other globals are guaranteed.

## 9.2 Required output

The generated code must assign the final model to:

```python
result
```

`result` must be one of:

* `cadquery.Workplane`;
* `cadquery.Shape`;
* another explicitly supported CadQuery solid result.

## 9.3 No imports

Generated source must not contain:

```python
import ...
from ... import ...
```

The worker supplies CadQuery and approved helpers.

## 9.4 No side effects

Generated code must not:

* read or write files;
* open network connections;
* start subprocesses;
* inspect environment variables;
* use reflection;
* dynamically evaluate code;
* modify Python modules;
* access system APIs.

## 9.5 Feature-oriented source structure

The code should use named intermediate variables.

Preferred:

```python
p = PARAMETERS

base = (
    cq.Workplane("XY")
    .box(
        p["overall_length"],
        p["overall_width"],
        p["body_height"],
    )
)

with_main_hole = (
    base
    .faces(">Z")
    .workplane()
    .hole(p["main_hole_diameter"])
)

result = with_main_hole
```

Avoid:

```python
result = cq.Workplane("XY").box(...).faces(">Z").workplane().hole(...).edges(...).fillet(...)
```

Named stages improve:

* debugging;
* repair;
* feature summary generation;
* future migration to a DSL.

## 9.6 Stable modeling practices

The generation prompt should prefer:

* construction from stable reference planes;
* coordinate-based feature placement;
* explicit workplanes;
* symmetry;
* parameterized transforms;
* simple boolean operations;
* selectors based on direction and geometry.

The prompt should discourage fragile deep selector chains.

Fragile:

```python
.faces(">Z").edges("<X").vertices(">Y").workplane()
```

More stable:

```python
cq.Workplane("XY")
    .workplane(offset=p["body_height"])
    .center(p["hole_x"], p["hole_y"])
```

---

# 10. Static code validation

The backend must parse generated source using:

```python
ast.parse(source)
```

## 10.1 Allowed syntax

Initially allowed:

* assignments;
* expressions;
* dictionaries;
* lists;
* tuples;
* numeric literals;
* string literals;
* calls;
* attribute access on approved root objects;
* indexing into `PARAMETERS`;
* basic arithmetic;
* unary operations;
* selected conditional expressions if required.

## 10.2 Forbidden AST nodes

Reject at minimum:

* `Import`;
* `ImportFrom`;
* `FunctionDef`;
* `AsyncFunctionDef`;
* `ClassDef`;
* `Lambda`;
* `With`;
* `AsyncWith`;
* `Try`;
* `Raise`;
* `Global`;
* `Nonlocal`;
* `Delete`;
* `While`;
* unbounded comprehensions;
* `Yield`;
* `Await`;
* `Exec`-equivalent patterns;
* dynamic code evaluation.

Loops should initially be forbidden or narrowly restricted.

Patterns should instead use CadQuery operations when possible.

A later version may permit bounded loops over literal ranges, for example:

```python
for i in range(4):
```

but only after stronger validation.

## 10.3 Forbidden names

Reject references to names such as:

```text
open
exec
eval
compile
__import__
globals
locals
vars
dir
getattr
setattr
delattr
input
help
breakpoint
os
sys
subprocess
socket
pathlib
requests
httpx
builtins
```

Reject all names beginning with:

```text
__
```

## 10.4 Attribute restrictions

Attribute access should be permitted only from approved roots and returned CadQuery objects.

The validator must reject access to private or dunder attributes.

Forbidden examples:

```python
cq.__dict__
result.__class__
object.__subclasses__()
```

## 10.5 Call restrictions

The first implementation may use an allowlist of CadQuery methods.

Example allowlist:

```text
Workplane
box
circle
ellipse
polygon
rect
slot2D
polyline
moveTo
lineTo
threePointArc
close
extrude
revolve
sweep
loft
cut
union
intersect
hole
cboreHole
cskHole
fillet
chamfer
shell
faces
edges
vertices
workplane
center
pushPoints
translate
rotate
mirror
rarray
polarArray
```

The allowlist can be extended based on real generated models.

---

# 11. Worker execution

## 11.1 Required isolation

Backend-compiled CadQuery must execute in a separate local worker process.

```text
API process
  |
  | job.json + model.py
  v
Local subprocess worker
  |
  | result.stl
  | result.step
  | result.json
  v
API process
```

## 11.2 Process restrictions

The worker must use:

* a dedicated temporary working directory;
* execution timeout;
* no request-controlled executable path or environment;
* termination when the parent request times out.

## 11.3 Timeout

Default execution timeout:

```text
30 seconds
```

Configurable range:

```text
5–120 seconds
```

Timeout must terminate the entire worker process tree.

## 11.4 Worker input

```json
{
  "parameters": {
    "overall_length": 90,
    "overall_width": 50
  },
  "source": "p = PARAMETERS\n..."
}
```

## 11.5 Worker output

```json
{
  "status": "success",
  "duration_ms": 1330,
  "bounding_box": {
    "x": 90,
    "y": 50,
    "z": 20
  },
  "volume_mm3": 70000,
  "solid_count": 1,
  "warnings": []
}
```

On failure:

```json
{
  "status": "error",
  "stage": "execution",
  "error_type": "ValueError",
  "message": "Fillet radius is too large",
  "traceback": "sanitized traceback",
  "duration_ms": 490
}
```

## 11.6 Export artifacts

A successful worker should create:

```text
preview.stl
model.step
result.json
```

Optionally:

```text
preview_front.png
preview_top.png
preview_right.png
preview_iso.png
```

Artifacts should be copied back before the worker is destroyed.

---

# 12. AI output schema

The CAD-generation model must return structured output through tool calling or strict JSON Schema.

The first implementation should use:

```text
OpenRouter: vision-only drawing analysis
DeepSeek: text-only CAD planning and repair
```

Required environment variables:

```text
OPEN_ROUTER_KEY
OPEN_ROUTER_MODEL
DEEP_SEEK_KEY
```

Secret values must stay server-side and must never be included in generated project JSON, logs, exported source, fixtures, or browser responses.

Example:

```json
{
  "title": "Mounting plate with central bore",
  "confidence": 0.84,

  "parameters": [
    {
      "id": "plate_length",
      "label": "Plate length",
      "value": 100,
      "unit": "mm",
      "min": 20,
      "max": 500,
      "step": 0.1,
      "source": "drawing",
      "confidence": 0.98
    },
    {
      "id": "plate_width",
      "label": "Plate width",
      "value": 60,
      "unit": "mm",
      "min": 10,
      "max": 300,
      "step": 0.1,
      "source": "drawing",
      "confidence": 0.95
    }
  ],

  "feature_summary": [
    {
      "id": "base",
      "type": "extrude",
      "description": "Rectangular base plate"
    },
    {
      "id": "central_bore",
      "type": "hole",
      "description": "Central through hole"
    }
  ],

  "assumptions": [
    "The hole is assumed to pass through the entire plate"
  ],

  "code": "p = PARAMETERS\n..."
}
```

The system should not rely on parsing Markdown code blocks.

---

# 13. Prompt requirements

The CAD-generation prompt must instruct the model to:

1. use CadQuery;
2. produce parameterized source;
3. put every editable dimension into `parameters`;
4. reference dimensions only through `PARAMETERS`;
5. assign the final model to `result`;
6. use named intermediate feature variables;
7. avoid imports;
8. avoid filesystem and network access;
9. avoid arbitrary Python helpers;
10. prefer stable coordinate-based construction;
11. model only geometry supported by the drawing;
12. explicitly list assumptions;
13. omit decorative features that cannot be determined;
14. not invent precise dimensions without marking them as assumed;
15. preserve symmetry and repeated patterns;
16. produce one valid solid where possible.

---

# 14. API specification

## 14.1 Analyze and generate project

```http
POST /api/projects/generate
Content-Type: multipart/form-data
```

Fields:

```text
file: drawing image
instructions: optional user text
```

Example instructions:

```text
The drawing uses millimeters.
The small holes are through holes.
Ignore the title block.
```

Response:

```json
{
  "status": "success",
  "project": {
    "...": "..."
  }
}
```

Possible statuses:

```text
success
needs_review
failed
```

## 14.2 Regenerate preview

```http
POST /api/projects/preview
Content-Type: application/json
```

Request:

```json
{
  "project": {
    "...": "..."
  },
  "parameters": {
    "plate_length": 120,
    "hole_diameter": 18
  }
}
```

Response:

```text
model/stl
```

Generation metadata may be returned in headers or through a separate JSON endpoint.

A cleaner alternative is:

```json
{
  "status": "success",
  "preview_url": "/api/jobs/{id}/preview.stl",
  "generation": {
    "...": "..."
  }
}
```

## 14.3 Export

```http
POST /api/projects/export?format=step
```

Supported formats:

```text
step
stl
py
json
```

## 14.4 Validate project

```http
POST /api/projects/validate
```

Response:

```json
{
  "valid": false,
  "errors": [
    {
      "stage": "parameters",
      "parameter": "hole_diameter",
      "message": "Hole diameter must be smaller than plate width"
    }
  ],
  "warnings": []
}
```

## 14.5 Repair generated model

```http
POST /api/projects/repair
```

Request:

```json
{
  "project": {},
  "user_feedback": "The upper groove is missing",
  "current_view": "optional screenshot or render reference"
}
```

Response contains a revised project while preserving previous versions.

---

# 15. Frontend specification

## 15.1 Main layout

Recommended desktop layout:

```text
┌──────────────────────┬───────────────────────────────────┐
│ Drawing              │ 3D preview                        │
│                      │                                   │
│ source image         │ STL-rendered model                │
│                      │                                   │
├──────────────────────┴───────────────────────────────────┤
│ Parameters | Features | Assumptions | Source             │
└───────────────────────────────────────────────────────────┘
```

A narrower layout may retain the existing sidebar structure.

## 15.2 Parameter editor

Each editable parameter should include:

* label;
* numeric input;
* unit;
* optional slider;
* confidence indicator;
* source indicator;
* reset button;
* validation message.

Example:

```text
Hole diameter
[ 30.00 ] mm
Source: drawing · Confidence: 96%
```

A numeric input is mandatory.

Sliders are optional and should not be the only editing control.

## 15.3 Regeneration behavior

Parameter changes should not rebuild on every keystroke.

Recommended behavior:

* update local state immediately;
* debounce regeneration by 500–800 ms;
* regenerate on input blur;
* regenerate on Enter;
* provide explicit `Generate` button;
* cancel an older request when a new one starts.

The current frontend already cancels obsolete generation requests and can retain that behavior.

## 15.4 Feature summary

Display a read-only feature list initially:

```text
1. Main body — rectangular extrusion
2. Central bore — through hole
3. Upper pocket — rectangular cut
4. Outer edges — 2 mm fillet
```

The first version does not need interactive feature editing.

## 15.5 Assumptions panel

Example:

```text
Warnings and assumptions

⚠ Pocket depth was not readable and was estimated as 8 mm.
⚠ The central bore was interpreted as a through hole.
✓ Overall length was read directly from the drawing.
```

## 15.6 Generated source panel

Provide a code viewer containing:

* generated CadQuery source;
* copy button;
* download `.py` button;
* generation attempt;
* validator status.

Source editing may be added later.

For the first version, source is read-only.

## 15.7 Failure UI

If generation fails, show:

* generation stage;
* understandable error;
* current parameters;
* automatic repair attempts;
* raw technical details in an expandable section;
* `Retry`;
* `Ask AI to repair`;
* `Edit source` in a future version.

---

# 16. Save and load

The project JSON should contain:

* source image, or a reference to it;
* analysis result;
* parameters;
* generated source;
* feature summary;
* assumptions;
* generation metadata.

The application should support:

```text
Save project
Load project
Export CadQuery source
Export STEP
Export STL
```

Loading a project must not invoke the AI again.

It should:

1. restore the project;
2. validate generated source;
3. rebuild the model with saved parameters;
4. show any compatibility error.

---

# 17. Automatic repair loop

## 17.1 Repair triggers

Automatic repair may run when:

* AST validation fails;
* generated code raises an exception;
* no solid is produced;
* STEP export fails;
* STL export fails;
* the model has zero volume;
* the output has an unexpected number of disconnected solids;
* dimensions differ strongly from expected bounds.

## 17.2 Repair prompt input

The repair model receives:

```json
{
  "drawing_analysis": {},
  "parameters": {},
  "feature_summary": [],
  "previous_code": "...",
  "error": {
    "stage": "execution",
    "message": "..."
  }
}
```

## 17.3 Repair constraints

The model must:

* preserve parameter IDs;
* preserve parameters not related to the error;
* make the smallest correction;
* not remove features silently;
* update assumptions if geometry changes;
* return a full replacement source.

## 17.4 Attempt limit

Default:

```text
2 automatic repair attempts
```

After that, show the user the failure and allow manual retry or feedback.

---

# 18. Optional render comparison loop

A later iteration may render the generated model from:

* front;
* top;
* right;
* isometric.

A vision model compares these renders with the source drawing and reports:

```json
{
  "match_score": 0.78,
  "issues": [
    {
      "severity": "high",
      "description": "The upper pocket is missing"
    },
    {
      "severity": "medium",
      "description": "The right hole appears too close to the edge"
    }
  ]
}
```

This comparison must initially be advisory.

It should not automatically alter a successful model unless the correction loop is explicitly enabled.

---

# 19. Security requirements

## 19.1 API key

The OpenRouter API key must remain on the backend.

It must never be returned to the browser.

The existing backend-side settings approach should be retained.

For a local-only application, the key may be stored in:

* environment variable;
* local configuration file with restricted permissions;
* operating-system keychain in a later version.

## 19.2 Server binding

The local application should bind to:

```text
127.0.0.1
```

instead of:

```text
0.0.0.0
```

unless remote access is explicitly enabled.

## 19.3 CORS

For a same-origin local app, permissive CORS is unnecessary.

Remove:

```python
allow_origins=["*"]
```

or restrict it to the actual frontend origin.

## 19.4 File validation

Uploaded files must be checked for:

* maximum byte size;
* valid image MIME type;
* valid image signature;
* maximum pixel dimensions;
* decompression bomb risk.

Recommended defaults:

```text
maximum file size: 20 MB
maximum dimensions: 12000 × 12000
```

## 19.5 Sensitive drawing retention

By default, uploaded drawings should not be stored permanently.

Possible modes:

```text
memory-only
project-storage
debug-retention
```

The default should be `memory-only`.

---

# 20. Error handling

Errors should identify the stage:

```text
upload
image_decode
vision_analysis
cad_generation
static_validation
sandbox_start
sandbox_timeout
cadquery_execution
stl_export
step_export
project_validation
```

Example:

```json
{
  "status": "error",
  "stage": "cadquery_execution",
  "message": "The requested fillet radius is larger than the selected edge allows.",
  "technical_details": "...",
  "repairable": true
}
```

User-facing messages should not expose:

* absolute server paths;
* environment variables;
* API keys;
* complete internal stack traces.

---

# 21. Testing strategy

## 21.1 Unit tests

Test:

* project-schema validation;
* parameter coercion;
* derived-expression evaluation;
* AST validation;
* forbidden identifiers;
* forbidden attributes;
* code contract enforcement;
* worker result parsing;
* project save/load.

## 21.2 Security tests

Generated source samples must be rejected:

```python
import os
```

```python
open("/etc/passwd").read()
```

```python
__import__("subprocess").run(...)
```

```python
cq.__dict__
```

```python
result.__class__.__mro__
```

```python
while True:
    pass
```

```python
eval("...")
```

## 21.3 Integration tests

Test complete jobs:

```text
drawing fixture
→ mocked AI result
→ static validation
→ worker execution
→ STL
→ STEP
```

## 21.4 Geometry tests

For generated fixtures, verify:

* non-zero volume;
* expected bounding box;
* expected solid count;
* expected hole count where measurable;
* STEP export succeeds;
* STL triangle count is reasonable.

## 21.5 AI evaluation set

Create a fixture set containing:

```text
simple_plate
plate_with_holes
shaft
stepped_shaft
flange
simple_bracket
u_bracket
bushing
pulley
block_with_pocket
revolved_part
```

For each drawing, record:

* expected parameters;
* acceptable tolerance;
* required features;
* forbidden invented features;
* expected generation success.

Primary evaluation metrics:

* successful executable code rate;
* correct parameter extraction rate;
* STEP export success rate;
* bounding-box accuracy;
* required-feature recall;
* repair success rate.

---

# 22. Migration from the current implementation

## Phase 1: remove predefined geometry

Remove:

```python
build_bolt
build_bracket
MODELS
```

Remove endpoints based on `model_id`:

```text
/api/model/{model_id}/gen
/api/model/{model_id}/preview
```

Replace them with project-based generation.

## Phase 2: introduce project schema

Add models for:

```text
CADProject
CADParameter
DrawingAnalysis
FeatureSummary
GenerationResult
```

Use Pydantic validation.

## Phase 3: add generated source execution

Implement:

```text
AST validator
worker protocol
sandbox runner
artifact collection
```

Initially, the AI response may be replaced by checked-in project fixtures that contain manually prepared CadQuery source.

CadQuery itself must not be mocked. The first execution path should run the real CadQuery package, create real solids, and export real STL and STEP artifacts.

## Phase 4: integrate AI generation

Implement:

```text
drawing analysis call
CAD code generation call
structured output
repair call
```

## Phase 5: update frontend

Replace model-schema loading with project parameter loading.

Current:

```text
GET /api/models
```

New:

```text
project.parameters
```

The frontend can retain:

* Three.js scene;
* STLLoader;
* parameter controls;
* request cancellation;
* save/load;
* STL/STEP buttons.

## Phase 6: add user review

Add:

* parameter provenance;
* confidence;
* assumptions;
* source viewer;
* feature summary;
* code viewer.

---

# 23. Recommended implementation order

## Milestone 1: manually defined generated-code project

Goal:

> Prove that a saved CadQuery program can be rebuilt after parameter changes.

Implement:

* universal project JSON;
* editable parameters;
* generated source field;
* AST validation;
* isolated execution;
* STL and STEP;
* frontend parameter editing.

Use manually written project fixtures as stand-ins for AI output, but execute them through the real CadQuery worker. This milestone validates the actual modeling, export, sandbox, and preview path before model-generated code is introduced.

## Milestone 2: AI-generated code

Goal:

> Generate an executable project from a simple technical drawing.

Support initially:

* extruded profiles;
* revolved profiles;
* through holes;
* pockets;
* chamfers;
* fillets.

## Milestone 3: repair loop

Goal:

> Automatically correct common generation failures.

Add:

* validator feedback;
* execution feedback;
* two repair attempts;
* generation version history.

## Milestone 4: structured Feature Graph

Goal:

> Represent recognized geometry as generic operations with explicit targets, placement, patterns, evidence, and coverage state.

Add stable feature IDs, strict feature inventory, Feature Graph persistence, and explicit `implemented`, `approximated`, `unresolved`, or `unsupported` outcomes.

## Milestone 5: trusted feature compiler

Goal:

> Build common operations without relying on a whole-part template or unrestricted model-generated code.

Compile bodies, cuts, ribs, holes, pockets, patterns, modifiers, and planar text into CadQuery. Keep validated generated source as a declared fallback for unsupported operations.

## Milestone 6: feature-preserving repair

Goal:

> Prevent stabilization and repair from silently removing recognized features.

Add feature coverage guards to existing stabilizers and move repair toward individual Feature Graph operations.

## Milestone 7: semantic geometry verification

Goal:

> Detect missing or incorrect features even when the model exports and its bounding box is correct.

Add feature-oriented measurements and validation for cuts, additions, counts, spacing, placement, and printable solid constraints.

## Milestone 8: visual verification

Goal:

> Detect visual mismatches that cannot be established reliably from topology and aggregate measurements.

Add multi-view rendering, advisory AI comparison, and feature-linked targeted correction.

## Milestone 9: capability evaluation

Goal:

> Base support claims on measured feature capabilities rather than part-family examples.

Maintain recorded real-provider fixtures and deterministic tests for each supported or experimental feature class.

---

# 24. Future migration to a CAD DSL

The generated CadQuery implementation is intentionally designed as an intermediate architecture.

To support future migration:

1. parameters are already separate;
2. feature stages use named variables;
3. feature summaries are structured;
4. allowed CadQuery methods are restricted;
5. source code follows predictable patterns;
6. generation examples can be collected.

Over time, common patterns can be represented as DSL operations.

Example generated code:

```python
base = cq.Workplane("XY").box(
    p["length"],
    p["width"],
    p["height"],
)

result = (
    base.faces(">Z")
    .workplane()
    .hole(p["hole_diameter"])
)
```

Future DSL:

```json
{
  "operations": [
    {
      "id": "base",
      "type": "box",
      "length": "length",
      "width": "width",
      "height": "height"
    },
    {
      "id": "main_hole",
      "type": "hole",
      "target": "base",
      "face": "top",
      "diameter": "hole_diameter",
      "through": true
    }
  ]
}
```

The first version must not block this migration.

---

# 25. Acceptance criteria

The first usable prototype is complete when:

1. A user can upload an unknown technical drawing.
2. The system generates a project without requiring a predefined part type.
3. The project contains editable named parameters.
4. Generated code references those parameters through `PARAMETERS`.
5. Generated code passes static validation.
6. Code executes outside the FastAPI process.
7. The result is displayed in Three.js.
8. Changing a parameter rebuilds the model.
9. STEP and STL can be downloaded.
10. The project can be saved and loaded without invoking AI again.
11. Assumed parameters are visibly distinguished.
12. Execution errors produce understandable diagnostics.
13. At least ten representative simple mechanical drawings are included in the evaluation suite.
14. At least seven of those ten drawings produce a valid solid after no more than two repair attempts.

---

# 26. Final product definition

The first version should be described as:

> Sketch → Parametric CAD converts mechanical technical drawings into editable CadQuery-based models. It extracts dimensions, generates a parameterized modeling program, validates and executes it in isolation, and lets the user modify dimensions before exporting STEP or STL.

It should not initially claim:

> Guaranteed conversion of every technical drawing into a production-ready CAD model.

The central technical hypothesis to validate is:

> A multimodal model can generate sufficiently stable parameterized CadQuery programs from a broad range of ordinary mechanical drawings, and execution feedback can repair a meaningful share of initial failures.

---

# 27. Architecture extension: feature-complete generation

## 27.1 Problem statement

Successful CadQuery execution does not prove that the generated model contains every feature visible in the drawing. A model may have the expected bounding box, positive volume, and one valid solid while still omitting perforations, ribs, pockets, local chamfers, or repeated cuts.

The current generated-code pipeline also allows a geometry stabilizer to replace the complete generated source with a known fixture implementation. This can repair a familiar base shape while silently removing unfamiliar features that were present in the drawing or in the original generated source.

The extended architecture must therefore distinguish three independent outcomes:

* syntactic validity: the project and generated program satisfy the static contract;
* geometric validity: CadQuery produces exportable solid geometry;
* semantic fidelity: the resulting solid contains the recognized features in the expected positions and quantities.

A project must not be reported as fully verified based only on syntactic and geometric validity.

## 27.2 Architecture decision

The application will use a trusted feature-oriented architecture:

1. the vision stage produces a structured inventory of drawing features and their evidence;
2. the planning stage converts that inventory into a generic Feature Graph;
3. trusted backend code compiles supported Feature Graph operations into CadQuery;
4. unsupported operations remain explicit and require review;
5. coverage, geometry measurements, and multi-view render comparison verify the result;
6. repair targets structured operations or parameters instead of replacing executable source.

The Feature Graph describes modeling operations, not predefined part families. It may contain generic operations such as body extrusion, rib addition, hole cutting, shelling, and linear patterning, but it must not require classification as a bracket, enclosure, bolt, or other fixed product type.

## 27.3 Feature Graph contract

Each recognized feature must have a stable ID and enough placement information to reconstruct or explicitly reject it.

Required conceptual fields:

```json
{
  "id": "left_rib_perforation",
  "type": "hole_pattern",
  "operation": "cut",
  "target": "left_rib",
  "profile": {
    "type": "circle",
    "diameter": "rib_hole_diameter"
  },
  "placement": {
    "reference": "left_rib_outer_face",
    "direction": "through_target",
    "start_margin": "rib_hole_margin"
  },
  "pattern": {
    "type": "linear",
    "count": "rib_hole_count",
    "pitch": "rib_hole_pitch",
    "axis": "rib_length_axis"
  },
  "evidence": {
    "views": ["front", "section_a"],
    "source": "visible_geometry"
  },
  "confidence": 0.91,
  "status": "planned"
}
```

The concrete schema may evolve, but it must represent:

* operation type: add, cut, intersect, modify, or pattern;
* target body or host feature;
* profile and extent;
* reference face, plane, axis, or coordinate system;
* placement and orientation;
* repeated-feature count, spacing, margins, and pattern direction;
* parameter references rather than duplicated engineering literals;
* drawing views or annotations that support the feature;
* confidence and unresolved assumptions;
* mapping to the generated CAD operation.

Unknown placement, depth, count, or dimensions must remain unresolved or assumed. They must not be silently discarded.

## 27.4 Extended processing pipeline

The extended pipeline is:

```text
source drawing(s)
  -> structured views, dimensions, and feature inventory
  -> feature completeness review
  -> parameter set + Feature Graph
  -> trusted Feature Graph compiler
  -> local subprocess CadQuery execution
  -> geometry measurements and feature coverage validation
  -> orthographic and isometric renders
  -> advisory visual comparison
  -> targeted repair of mismatched features
```

The original drawing must be available to the feature completeness and visual comparison stages. Passing only a prose drawing analysis to all later stages is insufficient because details lost during the first analysis cannot otherwise be recovered.

The first implementation may perform feature completeness review in the same vision request if its output is structured and testable. A separate model call should be added only when evaluation demonstrates a meaningful accuracy improvement.

## 27.5 Trusted Feature Graph compiler

The compiler must implement generic geometric capabilities incrementally. Initial operations should cover:

* additive and subtractive extrusions;
* revolutions;
* holes, counterbores, countersinks, slots, and pockets;
* ribs and gussets;
* fillets and chamfers;
* shells and thin-wall bodies;
* mirrors;
* linear and polar patterns;
* patterns on a declared face, axis, or straight edge;
* engraved and embossed text on planar faces.

Later operations may add sweeps, lofts, draft angles, path patterns, and more complex face-local construction after representative fixtures prove them reliable.

The compiler must preserve operation IDs in diagnostics so a worker or comparison error can be traced back to the corresponding recognized feature.

Generated CadQuery fallback is not supported. When no trusted compiler operation can represent a feature, that
operation remains `unsupported`; high-confidence unsupported features produce `needs_review` and block verified
STL/STEP export.

## 27.6 Feature coverage

Every recognized feature must end in one of these states:

* `implemented`: mapped to a compiled or generated CAD operation;
* `approximated`: deliberately simplified and described in assumptions;
* `unresolved`: insufficient dimensions or ambiguous placement;
* `unsupported`: not currently representable by the compiler or fallback policy.

Generation must fail semantic verification if a high-confidence feature has no final state or is silently omitted.

Feature summaries alone are not sufficient. The backend must maintain an explicit mapping from analysis feature ID to Feature Graph operation ID and generated CAD stage.

## 27.7 Stabilizer policy

Existing whole-model fixture stabilizers are temporary compatibility code. They must not replace a generated source when the recognized feature inventory contains additional operations that the stabilizer does not implement.

Before applying a stabilizer, the backend must prove that:

* its declared feature set covers every high-confidence recognized feature;
* it preserves all required editable parameters;
* it does not downgrade an implemented feature to an unreported omission.

If those conditions are not met, the original project must continue through targeted repair or be returned as `needs_review`. As Feature Graph compiler coverage grows, whole-model stabilizers should be removed in favor of operation-level compilation and repair.

## 27.8 Semantic and visual verification

Semantic verification should use the strongest inexpensive checks available for each feature:

* bounding box, volume, and solid count;
* expected through-hole and pocket counts where measurable;
* repeated-feature count and spacing;
* minimum wall and rib thickness for printable solids;
* presence of material removal for cuts and engraving;
* presence of added material for ribs and embossing;
* symmetry where declared by the drawing;
* feature placement relative to reference faces and axes.

The worker should return structured measurements rather than only aggregate bounds and volume.

Visual verification must render at least front, top, right, and isometric views. A vision comparison receives both source drawing views and generated renders and returns feature-linked issues. Initially these findings are advisory. Automatic correction may be enabled only for high-confidence issues that identify a specific Feature Graph operation.

## 27.9 Perforated rib acceptance example

A perforated enclosure rib is considered supported only when the generated project records and verifies:

* the rib as an additive feature with its host body and thickness;
* the perforation profile and cut direction;
* hole count or an explicit uncertainty;
* pitch and edge margins or explicit assumptions;
* a pattern operation attached to the rib;
* measurable evidence that the holes pass through the intended rib;
* no loss of the perforation after stabilization or repair;
* successful STL and STEP export as printable solid geometry.

Matching only the enclosure bounding box is not sufficient.

## 27.10 Capability levels

Feature support must be reported by capability rather than by part family:

* `supported`: covered by the trusted compiler and semantic tests;
* `experimental`: generated fallback exists, but verification is incomplete;
* `unsupported`: the application preserves the observation and requests review instead of silently simplifying it.

The UI should surface approximated, unresolved, and unsupported features alongside assumptions. This prevents a geometrically valid but incomplete model from appearing production-ready for 3D printing.

## 27.11 Extended acceptance criteria

The architecture extension is complete when:

1. recognized features have stable IDs and structured placement or explicit uncertainty;
2. every high-confidence feature has a final coverage state;
3. generic compiled operations can create a body, rib, perforation, and linear pattern without a part-family template;
4. whole-model stabilizers cannot silently remove additional features;
5. worker diagnostics link failures to Feature Graph operation IDs where possible;
6. semantic tests detect a missing perforation even when bounding box and solid count still match;
7. multi-view renders and advisory comparison results are stored with the project;
8. fixture tests include both supported and intentionally unresolved feature examples;
9. exported STL remains a positive-volume printable solid and STEP export succeeds;
10. feature capability status is visible to the user before export.

## 27.12 Verification matrix

The following criteria are the definition of done for Milestones 4 through 9. A milestone is complete only when every criterion assigned to it has automated evidence. A manual visual check may supplement automated evidence but must not replace it.

Test artifacts must not contain provider secrets. Real-provider responses used as evidence must be recorded as sanitized fixtures and replayed in deterministic tests.

### Milestone 4: Structured Feature Graph

`M4-AC1 — Strict schema`

* Check: validate valid and invalid Feature Graph JSON fixtures through the project Pydantic model.
* Pass condition: valid bodies, ribs, cuts, and patterns load successfully; missing IDs, invalid references, unsupported status values, and malformed placement or pattern data produce specific validation errors.

`M4-AC2 — Stable identity and relationships`

* Check: normalize the same recorded drawing-analysis response twice and serialize both results.
* Pass condition: feature IDs and target relationships are identical across both runs and every target references an existing body or feature.

`M4-AC3 — Complete coverage state`

* Check: process a fixture containing a body, two ribs, and repeated rib perforations.
* Pass condition: every high-confidence analysis feature maps to a Feature Graph operation or has an explicit `approximated`, `unresolved`, or `unsupported` state; an omitted feature causes semantic planning validation to fail.

`M4-AC4 — Persistence`

* Check: save and reload a project containing Feature Graph operations, evidence, assumptions, and coverage states.
* Pass condition: the project round trip is lossless for all Feature Graph and coverage fields and does not invoke an AI provider.

### Milestone 5: Trusted Feature Compiler

`M5-AC1 — Generic construction`

* Check: compile a fixture containing a base body, two ribs, and a linear pattern of through-holes without a part-family identifier.
* Pass condition: the compiler produces valid CadQuery, the local worker exports both STL and STEP, and the result is one positive-volume solid.

`M5-AC2 — Parameter behavior`

* Check: rebuild the perforated-rib fixture with overrides for rib thickness, hole diameter, count, pitch, and margin.
* Pass condition: measured geometry changes according to every override without editing generated source or creating invalid geometry within declared parameter limits.

`M5-AC3 — Operation traceability`

* Check: force one compiled operation to fail with an invalid but schema-valid geometric value.
* Pass condition: diagnostics identify the corresponding Feature Graph operation ID and do not report only an unscoped CadQuery traceback.

`M5-AC4 — Controlled fallback`

* Check: submit one supported operation and one unknown operation.
* Pass condition: the supported operation uses the trusted compiler; the unknown operation is either mapped to validated fallback source with declared feature IDs or marked `unsupported`; it is never silently omitted.

### Milestone 6: Feature-Preserving Repair

`M6-AC1 — Stabilizer coverage guard`

* Check: pass an L-shaped part with additional rib perforations through the legacy L-bracket stabilization path.
* Pass condition: stabilization is skipped or preserves the perforation operations; a resulting project with lost perforations fails the guard.

`M6-AC2 — Targeted repair`

* Check: introduce a failure in one perforation-pattern operation and run automatic repair.
* Pass condition: the repair changes only the failed operation and required dependent parameters; unaffected operation IDs and serialized definitions remain unchanged.

`M6-AC3 — Honest failure state`

* Check: provide a high-confidence feature that neither compiler nor fallback can preserve after the repair limit.
* Pass condition: the project ends as `needs_review` with the feature marked `unsupported` or `unresolved`, and does not return semantic success.

`M6-AC4 — History`

* Check: inspect the project after successful and unsuccessful repairs.
* Pass condition: history records the original operation definitions, targeted error, repair attempt, coverage before and after repair, and final status.

### Milestone 7: Semantic Geometry Verification

`M7-AC1 — Missing-feature detection`

* Check: compare two exportable fixtures with identical bounding boxes: one has the required perforations and one omits them.
* Pass condition: the complete model passes and the incomplete model fails semantic verification with the missing feature ID.

`M7-AC2 — Pattern measurements`

* Check: measure a known linear perforation pattern.
* Pass condition: count is exact and diameter, pitch, and edge margins are within the greater of 0.2 mm or 1% of the declared value.

`M7-AC3 — Add/cut evidence`

* Check: build the same base with and without each tested additive or subtractive operation.
* Pass condition: additive operations increase material and subtractive operations decrease material by a non-zero amount consistent with the declared feature; a no-op boolean fails verification.

`M7-AC4 — Independent statuses`

* Check: exercise projects that fail syntax, execution, and semantic verification separately.
* Pass condition: the project records distinct syntax, geometry execution, and semantic fidelity statuses so an exportable but incomplete model cannot appear fully verified.

`M7-AC5 — Printable solid constraints`

* Check: run fixtures with a disconnected rib, zero-thickness contact, and wall or rib thickness below its declared printable minimum.
* Pass condition: each fixture reports a feature-linked failure or warning according to policy; the valid control fixture exports as one positive-volume solid.

### Milestone 8: Render Comparison And Targeted Correction

`M8-AC1 — Render artifacts`

* Check: render a known asymmetric fixture from front, top, right, and isometric cameras.
* Pass condition: four non-blank images are stored with stable view metadata, expected dimensions, and visible model pixels; distinct views must not be byte-identical.

`M8-AC2 — Feature-linked comparison`

* Check: compare source views against renders of one complete fixture and variants with a missing perforation, misplaced hole, and extra rib.
* Pass condition: comparison reports the correct issue class and related feature ID for each defective variant and produces no high-severity issue for the complete control.

`M8-AC3 — Advisory behavior`

* Check: return a visual mismatch while automatic visual correction is disabled.
* Pass condition: the issue is stored and shown to the user, project geometry remains unchanged, and semantic status is not rewritten without evidence.

`M8-AC4 — Targeted visual repair`

* Check: enable correction for a high-confidence issue tied to one operation.
* Pass condition: only that operation and necessary dependent parameters change; unrelated operation definitions and IDs remain unchanged; the model is re-rendered and compared again.

### Milestone 9: Capability Evaluation

`M9-AC1 — Capability fixture minimum`

* Check: inspect the evaluation manifest for every capability labelled `supported`.
* Pass condition: each supported capability has at least five representative source drawings, including one negative or ambiguous case, and deterministic expected feature outcomes.

`M9-AC2 — Supported quality gate`

* Check: run the complete evaluation set and calculate metrics by capability.
* Pass condition: each supported capability reaches at least 90% feature precision, 90% feature recall, 95% valid STL/STEP export rate, and median declared-dimension error no greater than the larger of 1 mm or 2%; no high-confidence feature is silently omitted.

`M9-AC3 — Recorded-provider reproducibility`

* Check: run each supported capability through the configured real providers once, sanitize and record the responses, then replay them without network access.
* Pass condition: replay produces the same normalized Feature Graph and verification outcome; fixtures contain no API keys, authorization headers, or private connection values.

`M9-AC4 — Capability presentation`

* Check: load projects containing supported, experimental, approximated, unresolved, and unsupported features.
* Pass condition: the API and UI show the status of every feature before export and do not describe an experimental or unresolved result as fully verified.

`M9-AC5 — Regression command`

* Check: run the documented local test command using the project `uv` environment and without Docker.
* Pass condition: schema, compiler, worker, semantic, render, recorded-provider, and fixture regression suites complete successfully and produce a machine-readable summary grouped by capability.
