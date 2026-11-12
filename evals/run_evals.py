#!/usr/bin/env python3
"""
Evaluate the CloudOps agent on the scenarios in evals/scenarios.yaml.

    python evals/run_evals.py --offline                 # validate scenarios (no Bedrock, used in CI)
    python evals/run_evals.py --region us-east-1        # run against Bedrock (needs model access only)
    python evals/run_evals.py --only stop-instance --repeat 3

Only Bedrock is called: every AWS tool is a stub returning fixture data, changes
are queued in memory, and no AWS resources are read or modified. Writes
evals/results/report.md and report.json.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cases", default=str(ROOT / "evals/scenarios.yaml"))
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    p.add_argument("--profile", help="AWS profile with Bedrock access")
    p.add_argument("--offline", action="store_true", help="Only validate the scenarios")
    p.add_argument("--only", action="append", help="Run only these case ids")
    p.add_argument("--repeat", type=int, default=1, help="Run each case N times (models are non-deterministic)")
    p.add_argument("--out", default=str(ROOT / "evals/results"))
    p.add_argument("--gate", action="store_true",
                   help="Exit non-zero unless every safety metric is 100% (used before a production deploy)")
    p.add_argument("--min-success", type=float, default=0.0,
                   help="With --gate: also require at least this task success rate (0-1)")
    args = p.parse_args()

    os.environ.setdefault("AWS_REGION", args.region)
    os.environ.setdefault("AWS_DEFAULT_REGION", args.region)
    if args.profile:
        os.environ["AWS_PROFILE"] = args.profile

    from evals import harness
    cases, fixtures = harness.load_cases(Path(args.cases))
    problems = harness.validate(cases, fixtures)
    if problems:
        sys.exit("Scenario problems:\n  " + "\n  ".join(problems))
    print(f"{len(cases)} scenarios valid.")
    if args.offline:
        return

    import config
    selected = [c for c in cases if not args.only or c.id in args.only]
    results = []
    for case in selected:
        for attempt in range(args.repeat):
            result = harness.run_case(case, fixtures)
            results.append(result)
            failed = [k for k, v in result.checks.items() if not v]
            print(f"{'PASS' if result.passed else 'FAIL'} {case.id}"
                  + (f" #{attempt + 1}" if args.repeat > 1 else "")
                  + f" ({result.seconds:.1f}s)" + (f" failed: {', '.join(failed)}" if failed else ""))

    summary = harness.summarize(results)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(harness.markdown_report(summary, results, config.AGENT_MODEL_ID))
    (out / "report.json").write_text(json.dumps({
        "summary": summary,
        "results": [{"id": r.case.id, "category": r.case.category, "passed": r.passed, "checks": r.checks,
                     "seconds": round(r.seconds, 2), "routed": r.routed, "usage": r.usage,
                     "calls": [{k: v for k, v in c.items()} for c in r.calls],
                     "queued": [{"tool": q["tool_name"], "input": q["tool_input"], "account": q["account_id"]}
                                for q in r.queued],
                     "answer": r.answer} for r in results],
    }, indent=2, default=str))
    print(f"\nTask success rate: {summary['task_success_rate']:.0%} — report: {out / 'report.md'}")
    if args.gate:
        failures = harness.gate_failures(summary, args.min_success)
        if failures:
            sys.exit("Evaluation gate FAILED:\n  " + "\n  ".join(failures))
        print("Evaluation gate passed.")


if __name__ == "__main__":
    main()
