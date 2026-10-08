# Job-post PDF and PNG attachment example

This example extracts structured facts from two PDFs and two PNG screenshots.
The four source files are copied under `data/attachments/` with normalized
filenames. `data/attachments/SHA256SUMS` records their exact hashes. The example
creates four requests across two batch segments for either provider.

The files were copied from the local download directories as follows:

- `other_resources/pdf_jobpost/Postdoctoral Research Scholar - Maywood, IL 60153 - Indeed.com.pdf`
  → `data/attachments/pdf/postdoctoral-research-scholar.pdf`
- `other_resources/pdf_jobpost/Research Professional - Chicago, IL 60637 - Indeed.com.pdf`
  → `data/attachments/pdf/research-professional.pdf`
- the two files under `other_resources/png_jobpost/`, in filename order
  → `data/attachments/png/job-post-screenshot-1.png` and
  `data/attachments/png/job-post-screenshot-2.png`

Preparation and attachment are local. `submit` is the first paid operation;
file-processing cost is not included in the prepared text-only estimate. The
scripts never use `--yes`, print credentials, or infer a run directory.

## Prepare and attach

```bash
examples/job_post_attachments/prepare.sh openai
# or prepare a separate run with anthropic
examples/job_post_attachments/prepare.sh anthropic
```

Copy the complete `RUN_DIR` printed above. Inspect the original text-only files,
then attach the local documents:

```bash
examples/job_post_attachments/attach.sh RUN_DIR
```

Review these artifacts before submission:

- `api_requests/segment_*.jsonl`: immutable original text-only requests.
- `api_requests/attached_segment_*.jsonl`: requests that embed the PDF/PNG
  bytes and will actually be submitted.
- `manifest.json`: attachment paths, media types, byte counts, SHA-256 hashes,
  and the `text_only_excludes_files` cost-estimate scope.
- `REVIEW.md`: provider-specific commands for inspecting the attached prompt
  text without printing the base64 file bodies.

## Submit and retrieve

```bash
examples/job_post_attachments/submit.sh RUN_DIR
examples/job_post_attachments/sync.sh RUN_DIR
kllm-batch audit RUN_DIR
```

Run the workflow once for OpenAI and once for Anthropic, using a newly prepared
run each time. Compare the resulting structured fields with `kllm-batch
compare OPENAI_RUN_DIR ANTHROPIC_RUN_DIR`. Differences can reflect model
behavior and the fact that the PNG records contain only the visible screenshot
area, while the PDFs contain multiple pages.

If an attached run produces retryable failures, `kllm-batch retry RUN_DIR`
creates a child whose attachment state is `required`. Run `attach.sh` on that
child before submitting it; the command verifies every file against the parent
run's recorded checksum.
