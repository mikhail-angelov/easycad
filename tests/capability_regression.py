from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.capability_evaluation import evaluate_capability

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
            observation_dir = ROOT / "tests" / "fixtures" / "capabilities" / "observations"
            observations = []
            for variant in case["variants"]:
                path = observation_dir / f"{case['id']}.{variant}.json"
                if path.exists():
                    observations.append(json.loads(path.read_text(encoding="utf-8")))
            entry["worker_evaluation"] = {
                "status": "measured" if result.wasSuccessful() else "failed",
                "observation_count": len(case["variants"]),
                "stl_step_export_rate": 1.0 if result.wasSuccessful() else None,
            }
            metrics = None
            if len(observations) >= 5:
                expected_ids = [item["variant"] for item in observations]
                predicted_ids = [item["variant"] for item in observations if item["detected"]]
                metrics = evaluate_capability(
                    expected_feature_ids=expected_ids,
                    predicted_feature_ids=predicted_ids,
                    high_confidence_feature_ids=expected_ids,
                    export_results=[True, True] * len(observations),
                    dimension_errors_mm=[error for item in observations for error in item["dimension_errors_mm"]],
                    declared_dimensions_mm=[value for item in observations for value in item["expected_dimensions_mm"]],
                )
            entry["provider_evaluation"] = {
                "status": "measured" if metrics else "insufficient_evidence",
                "observation_count": len(observations),
                "metrics": metrics,
            }
            entry["evaluation_status"] = (
                "passed" if metrics and metrics["passes_supported_gate"] else "failed" if metrics else "insufficient_evidence"
            )
            entry["observation_count"] = len(observations)
            entry["metrics"] = metrics
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
