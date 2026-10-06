# Building a Reliable LLM Research Pipeline

Use this checklist when starting a package that sends research data to an LLM
API. The goal is not merely to obtain an output, but to make each result
reviewable, reproducible, cost-aware, and recoverable.

## Core design principles

### 1. Make every research unit identifiable

- Give each input record a stable, unique, nonempty ID.
- Join requests, responses, retries, and evaluations by ID—never by row order.
- Check for duplicate IDs, duplicate content, missing required fields, and
  unexpected row-count changes before making an API call.
- Preserve useful local metadata separately from the fields sent to the model.

### 2. Keep source data and provider responses pristine

- Treat the original input as read-only. Write cleaned or transformed data to a
  new location.
- Snapshot the exact selected inputs, prompts, schema, configuration, and
  context used for each run.
- Save the provider's raw response before parsing, normalizing, or correcting
  it. Never replace raw responses with edited versions.
- Write normalized results, validation reports, and merged retry results as
  derived artifacts in separate files.
- Record SHA-256 checksums for important inputs and snapshots so later changes
  are detectable.

### 3. Separate preparation from execution

Design two distinct stages:

1. **Prepare locally:** select records, render prompts, validate payloads,
   estimate cost, and write exact provider-native requests for review.
2. **Execute explicitly:** submit only the reviewed, immutable requests after a
   clear confirmation step.

This boundary makes it possible to inspect what will leave the computer before
credentials are used or costs are incurred. If preparation reveals a problem,
fix the source project and prepare a new run instead of patching generated
requests.

### 4. Start with a deterministic pilot

- Set and record an explicit random seed for sampling and any other randomized
  operation.
- Select a small but representative pilot using the same validation,
  preparation, provider adapter, parsing, and audit path as the full run.
- Include edge cases, long inputs, missing information, and minority categories
  in deliberate tests; a random sample alone may miss them.
- Inspect prompts, raw responses, normalized results, failures, and costs before
  scaling up.
- When comparing providers, models, or prompts, reuse the same record IDs and
  seed so differences are interpretable.

### 5. Estimate cost before submission and track actual cost afterward

- Estimate input tokens, maximum output tokens, request count, and cost during
  preparation.
- Store the model, pricing source or date, execution mode, and any price
  overrides used by the estimate.
- Enforce a configurable pre-submission budget ceiling. Require a separate,
  explicit acknowledgment for inputs whose cost cannot be estimated reliably.
- Display the request count and estimate again at submission time.
- After processing, aggregate the provider-reported input and output tokens and
  calculate actual usage cost when the applicable rates are known.
- Label estimates honestly. Do not describe a text-only estimate as a total
  estimate when requests also contain images or documents.

### 6. Version the full analytical contract

Record and snapshot all material inputs to model behavior:

- system and user prompts, with an explicit prompt version;
- model and provider;
- generation parameters and maximum output length;
- output schema and codebook or other context files;
- package and important dependency versions;
- configuration and execution mode; and
- Git commit, when available.

Use a strict machine-readable output schema when possible. Validate every
response locally, require one result per expected record ID, reject unknown
fields, and use `null` for facts that are absent rather than encouraging the
model to infer unsupported details.

### 7. Make runs append-only and retries explicit

- Give every run a unique ID and UTC creation timestamp.
- Use a simple state machine such as `prepared → submitted → downloaded →
  processed → audited`.
- Make submission idempotent: rerunning a command must not silently submit the
  same recorded work twice.
- Checkpoint long synchronous runs and save remote batch IDs immediately.
- Put retries in linked child runs containing only failed or missing record
  IDs. Do not overwrite the parent run.
- Merge attempts into a new derived file, preferring the newest valid result,
  while retaining the complete attempt history.
- Handle partial provider success: cancellation or expiration does not imply
  that every request failed.

### 8. Audit completeness, not just response validity

For every run, report at least:

- expected, returned, valid, invalid, missing, and duplicate record counts;
- unexpected response IDs;
- provider/request failures by category;
- input and output token totals;
- estimated and actual cost; and
- evaluation metrics when independently reviewed reference labels exist.

A syntactically valid response is not a complete run. Results should not be
treated as analysis-ready until record-level validity and run-level
completeness both pass.

### 9. Define the privacy boundary explicitly

- Send only the fields required for the task.
- Keep API credentials in environment variables or an approved secret manager;
  never place them in source code, configuration committed to Git, logs, or
  provenance files.
- Document which files leave the local environment and which remain local.
- Treat rendered requests, input snapshots, raw responses, and file attachments
  as potentially sensitive research data.
- Perform de-identification before submission and use only approved providers,
  storage locations, retention settings, and access controls.

## Suggested run layout

Keep one immutable directory per attempt:

```text
runs/RUN_ID/
├── manifest.json
├── state.json
├── REVIEW.md
├── input_snapshot/
├── project_snapshot/
├── api_requests/
├── raw_responses/
├── outputs/
└── run_reports/
```

The exact names are flexible; the separation is what matters. Inputs and raw
responses are evidence. Parsed outputs and reports are derived products.

## Minimum provenance record

A run manifest should be machine-readable and should contain fields like these:

```json
{
  "run_id": "20261006T180000Z_openai_a1b2c3d4",
  "created_at_utc": "2026-10-06T18:00:00Z",
  "project": "study-name",
  "provider": "openai",
  "model_requested": "exact-model-id",
  "model_returned": null,
  "prompt_version": "1.0",
  "random_seed": 42,
  "selection": {
    "method": "random",
    "selected_count": 20
  },
  "source_path": "data/input.csv",
  "source_sha256": "...",
  "request_count": 20,
  "execution_mode": "batch",
  "estimated_cost_usd": 0.42,
  "pricing_as_of": "YYYY-MM-DD",
  "git_commit": "...",
  "package_version": "...",
  "python_version": "..."
}
```

Use `null` or `"unknown"` for unavailable metadata; never invent provenance.
Add hashes for prompts, schemas, context files, and prepared requests. After
processing, save a separate summary with returned model IDs, token usage,
actual cost, row counts, failures, and output paths.

## Preflight checklist

Before the first paid request:

- [ ] Stable IDs are present and unique.
- [ ] Raw input is preserved and checksummed.
- [ ] Required fields, duplicates, and row counts have been checked.
- [ ] Only necessary fields will be sent to the provider.
- [ ] Prompts, codebook/context, and output schema are versioned and aligned.
- [ ] A deterministic seed and pilot selection are recorded.
- [ ] Exact provider requests can be inspected locally.
- [ ] Request sizes and model limits have been checked.
- [ ] Cost is estimated, pricing assumptions are recorded, and a budget ceiling
      is enforced.
- [ ] Credentials and sensitive local fields are absent from logs and requests.
- [ ] The retry, resume, cancellation, and duplicate-submission behavior is
      tested with a fake provider.

Before scaling from the pilot to the full run:

- [ ] Pilot outputs have been reviewed by a domain-informed researcher.
- [ ] Every expected pilot ID is accounted for.
- [ ] Schema violations, missing outputs, and provider errors have been audited.
- [ ] Token usage and actual pilot cost are reasonable.
- [ ] Reference-label metrics or inter-reviewer checks have been examined when
      available.
- [ ] The full run is prepared as a new run and reviewed independently.

Before analysis or publication:

- [ ] Raw responses remain unchanged and available in approved storage.
- [ ] Normalized results can be traced to source records and request IDs.
- [ ] Retries and merges retain parent-child provenance.
- [ ] Limitations, model/provider versions, prompt version, sampling method,
      seed, exclusions, failures, and human review procedures are documented.
- [ ] The analysis uses audited outputs rather than an unaudited download.

## Tests worth requiring in a new package

- Deterministic selection with a fixed seed.
- Validation of missing IDs, duplicate IDs, duplicate content, and missing
  required fields.
- Exact request rendering and schema enforcement.
- Budget rejection before provider contact.
- Fake-provider end-to-end processing with no paid calls.
- Partial success, cancellation, malformed output, and missing-output recovery.
- Safe resume without duplicate submission.
- Retry and merge behavior that leaves the parent run unchanged.
- Checks that secrets and locally preserved columns never enter provider
  payloads.
- Provenance and checksum checks that fail when prepared artifacts change.

The strongest implementation is deliberately boring: explicit inputs,
deterministic selection, inspectable requests, immutable evidence, append-only
runs, strict validation, and enough provenance to explain exactly how every
result was produced.
