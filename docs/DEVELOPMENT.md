# Development

## Capability regression

Run the complete local capability suite with the project `uv`-managed `.venv` and local CadQuery worker:

```sh
make test-capabilities
```

The command does not use Docker and writes its machine-readable result to
`artifacts/capability-summary.json`.

### Evidence states

The capability report uses four evidence states. They are deliberately distinct from an implementation checkbox:

- `implemented`: code path exists, but there is no paired deterministic failure proving the claimed effect.
- `verified`: a deterministic positive fixture passes and its paired negative fixture fails for the expected reason.
- `observed`: a sanitized real-provider response was captured and replayed without network access.
- `supported`: both `verified` deterministic evidence and an `observed` provider success exist for the capability.

`needs_review` is not evidence of support. It means the application safely refused to claim verification and must not
be counted as a verified export or a successful provider observation.

### Silhouette fixtures

Deterministic silhouette comparison applies only to CAD renders with fixture-defined camera, dimensions, and
thresholds. It measures binary-mask bounds, occupied area, and symmetric difference. It is not applied to arbitrary
uploads because drawing perspective, annotation, crop, and line style are not calibrated; their visual comparison
remains advisory.

The summary reports worker and provider evidence separately. A passing worker export suite does not imply measured
provider precision, recall, or dimension accuracy. Provider gates are calculated only after recorded outcomes exist
for every independent capability drawing.

Run real-provider contract tests with secrets from `.env`:

```sh
make test-e2e-real
```

The real suite accepts either a verified export or an explicit `needs_review` result. Schema-invalid provider plans
and repairs must never execute fallback Python or produce an internal server error.
