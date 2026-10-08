#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 || ! -d "$1" ]]; then
  echo "Usage: $0 RUN_DIR START END" >&2
  echo "END is exclusive; use -1 to continue through the final segment." >&2
  exit 2
fi

kllm-batch submit "$1" --seg_start "$2" --seg_end "$3"
