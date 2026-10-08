#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! -d "$1" ]]; then
  echo "Usage: $0 RUN_DIR" >&2
  exit 2
fi

kllm-batch retry "$1"

echo "Inspect the child run printed above before submitting it."
