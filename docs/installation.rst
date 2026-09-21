.. _installation:

Installation
============

Requirements
------------

Kellogg LLM Batch supports Python 3.10 through 3.12. A dedicated environment
keeps its dependencies separate from other research projects. For example,
with mamba:

.. code-block:: console

   $ mamba create -n kllm-batch python=3.12 -y
   $ mamba activate kllm-batch

Install from GitHub
-------------------

Install the current version directly from the public repository:

.. code-block:: console

   $ python -m pip install "kellogg-llm-batch @ git+https://github.com/rs-kellogg/llm-batch_pipeline.git"
   $ kllm-batch --help

The optional local project builder requires the ``gui`` extra:

.. code-block:: console

   $ python -m pip install "kellogg-llm-batch[gui] @ git+https://github.com/rs-kellogg/llm-batch_pipeline.git"

The GUI binds only to ``127.0.0.1`` and does not call a provider. Preparation
and provider operations remain CLI-only.

Contributor installation
------------------------

For an editable checkout with tests and documentation tools:

.. code-block:: console

   $ git clone https://github.com/rs-kellogg/llm-batch_pipeline.git
   $ cd llm-batch_pipeline
   $ python -m pip install -e '.[dev,docs]'
   $ python -m pytest

Provider credentials
--------------------

Set only the credential for the provider you intend to use:

.. code-block:: console

   $ export OPENAI_API_KEY="your-key"
   $ export ANTHROPIC_API_KEY="your-key"

Credentials are not needed for ``init``, ``gui``, ``validate``, or
``prepare``. They are required when a command contacts the corresponding
provider.

.. warning::

   Never put credentials in ``project.yaml``, prompt files, shared shell
   history, or Git. Keep local secret files outside the repository or add them
   to an appropriate ignore rule before use.

Next step
---------

Continue to the :doc:`basic` guide to create a project and run a small pilot
before submitting a complete batch.
