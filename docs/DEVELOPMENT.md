# Development

## Capability regression

Run the complete local capability suite with the project `uv`-managed `.venv` and local CadQuery worker:

```sh
make test-capabilities
```

The command does not use Docker and writes its machine-readable result to
`artifacts/capability-summary.json`.

The summary reports worker and provider evidence separately. A passing worker export suite does not imply measured
provider precision, recall, or dimension accuracy. Provider gates are calculated only after recorded outcomes exist
for every independent capability drawing.

Run real-provider contract tests with secrets from `.env`:

```sh
make test-e2e-real
```

The real suite accepts either a verified export or an explicit `needs_review` result. Schema-invalid provider plans
and repairs must never execute fallback Python or produce an internal server error.
