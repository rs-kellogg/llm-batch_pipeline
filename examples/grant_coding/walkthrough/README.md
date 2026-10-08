# Grant-coding walkthrough scripts

These scripts are thin wrappers around the commands in the parent
[`README.md`](../README.md). They resolve `project.yaml` from the script
location, so they can be run from any working directory. Provider-facing
scripts require an explicit run directory, and submission keeps the normal
interactive cost confirmation.

## Local validation and preparation

```bash
examples/grant_coding/walkthrough/validate.sh

examples/grant_coding/walkthrough/prepare-pilot.sh openai
# or: prepare-pilot.sh anthropic

examples/grant_coding/walkthrough/prepare-batch.sh openai
# or: prepare-batch.sh anthropic
```

The pilot selects four rows deterministically with seed 42 and uses synchronous
execution. The batch includes all ten rows. Copy the complete `RUN_DIR` printed
by either preparation command and inspect `REVIEW.md`, `manifest.json`, and the
request JSONL before submission.

## Execute a reviewed run

For a synchronous pilot, only submission is needed:

```bash
examples/grant_coding/walkthrough/submit.sh PILOT_RUN_DIR
```

For a batch run:

```bash
examples/grant_coding/walkthrough/submit.sh BATCH_RUN_DIR
examples/grant_coding/walkthrough/sync.sh BATCH_RUN_DIR
```

`status.sh` provides an optional one-time provider status check. `audit.sh`
recomputes the local audit when needed:

```bash
examples/grant_coding/walkthrough/status.sh BATCH_RUN_DIR
examples/grant_coding/walkthrough/audit.sh BATCH_RUN_DIR
```

## Retry and compare

```bash
examples/grant_coding/walkthrough/retry.sh RUN_DIR_WITH_FAILURES
examples/grant_coding/walkthrough/compare.sh OPENAI_RUN_DIR ANTHROPIC_RUN_DIR
```

`retry.sh` only prepares a child run; it does not submit it. Review the child
before passing it to `submit.sh`. `compare.sh` is local and expects two
processed runs.

The wrappers never use `--yes`, choose a run automatically, or read or print
API keys. The parent README remains the authoritative explanation of artifacts,
cost boundaries, recovery, and interpretation.
