#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! -d "$1" ]]; then
  echo "Usage: $0 RUN_DIR" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

kllm-batch attach-files "$1" \
  --column attachment_file \
  --files-dir "$script_dir/data/attachments" \
  --acknowledge-unestimated-cost
