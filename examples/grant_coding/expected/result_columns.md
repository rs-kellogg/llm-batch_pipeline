# Result columns

- `record_id`: stable source identifier.
- `source_row`: zero-based row in the source snapshot.
- `project_title`, `abstract`: normalized model-facing fields.
- `year`, `investigator`, `source_file`: locally preserved metadata.
- `primary_label`, `secondary_label`, `confidence`, `justification`: schema-defined predictions.
- `provider`, `model_requested`, `model_returned`, `run_id`, `custom_id`: run provenance.
- `_kllm_truncated_fields`: JSON description of any explicit truncation; empty in this example.

The complete prompt, schema, configuration, source checksum, batch IDs, token usage, and validation timestamps are retained in the run manifest and provenance report.
