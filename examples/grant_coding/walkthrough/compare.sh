#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 || ! -d "$1" || ! -d "$2" ]]; then
  echo "Usage: $0 RUN_DIR_A RUN_DIR_B" >&2
  exit 2
fi

kllm-batch compare "$1" "$2"
