"""Sphinx configuration for the Kellogg LLM Batch user guide."""

from __future__ import annotations

import importlib.metadata


project = "Kellogg LLM Batch"
author = "Kellogg Research Support"
copyright = "2026, Kellogg Research Support"

try:
    release = importlib.metadata.version("kellogg-llm-batch")
except importlib.metadata.PackageNotFoundError:
    release = "development"

extensions: list[str] = []
templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_title = "Kellogg LLM Batch"
html_static_path: list[str] = []
html_theme_options = {
    "navigation_depth": 3,
    "collapse_navigation": False,
}
