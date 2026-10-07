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

* ``data/input-data.csv`` — ten rows with a stable ``grant_id``, title,
  abstract, year, investigator, source file, and an illustrative reference
  label.
* ``project.yaml`` — sends title and abstract to the model, keeps year,
  investigator, and source file local, and uses the reference label only for
  local evaluation.
* ``context/codebook.csv`` — defines the ``financial``, ``organizational``,
  ``technical``, and ``other`` labels, inserted into the prompt as
  ``${codebook_json}`` alongside the records in ``${records_json}``.
* ``schema.json`` — requires ``primary_label`` (one of the four codebook
  values), an optional ``secondary_label`` (one of them, or ``null``), a
  confidence score, and a justification. The package adds ``record_id`` and
  the outer ``results`` array.

You can validate and prepare a pilot with these synthetic files as-is. For
your own research:

* replace the input rows and codebook;
* update ``project.yaml``, both prompts, and ``schema.json`` together, keeping
  both schema label fields aligned with the codebook; and
* replace the illustrative reference labels with your own reviewed labels, or
  remove the gold-column mapping.

See :doc:`project_settings` for a field-by-field guide to the YAML file, and
:doc:`grant_coding` for this same project carried through to results.

.. _optional-gui:

Optional: use the project builder
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The standard installation includes the project builder. To edit the
initialized project in a local browser, run:

.. code-block:: console

   $ kllm-batch gui my-project

The builder edits the same files as the CLI. It can:

* map input columns;
* draft the response schema and prompts;
* preview a complete request;
* save explicitly; and
* run local validation.

Use ``--no-browser`` to skip auto-opening a browser, or ``--port PORT`` to
choose another local port.

2. Validate locally
-------------------

.. code-block:: console

   $ kllm-batch validate -c my-project/project.yaml

``validate`` is local and free. It checks:

* configuration and missing required values;
* normalized record IDs and duplicate model inputs;
* prompts and schema; and
* request sizes and estimated costs.

Fix every ``ERROR`` before continuing — failed validation creates no run or
provider payload.

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
