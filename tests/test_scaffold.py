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


def test_scaffold_matches_checked_in_minimal_task_example(tmp_path):
    root = scaffold_project(tmp_path / "project")
    example = Path(__file__).resolve().parents[1] / "examples" / "minimal_task"

    for relative in (
        "data/input-data.csv",
        "context/codebook.csv",
        "schema.json",
        "prompts/system.txt",
        "prompts/user.txt",
    ):
        assert (root / relative).read_bytes() == (example / relative).read_bytes()
    schema_text = (root / "schema.json").read_text(encoding="utf-8")
    assert schema_text == json.dumps(json.loads(schema_text), indent=2, ensure_ascii=False) + "\n"
    generated_config = yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8"))
    example_config = yaml.safe_load((example / "project.yaml").read_text(encoding="utf-8"))
    assert generated_config["input"]["path"] == "data/input-data.csv"
    assert (root / generated_config["input"]["path"]).is_file()
    assert generated_config == example_config
    assert "options" not in generated_config["providers"]["openai"]
    assert (root / "runs").is_dir()
    assert not list((root / "runs").iterdir())
    assert [path.name for path in (root / "context").iterdir()] == ["codebook.csv"]
    assert (root / ".gitignore").read_text(encoding="utf-8") == "runs/\n.env\n"


def test_scaffold_minimal_task_schema_and_preview_agree(tmp_path):
    root = scaffold_project(tmp_path / "project")
    config = yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8"))
    schema = json.loads((root / "schema.json").read_text(encoding="utf-8"))
    input_rows = pd.read_csv(root / "data" / "input-data.csv")

    assert len(input_rows) == 2
    assert input_rows["id"].is_unique
    assert input_rows["text"].notna().all()
    assert set(schema["properties"]) == {"label", "rationale"}
    assert schema["required"] == ["label", "rationale"]
    assert schema["additionalProperties"] is False
    assert config["input"]["id_column"] == "id"
    assert config["input"]["fields_sent"] == {"text": "text"}
    assert config["input"]["required_fields"] == []
    assert "evaluation" not in config
    assert config["prompt"]["context"] == {"codebook": {"path": "context/codebook.csv", "format": "csv"}}
    codebook = pd.read_csv(root / "context" / "codebook.csv")
    assert list(codebook.columns) == ["label", "definition"]

    preview = render_project_preview(load_project_draft(root))
    assert input_rows.iloc[0]["text"] in preview.user_prompt
    assert codebook.iloc[0]["label"] in preview.user_prompt
    assert preview.record["record_id"] == input_rows.iloc[0]["id"]
    assert preview.record["text"] == input_rows.iloc[0]["text"]


def test_scaffold_prepares_two_record_pilot_locally(tmp_path, monkeypatch):
    root = scaffold_project(tmp_path / "project")
    adapter = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name, **_: adapter)

    run = prepare_run(root / "project.yaml", "openai", sample_size=2, seed=42)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    project_snapshot = yaml.safe_load(
        (run / "project_snapshot" / "project.yaml").read_text(encoding="utf-8")
    )
    selected = pd.read_parquet(run / "input_snapshot" / "canonical_input.parquet")
    segment_files = sorted((run / "api_requests").glob("segment_*.jsonl"))
    requests = [
        request
        for segment_file in segment_files
        for request in ProviderAdapter.read_jsonl(segment_file)
    ]

    assert manifest["selected_rows"] == len(selected) == 2
    assert manifest["execution"] == "sync"
    assert manifest["gold_columns"] == {}
    assert manifest["provider_options"] == {}
    assert project_snapshot["providers"]["openai"].get("options", {}) == manifest["provider_options"]
    assert manifest.get("gold_labels_file") is None
    assert len(requests) == 2
    assert "EX-" in requests[0]["body"]["input"]
    assert adapter.submissions == 0
