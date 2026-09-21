Kellogg LLM Batch
=================

Reliable, reviewable LLM batch pipelines for research coding and structured
extraction.

``kllm-batch`` turns tabular research data into provider-ready requests for
OpenAI or Anthropic, preserves the material needed to reproduce each run, and
normalizes the responses for analysis. Preparation is deliberately separate
from execution so that researchers can inspect prompts, selected records, and
the estimated maximum cost before making a provider call.

Start here
----------

New users should follow these two pages in order:

#. :doc:`installation` — install the command and configure provider credentials.
#. :doc:`basic` — create a project, run a pilot, and process a complete batch.

The :doc:`advanced` guide covers configuration, retries, comparisons, recovery,
and provenance. Use the :doc:`cli` page as a compact command index.

.. important::

   ``init``, ``validate``, ``prepare``, ``audit``, ``retry``, ``merge``, and
   ``compare`` are local commands. ``submit`` contacts a provider and may incur
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

   installation
   basic
   advanced
   cli
