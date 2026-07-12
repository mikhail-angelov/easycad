from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
def main() -> int:
    suite = unittest.TestLoader().discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    cases = json.loads(
        (ROOT / "tests" / "fixtures" / "capabilities" / "cases.json").read_text(encoding="utf-8")
    )
    capabilities = {}
    for case in cases:
        entry = {
            "status": case["status"],
            "fixture_count": len(case.get("variants", [case["source_drawing"]])),
        }
        if case["status"] == "supported":
            observations = case.get("observations", [])
            entry["evaluation_status"] = (
                "measured" if len(observations) >= 5 else "insufficient_evidence"
            )
            entry["observation_count"] = len(observations)
            entry["metrics"] = None
        capabilities[case["id"]] = entry

    summary = {
        "status": "passed" if result.wasSuccessful() else "failed",
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "capabilities": capabilities,
    }
    output = ROOT / "artifacts" / "capability-summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Capability summary: {output}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
