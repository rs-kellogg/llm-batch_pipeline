.. _cli-reference:

CLI reference
=============

Run ``kllm-batch --help`` for the command list and
``kllm-batch COMMAND --help`` for the complete, version-matched options and
recovery guidance.

.. list-table:: Command summary
   :header-rows: 1
   :widths: 14 37 15 34

   * - Command
     - Synopsis
     - Operation
     - Purpose
   * - ``init``
     - ``kllm-batch init DIRECTORY``
     - Local
     - Create a starter project in an empty directory. See :ref:`basic-workflow`.
   * - ``gui``
     - ``kllm-batch gui DIRECTORY [--port PORT] [--no-browser]``
     - Local
     - Open the optional project builder for an initialized project.
   * - ``validate``
     - ``kllm-batch validate -c PROJECT_YAML [--report PATH]``
     - Local
     - Validate data, mappings, prompts, schema, request sizes, and cost.
   * - ``prepare``
     - ``kllm-batch prepare -c PROJECT_YAML --provider PROVIDER [OPTIONS]``
     - Local
     - Select records and create an immutable, costed run for inspection.
   * - ``attach-files``
     - ``kllm-batch attach-files RUN_DIR --column NAME --files-dir DIR --acknowledge-unestimated-cost``
     - Local
     - Add one PNG/PDF per request to an unsubmitted run. See :doc:`file_attachments`.
   * - ``submit``
     - ``kllm-batch submit RUN_DIR [--yes]``
     - Provider / paid
     - Execute the exact prepared requests using their recorded execution mode.
   * - ``status``
     - ``kllm-batch status RUN_DIR``
     - Provider
     - Refresh a batch run's remote state once.
   * - ``sync``
     - ``kllm-batch sync RUN_DIR [--watch] [--poll-seconds N]``
     - Provider
     - Retrieve batch responses, normalize results, and audit completeness.
   * - ``cancel``
     - ``kllm-batch cancel RUN_DIR``
     - Provider
     - Request cancellation without deleting local artifacts.
   * - ``audit``
     - ``kllm-batch audit RUN_DIR``
     - Local
     - Recompute completeness, failures, and result-identifier checks.
   * - ``retry``
     - ``kllm-batch retry RUN_DIR``
     - Local
     - Prepare a linked child run for retryable failed rows.
   * - ``merge``
     - ``kllm-batch merge CHILD_RUN_DIR``
     - Local
     - Merge valid attempt-chain results, preferring the newest attempt.
   * - ``compare``
     - ``kllm-batch compare RUN_A RUN_B``
     - Local
     - Compare processed runs by record ID and export disagreements.

Prepare options used most often
-------------------------------

``--sample-size N``
   Select a deterministic random sample instead of all rows.

``--seed S``
   Set the sample seed. Without it, the configured evaluation seed is used.

``--ids-file PATH``
   Select IDs from a UTF-8 file with one source record ID per line and no
   header. This cannot be combined with ``--sample-size``.

``--execution sync|batch``
   Override the default execution mode. Selected runs default to ``sync``;
   complete runs default to ``batch``.

Command boundaries
------------------

Local commands can read and write project/run artifacts but do not call a
model provider. Provider commands require the corresponding environment
credential. ``submit`` may incur charges; ``status``, ``sync``, and ``cancel``
operate on already submitted batch work and may also involve billable work
already performed by the provider. See :doc:`advanced` for failure and recovery
behavior.
