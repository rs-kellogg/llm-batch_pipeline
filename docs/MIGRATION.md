# Migration from the original batch scripts

The files under `pipeline_openai/` and `pipeline_post/` remain unchanged as
reference material. New projects should use the package API or `kllm-batch`
rather than importing their project-specific globals.

| Earlier behavior | New workflow |
| --- | --- |
| Build prompts and JSONL in `codebook_batch.py` | Configure `fields_sent`, prompt files, context, and `schema.json`; run `kllm-batch validate` and `prepare` |
| Split files and submit in `simple_batch_manager.py` | `prepare` splits by request count and bytes; `submit` records every remote ID atomically |
| Poll and download with the manager | `status` and idempotent `sync [--watch]` |
| Locate malformed chunks with `find_bad_chunks` | `sync` validates JSON, the schema, expected IDs, completeness, and duplicate output IDs; `audit` writes reports |
| Repair chunks with `fix_failed_chunks` | `retry RUN_ID` creates an immutable linked child attempt containing retryable failed rows |
| Resubmit missing rows with `retry_missing_batch.py` | `retry`, review the new estimate, then explicitly `submit CHILD_RUN_ID` |
| Concatenate repaired outputs manually | `merge CHILD_RUN_ID` deterministically prefers the newest valid result by `record_id` |

## Suggested migration sequence

1. Copy the original source table without modifying it.
2. Select its stable key as `input.id_column` and map only necessary model
   fields under `fields_sent`.
3. Move the system instructions and user template into separate prompt files.
4. Convert one output row into a portable JSON Schema. Optional values must be
   required-but-nullable so both providers share the same contract.
5. Put codebooks or taxonomies under `context/` and reference them in YAML.
6. Run validation and correct all duplicate IDs/content upstream.
7. Run a small pilot, inspect validity and cost, then prepare the full run.

Earlier outputs do not automatically become new-package runs because they lack
the immutable manifest, mappings, and state contract. Preserve them as legacy
artifacts; start a new run when reproducible provenance is required.
