#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! -d "$1" ]]; then
  echo "Usage: $0 CHILD_RUN_DIR" >&2
  exit 2
fi

python - "$1" <<'PY'
import sys
from pathlib import Path

import pandas as pd

run_dir = Path(sys.argv[1]).resolve()
csv_path = run_dir / "outputs" / "merged.csv"
parquet_path = run_dir / "outputs" / "merged.parquet"
expected = {f"MRETRY-{index:03d}" for index in range(1, 31)}

for path in (csv_path, parquet_path):
    if not path.is_file():
        raise SystemExit(f"Missing merged output: {path}")

csv_frame = pd.read_csv(csv_path, dtype={"record_id": str})
parquet_frame = pd.read_parquet(parquet_path)

for label, frame in (("CSV", csv_frame), ("Parquet", parquet_frame)):
    if "record_id" not in frame.columns:
        raise SystemExit(f"{label} has no record_id column")
    ids = frame["record_id"].astype(str)
    if len(frame) != 30 or ids.nunique() != 30 or set(ids) != expected:
        raise SystemExit(
            f"{label} verification failed: rows={len(frame)}, "
            f"unique_ids={ids.nunique()}, missing={sorted(expected - set(ids))}, "
            f"extra={sorted(set(ids) - expected)}"
        )

print(f"Verified 30 unique records in {csv_path}")
print(f"Verified 30 unique records in {parquet_path}")
PY
