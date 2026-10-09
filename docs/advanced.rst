.. _advanced-workflows:

Advanced workflows
==================

Most researchers come here for one of two reasons: a completed run has failed
records to retry, or two completed runs need to be compared. Start with the
relevant workflow below; recovery, selection, comparison, configuration, and
provenance details follow afterward.

For an optional, separate PNG/PDF input step after preparation, see
:doc:`file_attachments`.

.. contents:: On this page
   :local:
   :depth: 2

.. _retries-and-reruns:

Retry failed records and merge the results
------------------------------------------

The checked-in `multi-segment retry smoke-test example
<https://github.com/rs-kellogg/llm-batch_pipeline/tree/main/examples/grant_coding_retry>`_
provides provider-selectable scripts and recorded missing outputs for
practicing this complete workflow without relying on an organic model failure.
It installs a terminal parent with three ``missing_output`` records; only
submission of the newly prepared retry child incurs provider usage.

Use a retry chain when ``outputs/failures.jsonl`` contains retryable provider,
request, malformed-output, schema, or missing-output failures. The original
run and its raw responses remain unchanged.

#. Inspect ``outputs/failures.jsonl`` and the run's audit report.
#. Prepare a child run containing only retryable failed records:

   .. code-block:: console

      $ kllm-batch retry RUN_DIR

#. Review the child run's ``REVIEW.md``, payloads, and cost estimate, then run
   it through its recorded execution mode. If the parent had attached files,
   first run ``attach-files`` on the child as described in
   :doc:`file_attachments`; ``submit`` refuses it until then.

   .. code-block:: console

      $ kllm-batch submit CHILD_RUN_DIR
      $ kllm-batch sync CHILD_RUN_DIR --watch  # batch children only

#. Combine successful parent and child results, preferring the newest valid
   attempt for each record:

   .. code-block:: console

      $ kllm-batch merge CHILD_RUN_DIR

``retry`` and ``merge`` are local and free; ``retry`` never submits
automatically. ``merge CHILD_RUN_DIR`` walks backward through that child's full
parent chain, concatenates each run's normalized results from oldest to newest,
and keeps the newest valid result when the same ``record_id`` appears more than
once. It then sorts the combined rows by ``record_id``.

The new files are written only inside the supplied child run as
``CHILD_RUN_DIR/outputs/merged.parquet`` and
``CHILD_RUN_DIR/outputs/merged.csv``. The command does not modify or copy files
into a parent run, and it does not replace any run's existing
``results.parquet`` or ``results.csv``. Raw responses and source files are also
left unchanged. Missing rows remain missing rather than being silently filled.

If a child run also has retryable failures, run ``retry`` on that child and
later run ``merge`` on the newest descendant. The attempt chain preserves the
history while the merge walks back through every parent.

Cancel and recover a run
-------------------------

Cancel and continue safely
~~~~~~~~~~~~~~~~~~~~~~~~~~

Cancellation stops outstanding provider work but does not discard requests
that already finished. OpenAI can expose partial output after a batch reaches
``cancelled``; Anthropic can finish non-interruptible requests already underway
and reports an outcome for each request when the batch ends.

After requesting cancellation, use ``sync --watch`` to wait for the provider's
terminal state and process every result that is available. Then retry only the
canceled, failed, or missing records and merge them with the original
successes:

.. code-block:: console

   $ kllm-batch cancel RUN_DIR
   $ kllm-batch sync RUN_DIR --watch
   $ kllm-batch retry RUN_DIR
   $ kllm-batch submit CHILD_RUN_DIR
   $ kllm-batch sync CHILD_RUN_DIR --watch  # batch children only
   $ kllm-batch merge CHILD_RUN_DIR

Do not run ``submit`` again on the canceled parent: the remote batch itself is
not resumable. ``retry`` recognizes the cancellation categories returned by
both providers and prepares a new linked run for the unfinished records. A
manual ``rerun_ids.txt`` is therefore unnecessary for normal cancellation
recovery; use ``--ids-file`` only when deliberately choosing a different
subset.

Other recovery commands
~~~~~~~~~~~~~~~~~~~~~~~

* ``status RUN_DIR`` refreshes a batch run's remote status once.
* ``cancel RUN_DIR`` requests cancellation without deleting local artifacts;
  completed provider work may still be billable.
* ``audit RUN_DIR`` recomputes local completeness and identifier checks. Normal
  ``submit``/``sync`` processing already runs this audit.
* After an interrupted batch operation, rerun ``status`` and then ``sync``.
  Saved remote IDs prevent duplicate submission of recorded segments.
* Synchronous execution checkpoints each completed request. Rerunning
  ``submit`` skips complete checkpoints, although an interruption after a
  provider response but before its local save can cause that one request to be
  executed again.
* A partial or invalid JSONL checkpoint blocks automatic resume for manual
  review. A completed state with a missing expected response produces an
  integrity warning and makes no API calls.
* If ``state.json`` is damaged, its reader falls back to
  ``state.previous.json``. Preserve both when seeking support.
* Remove a run's ``.run.lock`` only after verifying that no other local command
  is operating on that run.
* If ``prepare`` fails or is interrupted, it removes its own incomplete
  ``.RUN_ID.building`` directory automatically; nothing inside was ever
  submitted to a provider. If one is still found (for example, left over from
  an abrupt kill), the next ``prepare`` prints its path as a warning — it is
  safe to delete.

Intentionally rerun or select specific records
------------------------------------------------

``prepare`` supports three record-selection strategies:

* no selector prepares every validated row;
* ``--sample-size N --seed S`` chooses a deterministic random pilot; and
* ``--ids-file PATH`` chooses source record IDs from a UTF-8 file containing
  one ID per line and no header.

A selected run defaults to ``sync`` execution; a full run defaults to
``batch``. Override either decision with ``--execution sync`` or
``--execution batch``. Both modes use the same run layout, validation,
processing, audit, and provenance code.

To intentionally recode successful records, put their source IDs in a text
file. For example, ``rerun_ids.txt`` could contain:

.. code-block:: text

   GRANT-002
   GRANT-007
   GRANT-014

Use the values from the source column configured as ``input.id_column``. The
file must be UTF-8 text with one ID per line, no header, and no commas. Blank
lines are ignored; duplicate IDs and IDs that do not exist in the configured
source are rejected.

Prepare a separate run from that file:

.. code-block:: console

   $ kllm-batch prepare -c my-project/project.yaml --provider openai \
       --ids-file rerun_ids.txt

Keeping intentional reruns separate preserves the original results and makes
the downstream choice explicit. Use ``compare`` when you want to examine the
differences; reserve ``retry`` for recorded failures.

Compare providers or repeated runs
------------------------------------

``compare`` is useful for assessing robustness across providers, models,
prompt versions, or repeated runs. For the clearest interpretation, use the
same selected record IDs and the same output schema in both processed runs.

For example, prepare the same deterministic pilot for both providers:

.. code-block:: console

   $ kllm-batch prepare -c my-project/project.yaml --provider openai \
       --sample-size 100 --seed 42
   $ kllm-batch prepare -c my-project/project.yaml --provider anthropic \
       --sample-size 100 --seed 42

Review, submit, and process both runs. Then compare them:

.. code-block:: console

   $ kllm-batch compare OPENAI_RUN_DIR ANTHROPIC_RUN_DIR

Runs are joined by ``record_id``, never row order. For fields defined with
``enum`` in the first run's schema, the comparison reports the number compared,
percent agreement, and Cohen's kappa. It also counts records found in only one
run.

The command creates a sibling directory named
``comparison_RUN_A_vs_RUN_B`` containing:

* ``comparison.json`` with agreement metrics and record counts; and
* ``disagreements.csv`` with missing records and categorical disagreements for
  human review.

Comparison is local and free. It does not use another model to adjudicate
differences; the exported disagreements are intended for researcher review.

Configure a project in depth
----------------------------

See :doc:`project_settings` for what each ``project.yaml`` field means. This
section covers behavior that only matters for larger or less typical runs.

Input mappings
~~~~~~~~~~~~~~

Duplicate normalized IDs and exact duplicate content across all
``fields_sent`` values are blocking validation errors. Correct the input
upstream or send a genuinely distinguishing field; validation never chooses a
row to keep.

Prompts and context
~~~~~~~~~~~~~~~~~~~

The user prompt must include ``${records_json}``; validation rejects a
project whose user prompt omits it. Named context files configured under
``prompt.context`` become additional placeholders such as
``${codebook_json}`` and may be CSV, JSON, YAML, or plain text.

Schema portability
~~~~~~~~~~~~~~~~~~

``schema.json`` describes one result row. For portability across supported
providers, every object should set ``additionalProperties: false``, list every
property as required, and express optional values with a nullable type such as
``["string", "null"]``.

The Anthropic adapter converts nullable type arrays into equivalent ``anyOf``
branches and moves unsupported numeric or length bounds into provider schema
descriptions. The original project schema remains unchanged and is enforced
during local response validation.

For structured extraction, use nullable fields when a fact may be absent and
instruct the model to return ``null`` rather than infer unsupported details.
PDF parsing and OCR remain upstream steps.

Limits, models, and cost
~~~~~~~~~~~~~~~~~~~~~~~~

Beyond ``rows_per_request``, ``max_input_tokens``, and ``max_output_tokens``,
per-field ``field_limits`` truncate a named field; truncation is never
implicit. Each provider entry may also set provider options, segment limits,
and explicit sync or batch input/output prices — unknown pricing blocks
preparation until dated price overrides are configured, regardless of
``budget.max_estimated_usd``.

Provider options are copied into the exact prepared requests and recorded in
the run manifest. See :doc:`project_settings` for supported common controls,
reserved request keys, model-compatibility cautions, and the distinction
between model generation settings and the pilot row-selection seed.

Stage batch segments
~~~~~~~~~~~~~~~~~~~~

Preparation numbers segments from zero in the same order shown by ``status``
and used in filenames such as ``segment_0000.jsonl``. To submit a contiguous
half-open range of remote batch jobs, provide both range options:

.. code-block:: console

   $ kllm-batch submit RUN_DIR --seg_start 0 --seg_end 2

The range ``[0, 2)`` includes segments 0 and 1 because ``--seg_end`` is
exclusive. Use ``--seg_end -1`` to continue from ``--seg_start`` through the
final segment. The two options must be provided together. The confirmation
reports the requested range, resolved segment indexes, and request count. It
retains the full prepared-run cost estimate as a conservative ceiling rather
than presenting an inaccurate prorated estimate.

Segments outside the range remain ``prepared``. A later command can submit
another range, while omitting both options submits every remaining unsubmitted
segment. Segments with remote batch IDs, including completed segments, are
skipped and are not rerun. Repeating a submission for an already submitted
range is therefore an idempotent no-op.

``status`` and ``sync`` continue to operate across the run, contacting only
segments with remote jobs. This permits a staged workflow: submit a subset,
run ``sync --watch``, inspect its provisional outputs, and then submit the
remaining segments. Outputs are rebuilt cumulatively, while the final audit
and run summary wait until every segment is resolved.

Target cancellation the same way:

.. code-block:: console

   $ kllm-batch cancel RUN_DIR --seg_start 2 --seg_end -1

Omitting both range options requests cancellation for every submitted or
running segment. Completed and prepared segments are skipped. Prepared segments
outside the range remain available for later submission. An asynchronous
provider response is recorded as ``cancelling`` until a later status check
reaches a terminal state. The status table's ``Cancellation`` column preserves
the final provider request counts even after available results are processed;
it therefore distinguishes fully or partially cancelled work from a batch that
completed before cancellation took effect. Provider work completed before
cancellation may still be billable. Segment ranges are limited to batch
execution; synchronous pilots resume from their per-request checkpoints
instead of creating remote segment jobs.

Understand artifacts and provenance
-----------------------------------

Immediately after preparation, a run contains ``REVIEW.md``, ``manifest.json``,
``state.json``, ``api_requests/``, ``input_snapshot/``, and
``project_snapshot/``. Provider retrieval adds ``raw_responses/``; processing
adds ``outputs/`` and ``run_reports/``.

The manifest records source, prompt, schema, model, package, Python, Git,
selection, execution, hash, and cost information. Essential row provenance is
included in result and failure rows. Raw responses, earlier attempts, and
source inputs are never patched by processing commands.

Protect research data
---------------------

Only ``record_id`` and ``fields_sent`` enter provider prompts. Preserved
columns remain local, but inputs, snapshots, requests, and responses can still
contain sensitive research material and belong only in approved storage.
Credentials are read from environment variables and are not recorded by the
package.

PDF/document ingestion, OCR, de-identification, source-table combination,
fuzzy duplicate detection, and automatic adjudication are upstream or out of
scope for the current release.
