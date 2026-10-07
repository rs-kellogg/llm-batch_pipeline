Kellogg LLM Batch
=================

Reviewable LLM batch pipelines for research coding and structured
extraction.

``kllm-batch`` turns tabular research data into provider-ready requests for
OpenAI or Anthropic, preserves the material needed to reproduce each run, and
normalizes the responses for analysis. Preparation is deliberately separate
from execution so that researchers can inspect prompts, selected records, and
the estimated maximum cost before making a provider call.

Start here
----------

New users should follow these pages in order:

#. :doc:`installation` — install the command and configure provider credentials.
#. :doc:`basic` — create a project, run a pilot, and process a complete batch.
#. :doc:`project_settings` — understand and edit the generated configuration.
#. :doc:`grant_coding` — practice the workflow with ten synthetic grants.

The :doc:`advanced` guide covers configuration, retries, comparisons, recovery,
and provenance. Use the :doc:`cli` page as a compact command index.
For per-record PNG or PDF inputs, see :doc:`file_attachments`.

.. important::

   ``init``, ``validate``, ``prepare``, ``attach-files``, ``audit``, ``retry``,
   ``merge``, and ``compare`` are local commands. ``submit`` contacts a provider and may incur
   model charges. For batch runs, ``status``, ``sync``, and ``cancel`` also
   contact the provider.

What the package protects
-------------------------

* Source files are not overwritten.
* Prepared requests are immutable and inspectable before submission.
* Cost estimates and provider limits are checked during preparation.
* Raw responses and retry attempts remain separate.
* Results retain record-level provenance, while run-wide provenance is saved
  with the manifest.

.. toctree::
   :maxdepth: 2
   :caption: User guide
   :hidden:

   installation
   basic
   project_settings
   grant_coding
   advanced
   file_attachments
   cli
