# Development

## Capability regression

Run the complete local capability suite with the project `uv`-managed `.venv` and local CadQuery worker:

```sh
make test-capabilities
```

The command does not use Docker and writes its machine-readable result to
`artifacts/capability-summary.json`.
