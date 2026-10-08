.. _grant-coding-walkthrough:

Grant-coding walkthrough
========================

This walkthrough classifies ten entirely synthetic grant abstracts into four
topics. It demonstrates the complete pilot workflow without requiring you to
design a project first. The example contains no real research data and is safe
to inspect or modify. ``kllm-batch init my-project`` creates a new project
with the same ten grants and core project files, so you can practice this
walkthrough before adapting your own project.

The commands below assume you are in the root of a repository checkout. If you
installed the command directly from GitHub, clone the repository to obtain the
example files:

.. code-block:: console

   $ git clone https://github.com/rs-kellogg/llm-batch_pipeline.git
   $ cd llm-batch_pipeline
   $ python -m pip install -e .

What the example does
---------------------

The project combines five pieces:

.. code-block:: text

   data/input-data.csv ──┐
                     ├── prompts ──> provider request ──> validated results
   context/codebook.csv ────────────┘                └── schema.json

* ``data/input-data.csv`` contains ten grants identified by ``grant_id`` and
  illustrative reference labels for local evaluation.
* ``project.yaml`` sends each grant's title and abstract to the model while
  preserving year, investigator, and source-file metadata locally.
* ``context/codebook.csv`` defines the ``financial``, ``organizational``,
  ``technical``, and ``other`` labels.
* ``prompts/`` contains the coding instructions and inserts both the codebook
  and grant records into each request.
* ``schema.json`` requires a primary label, optional secondary label,
  confidence score, and short justification for every grant.

The relevant input mapping in ``project.yaml`` is:

.. code-block:: yaml

   input:
     id_column: grant_id
     fields_sent:
       project_title: project_title
       abstract: abstract
     columns_preserved:
       - year
       - investigator
       - source_file

Only ``record_id``, ``project_title``, and ``abstract`` enter the model
prompt; the preserved columns are joined back into results locally. With
``rows_per_request: 3``, the complete ten-row example produces four requests.
See :doc:`project_settings` for what each field controls.

The source column ``reference_primary_label`` is deliberately neither sent
nor preserved in results. Instead it drives local evaluation:

.. code-block:: yaml

   evaluation:
     random_seed: 42
     gold_columns:
       primary_label: reference_primary_label

See :doc:`project_settings` for how ``gold_columns`` scoring works. The ten
reference labels here are teaching examples based on the codebook, not an
externally validated benchmark — replace them with your own reviewed labels
before using agreement as research evidence.

1. Validate the project
-----------------------

Run the local validation before creating a run:

.. code-block:: console

   $ kllm-batch validate -c examples/grant_coding/project.yaml

See :doc:`basic` for what validation checks. This step is local, requires no
API key, and creates no run artifacts.

2. Prepare a four-grant pilot
-----------------------------

Prepare a deterministic sample with OpenAI:

.. code-block:: console

   $ kllm-batch prepare -c examples/grant_coding/project.yaml --provider openai --sample-size 4 --seed 42

See :doc:`basic` for what ``prepare`` does. The command prints a new directory
under ``examples/grant_coding/runs/``; use that complete path as ``RUN_DIR`` in
later commands. Because this is a selected pilot, it defaults to synchronous
execution.

3. Inspect before submitting
----------------------------

Open ``RUN_DIR/REVIEW.md`` first. Confirm:

* four of ten rows were selected with seed 42;
* the provider, model, and synchronous execution mode are correct;
* the request count and estimated maximum cost are reasonable; and
* the displayed next command points to this run.

Then inspect ``RUN_DIR/api_requests/segment_*.jsonl``. These files contain the
exact saved payloads, including the rendered system instructions, codebook,
and grant records. If anything is wrong, edit the source project and prepare a
new run—do not edit generated payloads.

.. important::

   Everything through this inspection step is local and free. The next command
   contacts the selected provider and may incur model charges.

4. Submit the pilot
-------------------

Set the OpenAI credential if it is not already present:

.. code-block:: console

   $ export OPENAI_API_KEY="your-key"

Submit the exact requests you reviewed:

.. code-block:: console

   $ kllm-batch submit RUN_DIR

See :doc:`basic` for what ``submit`` does for a synchronous pilot; it does not
require a separate ``status`` or ``sync`` command here.

The equivalent Anthropic pilot and complete-batch workflow appears below.

5. Review the results
---------------------

After a successful pilot, inspect:

.. list-table:: Pilot outputs
   :header-rows: 1
   :widths: 42 58

   * - Path
     - Contents
   * - ``RUN_DIR/outputs/results.csv``
     - Human-readable predictions, preserved metadata, and row provenance.
   * - ``RUN_DIR/outputs/results.parquet``
     - The same results in a type-stable format for analysis.
   * - ``RUN_DIR/run_reports/audit.json``
     - Expected, valid, missing, unexpected, and duplicate record counts.
   * - ``RUN_DIR/run_reports/run_summary.json``
     - ``evaluation_metrics.primary_label`` gives exact-match accuracy against
       the selected reference labels.
   * - ``RUN_DIR/input_snapshot/gold_labels.parquet``
     - Local reference labels selected for this run; its relative path is
       recorded as ``gold_labels_file`` in ``manifest.json``.
   * - ``RUN_DIR/run_reports/usage.json``
     - Request-level and total token usage.
   * - ``RUN_DIR/outputs/failures.jsonl``
     - Created only if a request or result failed validation.

Check that each selected grant has one valid result, the label and
justification are sensible, and the preserved metadata is present. The
accuracy metric counts only valid results with matching reference IDs; inspect
the audit report for missing or invalid results before interpreting it. If
failures occur, follow :ref:`retries-and-reruns` rather than editing raw
responses.

6. Continue to the complete batch
---------------------------------

Once the pilot is satisfactory, omit the sample options to prepare all ten
grants. A complete run defaults to batch execution:

.. code-block:: console

   $ kllm-batch prepare -c examples/grant_coding/project.yaml --provider openai
   $ kllm-batch submit RUN_DIR
   $ kllm-batch sync RUN_DIR --watch

Use the new run directory printed by the second ``prepare`` command; do not
reuse the pilot's path. Review the complete run before submitting it, just as
you reviewed the pilot.

7. Run with Anthropic
---------------------

Set the Anthropic credential if it is not already present:

.. code-block:: console

   $ export ANTHROPIC_API_KEY="your-key"

Prepare and submit a separate deterministic pilot. The selected pilot runs
synchronously, so ``submit`` processes its results and audit directly:

.. code-block:: console

   $ kllm-batch prepare -c examples/grant_coding/project.yaml --provider anthropic --sample-size 4 --seed 42
   $ kllm-batch submit ANTHROPIC_PILOT_RUN_DIR

After reviewing the pilot, prepare and process the complete Anthropic batch:

.. code-block:: console

   $ kllm-batch prepare -c examples/grant_coding/project.yaml --provider anthropic
   $ kllm-batch submit ANTHROPIC_RUN_DIR
   $ kllm-batch sync ANTHROPIC_RUN_DIR --watch

Replace each placeholder with the complete run directory printed by its
``prepare`` command. As with OpenAI batch runs, ``kllm-batch status
ANTHROPIC_RUN_DIR`` and ``kllm-batch audit ANTHROPIC_RUN_DIR`` are optional
diagnostics. ``sync --watch`` already polls status, processes the responses,
and runs the audit.

8. Compare providers
--------------------

After both complete runs have been processed, compare their results locally:

.. code-block:: console

   $ kllm-batch compare OPENAI_RUN_DIR ANTHROPIC_RUN_DIR

The comparison reports agreement on categorical and string fields and exports
disagreements for human review. It does not use a model to adjudicate
differences. Use runs containing the same record IDs and output schema for the
clearest interpretation.

Where to go next
----------------

* Read :doc:`advanced` for retry/merge chains, provider comparisons,
  cancellation recovery, targeted reruns, and detailed configuration.
* See the `full example README <https://github.com/rs-kellogg/llm-batch_pipeline/tree/main/examples/grant_coding>`_
  for teaching fixtures and additional operational detail.
