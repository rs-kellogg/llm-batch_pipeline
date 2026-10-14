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

Clone and install
-----------------

Clone the public repository and install the local checkout in editable mode:

.. code-block:: console

   $ git clone https://github.com/rs-kellogg/llm-batch_pipeline.git
   $ cd llm-batch_pipeline
   $ python -m pip install -e .
   $ kllm-batch --help

To update the package later, pull the latest code from the same checkout. The
editable installation uses the updated local source without requiring another
package installation:

.. code-block:: console

   $ git pull

The standard installation includes the local project builder. After creating
a project, you can open it in your browser:

.. code-block:: console

   $ kllm-batch init my-project
   $ kllm-batch gui my-project

Using the GUI is optional. It binds only to ``127.0.0.1`` and does not call a
provider. Preparation and provider operations remain CLI-only.

Contributor tools
-----------------

To add tests and documentation tools to the same editable checkout:

.. code-block:: console

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

For a self-hosted Ollama or vLLM server (the ``openai_compatible`` provider,
see :doc:`project_settings`), most local deployments need no credential at
all — only ``providers.openai_compatible.base_url`` in ``project.yaml``. If
your server sits behind authentication, set ``api_key_env_var`` to the name
of an environment variable and export it the same way:

.. code-block:: console

   $ export MY_LOCAL_SERVER_API_KEY="your-key"

.. warning::

   Never put credentials in ``project.yaml``, prompt files, shared shell
   history, or Git. Keep local secret files outside the repository or add them
   to an appropriate ignore rule before use.

Next step
---------

Continue to the :doc:`basic` guide to create a project and run a small pilot
before submitting a complete batch.
