.. _file-attachments:

Attach one PNG or PDF per prepared request
==========================================

File attachments are an optional step for projects that already use the normal
text-based CSV workflow. ``init``, ``gui``, ``validate``, and ``prepare`` behave
as before. The attachment command makes no provider calls; ``submit`` is the
first paid step.

Arrange files in a directory such as ``data/attachments/`` and add a filename
column to the input CSV. Keep at least one ordinary text field mapped in
``input.fields_sent``. Do **not** map ``attachment_file`` in
``input.fields_sent``: it identifies a local file and is not part of the text
prompt.

.. code-block:: text

   my-project/
   ├── project.yaml
   └── data/
       ├── input-data.csv
       └── attachments/
           ├── grant-001.pdf
           └── grant-002.png

For example, the CSV could contain:

.. code-block:: text

   grant_id,project_title,abstract,attachment_file
   G001,Community research,Study summary,grant-001.pdf
   G002,Instrument design,Study summary,grant-002.png

Set ``task.rows_per_request: 1`` in ``project.yaml`` so each request has one
record and one file. Then validate and prepare normally. To test the actual
file-input workflow, attach files to a selected pilot *before* submitting it:

.. code-block:: console

   $ kllm-batch validate -c my-project/project.yaml
   $ kllm-batch prepare -c my-project/project.yaml --provider openai --sample-size 2
   $ kllm-batch attach-files PILOT_RUN_ID --column attachment_file \
       --files-dir data/attachments --acknowledge-unestimated-cost
   $ kllm-batch submit PILOT_RUN_ID

For the full batch, run ``prepare`` without ``--sample-size``, then
``attach-files`` on that new run before ``submit``. The same command works with
Anthropic. Inspect ``REVIEW.md`` and ``api_requests/attached_segment_*.jsonl``
before submission. The original text-only request files remain intact; the
attached files are the ones executed.

.. warning::

   The cost estimate produced by ``prepare`` covers text, not image or PDF
   processing. ``attach-files`` therefore requires an explicit acknowledgment;
   the total cost is unknown until the provider returns usage. The configured
   estimated-cost budget does not cap the attached run's actual cost. Start
   with a small pilot and review provider pricing and file limits.

The command embeds file bytes in the prepared request artifacts. Treat the run
directory as sensitive if the source files are sensitive. The manifest records
the relative filenames and SHA-256 checksums, and submission rejects modified
prepared requests. Filenames must resolve inside ``--files-dir``; missing,
unsupported, or oversized files are rejected locally. Only PNG and PDF are
supported in this first version.

If an attached run has failures, ``kllm-batch retry RUN_ID`` prepares a child
run but does not attach files automatically. Run ``attach-files`` on the child
with the same column and directory before submitting it. Submission refuses a
retry child until the required files are attached, and the files must match
their parent-run checksums. See :ref:`retries-and-reruns` for merging results.
