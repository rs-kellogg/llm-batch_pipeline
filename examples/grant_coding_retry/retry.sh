#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! -d "$1" ]]; then
  echo "Usage: $0 PARENT_RUN_DIR" >&2
  exit 2
fi

kllm-batch retry "$1"

echo "Copy the complete retry child path above. Review it before using submit.sh."
