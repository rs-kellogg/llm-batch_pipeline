# Kellogg LLM Batch

`kellogg-llm-batch` provides the `kllm-batch` command for reproducible
classification and structured extraction with the OpenAI and Anthropic batch
APIs.

**[Read the complete documentation](https://rs-kellogg.github.io/llm-batch_pipeline/)**

Building a related tool or research pipeline? Start with the reusable
[LLM research pipeline checklist](RESEARCH_LLM_PIPELINE_CHECKLIST.md).

The package keeps local preparation separate from paid API operations.
`init`, `validate`, and `prepare` are local. `submit` executes the exact
prepared artifacts and may incur model charges. Batch-mode `status`, `sync`,
and `cancel` also contact the selected provider.

## Quick start

Clone the repository and install it in editable mode inside a dedicated Python
environment. Pulling later changes into the checkout immediately updates the
installed command:

```bash
git clone https://github.com/rs-kellogg/llm-batch_pipeline.git
cd llm-batch_pipeline
mamba create -n kllm-batch python=3.12 -y
mamba activate kllm-batch
python -m pip install -e .
```

Create and validate a project, then prepare a small deterministic pilot:

```bash
kllm-batch init my-project
kllm-batch validate -c my-project/project.yaml
kllm-batch prepare -c my-project/project.yaml --provider openai \
  --sample-size 4 --seed 42
```

The standard installation also includes the local project builder. Run
`kllm-batch gui my-project` to edit the initialized project in your browser.

Review the generated `RUN_ID/REVIEW.md` and exact request payloads before
allowing a provider call:

```bash
export OPENAI_API_KEY="..."
kllm-batch submit RUN_ID
```

Never commit API keys or place them in `project.yaml`. See the
[basic workflow](https://rs-kellogg.github.io/llm-batch_pipeline/basic.html)
for the complete pilot and batch process, or start with the worked
[grant-coding example](examples/grant_coding/README.md).

For concrete provider smoke tests, use the guarded
[multi-segment scripts](examples/grant_coding/multisegment/README.md) or the
[PDF/PNG job-post example](examples/job_post_attachments/README.md). These
examples keep preparation separate from paid submission.

## Development

Clone the repository and install the development and documentation extras:

```bash
git clone https://github.com/rs-kellogg/llm-batch_pipeline.git
cd llm-batch_pipeline
python -m pip install -e '.[dev,docs]'
python -m pytest
sphinx-build -W --keep-going -b html docs docs/_build/html
```
