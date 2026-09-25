.. _advanced-workflows:

Advanced workflows
==================

Most researchers come here for one of two reasons: a completed run has failed
records to retry, or two completed runs need to be compared. Start with the
relevant workflow below; configuration, recovery, and provenance details
follow afterward.

.. contents:: On this page
   :local:
   :depth: 2

.. _retries-and-reruns:

Retry failed records and merge the results
------------------------------------------

Use a retry chain when ``outputs/failures.jsonl`` contains retryable provider,
request, malformed-output, schema, or missing-output failures. The original
run and its raw responses remain unchanged.

#. Inspect ``outputs/failures.jsonl`` and the run's audit report.
#. Prepare a child run containing only retryable failed records:

   .. code-block:: console

      $ kllm-batch retry RUN_ID

#. Review the child run's ``REVIEW.md``, payloads, and cost estimate, then run
   it through its recorded execution mode:

   .. code-block:: console

      $ kllm-batch submit CHILD_RUN_ID
      $ kllm-batch sync CHILD_RUN_ID --watch  # batch children only

#. Combine successful parent and child results, preferring the newest valid
   attempt for each record:

   .. code-block:: console

      $ kllm-batch merge CHILD_RUN_ID

``retry`` and ``merge`` are local and free; ``retry`` never submits
automatically. ``merge CHILD_RUN_ID`` walks backward through that child's full
parent chain, concatenates each run's normalized results from oldest to newest,
and keeps the newest valid result when the same ``record_id`` appears more than
once. It then sorts the combined rows by ``record_id``.

The new files are written only inside the supplied child run as
``CHILD_RUN_ID/outputs/merged.parquet`` and
``CHILD_RUN_ID/outputs/merged.csv``. The command does not modify or copy files
into a parent run, and it does not replace any run's existing
``results.parquet`` or ``results.csv``. Raw responses and source files are also
left unchanged. Missing rows remain missing rather than being silently filled.

If a child run also has retryable failures, run ``retry`` on that child and
later run ``merge`` on the newest descendant. The attempt chain preserves the
history while the merge walks back through every parent.

Compare providers or repeated runs
----------------------------------

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

   $ kllm-batch compare OPENAI_RUN_ID ANTHROPIC_RUN_ID

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

Select or intentionally rerun records
-------------------------------------

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

Configure a project in depth
----------------------------

Input mappings
~~~~~~~~~~~~~~

One input row represents one research unit. Configure a stable ``id_column``
when units must be compared across versions. Without one, IDs are derived from
the source-file checksum and zero-based row number.

``fields_sent`` maps names visible to the model to source columns.
``columns_preserved`` is carried into normalized results locally and is not
placed in prompts. For example:

.. code-block:: yaml

   input:
     path: data/input-data.csv
     id_column: grant_id
     fields_sent:
       project_title: project_title
       abstract: abstract
     columns_preserved: [year, investigator, source_file]
     required_fields: [abstract]

Duplicate normalized IDs and exact duplicate content across all
``fields_sent`` values are blocking errors. Correct the input upstream or send
a genuinely distinguishing field; validation never chooses a row to keep.

Prompts and context
~~~~~~~~~~~~~~~~~~~

The user prompt must include ``${records_json}``. Named context files configured
under ``prompt.context`` become placeholders such as ``${codebook_json}``.
Context may be CSV, JSON, YAML, or text. ``rows_per_request`` controls how many
records are serialized into each rendered request.

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

Limits, models, and cost
~~~~~~~~~~~~~~~~~~~~~~~~

Use ``rows_per_request``, ``max_input_tokens``, ``max_output_tokens``, and
per-field ``field_limits`` to keep payloads within the chosen model's limits.
Truncation is never implicit; it must be configured for a named field.

Each provider entry selects a model and may set provider options, segment
limits, and explicit sync or batch input/output prices. Unknown pricing blocks
preparation until dated price overrides are configured. The
``budget.max_estimated_usd`` ceiling blocks runs whose conservative estimate is
too high.

Monitor and recover a run
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

   $ kllm-batch cancel RUN_ID
   $ kllm-batch sync RUN_ID --watch
   $ kllm-batch retry RUN_ID
   $ kllm-batch submit CHILD_RUN_ID
   $ kllm-batch sync CHILD_RUN_ID --watch  # batch children only
   $ kllm-batch merge CHILD_RUN_ID

Do not run ``submit`` again on the canceled parent: the remote batch itself is
not resumable. ``retry`` recognizes the cancellation categories returned by
both providers and prepares a new linked run for the unfinished records. A
manual ``rerun_ids.txt`` is therefore unnecessary for normal cancellation
recovery; use ``--ids-file`` only when deliberately choosing a different
subset.

Other recovery commands
~~~~~~~~~~~~~~~~~~~~~~~

* ``status RUN_ID`` refreshes a batch run's remote status once.
* ``cancel RUN_ID`` requests cancellation without deleting local artifacts;
  completed provider work may still be billable.
* ``audit RUN_ID`` recomputes local completeness and identifier checks. Normal
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

Further references
------------------

* `Migration from the original batch scripts <https://github.com/rs-kellogg/llm-batch_pipeline/blob/main/docs/MIGRATION.md>`_
* `Additional task recipes <https://github.com/rs-kellogg/llm-batch_pipeline/blob/main/docs/RECIPES.md>`_
* `Complete grant-coding example <https://github.com/rs-kellogg/llm-batch_pipeline/tree/main/examples/grant_coding>`_
