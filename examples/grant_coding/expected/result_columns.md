# Result columns

- `record_id`: stable source identifier.
- `source_row`: zero-based row in the source snapshot.
- `project_title`, `abstract`: normalized model-facing fields.
- `year`, `investigator`, `source_file`: locally preserved metadata.
- `primary_label`, `secondary_label`, `confidence`, `justification`: schema-defined predictions.
- `provider`, `model_requested`, `model_returned`, `run_id`, `parent_run`,
  `prompt_version`, `custom_id`, and `batch_id`: row-level run provenance.
- `validated_at`, `validation_status`, `input_tokens_request`,
  `output_tokens_request`, and `actual_request_cost_usd`: validation, usage,
  and cost provenance for the request containing the row.
- `_kllm_truncated_fields`: JSON description of any explicit truncation; empty in this example.

Both `results.csv` and `results.parquet` contain these columns. Shared prompt
hashes, schema, configuration, source checksum, provider options, environment,
and pricing assumptions are retained once in `manifest.json`. Raw provider
responses remain under `raw/`. If failures occur, `failures.jsonl` records the
error category and message with the same relevant row provenance; no empty
failure file is created for a successful run.
