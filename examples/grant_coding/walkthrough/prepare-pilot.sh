#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ( "$1" != "openai" && "$1" != "anthropic" ) ]]; then
  echo "Usage: $0 {openai|anthropic}" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
example_dir="$(cd "$script_dir/.." && pwd)"

kllm-batch prepare \
  -c "$example_dir/project.yaml" \
  --provider "$1" \
  --sample-size 4 \
  --seed 42 \
  --execution sync

echo "Copy the complete prepared run path above, inspect it, then run submit.sh."
