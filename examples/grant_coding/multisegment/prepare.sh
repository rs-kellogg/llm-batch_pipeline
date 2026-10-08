#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ( "$1" != "openai" && "$1" != "anthropic" ) ]]; then
  echo "Usage: $0 {openai|anthropic}" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
example_dir="$(cd "$script_dir/.." && pwd)"
config="$example_dir/project.multisegment.yaml"

kllm-batch validate -c "$config"
kllm-batch prepare -c "$config" --provider "$1" --execution batch

echo "Copy the complete prepared run path above for the guarded submit scripts."
