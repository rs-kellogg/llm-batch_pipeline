#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ( "$1" != "openai" && "$1" != "anthropic" ) ]]; then
  echo "Usage: $0 {openai|anthropic}" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fixtures_dir="$script_dir/fixtures"

case "$1" in
  openai)
    fixture="$fixtures_dir/openai/20261008T234504Z_openai_760d2c9d"
    ;;
  anthropic)
    fixture="$fixtures_dir/anthropic/20261008T234934Z_anthropic_132a1977"
    ;;
esac

target="$script_dir/runs/$(basename "$fixture")"
if [[ -e "$target" ]]; then
  echo "Refusing to overwrite existing run: $target" >&2
  echo "Use that run if it is valid, or move it aside before installing the fixture." >&2
  exit 1
fi

python - "$fixtures_dir" "$fixture" "$target" "$script_dir" "$1" <<'PY'
from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

fixtures_dir = Path(sys.argv[1]).resolve()
fixture = Path(sys.argv[2]).resolve()
target = Path(sys.argv[3]).resolve()
example_dir = Path(sys.argv[4]).resolve()
provider = sys.argv[5]
inventory = fixtures_dir / "SHA256SUMS"
prefix = f"{provider}/"

entries: list[tuple[str, str]] = []
for line in inventory.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    digest, relative = line.split(maxsplit=1)
    if relative.startswith(prefix):
        entries.append((digest, relative))

if not entries:
    raise SystemExit(f"No checksum entries found for {provider}")

for expected, relative in entries:
    path = fixtures_dir / relative
    if not path.is_file():
        raise SystemExit(f"Fixture file is missing: {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"Fixture checksum mismatch: {path}")

target.parent.mkdir(parents=True, exist_ok=True)
staging = target.parent / f".{target.name}.fixture-building"
if staging.exists():
    raise SystemExit(f"Incomplete fixture installation already exists: {staging}")

try:
    shutil.copytree(fixture, staging)
    replacement = str(example_dir).encode("utf-8")
    placeholder = b"__EXAMPLE_ROOT__"
    for path in sorted(staging.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if placeholder in data:
            path.write_bytes(data.replace(placeholder, replacement))
    unresolved = [
        path for path in staging.rglob("*")
        if path.is_file() and placeholder in path.read_bytes()
    ]
    if unresolved:
        raise RuntimeError(f"Unresolved path placeholders: {unresolved}")
    staging.rename(target)
except Exception:
    if staging.exists():
        shutil.rmtree(staging)
    raise

print(f"Installed failed parent fixture: {target}")
print(f"Prepare its retry child with: kllm-batch retry {target}")
PY
