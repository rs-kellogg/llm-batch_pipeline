from __future__ import annotations

import json
import shutil

import pandas as pd
import pytest
import yaml

import kellogg_llm_batch.core as core
from kellogg_llm_batch.core import audit_run, compare_runs, prepare_retry, prepare_run, submit_run, sync_run
from kellogg_llm_batch.state import load_state, save_state

from conftest import FakeAdapter


def _temporary_config(example_config, tmp_path):
    raw = yaml.safe_load(example_config.read_text())
    raw["input"]["path"] = str(example_config.parent / "data" / "grants.csv")
    raw["task"]["output_schema"] = str(example_config.parent / "schema.json")
    raw["prompt"]["system_file"] = str(example_config.parent / "prompts" / "system.txt")
    raw["prompt"]["user_file"] = str(example_config.parent / "prompts" / "user.txt")
    raw["prompt"]["context"]["codebook"]["path"] = str(example_config.parent / "context" / "codebook.csv")
    raw["output"]["runs_directory"] = str(tmp_path / "runs")
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_prepare_submit_sync_and_audit(example_config, tmp_path, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    config = _temporary_config(example_config, tmp_path)
    run = prepare_run(config, "openai")
    assert (run / "manifest.json").exists()
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["purpose"] == "production"
    assert manifest["execution"] == "batch"
    assert manifest["cost_estimate"]["execution"] == "batch"
    assert len(list((run / "requests").glob("segment_*.jsonl"))) == 1
    submit_run(run, fake)
    submit_run(run, fake)
    assert fake.submissions == 1
    sync_run(run, adapter=fake)
    results = pd.read_parquet(run / "results" / "results.parquet")
    assert len(results) == 10
    assert results["record_id"].is_unique
    assert audit_run(run)["complete"] is True
    provenance = json.loads((run / "results" / "provenance.json").read_text())
    assert len(provenance["rows"]) == 10
    assert provenance["rows"][0]["source_row_sha256"]
    assert provenance["rows"][0]["config_sha256"]
    assert provenance["rows"][0]["validation_status"] == "valid"
    assert load_state(run)["stage"] == "audited"


def test_compare_exports_disagreements(example_config, tmp_path, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    config = _temporary_config(example_config, tmp_path)
    run_a = prepare_run(config, "openai")
    run_b = prepare_run(config, "openai")
    for run in (run_a, run_b):
        submit_run(run, fake)
        sync_run(run, adapter=fake)
    changed = pd.read_parquet(run_b / "results" / "results.parquet")
    changed.loc[0, "primary_label"] = "technical"
    changed.to_parquet(run_b / "results" / "results.parquet", index=False)
    report = compare_runs(run_a, run_b)
    assert report["disagreement_rows"] >= 1
    assert report["metrics"]["primary_label"]["percent_agreement"] < 1


def test_prepare_sample_then_submit_sync_uses_exact_saved_requests(example_config, tmp_path, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    config = _temporary_config(example_config, tmp_path)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["input"]["fields_sent"] = {
        "research_heading": "project_title",
        "document_body": "abstract",
        "award_year_seen_by_model": "year",
    }
    raw["input"]["required_fields"] = ["document_body"]
    raw["input"]["field_limits"] = {}
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    run = prepare_run(config, "openai", sample_size=4, seed=17)

    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["purpose"] == "pilot"
    assert manifest["execution"] == "sync"
    assert manifest["cost_estimate"]["execution"] == "sync"
    assert manifest["selection"] == {"method": "random", "selected_count": 4, "seed": 17}
    assert manifest["selected_rows"] == 4
    model_records = [json.loads(line) for line in (run / "requests" / "model_records.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(model_records) == 4
    expected_model_fields = {"record_id", *raw["input"]["fields_sent"]}
    assert set(model_records[0]) == expected_model_fields
    rendered = [json.loads(line) for line in (run / "requests" / "rendered_prompts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert '"record_id"' in rendered[0]["user_prompt"]
    for logical_name in raw["input"]["fields_sent"]:
        assert f'"{logical_name}"' in rendered[0]["user_prompt"]
    assert "investigator" not in rendered[0]["user_prompt"]

    state = submit_run(run, fake)
    assert state["status"] == "completed"
    assert state["stage"] == "audited"
    assert len(pd.read_parquet(run / "results" / "results.parquet")) == 4
    sync_calls = fake.sync_calls
    submit_run(run, fake)
    assert fake.sync_calls == sync_calls


def test_prepare_with_explicit_ids_and_request_integrity(example_config, tmp_path, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    config = _temporary_config(example_config, tmp_path)
    ids_file = tmp_path / "pilot_ids.txt"
    ids_file.write_text("GRANT-007\nGRANT-002\n", encoding="utf-8")
    run = prepare_run(config, "openai", ids_file=ids_file)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["selection"]["method"] == "ids_file"
    assert manifest["execution"] == "sync"
    selected = pd.read_parquet(run / "requests" / "canonical_input.parquet")
    assert selected["record_id"].tolist() == ["GRANT-002", "GRANT-007"]

    prompts = run / "requests" / "rendered_prompts.jsonl"
    prompts.write_text(prompts.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact changed"):
        submit_run(run, fake)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sample_size": 2, "ids_file": "unused.txt"}, "only one record selection"),
        ({"seed": 7}, "seed can only be used"),
        ({"sample_size": 11}, "exceeds the 10 available records"),
    ],
)
def test_prepare_rejects_invalid_selection_options(example_config, tmp_path, monkeypatch, kwargs, message):
    monkeypatch.setattr(core, "get_provider", lambda name: FakeAdapter())
    config = _temporary_config(example_config, tmp_path)
    with pytest.raises(ValueError, match=message):
        prepare_run(config, "openai", **kwargs)
    assert not (tmp_path / "runs").exists()


def test_prepare_rejects_duplicate_and_unknown_explicit_ids(example_config, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "get_provider", lambda name: FakeAdapter())
    config = _temporary_config(example_config, tmp_path)
    ids_file = tmp_path / "pilot_ids.txt"
    ids_file.write_text("GRANT-002\nGRANT-002\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate IDs"):
        prepare_run(config, "openai", ids_file=ids_file)
    assert not (tmp_path / "runs").exists()

    ids_file.write_text("GRANT-002\nNOT-IN-SOURCE\n", encoding="utf-8")
    with pytest.raises(ValueError, match="do not exist in source"):
        prepare_run(config, "openai", ids_file=ids_file)
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize(
    ("kwargs", "purpose", "execution"),
    [
        ({"sample_size": 2, "execution": "batch"}, "pilot", "batch"),
        ({"execution": "sync"}, "production", "sync"),
    ],
)
def test_prepare_allows_explicit_execution_override(example_config, tmp_path, monkeypatch, kwargs, purpose, execution):
    monkeypatch.setattr(core, "get_provider", lambda name: FakeAdapter())
    config = _temporary_config(example_config, tmp_path)
    run = prepare_run(config, "openai", **kwargs)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["purpose"] == purpose
    assert manifest["execution"] == execution
    assert manifest["cost_estimate"]["execution"] == execution


class IncompleteAdapter(FakeAdapter):
    def download(self, batch_id, output_path, error_path):
        with output_path.open("w", encoding="utf-8") as handle:
            for index, payload in enumerate(self.batches[batch_id]):
                records = json.loads(payload["body"]["input"].split("Classify every record below:", 1)[1].strip())
                if index == 0:
                    records = records[:-1]
                results = [{"record_id": record["record_id"], "primary_label": "other", "secondary_label": None, "confidence": 0.8, "justification": "Synthetic test result."} for record in records]
                handle.write(json.dumps({"custom_id": payload["custom_id"], "status": "succeeded", "response_text": json.dumps({"results": results})}) + "\n")


def test_manual_retry_contains_only_failed_rows(example_config, tmp_path, monkeypatch):
    fake = IncompleteAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    config = _temporary_config(example_config, tmp_path)
    run = prepare_run(config, "openai")
    submit_run(run, fake)
    sync_run(run, adapter=fake)
    failures = pd.read_parquet(run / "results" / "failures.parquet")
    assert len(set(failures["record_id"])) == 1
    child = prepare_retry(run)
    child_input = pd.read_parquet(child / "requests" / "canonical_input.parquet")
    assert len(child_input) == 1
    assert json.loads((child / "manifest.json").read_text())["parent_run"] == str(run)


def test_retry_blocks_if_source_changed(example_config, tmp_path, monkeypatch):
    fake = IncompleteAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    source = tmp_path / "grants.csv"
    shutil.copy2(example_config.parent / "data" / "grants.csv", source)
    config = _temporary_config(example_config, tmp_path)
    raw = yaml.safe_load(config.read_text())
    raw["input"]["path"] = str(source)
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    run = prepare_run(config, "openai")
    submit_run(run, fake)
    sync_run(run, adapter=fake)
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Source data changed"):
        prepare_retry(run)


def test_state_recovers_last_valid_copy(example_config, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "get_provider", lambda name: FakeAdapter())
    run = prepare_run(_temporary_config(example_config, tmp_path), "openai")
    original = load_state(run)
    save_state(run, {**original, "status": "submitted", "stage": "submitted"})
    (run / "state.json").write_text("{broken", encoding="utf-8")
    recovered = load_state(run)
    assert recovered["status"] == "prepared"
    assert recovered["stage"] == "prepared"
