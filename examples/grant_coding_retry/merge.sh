#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! -d "$1" ]]; then
  echo "Usage: $0 CHILD_RUN_DIR" >&2
  exit 2
fi

kllm-batch merge "$1"
