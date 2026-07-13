from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.capability_reporting import capability_report_entry, report_status

ROOT = Path(__file__).resolve().parent.parent
def main() -> int:
    suite = unittest.TestLoader().discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    cases = json.loads(
        (ROOT / "tests" / "fixtures" / "capabilities" / "cases.json").read_text(encoding="utf-8")
    )
    capabilities = {}
    for case in cases:
        if case["status"] == "supported":
            observation_dir = ROOT / "tests" / "fixtures" / "capabilities" / "observations"
            observations = []
            for variant in case["variants"]:
                path = observation_dir / f"{case['id']}.{variant}.json"
                if path.exists():
                    observations.append(json.loads(path.read_text(encoding="utf-8")))
            entry = capability_report_entry(case, observations, worker_suite_passed=result.wasSuccessful())
        else:
            entry = {"status": case["status"], "fixture_count": 0}
        capabilities[case["id"]] = entry

    summary = {
        "status": report_status(worker_suite_passed=result.wasSuccessful(), capabilities=capabilities),
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
