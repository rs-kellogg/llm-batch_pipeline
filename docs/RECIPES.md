# Additional recipes

Start with the complete `examples/grant_coding/` workflow. These variations
only describe behavior that differs from that example.

## Structured extraction

Map document text under `fields_sent` and define factual fields in
`schema.json`. Include nullable values for facts that may not appear, and tell
the system prompt to return `null` rather than infer unsupported information.
Keep document date, corpus, and source path under `columns_preserved` when the
model does not need them. PDF parsing/OCR remains an upstream step.

## Gold-label pilot

Keep a human label in a local source column that is not sent to the model, then
map the predicted field to it:

```yaml
pilot:
  sample_size: 40
  random_seed: 2026
  gold_columns:
    primary_label: human_primary_label
```

Run `pilot generate` first and inspect its saved records and rendered prompts;
then use `pilot run PILOT_DIR`. The run reports exact-match accuracy for
configured fields. The pilot-and-impute workflow is deliberately not
automatic: inspect class balance, errors, and schema validity before selecting
a model.

## Configurable multi-row requests

Change `task.rows_per_request` to balance repeated prompt overhead against
context size and failure scope. A value of `1` isolates every record. Larger
values reduce repeated instructions but one malformed response can affect more
rows. `validate` estimates request tokens and provider-native JSONL bytes before
any run artifacts are created.
