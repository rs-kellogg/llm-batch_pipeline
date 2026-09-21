.. _advanced-workflows:

Advanced workflows
==================

Selection and execution modes
-----------------------------

``prepare`` supports three record-selection strategies:

* no selector prepares every validated row;
* ``--sample-size N --seed S`` chooses a deterministic random pilot; and
* ``--ids-file PATH`` chooses source record IDs from a UTF-8 file containing
  one ID per line and no header.

A selected run defaults to ``sync`` execution; a full run defaults to
``batch``. Override either decision explicitly with ``--execution sync`` or
``--execution batch``. Both modes use the same run layout, validation,
processing, audit, and provenance code.

Project configuration
---------------------

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
     path: data/input.csv
     id_column: grant_id
     fields_sent:
       project_title: title
       abstract: abstract_text
     columns_preserved: [year, investigator]
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

.. _retries-and-reruns:

Retries and intentional reruns
------------------------------

Provider or request failures are recorded rather than silently retried. After
processing a run, prepare a linked child containing only retryable failed rows:

.. code-block:: console

   $ kllm-batch retry RUN_ID
   $ kllm-batch submit CHILD_RUN_ID
   $ kllm-batch merge CHILD_RUN_ID

``retry`` prepares but never submits. Review the child run and its new cost
estimate first. ``merge`` creates a derived result that prefers the newest
valid attempt; it does not alter parents or raw responses.

To intentionally recode successful records, put their source IDs in a text
file and prepare a separate run:

.. code-block:: console

   $ kllm-batch prepare -c my-project/project.yaml --provider openai \
       --ids-file rerun_ids.txt

Keeping the runs separate makes the choice of which result to use explicit.

Compare providers or runs
-------------------------

Process both runs, then compare them by record ID:

.. code-block:: console

   $ kllm-batch compare OPENAI_RUN_ID ANTHROPIC_RUN_ID

The comparison reports agreement for categorical and string fields and exports
missing records and disagreements for human review. It does not use another
model to adjudicate results.

Operations and recovery
-----------------------

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

Run artifacts and provenance
----------------------------

Immediately after preparation, a run contains ``REVIEW.md``, ``manifest.json``,
``state.json``, ``api_requests/``, ``input_snapshot/``, and
``project_snapshot/``. Provider retrieval adds ``raw_responses/``; processing
adds ``outputs/`` and ``run_reports/``.

The manifest records source, prompt, schema, model, package, Python, Git,
selection, execution, hash, and cost information. Essential row provenance is
included in result and failure rows. Raw responses, earlier attempts, and
source inputs are never patched by processing commands.

Research-data boundary
----------------------

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
