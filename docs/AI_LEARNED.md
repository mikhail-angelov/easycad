## 2026-07-15 — Real specification flow to STL

### Goal

Verify the specification-first user path against real providers.

### Golden path

Run `.venv/bin/python -m unittest -v tests.e2e_real_user_specification_flow`.
The bracket fixture may return a valid but geometrically wrong draft; submit the Build diagnostics through the existing `build_repair` clarification and replan before rebuilding.

### Verification

The bracket flow completed with DeepSeek and exported `artifacts/real-user-flow-3/model.stl` (59,184 bytes).

### Failure pattern avoided

Valid tool JSON does not guarantee a geometrically valid CadQuery model; use feature-linked build diagnostics as explicit user clarification.

### Ruled-out approaches

- Treating a valid DraftSpecification as a successful build; it can still place geometry outside its target.

