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

* ``version: 1`` — identifies the configuration format.
* ``project.name`` / ``project.description`` — a recognizable name and
  description for your study. These are descriptive only; they do not choose
  a provider or submit requests.

Input data
----------

.. code-block:: yaml

   input:
     path: data/input-data.csv
     format: auto
     csv_encoding: utf-8
     id_column: grant_id
     fields_sent:
       project_title: project_title
       abstract: abstract
     columns_preserved: [year, investigator, source_file]
     required_fields: [abstract]

* ``path`` — the source table.
* ``format`` — the file type to read: CSV, Parquet, or JSON Lines. ``auto``
  (the default) guesses from the filename extension, so the starter's
  ``.csv`` file is read as CSV. Set it explicitly if your file's extension
  doesn't match its actual format.
* ``csv_encoding`` — the text encoding for CSV files, ``utf-8`` by default.
  Change it if your file was saved with a different encoding (for example,
  a CSV exported from Excel on Windows is often ``cp1252``/``latin-1``) —
  reading it with the wrong encoding produces an error or garbled text. Set
  ``input.csv_encoding`` to any Python codec name, for example:

  .. code-block:: yaml

     input:
       csv_encoding: cp1252

  If you aren't sure which encoding your file uses, check how it was
  exported (e.g. Excel's "CSV UTF-8" vs. plain "CSV" save option) or open it
  in a text editor that reports encoding.
* ``id_column`` — the source column used for stable record IDs. The package
  also supplies each record's ``record_id`` from this column.
* ``fields_sent`` — maps a model-facing name (left) to a source column
  (right). Here, only title and abstract are sent.
* ``columns_preserved`` — kept for local results (year, investigator, source
  file) without entering the model prompt.
* ``required_fields`` — uses the model-facing names from ``fields_sent``; a
  missing abstract is an error.

See :doc:`advanced` for per-field length limits.

Task and response schema
------------------------

.. code-block:: yaml

   task:
     rows_per_request: 3
     output_schema: schema.json
     max_input_tokens: 50000
     max_output_tokens: 1200

* ``rows_per_request`` — groups up to three input records in each model
  request.
* ``output_schema`` — the separate JSON file defining one result row's
  required fields and allowed labels.
* ``max_input_tokens`` / ``max_output_tokens`` — limit request size and
  response length; adjust for your task and chosen model.

See :doc:`advanced` for limits and provider-specific schema behavior.

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

* ``prompt.version`` — a version label recorded with the run. Keep it quoted
  (``"1.0"``) so YAML reads it as a string, and update it when you
  intentionally revise the prompt.
* ``system_file`` / ``user_file`` — the model instructions. The user prompt
  must insert ``${records_json}`` for each request.
* ``context.codebook`` — a named context file, inserted as
  ``${codebook_json}``. ``format: auto`` works by filename extension; ``csv``
  states it explicitly, as the starter does. Keep codebook labels aligned
  with the enums in ``schema.json``.

Providers and budget
--------------------

.. code-block:: yaml

   providers:
     openai:
       model: gpt-5.4-mini
       options:
         reasoning:
           effort: low
     anthropic:
       model: claude-haiku-4-5
       options:
         temperature: 0

   budget:
     max_estimated_usd: 5.0

* ``providers`` — the models available to this project. ``prepare --provider
  openai`` or ``prepare --provider anthropic`` chooses one for a run; listing
  providers here does not contact them. Credentials come from environment
  variables, not this file.
* ``options`` — provider-native generation settings copied into every request
  for that provider. The starter uses low reasoning effort for the OpenAI
  reasoning model and temperature zero for Claude Haiku 4.5. Omitting
  ``options`` uses the provider and model defaults.
* ``budget.max_estimated_usd`` — blocks a run whose estimated maximum cost
  exceeds this ceiling.

The estimate it checks against is a worst-case ceiling, not an expected
cost: input size is approximated from character count (not an exact
tokenizer count), and every request is assumed to use the full
``task.max_output_tokens``, since the model's actual output length is not
known in advance. Real spend is usually well below this ceiling. For a realistic forecast
before preparing a full run, prepare and submit a small pilot
(``--sample-size`` or ``--ids-file``), then run
``kllm-batch estimate-cost PILOT_RUN_DIR`` — it scales the pilot's recorded
provider-reported aggregate token usage by row count, defaulting to the
project's full source row count. A default synchronous pilot is repriced at
asynchronous batch rates; a pilot prepared with ``--execution batch`` retains
its batch rates. Pass ``--target-rows N`` to project to a different count.
The result applies configured token rates and is not an exact provider bill;
cached-token adjustments and other provider-specific billing details are not
separately retained. For runs with attached files, aggregate input usage may
include file processing, but text and attachment usage cannot be separated.

Common OpenAI options include ``temperature`` (0–2), ``top_p`` (0–1), and
``reasoning.effort``. Common Anthropic options include ``temperature`` (0–1),
``top_p`` (0–1), ``top_k`` (a nonnegative integer), and ``stop_sequences`` (an
array of strings). Unknown provider-native keys pass through so that new API
features do not require an immediate package release. ``validate`` checks
known value ranges and warns when both ``temperature`` and ``top_p`` are set;
provider guidance recommends changing one sampling control at a time.

Option support also depends on the selected model. Some OpenAI reasoning
configurations restrict sampling controls, and Claude 4.7 and later models do
not support ``temperature``, ``top_p``, or ``top_k``. Remove incompatible
options when changing models. A temperature of zero can improve sampling
consistency on supported Claude models, but it does not make responses fully
deterministic.

``seed`` is not a supported option for the OpenAI Responses or Anthropic
Messages endpoints used here. ``evaluation.random_seed`` below, and the
``prepare --seed`` override, control only which input rows are selected for a
pilot. They do not control model generation.

The package manages the model, prompts, token limit, and structured-output
configuration, so their provider request keys cannot be supplied under
``options``. After preparation, inspect the exact settings in
``RUN_DIR/api_requests/segment_*.jsonl`` and the normalized copy in
``RUN_DIR/manifest.json`` under ``provider_options`` before submission.

See :doc:`installation` for credentials and :doc:`advanced` for provider
options, pricing overrides, and batch limits.

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

* ``random_seed`` — used for deterministic input-row sampling when ``prepare
  --sample-size`` is given without ``--seed``. It does not seed model
  generation.
* ``gold_columns`` — maps a predicted schema field (``primary_label``) to a
  local source column (``reference_primary_label``) that stays out of
  ``fields_sent``/``columns_preserved`` and so never enters provider prompts
  or normalized results. Preparation saves the selected reference labels to
  ``RUN_DIR/input_snapshot/gold_labels.parquet`` (recorded as
  ``gold_labels_file`` in the manifest); after processing,
  ``RUN_DIR/run_reports/run_summary.json`` reports exact-match accuracy under
  ``evaluation_metrics.primary_label``. Validation rejects a mapping to a
  missing column. (``RUN_DIR`` is the run directory that ``prepare`` prints —
  see :doc:`basic`.)
* ``write_parquet`` / ``write_csv`` — whether to write results in each
  format; the starter writes both.
* ``runs_directory`` — where each prepared run gets its own subdirectory
  (``runs`` in the starter), separate from the original input data.

The starter labels are illustrative, not externally validated research ground
truth. When replacing the input, provide your own reviewed reference column
or remove ``gold_columns``.

Once these settings look right, return to the :doc:`basic` workflow to
validate, prepare, and inspect a pilot. For retries, comparisons, and recovery,
continue to :doc:`advanced`.
