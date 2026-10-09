# Example command cheatsheet

## Setup

```bash
mamba activate kllm-batch
python -m pip install -e .
kllm-batch --help
```

## Grant-coding pilot

```bash
examples/grant_coding/walkthrough/validate.sh
examples/grant_coding/walkthrough/prepare-pilot.sh openai
examples/grant_coding/walkthrough/submit.sh PILOT_RUN_DIR
```

## Complete batch

```bash
examples/grant_coding/walkthrough/prepare-batch.sh openai
examples/grant_coding/walkthrough/submit.sh BATCH_RUN_DIR
examples/grant_coding/walkthrough/status.sh BATCH_RUN_DIR
examples/grant_coding/walkthrough/sync.sh BATCH_RUN_DIR
examples/grant_coding/walkthrough/audit.sh BATCH_RUN_DIR
```

## Other provider and comparison

```bash
examples/grant_coding/walkthrough/prepare-pilot.sh anthropic
examples/grant_coding/walkthrough/compare.sh OPENAI_RUN_DIR ANTHROPIC_RUN_DIR
```

## Multi-segment operations

```bash
examples/grant_coding/multisegment/prepare.sh openai
examples/grant_coding/multisegment/submit-range.sh RUN_DIR 0 2
examples/grant_coding/multisegment/cancel-range.sh RUN_DIR 1 2
examples/grant_coding/multisegment/sync.sh RUN_DIR
```

## Retry and merge

```bash
examples/grant_coding_retry/setup-parent.sh openai
examples/grant_coding_retry/inspect-failures.sh PARENT_RUN_DIR
examples/grant_coding_retry/retry.sh PARENT_RUN_DIR
examples/grant_coding_retry/submit.sh CHILD_RUN_DIR
examples/grant_coding_retry/sync.sh CHILD_RUN_DIR
examples/grant_coding_retry/merge.sh CHILD_RUN_DIR
examples/grant_coding_retry/verify-merged.sh CHILD_RUN_DIR
```

## Attachments

```bash
examples/job_post_attachments/prepare.sh openai
examples/job_post_attachments/attach.sh RUN_DIR
examples/job_post_attachments/submit.sh RUN_DIR
examples/job_post_attachments/sync.sh RUN_DIR
```

- Replace placeholders with the complete run directory printed by the preceding command.
- `submit`, batch `status`, `sync`, and `cancel` contact providers; other commands shown are local.
