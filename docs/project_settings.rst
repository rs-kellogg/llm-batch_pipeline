.. _project-settings:

Project settings
================

``project.yaml`` tells ``kllm-batch`` where to find your data, what to send to
the model, and how to prepare a run. Paths in this file are relative to the
directory containing ``project.yaml``. The examples below come from
``kllm-batch init my-project``; edit them for your own research, then run
``kllm-batch validate -c my-project/project.yaml`` before preparing a run.
You can edit the file directly or use the local :ref:`project builder
<optional-gui>`.

Project identity
----------------

.. code-block:: yaml

   version: 1
   project:
     name: grant-topic-coding
     description: Classify synthetic grant abstracts using a small codebook.

``version: 1`` identifies the configuration format. Under ``project``, give
your study a recognizable ``name`` and ``description``. These describe the
project; they do not choose a provider or submit requests.

Input data
----------

.. code-block:: yaml

   input:
     path: data/input-data.csv
     format: auto
     id_column: grant_id
     fields_sent:
       project_title: project_title
       abstract: abstract
     columns_preserved: [year, investigator, source_file]
     required_fields: [abstract]

``path`` points to the source table. ``format: auto`` selects the reader from
its filename extension, not its contents: the starter's ``.csv`` file is read
as CSV. Input also supports Parquet and JSON Lines. Set ``format`` explicitly
if the extension does not indicate the intended format. ``csv_encoding`` is
``utf-8`` in the starter.

``id_column`` names the source column used for stable record IDs. In
``fields_sent``, the name on the left is visible to the model; the name on the
right is a source column. Here, only title and abstract are sent. The package
also supplies each record's ``record_id`` from ``grant_id``. By contrast,
``columns_preserved`` keeps year, investigator, and source file for local
results without putting them in the model prompt. ``required_fields`` uses
the model-facing names on the left of ``fields_sent``; a missing abstract is
an error. For per-field length limits, see :doc:`advanced`.

Task and response schema
------------------------

.. code-block:: yaml

   task:
     rows_per_request: 3
     output_schema: schema.json
     max_input_tokens: 50000
     max_output_tokens: 1200

``rows_per_request`` groups up to three input records in each model request.
``output_schema`` points to the separate JSON file that defines one result
row's required fields and allowed labels. The token settings limit request
size and response length; adjust them for your task and chosen model. See
:doc:`advanced` for limits and provider-specific schema behavior.

Prompts and codebook
--------------------

.. code-block:: yaml

   prompt:
     version: "1.0"
     system_file: prompts/system.txt
     user_file: prompts/user.txt
     context:
       codebook:
         path: context/codebook.csv
         format: csv

The two prompt files contain the model instructions. The user prompt inserts
``${records_json}`` for each request and ``${codebook_json}`` for this named
context file. Context ``format: auto`` also works by filename extension;
``csv`` states the starter codebook's format explicitly. Keep codebook labels
aligned with the enums in ``schema.json``.

``prompt.version`` is a version label recorded with the run. Keep ``"1.0"``
quoted so YAML reads it as a string rather than a number; update the label
when you intentionally revise the prompt.

Providers and budget
--------------------

.. code-block:: yaml

   providers:
     openai:
       model: gpt-5-mini
     anthropic:
       model: claude-haiku-4-5

   budget:
     max_estimated_usd: 5.0

Provider entries specify the models available to this project. ``prepare
--provider openai`` or ``prepare --provider anthropic`` chooses one for a run;
listing providers here does not contact them. Credentials come from
environment variables, not this file. ``max_estimated_usd`` blocks a run
whose estimated maximum cost exceeds the configured ceiling. See
:doc:`installation` for credentials and :doc:`advanced` for provider options,
pricing overrides, and batch limits.

Evaluation and output
---------------------

.. code-block:: yaml

   evaluation:
     random_seed: 42
     gold_columns:
       primary_label: reference_primary_label

   output:
     write_parquet: true
     write_csv: true
     runs_directory: runs

``random_seed`` is used for deterministic sampling when ``prepare
--sample-size`` is given without ``--seed``. ``gold_columns`` maps the predicted
schema field ``primary_label`` to the local source column
``reference_primary_label``. That column is not in ``fields_sent`` or
``columns_preserved``, so it does not enter provider prompts or the normalized
results. Preparation saves the selected reference labels as
``RUN_ID/input_snapshot/gold_labels.parquet`` and records that relative path
in the manifest's ``gold_labels_file`` field. After responses are processed,
``RUN_ID/run_reports/run_summary.json`` reports exact-match accuracy under
``evaluation_metrics.primary_label``.

The starter labels are illustrative, not externally validated research ground
truth. When replacing the input, provide your own reviewed reference column or
remove ``gold_columns``; validation rejects a mapping to a missing column.
The starter writes both Parquet and CSV results under ``runs/``. Each prepared
run gets its own directory there; the original input data remains separate.

Once these settings look right, return to the :doc:`basic` workflow to
validate, prepare, and inspect a pilot. For retries, comparisons, and recovery,
continue to :doc:`advanced`.
