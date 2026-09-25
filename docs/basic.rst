.. _basic-workflow:

Basic workflow
==============

This guide follows the recommended path: configure locally, validate, inspect
a small synchronous pilot, and only then prepare a complete batch.

1. Create a project
-------------------

``init`` creates a working grant-coding project with the same ten synthetic
grants, four-topic codebook, prompts, schema, and settings as the checked-in
:doc:`grant_coding` example. The destination must be empty.

.. code-block:: console

   $ kllm-batch init my-project

The important files are:

.. code-block:: text

   my-project/
   ├── data/input-data.csv
   ├── context/codebook.csv
   ├── prompts/system.txt
   ├── prompts/user.txt
   ├── project.yaml
   ├── schema.json
   └── runs/

The ten rows in ``data/input-data.csv`` contain a stable ``grant_id``, title,
abstract, year, investigator, and source file. ``project.yaml`` sends the
title and abstract to the model and preserves the other three columns locally.
``context/codebook.csv`` defines the ``financial``,
``organizational``, ``technical``, and ``other`` labels. The user prompt inserts
this CSV as ``${codebook_json}`` alongside the selected records in
``${records_json}``. The ``primary_label`` enum in ``schema.json`` allows the
same four values; ``secondary_label`` allows one of them or ``null`` when no
second theme is present. The schema also requires a confidence score and
justification. The package adds ``record_id`` and the outer ``results`` array
to the response schema.

You can validate and prepare a pilot with these synthetic files. For your own
research, replace the input rows and codebook, then update ``project.yaml``,
both prompts, and ``schema.json`` together. In particular, keep both schema
label fields aligned with the codebook. The :doc:`grant_coding` walkthrough
uses these same project files and carries the workflow through to results.

.. _optional-gui:

Optional: use the project builder
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The standard installation includes the project builder. To edit the
initialized project in a local browser, run:

.. code-block:: console

   $ kllm-batch gui my-project

The builder edits the same YAML, schema, prompt, input, and context files used
by the CLI. It can map input columns, draft the response schema and prompts,
preview a complete request, save explicitly, and run local validation. Use
``--no-browser`` to start the GUI without opening a browser automatically, or
``--port PORT`` to choose another local port.

2. Validate locally
-------------------

.. code-block:: console

   $ kllm-batch validate -c my-project/project.yaml

Validation is local and free. It checks configuration, missing required
values, normalized record IDs, duplicate model inputs, prompts, schema, request
sizes, and estimated costs. Fix every ``ERROR`` before continuing. Failed
validation creates no run or provider payload.

3. Prepare and inspect a pilot
------------------------------

Select a deterministic sample and prepare it for synchronous execution:

.. code-block:: console

   $ kllm-batch prepare -c my-project/project.yaml --provider openai \
       --sample-size 4 --seed 42

``prepare`` is local and free. It prints a run directory; use that complete
path as ``RUN_ID`` below. Start with ``RUN_ID/REVIEW.md``, then inspect:

* ``api_requests/segment_*.jsonl`` for the exact provider-native payloads and
  fully rendered prompts;
* ``manifest.json`` for selection, hashes, model, execution mode, and estimated
  maximum cost; and
* ``project_snapshot/schema.json`` for the response contract enforced locally.

If review reveals a problem, edit the source project and prepare a new run. Do
not patch generated request files.

4. Submit the pilot
-------------------

Set the provider credential as described in :doc:`installation`, then submit
the reviewed run:

.. code-block:: console

   $ kllm-batch submit RUN_ID

``submit`` displays the request count and estimated maximum cost before asking
for confirmation. A selected pilot defaults to synchronous execution, so the
command waits for responses, writes normalized outputs, and runs the
completeness audit automatically.

Inspect ``outputs/results.csv`` and ``run_reports/audit.json``. If failures are
present, do not edit raw responses; follow :ref:`retries-and-reruns`.

5. Prepare the complete batch
-----------------------------

After the pilot is satisfactory, omit selection options to prepare all rows.
Full runs default to the provider's batch API.

.. code-block:: console

   $ kllm-batch prepare -c my-project/project.yaml --provider openai

Review the new run's ``REVIEW.md``, request payloads, and cost estimate just as
carefully as the pilot, then submit it:

.. code-block:: console

   $ kllm-batch submit RUN_ID

6. Wait for and process the batch
---------------------------------

.. code-block:: console

   $ kllm-batch sync RUN_ID --watch

``sync --watch`` polls the provider, downloads completed responses, validates
and normalizes result rows, writes outputs, and runs the completeness audit.
It is safe to rerun after an interruption. ``status RUN_ID`` is available for
a one-time progress check, but is unnecessary while ``sync --watch`` is
running.

7. Use the results
------------------

A processed run normally contains:

* ``outputs/results.csv`` for quick inspection;
* ``outputs/results.parquet`` for type-stable analysis;
* ``outputs/failures.jsonl`` when failures occurred; and
* ``run_reports/audit.json``, ``run_summary.json``, and ``usage.json``.

The original data and immutable provider responses remain separate from these
derived outputs. Next, work through the checked-in :doc:`grant_coding` example,
or continue to :doc:`advanced` for retries, explicit execution modes,
comparisons, configuration details, and recovery guidance.
