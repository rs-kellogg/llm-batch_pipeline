from __future__ import annotations

import json
import os

import pandas as pd
import pytest
import yaml

import kellogg_llm_batch.core as core
from kellogg_llm_batch.core import prepare_run

from conftest import FakeAdapter


@pytest.mark.scale
@pytest.mark.skipif(os.environ.get("KLLM_RUN_SCALE_TEST") != "1", reason="set KLLM_RUN_SCALE_TEST=1")
def test_prepare_300000_rows_with_compact_state(example_config, tmp_path, monkeypatch):
    """Acceptance stress test; excluded from the fast default test suite."""
    row_count = 300_000
    source = tmp_path / "large.csv"
    pd.DataFrame(
        {
            "grant_id": [f"G{i:06d}" for i in range(row_count)],
            "project_title": [f"Synthetic title {i}" for i in range(row_count)],
            "abstract": [f"Synthetic abstract {i}" for i in range(row_count)],
            "year": 2026,
            "investigator": "Synthetic Researcher",
            "source_file": "generated",
        }
    ).to_csv(source, index=False)

    raw = yaml.safe_load(example_config.read_text(encoding="utf-8"))
    raw["input"]["path"] = str(source)
    raw["task"]["output_schema"] = str(example_config.parent / "schema.json")
    raw["task"]["rows_per_request"] = 1_000
    raw["prompt"]["system_file"] = str(example_config.parent / "prompts" / "system.txt")
    raw["prompt"]["user_file"] = str(example_config.parent / "prompts" / "user.txt")
    raw["prompt"]["context"]["codebook"]["path"] = str(example_config.parent / "context" / "codebook.csv")
    raw["providers"] = {"openai": raw["providers"]["openai"]}
    raw["providers"]["openai"]["input_price_per_million"] = 0
    raw["providers"]["openai"]["output_price_per_million"] = 0
    raw["output"]["runs_directory"] = str(tmp_path / "runs")
    raw["output"]["write_csv"] = True
    config = tmp_path / "large-project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    monkeypatch.setattr(core, "get_provider", lambda name: FakeAdapter())
    run = prepare_run(config, "openai")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    assert manifest["source_rows"] == row_count
    assert manifest["request_count"] == 300
    assert len(state["segments"]) == 1
    assert (run / "state.json").stat().st_size < 10_000
