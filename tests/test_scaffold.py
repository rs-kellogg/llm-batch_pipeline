import json
from pathlib import Path

import pandas as pd
import yaml

import kellogg_llm_batch.core as core
from kellogg_llm_batch.authoring import load_project_draft, render_project_preview
from kellogg_llm_batch.core import prepare_run
from kellogg_llm_batch.scaffold import scaffold_project
from kellogg_llm_batch.providers.base import ProviderAdapter
from kellogg_llm_batch.validation import validate_project
from conftest import FakeAdapter


def test_scaffold_is_immediately_valid(tmp_path):
    root = scaffold_project(tmp_path / "project")
    report = validate_project(root / "project.yaml")
    assert report["valid"] is True


def test_scaffold_matches_checked_in_grant_example(tmp_path):
    root = scaffold_project(tmp_path / "project")
    example = Path(__file__).resolve().parents[1] / "examples" / "grant_coding"

    for relative in (
        "data/grants.csv",
        "context/codebook.csv",
        "schema.json",
        "prompts/system.txt",
        "prompts/user.txt",
    ):
        assert (root / relative).read_bytes() == (example / relative).read_bytes()
    assert yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8")) == yaml.safe_load(
        (example / "project.yaml").read_text(encoding="utf-8")
    )
    assert (root / "runs").is_dir()
    assert not list((root / "runs").iterdir())
    assert (root / ".gitignore").read_text(encoding="utf-8") == "runs/\n.env\n"
    assert not (root / "data" / "invalid_duplicate_ids.csv").exists()
    assert not (root / "expected").exists()


def test_scaffold_codebook_schema_and_preview_agree(tmp_path):
    root = scaffold_project(tmp_path / "project")
    config = yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8"))
    schema = json.loads((root / "schema.json").read_text(encoding="utf-8"))
    input_rows = pd.read_csv(root / "data" / "grants.csv")
    codebook = pd.read_csv(root / "context" / "codebook.csv")

    assert len(input_rows) == 10
    assert input_rows["grant_id"].is_unique
    assert input_rows[["project_title", "abstract"]].notna().all().all()
    assert set(codebook.columns) == {"label", "definition"}
    assert codebook["label"].tolist() == schema["properties"]["primary_label"]["enum"]
    assert schema["properties"]["secondary_label"]["enum"] == codebook["label"].tolist() + [None]
    assert codebook["label"].tolist() == [
        "financial", "organizational", "technical", "other"
    ]
    assert config["input"]["id_column"] == "grant_id"
    assert config["input"]["columns_preserved"] == ["year", "investigator", "source_file"]
    assert config["prompt"]["context"]["codebook"] == {
        "path": "context/codebook.csv",
        "format": "csv",
    }

    preview = render_project_preview(load_project_draft(root))
    assert "financial" in preview.user_prompt
    assert "organizational" in preview.user_prompt
    assert "technical" in preview.user_prompt
    assert "other" in preview.user_prompt
    assert preview.record["record_id"] == input_rows.iloc[0]["grant_id"]
    assert preview.record["project_title"] == input_rows.iloc[0]["project_title"]
    assert all(column not in preview.record for column in config["input"]["columns_preserved"])


def test_scaffold_prepares_four_record_pilot_locally(tmp_path, monkeypatch):
    root = scaffold_project(tmp_path / "project")
    adapter = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: adapter)

    run = prepare_run(root / "project.yaml", "openai", sample_size=4, seed=42)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    selected = pd.read_parquet(run / "input_snapshot" / "canonical_input.parquet")
    segment_files = sorted((run / "api_requests").glob("segment_*.jsonl"))
    requests = [
        request
        for segment_file in segment_files
        for request in ProviderAdapter.read_jsonl(segment_file)
    ]

    assert manifest["selected_rows"] == len(selected) == 4
    assert manifest["execution"] == "sync"
    assert len(requests) == 2
    assert "financial" in requests[0]["body"]["input"]
    assert "GRANT-" in requests[0]["body"]["input"]
    assert adapter.submissions == 0
