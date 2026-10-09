#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! -d "$1" ]]; then
  echo "Usage: $0 RUN_DIR" >&2
  exit 2
fi

python - "$1" <<'PY'
import json
import sys
from collections import Counter
from pathlib import Path

run_dir = Path(sys.argv[1]).resolve()
failures_path = run_dir / "outputs" / "failures.jsonl"
audit_path = run_dir / "run_reports" / "audit.json"
expected = {"MRETRY-002", "MRETRY-014", "MRETRY-026"}

failures = []
if failures_path.exists():
    for line in failures_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            failures.append(json.loads(line))

by_id = {str(item.get("record_id")): item for item in failures}
missing_expected = sorted(expected - set(by_id))
unexpected = sorted(set(by_id) - expected)
wrong_category = sorted(
    record_id
    for record_id in expected & set(by_id)
    if by_id[record_id].get("category") != "missing_output"
)

print(f"Run: {run_dir}")
print(f"Failure records: {len(failures)}")
print(f"Categories: {dict(sorted(Counter(item.get('category') for item in failures).items()))}")
print(f"Expected controlled IDs found: {sorted(expected & set(by_id))}")
if missing_expected:
    print(f"Expected IDs not reported as failures: {missing_expected}")
if wrong_category:
    print(f"Expected IDs with a different failure category: {wrong_category}")
if unexpected:
    print(f"Additional provider/model failures: {unexpected}")

if audit_path.exists():
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    print(f"Audit: {json.dumps(audit, sort_keys=True)}")
else:
    print(f"Audit file not found: {audit_path}")

if missing_expected or wrong_category:
    print(
        "The installed parent does not contain the expected fixture failures; "
        "review the files above before retrying.",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
