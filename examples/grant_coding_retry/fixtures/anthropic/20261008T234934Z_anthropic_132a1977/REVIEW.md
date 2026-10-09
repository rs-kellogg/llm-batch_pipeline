# Review before submission

- Project: grant-topic-coding-multisegment-retry
- Purpose: production
- Selection: all
- Records: 30 of 30
- Requests: 10 across 5 segment(s)
- Provider/model: anthropic / claude-haiku-4-5
- Execution: batch
- Estimated maximum cost: $0.0337

## What to inspect

1. `api_requests/segment_*.jsonl` contains the exact provider-native payloads that will be executed. The prompts are at `params.system` (system prompt) and `params.messages[].content` (user prompt).
2. `manifest.json` records selection, hashes, model, pricing, and environment provenance.

To print every rendered system and user prompt (requires `jq`):

```bash
# Run from inside this run directory
jq -r '.params.system, .params.messages[].content' api_requests/segment_*.jsonl
```

Files under `input_snapshot/` and `project_snapshot/` support joins, retries, validation, and reproducibility; they normally do not need manual review.

## Submit after review

From inside this run directory:

```bash
kllm-batch submit .
```
