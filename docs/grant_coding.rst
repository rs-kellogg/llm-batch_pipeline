.. _grant-coding-walkthrough:

Grant-coding walkthrough
========================

This walkthrough classifies ten entirely synthetic grant abstracts into four
topics. It demonstrates the complete pilot workflow without requiring you to
design a project first. The example contains no real research data and is safe
to inspect or modify.

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

   data/grants.csv ──┐
                     ├── prompts ──> provider request ──> validated results
   context/codebook.csv ────────────┘                └── schema.json

* ``data/grants.csv`` contains ten grants identified by ``grant_id``.
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

Only ``record_id``, ``project_title``, and ``abstract`` enter the model prompt.
The three preserved columns are joined back into results locally. With
``rows_per_request: 3``, the complete ten-row example produces four requests.

1. Validate the project
-----------------------

Run the local validation before creating a run:

.. code-block:: console

   $ kllm-batch validate -c examples/grant_coding/project.yaml

Validation checks the data, duplicate IDs and content, required abstracts,
prompt placeholders, output schema, request sizes, and estimated cost. This
step is local, requires no API key, and creates no run artifacts.

2. Prepare a four-grant pilot
-----------------------------

Prepare a deterministic sample with OpenAI:

.. code-block:: console

   $ kllm-batch prepare \
       -c examples/grant_coding/project.yaml \
       --provider openai \
       --sample-size 4 \
       --seed 42

Preparation is also local and free. The command prints a new directory under
``examples/grant_coding/runs/``. Use that complete path as ``RUN_ID`` in later
commands. Because this is a selected pilot, it defaults to synchronous
execution.

3. Inspect before submitting
----------------------------

Open ``RUN_ID/REVIEW.md`` first. Confirm:

* four of ten rows were selected with seed 42;
* the provider, model, and synchronous execution mode are correct;
* the request count and estimated maximum cost are reasonable; and
* the displayed next command points to this run.

Then inspect ``RUN_ID/api_requests/segment_*.jsonl``. These files contain the
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

   $ kllm-batch submit RUN_ID

The command shows the estimated maximum cost and asks for confirmation. A
synchronous pilot waits for its responses, normalizes and validates the rows,
and runs the completeness audit automatically. It does not require a separate
``status`` or ``sync`` command.

To use Anthropic instead, prepare a separate pilot with
``--provider anthropic``, set ``ANTHROPIC_API_KEY``, and submit that new run.

5. Review the results
---------------------

After a successful pilot, inspect:

.. list-table:: Pilot outputs
   :header-rows: 1
   :widths: 42 58

   * - Path
     - Contents
   * - ``RUN_ID/outputs/results.csv``
     - Human-readable predictions, preserved metadata, and row provenance.
   * - ``RUN_ID/outputs/results.parquet``
     - The same results in a type-stable format for analysis.
   * - ``RUN_ID/run_reports/audit.json``
     - Expected, valid, missing, unexpected, and duplicate record counts.
   * - ``RUN_ID/run_reports/usage.json``
     - Request-level and total token usage.
   * - ``RUN_ID/outputs/failures.jsonl``
     - Created only if a request or result failed validation.

Check that each selected grant has one valid result, the label and
justification are sensible, and the preserved metadata is present. If failures
occur, follow :ref:`retries-and-reruns` rather than editing raw responses.

6. Continue to the complete batch
---------------------------------

Once the pilot is satisfactory, omit the sample options to prepare all ten
grants. A complete run defaults to batch execution:

.. code-block:: console

   $ kllm-batch prepare \
       -c examples/grant_coding/project.yaml \
       --provider openai
   $ kllm-batch submit RUN_ID
   $ kllm-batch sync RUN_ID --watch

Use the new run directory printed by the second ``prepare`` command; do not
reuse the pilot's path. Review the complete run before submitting it, just as
you reviewed the pilot.

Where to go next
----------------

* Read :doc:`advanced` for retry/merge chains, provider comparisons,
  cancellation recovery, targeted reruns, and detailed configuration.
* See the `full example README <https://github.com/rs-kellogg/llm-batch_pipeline/tree/main/examples/grant_coding>`_
  for teaching fixtures and additional operational detail.
